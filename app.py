import os
import logging
import secrets
import string
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, ConversationHandler, filters
)

import db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("auction_bot")

TOKEN = os.getenv("BOT_TOKEN")
PUBLIC_URL = os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "change-me")
INITIAL_BALANCE = float(os.getenv("INITIAL_BALANCE", "100"))
BID_SECONDS = int(os.getenv("BID_SECONDS", "30"))
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip().isdigit()}

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not PUBLIC_URL:
    raise RuntimeError("PUBLIC_URL or RENDER_EXTERNAL_URL is required")

BOT = Application.builder().token(TOKEN).build()
CREATE_NAME, CREATE_LIMIT, CREATE_PLAYERS = range(3)
auction_tasks = {}

def code():
    return "".join(secrets.choice(string.digits) for _ in range(6))

def kb(rows):
    return InlineKeyboardMarkup(rows)

def is_admin(uid):
    return uid in ADMIN_IDS

def can_control(a, uid):
    return a and (a["host_id"] == uid or is_admin(uid))

def auction_text(a):
    s=db.stats(a["id"])
    return (
        f"🎯 *{a['name']}*\\n"
        f"🆔 `{a['code']}`\\n"
        f"👥 Participants: {s['participants']}/{a['max_participants']}\\n"
        f"👤 Players: {s['players']}/500\\n"
        f"💰 Base price: 2.0 Cr\\n"
        f"📈 Increment: 0.5 Cr\\n"
        f"📌 State: *{a['state']}*"
    )

