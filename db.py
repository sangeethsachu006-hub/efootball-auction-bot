import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any


DB_FILE = "auction.db"


class Database:
    def __init__(self, db_file: str = DB_FILE):
        self.db_file = db_file
        self.init_db()

    def connect(self):
        conn = sqlite3.connect(
            self.db_file,
            timeout=30,
            check_same_thread=False
        )
        conn.row_factory = sqlite3.Row

        # Better SQLite performance
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")

        return conn

    # ========================================================
    # DATABASE INITIALIZATION
    # ========================================================

    def init_db(self):
        conn = self.connect()

        try:
            cursor = conn.cursor()

            # ------------------------------------------------
            # PARTICIPANTS
            # ------------------------------------------------

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS participants (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    username TEXT DEFAULT '',
                    budget REAL NOT NULL DEFAULT 100.0,
                    spent REAL NOT NULL DEFAULT 0.0,
                    registered_at TEXT NOT NULL
                )
            """)

            # ------------------------------------------------
            # PLAYERS
            # ------------------------------------------------

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS players (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    sold_to INTEGER,
                    sold_price REAL DEFAULT 0.0,
                    created_at TEXT NOT NULL,

                    FOREIGN KEY (sold_to)
                    REFERENCES participants(id)
                    ON DELETE SET NULL
                )
            """)

            # ------------------------------------------------
            # SQUADS
            # ------------------------------------------------

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS squad (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    participant_id INTEGER NOT NULL,
                    player_id INTEGER NOT NULL UNIQUE,
                    purchase_price REAL NOT NULL,

                    FOREIGN KEY (participant_id)
                    REFERENCES participants(id)
                    ON DELETE CASCADE,

                    FOREIGN KEY (player_id)
                    REFERENCES players(id)
                    ON DELETE CASCADE
                )
            """)

            # ------------------------------------------------
            # AUCTION
            # ------------------------------------------------

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS auction (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    active INTEGER NOT NULL DEFAULT 0,
                    player_id INTEGER,
                    highest_bid REAL NOT NULL DEFAULT 0.0,
                    highest_bidder INTEGER,
                    started_at TEXT,
                    ends_at TEXT,

                    FOREIGN KEY (player_id)
                    REFERENCES players(id)
                    ON DELETE SET NULL,

                    FOREIGN KEY (highest_bidder)
                    REFERENCES participants(id)
                    ON DELETE SET NULL
                )
            """)

            # ------------------------------------------------
            # BID HISTORY
            # ------------------------------------------------

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bids (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id INTEGER NOT NULL,
                    participant_id INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    increment REAL NOT NULL,
                    created_at TEXT NOT NULL,

                    FOREIGN KEY (player_id)
                    REFERENCES players(id)
                    ON DELETE CASCADE,

                    FOREIGN KEY (participant_id)
                    REFERENCES participants(id)
                    ON DELETE CASCADE
                )
            """)

            # ------------------------------------------------
            # SETTINGS
            # ------------------------------------------------

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

            # ------------------------------------------------
            # DEFAULT SETTINGS
            # ------------------------------------------------

            default_settings = {
                "max_participants": "500",
                "max_players": "500",
                "starting_balance": "100",
                "minimum_start_bid": "2",
                "minimum_increment": "0.5",
                "maximum_increment": "2",
                "auction_duration": "30"
            }

            for key, value in default_settings.items():
                cursor.execute("""
                    INSERT OR IGNORE INTO settings
                    (key, value)
                    VALUES (?, ?)
                """, (key, value))

            # ------------------------------------------------
            # DEFAULT AUCTION ROW
            # ------------------------------------------------

            cursor.execute("""
                INSERT OR IGNORE INTO auction
                (id, active)
                VALUES (1, 0)
            """)

            conn.commit()

        finally:
            conn.close()

    # ========================================================
    # SETTINGS
    # ========================================================

    def get_setting(self, key: str):
        conn = self.connect()

        try:
            row = conn.execute("""
                SELECT value
                FROM settings
                WHERE key = ?
            """, (key,)).fetchone()

            if row:
                return row["value"]

            return None

        finally:
            conn.close()

    def set_setting(self, key: str, value: Any):
        conn = self.connect()

        try:
            conn.execute("""
                INSERT INTO settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key)
                DO UPDATE SET value = excluded.value
            """, (key, str(value)))

            conn.commit()

        finally:
            conn.close()

    # ========================================================
    # PARTICIPANTS
    # ========================================================

    def participant_count(self):
        conn = self.connect()

        try:
            row = conn.execute("""
                SELECT COUNT(*) AS count
                FROM participants
            """).fetchone()

            return row["count"]

        finally:
            conn.close()

    def participant_exists(self, user_id: int):
        conn = self.connect()

        try:
            row = conn.execute("""
                SELECT id
                FROM participants
                WHERE id = ?
            """, (user_id,)).fetchone()

            return row is not None

        finally:
            conn.close()

    def add_participant(
        self,
        user_id: int,
        name: str,
        username: str = ""
    ):
        max_participants = int(
            self.get_setting("max_participants")
        )

        if self.participant_count() >= max_participants:
            return False, "Participant limit reached."

        if self.participant_exists(user_id):
            return False, "Already registered."

        starting_balance = float(
            self.get_setting("starting_balance")
        )

        conn = self.connect()

        try:
            conn.execute("""
                INSERT INTO participants
                (
                    id,
                    name,
                    username,
                    budget,
                    spent,
                    registered_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                user_id,
                name,
                username,
                starting_balance,
                0.0,
                datetime.now().isoformat()
            ))

            conn.commit()

            return True, "Registration successful."

        finally:
            conn.close()

    def get_participant(self, user_id: int):
        conn = self.connect()

        try:
            row = conn.execute("""
                SELECT
                    id,
                    name,
                    username,
                    budget,
                    spent,
                    ROUND(budget - spent, 2) AS balance,
                    registered_at
                FROM participants
                WHERE id = ?
            """, (user_id,)).fetchone()

            return dict(row) if row else None

        finally:
            conn.close()

    def get_all_participants(self):
        conn = self.connect()

        try:
            rows = conn.execute("""
                SELECT
                    p.id,
                    p.name,
                    p.username,
                    p.budget,
                    p.spent,
                    ROUND(
                        p.budget - p.spent,
                        2
                    ) AS balance,

                    (
                        SELECT COUNT(*)
                        FROM squad s
                        WHERE s.participant_id = p.id
                    ) AS squad_count

                FROM participants p
                ORDER BY p.name COLLATE NOCASE
            """).fetchall()

            return [dict(row) for row in rows]

        finally:
            conn.close()

    def get_balance(self, user_id: int):
        participant = self.get_participant(user_id)

        if not participant:
            return 0.0

        return round(
            participant["budget"]
            - participant["spent"],
            2
        )

    # ========================================================
    # PLAYERS
    # ========================================================

    def player_count(self):
        conn = self.connect()

        try:
            row = conn.execute("""
                SELECT COUNT(*) AS count
                FROM players
            """).fetchone()

            return row["count"]

        finally:
            conn.close()

    def add_player(self, name: str):
        max_players = int(
            self.get_setting("max_players")
        )

        if self.player_count() >= max_players:
            return False, None, "Player limit reached."

        conn = self.connect()

        try:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO players
                (
                    name,
                    status,
                    created_at
                )
                VALUES (?, 'pending', ?)
            """, (
                name,
                datetime.now().isoformat()
            ))

            player_id = cursor.lastrowid

            conn.commit()

            return True, player_id, "Player added."

        finally:
            conn.close()

    def get_player(self, player_id: int):
        conn = self.connect()

        try:
            row = conn.execute("""
                SELECT
                    p.id,
                    p.name,
                    p.status,
                    p.sold_to,
                    p.sold_price,
                    p.created_at,
                    pa.name AS winner_name

                FROM players p

                LEFT JOIN participants pa
                    ON pa.id = p.sold_to

                WHERE p.id = ?
            """, (player_id,)).fetchone()

            return dict(row) if row else None

        finally:
            conn.close()

    def get_all_players(self):
        conn = self.connect()

        try:
            rows = conn.execute("""
                SELECT
                    p.id,
                    p.name,
                    p.status,
                    p.sold_to,
                    p.sold_price,
                    p.created_at,
                    pa.name AS winner_name

                FROM players p

                LEFT JOIN participants pa
                    ON pa.id = p.sold_to

                ORDER BY p.id ASC
            """).fetchall()

            return [dict(row) for row in rows]

        finally:
            conn.close()

    def get_pending_players(self):
        conn = self.connect()

        try:
            rows = conn.execute("""
                SELECT
                    id,
                    name,
                    status
                FROM players
                WHERE status = 'pending'
                ORDER BY id ASC
            """).fetchall()

            return [dict(row) for row in rows]

        finally:
            conn.close()

    def remove_player(self, player_id: int):
        player = self.get_player(player_id)

        if not player:
            return False, "Player not found."

        if player["status"] == "sold":
            return False, "Sold player cannot be removed."

        auction = self.get_auction()

        if (
            auction
            and auction["active"]
            and auction["player_id"] == player_id
        ):
            return False, "Player is currently being auctioned."

        conn = self.connect()

        try:
            conn.execute("""
                DELETE FROM players
                WHERE id = ?
            """, (player_id,))

            conn.commit()

            return True, "Player removed."

        finally:
            conn.close()

    # ========================================================
    # AUCTION
    # ========================================================

    def get_auction(self):
        conn = self.connect()

        try:
            row = conn.execute("""
                SELECT
                    a.id,
                    a.active,
                    a.player_id,
                    a.highest_bid,
                    a.highest_bidder,
                    a.started_at,
                    a.ends_at,

                    p.name AS player_name,

                    pa.name AS bidder_name

                FROM auction a

                LEFT JOIN players p
                    ON p.id = a.player_id

                LEFT JOIN participants pa
                    ON pa.id = a.highest_bidder

                WHERE a.id = 1
            """).fetchone()

            return dict(row) if row else None

        finally:
            conn.close()

    def start_auction(
        self,
        player_id: int,
        started_at: str,
        ends_at: str
    ):
        player = self.get_player(player_id)

        if not player:
            return False, "Player not found."

        if player["status"] != "pending":
            return False, "Player is not pending."

        current = self.get_auction()

        if current and current["active"]:
            return False, "Another auction is active."

        conn = self.connect()

        try:
            conn.execute("""
                UPDATE players
                SET status = 'auctioned'
                WHERE id = ?
            """, (player_id,))

            conn.execute("""
                UPDATE auction

                SET
                    active = 1,
                    player_id = ?,
                    highest_bid = 0,
                    highest_bidder = NULL,
                    started_at = ?,
                    ends_at = ?

                WHERE id = 1
            """, (
                player_id,
                started_at,
                ends_at
            ))

            conn.commit()

            return True, "Auction started."

        finally:
            conn.close()

    def place_bid(
        self,
        user_id: int,
        increment: float
    ):
        """
        Places a bid safely inside one SQLite transaction.

        Returns:
            {
                success: bool,
                message: str,
                amount: float
            }
        """

        conn = self.connect()

        try:
            conn.execute("BEGIN IMMEDIATE")

            # ------------------------------------------------
            # Participant
            # ------------------------------------------------

            participant = conn.execute("""
                SELECT
                    id,
                    name,
                    budget,
                    spent
                FROM participants
                WHERE id = ?
            """, (user_id,)).fetchone()

            if not participant:
                conn.rollback()

                return {
                    "success": False,
                    "message": "You are not registered.",
                    "amount": 0
                }

            balance = round(
                participant["budget"]
                - participant["spent"],
                2
            )

            # ------------------------------------------------
            # Auction
            # ------------------------------------------------

            auction = conn.execute("""
                SELECT
                    active,
                    player_id,
                    highest_bid,
                    highest_bidder,
                    ends_at
                FROM auction
                WHERE id = 1
            """).fetchone()

            if not auction or not auction["active"]:
                conn.rollback()

                return {
                    "success": False,
                    "message": "No active auction.",
                    "amount": 0
                }

            # ------------------------------------------------
            # Time validation
            # ------------------------------------------------

            if auction["ends_at"]:

                end_time = datetime.fromisoformat(
                    auction["ends_at"]
                )

                if datetime.now() >= end_time:
                    conn.rollback()

                    return {
                        "success": False,
                        "message": "Auction time is over.",
                        "amount": 0
                    }

            # ------------------------------------------------
            # Cannot bid against yourself
            # ------------------------------------------------

            if (
                auction["highest_bidder"]
                == user_id
            ):
                conn.rollback()

                return {
                    "success": False,
                    "message":
                        "You are already the highest bidder.",
                    "amount": 0
                }

            current_bid = float(
                auction["highest_bid"]
            )

            minimum_increment = float(
                self.get_setting(
                    "minimum_increment"
                )
            )

            maximum_increment = float(
                self.get_setting(
                    "maximum_increment"
                )
            )

            minimum_start_bid = float(
                self.get_setting(
                    "minimum_start_bid"
                )
            )

            # ------------------------------------------------
            # Validate increment
            # ------------------------------------------------

            if current_bid > 0:

                if increment < minimum_increment:
                    conn.rollback()

                    return {
                        "success": False,
                        "message":
                            "Minimum increment is 0.5 Cr.",
                        "amount": 0
                    }

                if increment > maximum_increment:
                    conn.rollback()

                    return {
                        "success": False,
                        "message":
                            "Maximum increment is 2 Cr.",
                        "amount": 0
                    }

                new_bid = round(
                    current_bid + increment,
                    2
                )

            else:

                # First bid must be at least 2 Cr.
                if increment < 0:
                    conn.rollback()

                    return {
                        "success": False,
                        "message": "Invalid bid.",
                        "amount": 0
                    }

                new_bid = minimum_start_bid

                # The UI normally sends the desired
                # increase from the starting bid.
                if increment > 0:
                    new_bid = round(
                        minimum_start_bid
                        + increment,
                        2
                    )

            # ------------------------------------------------
            # Balance validation
            # ------------------------------------------------

            if new_bid > balance:

                conn.rollback()

                return {
                    "success": False,
                    "message":
                        f"Insufficient balance. "
                        f"Available: {balance:.1f} Cr.",
                    "amount": 0
                }

            # ------------------------------------------------
            # Save bid
            # ------------------------------------------------

            conn.execute("""
                UPDATE auction
                SET
                    highest_bid = ?,
                    highest_bidder = ?
                WHERE id = 1
            """, (
                new_bid,
                user_id
            ))

            conn.execute("""
                INSERT INTO bids
                (
                    player_id,
                    participant_id,
                    amount,
                    increment,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?)
            """, (
                auction["player_id"],
                user_id,
                new_bid,
                increment,
                datetime.now().isoformat()
            ))

            conn.commit()

            return {
                "success": True,
                "message": "Bid placed.",
                "amount": new_bid
            }

        except Exception as e:

            conn.rollback()

            return {
                "success": False,
                "message": f"Database error: {e}",
                "amount": 0
            }

        finally:
            conn.close()

    # ========================================================
    # FINISH AUCTION
    # ========================================================

    def finish_auction(self):
        """
        Finishes the current auction.

        If there is no bidder:
            UNSOLD

        If there is a bidder:
            SOLD

        Winner's balance is deducted here.
        """

        conn = self.connect()

        try:
            conn.execute("BEGIN IMMEDIATE")

            auction = conn.execute("""
                SELECT
                    active,
                    player_id,
                    highest_bid,
                    highest_bidder
                FROM auction
                WHERE id = 1
            """).fetchone()

            if not auction or not auction["active"]:
                conn.rollback()

                return {
                    "success": False,
                    "status": "none"
                }

            player = conn.execute("""
                SELECT
                    id,
                    name
                FROM players
                WHERE id = ?
            """, (
                auction["player_id"],
            )).fetchone()

            if not player:
                conn.rollback()

                return {
                    "success": False,
                    "status": "none"
                }

            bidder_id = auction[
                "highest_bidder"
            ]

            highest_bid = float(
                auction["highest_bid"]
            )

            # ------------------------------------------------
            # UNSOLD
            # ------------------------------------------------

            if not bidder_id:

                conn.execute("""
                    UPDATE players
                    SET
                        status = 'unsold',
                        sold_to = NULL,
                        sold_price = 0
                    WHERE id = ?
                """, (
                    player["id"],
                ))

                result = {
                    "success": True,
                    "status": "unsold",
                    "player_id": player["id"],
                    "player_name": player["name"],
                    "winner_id": None,
                    "winner_name": None,
                    "price": 0
                }

            # ------------------------------------------------
            # SOLD
            # ------------------------------------------------

            else:

                winner = conn.execute("""
                    SELECT
                        id,
                        name,
                        budget,
                        spent
                    FROM participants
                    WHERE id = ?
                """, (
                    bidder_id,
                )).fetchone()

                if not winner:
                    conn.rollback()

                    return {
                        "success": False,
                        "status": "error"
                    }

                remaining = round(
                    winner["budget"]
                    - winner["spent"],
                    2
                )

                if highest_bid > remaining:

                    # This should normally never happen
                    # because place_bid already checks it.
                    conn.rollback()

                    return {
                        "success": False,
                        "status": "error",
                        "message":
                            "Winner balance is insufficient."
                    }

                # Deduct money
                conn.execute("""
                    UPDATE participants
                    SET spent = spent + ?
                    WHERE id = ?
                """, (
                    highest_bid,
                    bidder_id
                ))

                # Mark player sold
                conn.execute("""
                    UPDATE players
                    SET
                        status = 'sold',
                        sold_to = ?,
                        sold_price = ?
                    WHERE id = ?
                """, (
                    bidder_id,
                    highest_bid,
                    player["id"]
                ))

                # Add to squad
                conn.execute("""
                    INSERT INTO squad
                    (
                        participant_id,
                        player_id,
                        purchase_price
                    )
                    VALUES (?, ?, ?)
                """, (
                    bidder_id,
                    player["id"],
                    highest_bid
                ))

                new_balance = round(
                    remaining - highest_bid,
                    2
                )

                result = {
                    "success": True,
                    "status": "sold",
                    "player_id": player["id"],
                    "player_name": player["name"],
                    "winner_id": winner["id"],
                    "winner_name": winner["name"],
                    "price": highest_bid,
                    "remaining_balance": new_balance
                }

            # ------------------------------------------------
            # Reset auction
            # ------------------------------------------------

            conn.execute("""
                UPDATE auction
                SET
                    active = 0,
                    player_id = NULL,
                    highest_bid = 0,
                    highest_bidder = NULL,
                    started_at = NULL,
                    ends_at = NULL
                WHERE id = 1
            """)

            conn.commit()

            return result

        except Exception as e:

            conn.rollback()

            return {
                "success": False,
                "status": "error",
                "message": str(e)
            }

        finally:
            conn.close()

    # ========================================================
    # CANCEL AUCTION
    # ========================================================

    def cancel_auction(self):

        conn = self.connect()

        try:
            conn.execute("BEGIN IMMEDIATE")

            auction = conn.execute("""
                SELECT
                    active,
                    player_id
                FROM auction
                WHERE id = 1
            """).fetchone()

            if not auction or not auction["active"]:
                conn.rollback()

                return False, "No active auction."

            if auction["player_id"]:

                conn.execute("""
                    UPDATE players
                    SET status = 'pending'
                    WHERE id = ?
                """, (
                    auction["player_id"],
                ))

            conn.execute("""
                UPDATE auction
                SET
                    active = 0,
                    player_id = NULL,
                    highest_bid = 0,
                    highest_bidder = NULL,
                    started_at = NULL,
                    ends_at = NULL
                WHERE id = 1
            """)

            conn.commit()

            return True, "Auction cancelled."

        except Exception as e:

            conn.rollback()

            return False, str(e)

        finally:
            conn.close()

    # ========================================================
    # SKIP PLAYER
    # ========================================================

    def skip_auction(self):

        conn = self.connect()

        try:
            conn.execute("BEGIN IMMEDIATE")

            auction = conn.execute("""
                SELECT
                    active,
                    player_id
                FROM auction
                WHERE id = 1
            """).fetchone()

            if not auction or not auction["active"]:
                conn.rollback()

                return False, "No active auction."

            player_id = auction["player_id"]

            if player_id:

                conn.execute("""
                    UPDATE players
                    SET status = 'unsold'
                    WHERE id = ?
                """, (
                    player_id,
                ))

            conn.execute("""
                UPDATE auction
                SET
                    active = 0,
                    player_id = NULL,
                    highest_bid = 0,
                    highest_bidder = NULL,
                    started_at = NULL,
                    ends_at = NULL
                WHERE id = 1
            """)

            conn.commit()

            return True, "Player skipped."

        except Exception as e:

            conn.rollback()

            return False, str(e)

        finally:
            conn.close()

    # ========================================================
    # BID HISTORY
    # ========================================================

    def get_bid_history(
        self,
        player_id: Optional[int] = None,
        limit: int = 100
    ):

        conn = self.connect()

        try:

            if player_id:

                rows = conn.execute("""
                    SELECT
                        b.id,
                        b.player_id,
                        b.participant_id,
                        b.amount,
                        b.increment,
                        b.created_at,
                        p.name AS participant_name,
                        pl.name AS player_name

                    FROM bids b

                    JOIN participants p
                        ON p.id = b.participant_id

                    JOIN players pl
                        ON pl.id = b.player_id

                    WHERE b.player_id = ?

                    ORDER BY b.id DESC

                    LIMIT ?
                """, (
                    player_id,
                    limit
                )).fetchall()

            else:

                rows = conn.execute("""
                    SELECT
                        b.id,
                        b.player_id,
                        b.participant_id,
                        b.amount,
                        b.increment,
                        b.created_at,
                        p.name AS participant_name,
                        pl.name AS player_name

                    FROM bids b

                    JOIN participants p
                        ON p.id = b.participant_id

                    JOIN players pl
                        ON pl.id = b.player_id

                    ORDER BY b.id DESC

                    LIMIT ?
                """, (
                    limit,
                )).fetchall()

            return [
                dict(row)
                for row in rows
            ]

        finally:
            conn.close()

    # ========================================================
    # SQUAD
    # ========================================================

    def get_squad(self, user_id: int):

        conn = self.connect()

        try:

            rows = conn.execute("""
                SELECT
                    s.id,
                    s.player_id,
                    s.purchase_price,
                    p.name AS player_name

                FROM squad s

                JOIN players p
                    ON p.id = s.player_id

                WHERE s.participant_id = ?

                ORDER BY s.id ASC
            """, (
                user_id,
            )).fetchall()

            return [
                dict(row)
                for row in rows
            ]

        finally:
            conn.close()

    # ========================================================
    # LEADERBOARD
    # ========================================================

    def get_leaderboard(self):

        conn = self.connect()

        try:

            rows = conn.execute("""
                SELECT
                    p.id,
                    p.name,
                    p.username,
                    p.budget,
                    p.spent,

                    ROUND(
                        p.budget - p.spent,
                        2
                    ) AS balance,

                    COUNT(s.id) AS players_bought

                FROM participants p

                LEFT JOIN squad s
                    ON s.participant_id = p.id

                GROUP BY p.id

                ORDER BY
                    players_bought DESC,
                    p.spent DESC
            """).fetchall()

            return [
                dict(row)
                for row in rows
            ]

        finally:
            conn.close()

    # ========================================================
    # RESET DATABASE
    # ========================================================

    def reset_all(self):

        conn = self.connect()

        try:

            conn.execute("BEGIN IMMEDIATE")

            conn.execute("DELETE FROM bids")
            conn.execute("DELETE FROM squad")
            conn.execute("DELETE FROM players")
            conn.execute("DELETE FROM participants")

            conn.execute("""
                UPDATE auction
                SET
                    active = 0,
                    player_id = NULL,
                    highest_bid = 0,
                    highest_bidder = NULL,
                    started_at = NULL,
                    ends_at = NULL
                WHERE id = 1
            """)

            conn.commit()

            return True

        except Exception:

            conn.rollback()

            return False

        finally:
            conn.close()

    # ========================================================
    # STATISTICS
    # ========================================================

    def get_statistics(self):

        conn = self.connect()

        try:

            participants = conn.execute("""
                SELECT COUNT(*) AS count
                FROM participants
            """).fetchone()["count"]

            players = conn.execute("""
                SELECT COUNT(*) AS count
                FROM players
            """).fetchone()["count"]

            sold = conn.execute("""
                SELECT COUNT(*) AS count
                FROM players
                WHERE status = 'sold'
            """).fetchone()["count"]

            unsold = conn.execute("""
                SELECT COUNT(*) AS count
                FROM players
                WHERE status = 'unsold'
            """).fetchone()["count"]

            pending = conn.execute("""
                SELECT COUNT(*) AS count
                FROM players
                WHERE status = 'pending'
            """).fetchone()["count"]

            bids = conn.execute("""
                SELECT COUNT(*) AS count
                FROM bids
            """).fetchone()["count"]

            total_spent = conn.execute("""
                SELECT COALESCE(
                    SUM(spent),
                    0
                ) AS total
                FROM participants
            """).fetchone()["total"]

            return {
                "participants": participants,
                "players": players,
                "sold": sold,
                "unsold": unsold,
                "pending": pending,
                "bids": bids,
                "total_spent": round(
                    total_spent,
                    2
                )
            }

        finally:
            conn.close()


# ============================================================
# DATABASE INSTANCE
# ============================================================

db = Database()
