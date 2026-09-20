import os
import logging
import secrets
import string
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

import db


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)

log = logging.getLogger("auction_bot")


# =========================================================
# ENVIRONMENT
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")

PUBLIC_URL = (
    os.getenv("PUBLIC_URL")
    or os.getenv("RENDER_EXTERNAL_URL")
)

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "change-me")

INITIAL_BALANCE = float(
    os.getenv("INITIAL_BALANCE", "100")
)

BID_SECONDS = int(
    os.getenv("BID_SECONDS", "30")
)

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}


if not TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not PUBLIC_URL:
    raise RuntimeError(
        "PUBLIC_URL or RENDER_EXTERNAL_URL is required"
    )


# =========================================================
# TELEGRAM APPLICATION
# =========================================================

BOT = Application.builder().token(TOKEN).build()


# =========================================================
# CONVERSATION STATES
# =========================================================

CREATE_NAME, CREATE_LIMIT, CREATE_PLAYERS = range(3)


# =========================================================
# AUCTION TASKS
# =========================================================

auction_tasks = {}


# =========================================================
# HELPERS
# =========================================================

def generate_code():
    """Generate a unique 6-digit auction code."""
    return "".join(
        secrets.choice(string.digits)
        for _ in range(6)
    )


def kb(rows):
    return InlineKeyboardMarkup(rows)


def is_admin(uid):
    return uid in ADMIN_IDS


def can_control(auction, uid):
    return (
        auction
        and (
            auction["host_id"] == uid
            or is_admin(uid)
        )
    )


def auction_text(auction):
    stats = db.stats(auction["id"])

    return (
        f"🎯 *{auction['name']}*\n"
        f"🆔 `{auction['code']}`\n"
        f"👥 Participants: "
        f"{stats['participants']}/{auction['max_participants']}\n"
        f"👤 Players: {stats['players']}/500\n"
        f"💰 Base price: 2.0 Cr\n"
        f"📈 Increment: 0.5 Cr\n"
        f"📌 State: *{auction['state']}*"
    )


def dashboard():
    return kb([
        [
            InlineKeyboardButton(
                "🏆 Create Auction",
                callback_data="create"
            ),
            InlineKeyboardButton(
                "🎯 Join Auction",
                callback_data="join"
            ),
        ],
        [
            InlineKeyboardButton(
                "🔥 Live Auctions",
                callback_data="live"
            ),
            InlineKeyboardButton(
                "👤 My Team",
                callback_data="team"
            ),
        ],
        [
            InlineKeyboardButton(
                "💰 My Balance",
                callback_data="balance"
            ),
            InlineKeyboardButton(
                "📊 Auction History",
                callback_data="history"
            ),
        ],
        [
            InlineKeyboardButton(
                "❓ Help",
                callback_data="help"
            )
        ],
    ])


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    db.upsert_user(update.effective_user)

    await update.effective_message.reply_text(
        "⚽ *eFootball Auction Bot*\n\n"
        "Virtual credits only. Free to use.\n"
        "Each auction: maximum 100 participants "
        "and 500 players.\n"
        "Starting balance: 100 Cr.\n\n"
        "Choose an option:",
        parse_mode="Markdown",
        reply_markup=dashboard(),
    )


# =========================================================
# HELP
# =========================================================

async def help_cmd(update, context):

    text = (
        "❓ *HELP*\n\n"
        "/createauction — create an auction\n"
        "/join 123456 — join an auction\n"
        "/live — view live auctions\n"
        "/balance 123456 — view balance\n"
        "/team 123456 — view your team\n"
        "/auction 123456 — auction dashboard\n"
        "/mybids 123456 — current bid history\n"
        "/history — auction history\n"
        "/admin — admin commands\n\n"
        "💰 Base price: 2.0 Cr\n"
        "📈 Minimum bid increment: 0.5 Cr"
    )

    await update.effective_message.reply_text(
        text,
        parse_mode="Markdown"
    )


# =========================================================
# CREATE AUCTION
# =========================================================

async def create_command(update, context):
    """
    Starts the create auction conversation from /createauction.
    """

    db.upsert_user(update.effective_user)

    await update.effective_message.reply_text(
        "🏆 *Create Auction*\n\n"
        "Enter the auction name:",
        parse_mode="Markdown"
    )

    return CREATE_NAME