def dashboard():
    return kb([
        [InlineKeyboardButton("🏆 Create Auction", callback_data="create"),
         InlineKeyboardButton("🎯 Join Auction", callback_data="join")],
        [InlineKeyboardButton("🔥 Live Auctions", callback_data="live"),
         InlineKeyboardButton("👤 My Team", callback_data="team")],
        [InlineKeyboardButton("💰 My Balance", callback_data="balance"),
         InlineKeyboardButton("📊 Auction History", callback_data="history")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.upsert_user(update.effective_user)
    await update.message.reply_text(
        "⚽ *eFootball Auction Bot*\\n\\n"
        "Virtual credits only. Free to use.\\n"
        "Each auction: max 100 participants and 500 players.\\n"
        "Starting balance: 100 Cr.\\n\\n"
        "Choose an option:",
        parse_mode="Markdown", reply_markup=dashboard()
    )

async def help_cmd(update, context):
    text=(
        "❓ *HELP*\\n\\n"
        "/createauction — create an auction\\n"
        "/join 123456 — join by auction ID\\n"
        "/live — live/lobby auctions\\n"
        "/balance 123456 — your balance\\n"
        "/team 123456 — your purchased players\\n"
        "/auction 123456 — auction dashboard\\n"
        "/admin — admin help\\n\\n"
        "Bids start at 2.0 Cr and increase by exactly 0.5 Cr."
    )
    await update.effective_message.reply_text(text,parse_mode="Markdown")

async def create_start(update, context):
    if update.callback_query: await update.callback_query.answer()
    await update.effective_message.reply_text("🏆 Enter the auction name:")
    return CREATE_NAME

async def create_name(update, context):
    name=update.message.text.strip()
    if not name or len(name)>80:
        await update.message.reply_text("Please use an auction name up to 80 characters.")
        return CREATE_NAME
    context.user_data["auction_name"]=name
    await update.message.reply_text("👥 Enter maximum participants (1–100):")
    return CREATE_LIMIT

async def create_limit(update, context):
    try: n=int(update.message.text.strip())
    except:
        await update.message.reply_text("Enter a whole number from 1 to 100.")
        return CREATE_LIMIT
    if not 1<=n<=100:
        await update.message.reply_text("Maximum is 100 participants.")
        return CREATE_LIMIT
    context.user_data["max_participants"]=n
    await update.message.reply_text(
        "📋 Send the player list, one player per line. Maximum 500 players.\n"
        "Example:\nLionel Messi\nCristiano Ronaldo\nKylian Mbappe"
    )
    return CREATE_PLAYERS

async def create_players(update, context):
    names=[x.strip() for x in update.message.text.splitlines() if x.strip()]
    if not names or len(names)>500:
        await update.message.reply_text("Send between 1 and 500 players, one per line.")
        return CREATE_PLAYERS
    name=context.user_data["auction_name"]
    limit=context.user_data["max_participants"]
    c=code()
    while db.get_auction(c): c=code()
    aid=db.create_auction(update.effective_user.id,name,limit,c)
    db.add_players(aid,names)
    context.user_data.clear()
    await update.message.reply_text(
        f"🎯 *Auction Created!*\n\n{auction_text(db.get_auction(c))}\n\n"
        "Share this ID with participants:\n"
        f"`/join {c}`\n\n"
        "You can start it after participants join with:\n"
        f"`/startauction {c}`",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def cancel_create(update, context):
    context.user_data.clear()
    await update.effective_message.reply_text("Cancelled.")
    return ConversationHandler.END

async def join(update, context):
    args=context.args
    if not args:
        await update.effective_message.reply_text("Use `/join 123456`",parse_mode="Markdown"); return
    a=db.get_auction(args[0])
    if not a:
        await update.effective_message.reply_text("Auction not found."); return
    db.upsert_user(update.effective_user)
    ok,msg=db.join_auction(a["id"],update.effective_user.id,INITIAL_BALANCE)
    await update.effective_message.reply_text(("✅ " if ok else "❌ ")+msg+f"\n\n{auction_text(a)}")

async def live(update, context):
    rows=db.live_auctions()
    if not rows:
        await update.effective_message.reply_text("No live auctions.")
        return
    buttons=[[InlineKeyboardButton(f"🎯 {a['name']} #{a['code']}",callback_data=f"view:{a['code']}")] for a in rows]
    await update.effective_message.reply_text("🔥 *Live Auctions*",parse_mode="Markdown",reply_markup=kb(buttons))

async def auction_view(update, context):
    if not context.args:
        await update.effective_message.reply_text("Use `/auction 123456`"); return
    a=db.get_auction(context.args[0])
    if not a:
        await update.effective_message.reply_text("Auction not found."); return
    await update.effective_message.reply_text(auction_text(a),parse_mode="Markdown")

async def balance(update, context):
    if not context.args:
        await update.effective_message.reply_text("Use `/balance 123456`"); return
    a=db.get_auction(context.args[0]); 
    if not a: await update.effective_message.reply_text("Auction not found."); return
    p=db.participant(a["id"],update.effective_user.id)
    if not p: await update.effective_message.reply_text("You are not a participant."); return
    await update.effective_message.reply_text(f"💰 Balance: *{p['balance']:.1f} Cr*",parse_mode="Markdown")

async def team(update, context):
    if not context.args:
        await update.effective_message.reply_text("Use `/team 123456`"); return
    a=db.get_auction(context.args[0]); 
    if not a: await update.effective_message.reply_text("Auction not found."); return
    rows=db.my_holdings(a["id"],update.effective_user.id)
    p=db.participant(a["id"],update.effective_user.id)
    if not p: await update.effective_message.reply_text("You are not a participant."); return
    text="👤 *MY TEAM*\\n\\n"
    if rows:
        text+="\\n".join(f"• {r['name']} — {r['price']:.1f} Cr" for r in rows)
    else: text+="No players yet."
    text+=f"\\n\\nTotal players: {len(rows)}\\n💰 Remaining: {p['balance']:.1f} Cr"
    await update.effective_message.reply_text(text,parse_mode="Markdown")

async def history(update, context):
    rows=db.user_history(update.effective_user.id)
    if not rows:
        await update.effective_message.reply_text("📊 No auction history yet.")
        return
    text="📊 *AUCTION HISTORY*\\n\\n"
    for r in rows:
        text += f"• `{r['code']}` {r['name']} — {r['state']}\\n  Won: {r['players_won']} | Spent: {r['spent']:.1f} Cr\\n"
    await update.effective_message.reply_text(text,parse_mode="Markdown")

async def mybids(update, context):
    if not context.args:
        await update.effective_message.reply_text("Use `/mybids 123456`"); return
    a=db.get_auction(context.args[0])
    if not a: await update.effective_message.reply_text("Auction not found."); return
    p=db.participant(a["id"],update.effective_user.id)
    if not p: await update.effective_message.reply_text("You are not a participant."); return
    # Current bid history for active player
    cp=db.current_player(a["id"])
    if not cp: await update.effective_message.reply_text("No active player."); return
    rows=db.bid_history(a["id"],cp["id"])
    text="📊 *CURRENT BID HISTORY*\\n\\n"
    text+="\\n".join(f"{r['amount']:.1f} Cr — @{r['username'] or r['first_name']}" for r in rows) or "No bids."
    await update.effective_message.reply_text(text,parse_mode="Markdown")

async def startauction(update, context):
    if not context.args:
        await update.effective_message.reply_text("Use `/startauction 123456`"); return
    a=db.get_auction(context.args[0])
    if not a or not can_control(a,update.effective_user.id):
        await update.effective_message.reply_text("Only the host/admin can start this auction."); return
    ok,msg=db.start_auction(a["id"],a["host_id"] if not is_admin(update.effective_user.id) else a["host_id"])
    if not ok:
        await update.effective_message.reply_text("❌ "+msg); return
    await announce_current(a["id"],context)

async def pause(update, context):
    await control_state(update,context,"PAUSED")

async def resume(update, context):
    await control_state(update,context,"RUNNING")

async def stop(update, context):
    if not context.args: await update.effective_message.reply_text("Use `/stopauction 123456`"); return
    a=db.get_auction(context.args[0])
    if not a or not can_control(a,update.effective_user.id):
        await update.effective_message.reply_text("Not authorized."); return
    db.set_state(a["id"],"CANCELLED")
    await update.effective_message.reply_text("🛑 Auction cancelled.")

async def control_state(update,context,state):
    if not context.args: await update.effective_message.reply_text(f"Use `/{'pauseauction' if state=='PAUSED' else 'resumeauction'} 123456`",parse_mode="Markdown"); return
    a=db.get_auction(context.args[0])
    if not a or not can_control(a,update.effective_user.id):
        await update.effective_message.reply_text("Not authorized."); return
    db.set_state(a["id"],state)
    await update.effective_message.reply_text(("⏸ Paused." if state=="PAUSED" else "▶️ Resumed."))

async def addplayer(update, context):
    if len(context.args)<2:
        await update.effective_message.reply_text("Use `/addplayer 123456 Player Name`"); return
    a=db.get_auction(context.args[0])
    if not a or not can_control(a,update.effective_user.id):
        await update.effective_message.reply_text("Not authorized."); return
    if a["state"] not in ("LOBBY","DRAFT"):
        await update.effective_message.reply_text("Players can only be added before the auction starts."); return
    try:
        db.add_player(a["id"]," ".join(context.args[1:]))
        await update.effective_message.reply_text("✅ Player added.")
    except ValueError as e:
        await update.effective_message.reply_text("❌ "+str(e))

async def reauction(update, context):
    if len(context.args)<2:
        await update.effective_message.reply_text("Use `/reauction 123456 PLAYER_ID`"); return
    a=db.get_auction(context.args[0])
    if not a or not can_control(a,update.effective_user.id):
        await update.effective_message.reply_text("Not authorized."); return
    ok,msg=db.reauction_player(a["id"],a["host_id"],int(context.args[1]))
    await update.effective_message.reply_text(("✅ " if ok else "❌ ")+msg)

async def removeplayer(update, context):
    if len(context.args)<2:
        await update.effective_message.reply_text("Use `/removeplayer 123456 PLAYER_ID`"); return
    a=db.get_auction(context.args[0])
    if not a or not can_control(a,update.effective_user.id):
        await update.effective_message.reply_text("Not authorized."); return
    try:
        ok=db.remove_player(a["id"],int(context.args[1]))
    except: ok=False
    await update.effective_message.reply_text("✅ Removed." if ok else "❌ Player not found/pending.")

async def skip(update, context):
    if not context.args:
        await update.effective_message.reply_text("Use `/skip 123456`"); return
    a=db.get_auction(context.args[0])
    if not a or not can_control(a,update.effective_user.id):
        await update.effective_message.reply_text("Not authorized."); return
    result=db.mark_unsold_and_next(a["id"])
    if not result:
        await update.effective_message.reply_text("No active player."); return
    if result["next"]:
        await announce_current(a["id"],context)
    else:
        await update.effective_message.reply_text("🏁 Auction completed.")

async def unsold(update, context):
    await skip(update,context)

async def auctionstats(update, context):
    if not context.args:
        await update.effective_message.reply_text("Use `/auctionstats 123456`"); return
    a=db.get_auction(context.args[0])
    if not a: await update.effective_message.reply_text("Auction not found."); return
    s=db.stats(a["id"])
    await update.effective_message.reply_text(
        f"📊 *{a['name']}*\\nPlayers: {s['players']}\\nSold: {s['sold']}\\n"
        f"Unsold: {s['unsold']}\\nRemaining: {s['pending']}\\nParticipants: {s['participants']}",
        parse_mode="Markdown"
    )

async def participants(update, context):
    if not context.args: await update.effective_message.reply_text("Use `/participants 123456`"); return
    a=db.get_auction(context.args[0])
    if not a or not can_control(a,update.effective_user.id):
        await update.effective_message.reply_text("Not authorized."); return
    with db.conn() as c:
        rows=c.execute("""SELECT u.username,u.first_name,p.balance FROM participants p
                          JOIN users u ON u.telegram_id=p.user_id
                          WHERE p.auction_id=? ORDER BY p.joined_at""",(a["id"],)).fetchall()
    text="👥 *PARTICIPANTS*\\n\\n"
    text+="\\n".join(f"• @{r['username'] or r['first_name']} — {r['balance']:.1f} Cr" for r in rows) or "None"
    await update.effective_message.reply_text(text,parse_mode="Markdown")

async def results(update, context):
    if not context.args: await update.effective_message.reply_text("Use `/results 123456`"); return
    a=db.get_auction(context.args[0])
    if not a: await update.effective_message.reply_text("Auction not found."); return
    rows=db.leaderboard(a["id"])
    text="🏆 *AUCTION RESULTS*\\n\\n"
    medals=["🥇","🥈","🥉"]
    for i,r in enumerate(rows[:10]):
        text+=f"{medals[i] if i<3 else '▫️'} @{r['username'] or r['first_name']} — {r['players_won']} players, {r['spent']:.1f} Cr\\n"
    await update.effective_message.reply_text(text,parse_mode="Markdown")

async def admin(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("Admin only."); return
    await update.effective_message.reply_text(
        "🛠 *ADMIN*\\n\\n"
        "/createauction\\n/startauction CODE\\n/pauseauction CODE\\n/resumeauction CODE\\n"
        "/stopauction CODE\\n/addplayer CODE NAME\\n/removeplayer CODE PLAYER_ID\\n/reauction CODE PLAYER_ID\\n"
        "/skip CODE\\n/participants CODE\\n/auctionstats CODE\\n/results CODE\\n/broadcast TEXT",
        parse_mode="Markdown"
    )

async def broadcast(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("Admin only."); return
    text=" ".join(context.args).strip()
    if not text:
        await update.effective_message.reply_text("Use `/broadcast message`"); return
    # Broadcast to known users. Errors are ignored per user.
    with db.conn() as c:
        ids=[r["telegram_id"] for r in c.execute("SELECT telegram_id FROM users").fetchall()]
    sent=0
    for uid in ids:
        try:
            await context.bot.send_message(uid,text)
            sent+=1
        except Exception:
            pass
    await update.effective_message.reply_text(f"📣 Sent to {sent} users.")

async def announce_current(aid, context):
    a=db.get_auction_by_id(aid)
    p=db.current_player(aid)
    if not p:
        await context.bot.send_message(a["host_id"],"🏁 Auction completed.")
        return
    buttons=[
        [InlineKeyboardButton("💰 Bid +0.5 Cr",callback_data=f"bid:{a['code']}")],
        [InlineKeyboardButton("📊 Current Bid",callback_data=f"bids:{a['code']}"),
         InlineKeyboardButton("👤 My Balance",callback_data=f"bal:{a['code']}")],
        [InlineKeyboardButton("⏭ Skip Player",callback_data=f"skip:{a['code']}")]
    ]
    text=f"🔥 *PLAYER AUCTION STARTED*\\n\\n🔨 *{p['name']}*\\n"
    text+="💰 Current Bid: 2.0 Cr\\n👑 Highest Bidder: None\\n"
    text+=f"⏱ Bidding time: {BID_SECONDS}s"
    # send to all participants
    with db.conn() as c:
        ids=[r["user_id"] for r in c.execute("SELECT user_id FROM participants WHERE auction_id=?",(aid,)).fetchall()]
    for uid in ids:
        try: await context.bot.send_message(uid,text,parse_mode="Markdown",reply_markup=kb(buttons))
        except Exception: pass
    old=auction_tasks.get(aid)
    if old and not old.done(): old.cancel()
    auction_tasks[aid]=asyncio.create_task(player_timer(aid,context))

async def player_timer(aid,context):
    try:
        remaining=BID_SECONDS
        while remaining>0:
            await asyncio.sleep(1)
            a=db.get_auction_by_id(aid)
            if not a or a["state"]=="CANCELLED" or a["state"]=="COMPLETED":
                return
            if a["state"]=="PAUSED":
                continue
            remaining-=1
        a=db.get_auction_by_id(aid)
        if not a or a["state"]!="RUNNING": return
        result=db.sell_current(aid)
        if not result: return
        p=result["sold_player"]; last=result["last"]
        if last:
            winner=last["username"] if "username" in last.keys() else None
        a2=db.get_auction_by_id(aid)
        with db.conn() as c:
            row=c.execute("SELECT username,first_name FROM users WHERE telegram_id=?",(last["user_id"],)).fetchone() if last else None
        if last:
            msg=f"🔨 *SOLD!*\n\n{p['name']}\n💰 Sold for: {last['amount']:.1f} Cr\n👑 Winner: @{row['username'] or row['first_name']}"
        else:
            msg=f"⚪ *UNSOLD*\n\n{p['name']}\nNo valid bids."
        with db.conn() as c:
            ids=[r["user_id"] for r in c.execute("SELECT user_id FROM participants WHERE auction_id=?",(aid,)).fetchall()]
        for uid in ids:
            try: await context.bot.send_message(uid,msg,parse_mode="Markdown")
            except Exception: pass
        if result["next"]:
            await asyncio.sleep(2)
            await announce_current(aid,context)
        else:
            # results to all
            rows=db.leaderboard(aid)
            text="🏁 *AUCTION COMPLETED*\\n\\n🏆 *RESULTS*\\n"
            for i,r in enumerate(rows[:10]):
                text+=f"{i+1}. @{r['username'] or r['first_name']} — {r['players_won']} players — {r['spent']:.1f} Cr\\n"
            for uid in ids:
                try: await context.bot.send_message(uid,text,parse_mode="Markdown")
                except Exception: pass
    except asyncio.CancelledError:
        return
    except Exception:
        log.exception("player timer failed")

async def button(update,context):
    q=update.callback_query
    await q.answer()
    data=q.data
    if data=="create":
        await q.message.reply_text("Use `/createauction` to create an auction.",parse_mode="Markdown"); return
    if data=="join":
        await q.message.reply_text("Use `/join 123456` with the auction code.",parse_mode="Markdown"); return
    if data=="live":
        await live(update,context); return
    if data=="help":
        await help_cmd(update,context); return
    if data in ("team","balance","history"):
        await q.message.reply_text("Use the corresponding command with an auction code. Example: `/balance 123456`.",parse_mode="Markdown"); return
    if data.startswith("view:"):
        a=db.get_auction(data.split(":",1)[1])
        if a: await q.message.reply_text(auction_text(a),parse_mode="Markdown")
        return
    if ":" not in data: return
    action,codev=data.split(":",1)
    a=db.get_auction(codev)
    if not a: await q.message.reply_text("Auction not found."); return
    if action=="bid":
        ok,msg,info=db.bid(a["id"],q.from_user.id)
        if not ok: await q.message.reply_text("❌ "+msg); return
        await q.message.reply_text(
            f"🔥 *NEW BID!*\n\nPlayer: {info['player']['name']}\n"
            f"New Bid: {info['amount']:.1f} Cr\nNext valid bid: {info['amount']+0.5:.1f} Cr",
            parse_mode="Markdown"
        )
    elif action=="bids":
        p=db.current_player(a["id"])
        if not p: await q.message.reply_text("No active player."); return
        rows=db.bid_history(a["id"],p["id"])
        txt="📊 *BID HISTORY*\n\n"+("\n".join(f"{r['amount']:.1f} Cr — @{r['username'] or r['first_name']}" for r in rows) or "No bids.")
        await q.message.reply_text(txt,parse_mode="Markdown")
    elif action=="bal":
        p=db.participant(a["id"],q.from_user.id)
        await q.message.reply_text(f"💰 Balance: {p['balance']:.1f} Cr" if p else "Not a participant.")
    elif action=="skip":
        if not can_control(a,q.from_user.id):
            await q.message.reply_text("Host/admin only."); return
        result=db.mark_unsold_and_next(a["id"])
        if result and result["next"]: await announce_current(a["id"],context)
        else: await q.message.reply_text("🏁 Auction completed.")

async def create_command(update,context):
    return await create_start(update,context)

async def startup():
    db.init_db()
    await BOT.initialize()
    await BOT.start()
    await BOT.bot.set_webhook(url=f"{PUBLIC_URL.rstrip('/')}/telegram/webhook", secret_token=WEBHOOK_SECRET)
    log.info("Webhook configured")

async def shutdown():
    for t in auction_tasks.values():
        if not t.done(): t.cancel()
    await BOT.stop()
    await BOT.shutdown()

@asynccontextmanager
async def lifespan(app):
    await startup()
    yield
    await shutdown()

app=FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {"status":"ok","bot":"eFootball Auction Bot"}

@app.post("/telegram/webhook")
async def webhook(request: Request):
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        raise HTTPException(status_code=403,detail="forbidden")
    data=await request.json()
    update=Update.de_json(data,BOT.bot)
    await BOT.process_update(update)
    return {"ok":True}

conv=ConversationHandler(
    entry_points=[CommandHandler("createauction",create_command),CallbackQueryHandler(create_start,pattern="^create$")],
    states={
        CREATE_NAME:[MessageHandler(filters.TEXT & ~filters.COMMAND,create_name)],
        CREATE_LIMIT:[MessageHandler(filters.TEXT & ~filters.COMMAND,create_limit)],
        CREATE_PLAYERS:[MessageHandler(filters.TEXT & ~filters.COMMAND,create_players)]
    },
    fallbacks=[CommandHandler("cancel",cancel_create)],
    per_user=True, per_chat=True
)

BOT.add_handler(conv)
BOT.add_handler(CommandHandler("start",start))
BOT.add_handler(CommandHandler("help",help_cmd))
BOT.add_handler(CommandHandler("join",join))
BOT.add_handler(CommandHandler("live",live))
BOT.add_handler(CommandHandler("auction",auction_view))
BOT.add_handler(CommandHandler("balance",balance))
BOT.add_handler(CommandHandler("team",team))
BOT.add_handler(CommandHandler("mybids",mybids))
BOT.add_handler(CommandHandler("history",history))
BOT.add_handler(CommandHandler("startauction",startauction))
BOT.add_handler(CommandHandler("pauseauction",pause))
BOT.add_handler(CommandHandler("resumeauction",resume))
BOT.add_handler(CommandHandler("stopauction",stop))
BOT.add_handler(CommandHandler("addplayer",addplayer))
BOT.add_handler(CommandHandler("removeplayer",removeplayer))
BOT.add_handler(CommandHandler("reauction",reauction))
BOT.add_handler(CommandHandler("skip",skip))
BOT.add_handler(CommandHandler("unsold",unsold))
BOT.add_handler(CommandHandler("participants",participants))
BOT.add_handler(CommandHandler("auctionstats",auctionstats))
BOT.add_handler(CommandHandler("results",results))
BOT.add_handler(CommandHandler("admin",admin))
BOT.add_handler(CommandHandler("broadcast",broadcast))
BOT.add_handler(CallbackQueryHandler(button))
BOT.add_handler(MessageHandler(filters.COMMAND, help_cmd))
