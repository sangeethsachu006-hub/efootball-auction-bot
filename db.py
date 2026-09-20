import os
import sqlite3
import threading
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "auction.db")
DB_LOCK = threading.RLock()

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS auctions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    host_id INTEGER NOT NULL,
    max_participants INTEGER NOT NULL CHECK(max_participants BETWEEN 1 AND 100),
    state TEXT NOT NULL CHECK(state IN ('DRAFT','LOBBY','RUNNING','PAUSED','COMPLETED','CANCELLED')),
    current_player_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS participants (
    auction_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    balance REAL NOT NULL DEFAULT 100.0,
    joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (auction_id, user_id),
    FOREIGN KEY(auction_id) REFERENCES auctions(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(telegram_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    auction_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    position TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','CURRENT','SOLD','UNSOLD')),
    sequence INTEGER NOT NULL,
    sold_to INTEGER,
    sold_price REAL,
    FOREIGN KEY(auction_id) REFERENCES auctions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS bids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    auction_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(auction_id) REFERENCES auctions(id) ON DELETE CASCADE,
    FOREIGN KEY(player_id) REFERENCES players(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS holdings (
    auction_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    price REAL NOT NULL,
    won_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(auction_id, player_id),
    FOREIGN KEY(auction_id) REFERENCES auctions(id) ON DELETE CASCADE,
    FOREIGN KEY(player_id) REFERENCES players(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_players_auction_status ON players(auction_id, status, sequence);
CREATE INDEX IF NOT EXISTS idx_bids_player ON bids(auction_id, player_id, id);
CREATE INDEX IF NOT EXISTS idx_holdings_user ON holdings(auction_id, user_id);
"""

@contextmanager
def conn():
    with DB_LOCK:
        c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=30000")
        try:
            yield c
        finally:
            c.close()

def init_db():
    with conn() as c:
        c.executescript(SCHEMA)

def upsert_user(user):
    with conn() as c:
        c.execute("""INSERT INTO users(telegram_id,username,first_name)
                     VALUES(?,?,?)
                     ON CONFLICT(telegram_id) DO UPDATE SET
                     username=excluded.username, first_name=excluded.first_name""",
                  (user.id, user.username, user.first_name))

def create_auction(host_id, name, max_participants, code):
    with conn() as c:
        cur=c.execute("""INSERT INTO auctions(code,name,host_id,max_participants,state)
                        VALUES(?,?,?,?, 'LOBBY')""",
                     (code,name,max_participants,host_id))
        return cur.lastrowid

def get_auction(code):
    with conn() as c:
        return c.execute("SELECT * FROM auctions WHERE code=?", (code,)).fetchone()

def get_auction_by_id(aid):
    with conn() as c:
        return c.execute("SELECT * FROM auctions WHERE id=?", (aid,)).fetchone()

def count_players(aid):
    with conn() as c:
        return c.execute("SELECT COUNT(*) n FROM players WHERE auction_id=?", (aid,)).fetchone()["n"]

def add_players(aid, names):
    with conn() as c:
        current=c.execute("SELECT COUNT(*) n FROM players WHERE auction_id=?", (aid,)).fetchone()["n"]
        if current + len(names) > 500:
            raise ValueError("Player limit is 500.")
        for i,name in enumerate(names, start=current+1):
            c.execute("INSERT INTO players(auction_id,name,sequence) VALUES(?,?,?)",
                      (aid,name.strip(),i))

def add_player(aid,name):
    add_players(aid,[name])

def remove_player(aid, player_id):
    with conn() as c:
        p=c.execute("SELECT * FROM players WHERE id=? AND auction_id=?", (player_id,aid)).fetchone()
        if not p or p["status"] != "PENDING":
            return False
        c.execute("DELETE FROM players WHERE id=?", (player_id,))
        return True

def list_players(aid, status=None, limit=100):
    with conn() as c:
        if status:
            return c.execute("SELECT * FROM players WHERE auction_id=? AND status=? ORDER BY sequence LIMIT ?",
                             (aid,status,limit)).fetchall()
        return c.execute("SELECT * FROM players WHERE auction_id=? ORDER BY sequence LIMIT ?",
                         (aid,limit)).fetchall()

def join_auction(aid,user_id,initial_balance):
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        a=c.execute("SELECT * FROM auctions WHERE id=?", (aid,)).fetchone()
        if not a or a["state"] not in ("LOBBY",):
            c.execute("ROLLBACK")
            return False,"Auction is not accepting participants."
        n=c.execute("SELECT COUNT(*) n FROM participants WHERE auction_id=?", (aid,)).fetchone()["n"]
        if n >= a["max_participants"]:
            c.execute("ROLLBACK")
            return False,"This auction is full."
        exists=c.execute("SELECT 1 FROM participants WHERE auction_id=? AND user_id=?", (aid,user_id)).fetchone()
        if exists:
            c.execute("ROLLBACK")
            return False,"You already joined this auction."
        c.execute("INSERT INTO participants(auction_id,user_id,balance) VALUES(?,?,?)",
                  (aid,user_id,initial_balance))
        c.execute("COMMIT")
        return True,"Joined"

def participant(aid,user_id):
    with conn() as c:
        return c.execute("SELECT * FROM participants WHERE auction_id=? AND user_id=?",
                         (aid,user_id)).fetchone()

def participant_count(aid):
    with conn() as c:
        return c.execute("SELECT COUNT(*) n FROM participants WHERE auction_id=?", (aid,)).fetchone()["n"]

def start_auction(aid,host_id):
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        a=c.execute("SELECT * FROM auctions WHERE id=?", (aid,)).fetchone()
        if not a or a["host_id"] != host_id or a["state"] != "LOBBY":
            c.execute("ROLLBACK")
            return False,"Only the host can start a lobby auction."
        p=c.execute("SELECT * FROM players WHERE auction_id=? AND status='PENDING' ORDER BY sequence LIMIT 1",(aid,)).fetchone()
        if not p:
            c.execute("ROLLBACK")
            return False,"Add at least one player first."
        c.execute("UPDATE players SET status='CURRENT' WHERE id=?", (p["id"],))
        c.execute("UPDATE auctions SET state='RUNNING', current_player_id=?, started_at=CURRENT_TIMESTAMP WHERE id=?",
                  (p["id"],aid))
        c.execute("COMMIT")
        return True,"Started"

def set_state(aid,state):
    with conn() as c:
        c.execute("UPDATE auctions SET state=? WHERE id=?", (state,aid))

def current_player(aid):
    with conn() as c:
        a=c.execute("SELECT * FROM auctions WHERE id=?", (aid,)).fetchone()
        if not a or not a["current_player_id"]:
            return None
        return c.execute("SELECT * FROM players WHERE id=?", (a["current_player_id"],)).fetchone()

def bid(aid,user_id):
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        a=c.execute("SELECT * FROM auctions WHERE id=?", (aid,)).fetchone()
        if not a or a["state"] != "RUNNING" or not a["current_player_id"]:
            c.execute("ROLLBACK")
            return False,"Auction is not accepting bids.",None
        p=c.execute("SELECT * FROM players WHERE id=?", (a["current_player_id"],)).fetchone()
        if not p or p["status"] != "CURRENT":
            c.execute("ROLLBACK")
            return False,"No active player.",None
        part=c.execute("SELECT * FROM participants WHERE auction_id=? AND user_id=?", (aid,user_id)).fetchone()
        if not part:
            c.execute("ROLLBACK")
            return False,"Join the auction first.",None
        last=c.execute("SELECT * FROM bids WHERE auction_id=? AND player_id=? ORDER BY id DESC LIMIT 1",
                       (aid,p["id"])).fetchone()
        if last and last["user_id"] == user_id:
            c.execute("ROLLBACK")
            return False,"You are already the highest bidder.",None
        new_amount = 2.0 if not last else round(last["amount"] + 0.5, 2)
        if part["balance"] + 1e-9 < new_amount:
            c.execute("ROLLBACK")
            return False,f"Insufficient balance. You need {new_amount:.1f} Cr.",None
        if last:
            c.execute("UPDATE participants SET balance=balance+? WHERE auction_id=? AND user_id=?",
                      (last["amount"],aid,last["user_id"]))
        c.execute("UPDATE participants SET balance=balance-? WHERE auction_id=? AND user_id=?",
                  (new_amount,aid,user_id))
        c.execute("INSERT INTO bids(auction_id,player_id,user_id,amount) VALUES(?,?,?,?)",
                  (aid,p["id"],user_id,new_amount))
        c.execute("COMMIT")
        return True,"Bid accepted",{"player":p,"amount":new_amount,"previous":last}

def sell_current(aid):
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        a=c.execute("SELECT * FROM auctions WHERE id=?", (aid,)).fetchone()
        if not a or not a["current_player_id"]:
            c.execute("ROLLBACK")
            return None
        p=c.execute("SELECT * FROM players WHERE id=?", (a["current_player_id"],)).fetchone()
        last=c.execute("SELECT * FROM bids WHERE auction_id=? AND player_id=? ORDER BY id DESC LIMIT 1",
                       (aid,p["id"])).fetchone()
        if last:
            c.execute("UPDATE players SET status='SOLD', sold_to=?, sold_price=? WHERE id=?",
                      (last["user_id"],last["amount"],p["id"]))
            c.execute("INSERT OR REPLACE INTO holdings(auction_id,player_id,user_id,price) VALUES(?,?,?,?)",
                      (aid,p["id"],last["user_id"],last["amount"]))
        else:
            c.execute("UPDATE players SET status='UNSOLD' WHERE id=?", (p["id"],))
        nxt=c.execute("SELECT * FROM players WHERE auction_id=? AND status='PENDING' ORDER BY sequence LIMIT 1",(aid,)).fetchone()
        if nxt:
            c.execute("UPDATE players SET status='CURRENT' WHERE id=?", (nxt["id"],))
            c.execute("UPDATE auctions SET current_player_id=? WHERE id=?", (nxt["id"],aid))
        else:
            c.execute("UPDATE auctions SET current_player_id=NULL,state='COMPLETED',ended_at=CURRENT_TIMESTAMP WHERE id=?", (aid,))
        c.execute("COMMIT")
        return {"sold_player":p,"last":last,"next":nxt}

def mark_unsold_and_next(aid):
    return sell_current(aid)

def leaderboard(aid):
    with conn() as c:
        return c.execute("""SELECT p.user_id,u.username,u.first_name,
                            COUNT(h.player_id) players_won,
                            COALESCE(SUM(h.price),0) spent,
                            p.balance
                            FROM participants p
                            JOIN users u ON u.telegram_id=p.user_id
                            LEFT JOIN holdings h ON h.auction_id=p.auction_id AND h.user_id=p.user_id
                            WHERE p.auction_id=?
                            GROUP BY p.user_id
                            ORDER BY players_won DESC, spent DESC""",(aid,)).fetchall()

def my_holdings(aid,user_id):
    with conn() as c:
        return c.execute("""SELECT pl.name,h.price FROM holdings h
                            JOIN players pl ON pl.id=h.player_id
                            WHERE h.auction_id=? AND h.user_id=? ORDER BY h.won_at""",
                         (aid,user_id)).fetchall()

def bid_history(aid,player_id,limit=20):
    with conn() as c:
        return c.execute("""SELECT b.amount,b.created_at,u.username,u.first_name
                            FROM bids b JOIN users u ON u.telegram_id=b.user_id
                            WHERE b.auction_id=? AND b.player_id=?
                            ORDER BY b.id DESC LIMIT ?""",(aid,player_id,limit)).fetchall()

def stats(aid):
    with conn() as c:
        return {
            "players": c.execute("SELECT COUNT(*) n FROM players WHERE auction_id=?",(aid,)).fetchone()["n"],
            "sold": c.execute("SELECT COUNT(*) n FROM players WHERE auction_id=? AND status='SOLD'",(aid,)).fetchone()["n"],
            "unsold": c.execute("SELECT COUNT(*) n FROM players WHERE auction_id=? AND status='UNSOLD'",(aid,)).fetchone()["n"],
            "pending": c.execute("SELECT COUNT(*) n FROM players WHERE auction_id=? AND status='PENDING'",(aid,)).fetchone()["n"],
            "participants": participant_count(aid)
        }

def live_auctions(limit=10):
    with conn() as c:
        return c.execute("""SELECT * FROM auctions WHERE state IN ('LOBBY','RUNNING','PAUSED')
                            ORDER BY id DESC LIMIT ?""",(limit,)).fetchall()


def user_history(user_id, limit=10):
    with conn() as c:
        return c.execute("""SELECT a.code,a.name,a.state,a.created_at,
                                   COUNT(h.player_id) players_won,
                                   COALESCE(SUM(h.price),0) spent
                            FROM auctions a
                            JOIN participants p ON p.auction_id=a.id AND p.user_id=?
                            LEFT JOIN holdings h ON h.auction_id=a.id AND h.user_id=?
                            GROUP BY a.id
                            ORDER BY a.id DESC LIMIT ?""",
                         (user_id,user_id,limit)).fetchall()

def reauction_player(aid, host_id, player_id):
    with conn() as c:
        c.execute("BEGIN IMMEDIATE")
        a=c.execute("SELECT * FROM auctions WHERE id=?", (aid,)).fetchone()
        if not a or a["host_id"] != host_id or a["state"] != "LOBBY":
            c.execute("ROLLBACK")
            return False,"Only the host can re-auction while the auction is in lobby."
        p=c.execute("SELECT * FROM players WHERE id=? AND auction_id=?", (player_id,aid)).fetchone()
        if not p or p["status"] != "UNSOLD":
            c.execute("ROLLBACK")
            return False,"Player is not an unsold player."
        c.execute("UPDATE players SET status='PENDING', sold_to=NULL, sold_price=NULL WHERE id=?", (player_id,))
        c.execute("COMMIT")
        return True,"Player returned to the queue."