async def create_start(update, context):
    """
    Starts the same conversation when the
    Create Auction inline button is pressed.
    """

    if update.callback_query:
        await update.callback_query.answer()

    db.upsert_user(update.effective_user)

    await update.effective_message.reply_text(
        "🏆 *Create Auction*\n\n"
        "Enter the auction name:",
        parse_mode="Markdown"
    )

    return CREATE_NAME


async def create_name(update, context):

    name = update.message.text.strip()

    if not name:
        await update.message.reply_text(
            "❌ Auction name cannot be empty.\n\n"
            "Please enter the auction name:"
        )
        return CREATE_NAME

    if len(name) > 80:
        await update.message.reply_text(
            "❌ Auction name must be 80 characters or less.\n\n"
            "Please enter another name:"
        )
        return CREATE_NAME

    context.user_data["auction_name"] = name

    await update.message.reply_text(
        "👥 Enter maximum participants.\n\n"
        "Allowed: 1–100"
    )

    return CREATE_LIMIT


async def create_limit(update, context):

    try:
        number = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text(
            "❌ Please enter a whole number from 1 to 100."
        )
        return CREATE_LIMIT

    if not 1 <= number <= 100:
        await update.message.reply_text(
            "❌ Maximum participants is 100.\n\n"
            "Enter a number from 1 to 100:"
        )
        return CREATE_LIMIT

    context.user_data["max_participants"] = number

    await update.message.reply_text(
        "📋 *Send the player list*\n\n"
        "Send one player per line.\n"
        "Maximum: 500 players.\n\n"
        "Example:\n"
        "Lionel Messi\n"
        "Cristiano Ronaldo\n"
        "Kylian Mbappe",
        parse_mode="Markdown"
    )

    return CREATE_PLAYERS


async def create_players(update, context):

    names = [
        x.strip()
        for x in update.message.text.splitlines()
        if x.strip()
    ]

    if not names:
        await update.message.reply_text(
            "❌ Please send at least 1 player."
        )
        return CREATE_PLAYERS

    if len(names) > 500:
        await update.message.reply_text(
            "❌ Maximum 500 players allowed."
        )
        return CREATE_PLAYERS

    auction_name = context.user_data.get(
        "auction_name"
    )

    max_participants = context.user_data.get(
        "max_participants"
    )

    if not auction_name or not max_participants:
        context.user_data.clear()

        await update.message.reply_text(
            "❌ Create session expired.\n"
            "Please use /createauction again."
        )

        return ConversationHandler.END

    # Generate unique code
    auction_code = generate_code()

    while db.get_auction(auction_code):
        auction_code = generate_code()

    # Create auction
    auction_id = db.create_auction(
        update.effective_user.id,
        auction_name,
        max_participants,
        auction_code,
    )

    # Add players
    db.add_players(
        auction_id,
        names
    )

    # Clear conversation data
    context.user_data.clear()

    auction = db.get_auction(auction_code)

    await update.message.reply_text(
        "✅ *AUCTION CREATED!*\n\n"
        f"{auction_text(auction)}\n\n"
        "📢 Share this ID with participants:\n"
        f"`/join {auction_code}`\n\n"
        "When everyone has joined, start it using:\n"
        f"`/startauction {auction_code}`",
        parse_mode="Markdown"
    )

    return ConversationHandler.END


async def cancel_create(update, context):

    context.user_data.clear()

    await update.effective_message.reply_text(
        "❌ Auction creation cancelled."
    )

    return ConversationHandler.END


# =========================================================
# JOIN
# =========================================================

async def join(update, context):

    if not context.args:
        await update.effective_message.reply_text(
            "Use `/join 123456`",
            parse_mode="Markdown"
        )
        return

    code_value = context.args[0]

    auction = db.get_auction(code_value)

    if not auction:
        await update.effective_message.reply_text(
            "❌ Auction not found."
        )
        return

    db.upsert_user(update.effective_user)

    ok, message = db.join_auction(
        auction["id"],
        update.effective_user.id,
        INITIAL_BALANCE
    )

    await update.effective_message.reply_text(
        ("✅ " if ok else "❌ ")
        + message
        + "\n\n"
        + auction_text(auction),
        parse_mode="Markdown"
    )


# =========================================================
# LIVE AUCTIONS
# =========================================================

async def live(update, context):

    auctions = db.live_auctions()

    if not auctions:
        await update.effective_message.reply_text(
            "🔥 No live auctions currently."
        )
        return

    buttons = []

    for auction in auctions:

        buttons.append([
            InlineKeyboardButton(
                f"🎯 {auction['name']} #{auction['code']}",
                callback_data=f"view:{auction['code']}"
            )
        ])

    await update.effective_message.reply_text(
        "🔥 *LIVE AUCTIONS*",
        parse_mode="Markdown",
        reply_markup=kb(buttons)
    )


# =========================================================
# AUCTION VIEW
# =========================================================

async def auction_view(update, context):

    if not context.args:
        await update.effective_message.reply_text(
            "Use `/auction 123456`",
            parse_mode="Markdown"
        )
        return

    auction = db.get_auction(
        context.args[0]
    )

    if not auction:
        await update.effective_message.reply_text(
            "❌ Auction not found."
        )
        return

    await update.effective_message.reply_text(
        auction_text(auction),
        parse_mode="Markdown"
    )


# =========================================================
# BALANCE
# =========================================================

async def balance(update, context):

    if not context.args:
        await update.effective_message.reply_text(
            "Use `/balance 123456`",
            parse_mode="Markdown"
        )
        return

    auction = db.get_auction(
        context.args[0]
    )

    if not auction:
        await update.effective_message.reply_text(
            "❌ Auction not found."
        )
        return

    participant = db.participant(
        auction["id"],
        update.effective_user.id
    )

    if not participant:
        await update.effective_message.reply_text(
            "❌ You are not a participant."
        )
        return

    await update.effective_message.reply_text(
        f"💰 Balance: *{participant['balance']:.1f} Cr*",
        parse_mode="Markdown"
    )


# =========================================================
# TEAM
# =========================================================

async def team(update, context):

    if not context.args:
        await update.effective_message.reply_text(
            "Use `/team 123456`",
            parse_mode="Markdown"
        )
        return

    auction = db.get_auction(
        context.args[0]
    )

    if not auction:
        await update.effective_message.reply_text(
            "❌ Auction not found."
        )
        return

    participant = db.participant(
        auction["id"],
        update.effective_user.id
    )

    if not participant:
        await update.effective_message.reply_text(
            "❌ You are not a participant."
        )
        return

    rows = db.my_holdings(
        auction["id"],
        update.effective_user.id
    )

    text = "👤 *MY TEAM*\n\n"

    if rows:
        text += "\n".join(
            f"• {row['name']} — {row['price']:.1f} Cr"
            for row in rows
        )
    else:
        text += "No players yet."

    text += (
        f"\n\n"
        f"Total players: {len(rows)}\n"
        f"💰 Remaining: {participant['balance']:.1f} Cr"
    )

    await update.effective_message.reply_text(
        text,
        parse_mode="Markdown"
    )


# =========================================================
# HISTORY
# =========================================================

async def history(update, context):

    rows = db.user_history(
        update.effective_user.id
    )

    if not rows:
        await update.effective_message.reply_text(
            "📊 No auction history yet."
        )
        return

    text = "📊 *AUCTION HISTORY*\n\n"

    for row in rows:

        text += (
            f"• `{row['code']}` {row['name']} — "
            f"{row['state']}\n"
            f"  Won: {row['players_won']} | "
            f"Spent: {row['spent']:.1f} Cr\n\n"
        )

    await update.effective_message.reply_text(
        text,
        parse_mode="Markdown"
    )


# =========================================================
# MY BIDS
# =========================================================

async def mybids(update, context):

    if not context.args:
        await update.effective_message.reply_text(
            "Use `/mybids 123456`",
            parse_mode="Markdown"
        )
        return

    auction = db.get_auction(
        context.args[0]
    )

    if not auction:
        await update.effective_message.reply_text(
            "❌ Auction not found."
        )
        return

    participant = db.participant(
        auction["id"],
        update.effective_user.id
    )

    if not participant:
        await update.effective_message.reply_text(
            "❌ You are not a participant."
        )
        return

    current_player = db.current_player(
        auction["id"]
    )

    if not current_player:
        await update.effective_message.reply_text(
            "ℹ️ No active player."
        )
        return

    rows = db.bid_history(
        auction["id"],
        current_player["id"]
    )

    text = (
        "📊 *CURRENT BID HISTORY*\n\n"
        f"🔨 {current_player['name']}\n\n"
    )

    if rows:
        text += "\n".join(
            f"{row['amount']:.1f} Cr — "
            f"@{row['username'] or row['first_name']}"
            for row in rows
        )
    else:
        text += "No bids yet."

    await update.effective_message.reply_text(
        text,
        parse_mode="Markdown"
    )


# =========================================================
# START AUCTION
# =========================================================

async def startauction(update, context):

    if not context.args:
        await update.effective_message.reply_text(
            "Use `/startauction 123456`",
            parse_mode="Markdown"
        )
        return

    auction = db.get_auction(
        context.args[0]
    )

    if not auction:
        await update.effective_message.reply_text(
            "❌ Auction not found."
        )
        return

    if not can_control(
        auction,
        update.effective_user.id
    ):
        await update.effective_message.reply_text(
            "❌ Only the host/admin can start this auction."
        )
        return

    ok, message = db.start_auction(
        auction["id"],
        auction["host_id"]
    )

    if not ok:
        await update.effective_message.reply_text(
            "❌ " + message
        )
        return

    await update.effective_message.reply_text(
        "🚀 Auction started!"
    )

    await announce_current(
        auction["id"],
        context
    )


# =========================================================
# PAUSE / RESUME
# =========================================================

async def pause(update, context):
    await control_state(
        update,
        context,
        "PAUSED"
    )


async def resume(update, context):
    await control_state(
        update,
        context,
        "RUNNING"
    )


async def control_state(
    update,
    context,
    state
):

    command = (
        "pauseauction"
        if state == "PAUSED"
        else "resumeauction"
    )

    if not context.args:

        await update.effective_message.reply_text(
            f"Use `/{command} 123456`",
            parse_mode="Markdown"
        )

        return

    auction = db.get_auction(
        context.args[0]
    )

    if not auction:
        await update.effective_message.reply_text(
            "❌ Auction not found."
        )
        return

    if not can_control(
        auction,
        update.effective_user.id
    ):
        await update.effective_message.reply_text(
            "❌ Not authorized."
        )
        return

    db.set_state(
        auction["id"],
        state
    )

    if state == "PAUSED":
        await update.effective_message.reply_text(
            "⏸ Auction paused."
        )
    else:
        await update.effective_message.reply_text(
            "▶️ Auction resumed."
        )


# =========================================================
# STOP
# =========================================================

async def stop(update, context):

    if not context.args:

        await update.effective_message.reply_text(
            "Use `/stopauction 123456`",
            parse_mode="Markdown"
        )

        return

    auction = db.get_auction(
        context.args[0]
    )

    if not auction:
        await update.effective_message.reply_text(
            "❌ Auction not found."
        )
        return

    if not can_control(
        auction,
        update.effective_user.id
    ):
        await update.effective_message.reply_text(
            "❌ Not authorized."
        )
        return

    db.set_state(
        auction["id"],
        "CANCELLED"
    )

    task = auction_tasks.get(
        auction["id"]
    )

    if task and not task.done():
        task.cancel()

    await update.effective_message.reply_text(
        "🛑 Auction cancelled."
    )


# =========================================================
# ADD PLAYER
# ===================
app = FastAPI(
    title="eFootball Auction Bot",
    lifespan=lifespan
)


@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "eFootball Auction Bot"
    }


@app.get("/health")
async def health():
    return {
        "status": "healthy"
    }


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):

    received_secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token"
    )

    if received_secret != WEBHOOK_SECRET:
        raise HTTPException(
            status_code=403,
            detail="forbidden"
        )

    try:
        data = await request.json()

        update = Update.de_json(
            data,
            BOT.bot
        )

        await BOT.process_update(update)

        return {"ok": True}

    except Exception as error:
        log.exception(
            "Webhook processing error: %s",
            error
        )

        raise HTTPException(
            status_code=500,
            detail="Webhook processing failed"
        )
