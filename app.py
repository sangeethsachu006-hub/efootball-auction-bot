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
# =========================================================

async def addplayer(update, context):

    if len(context.args) < 2:

        await update.effective_message.reply_text(
            "Use `/addplayer 123456 Player Name`",
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

    if auction["state"] not in (
        "LOBBY",
        "DRAFT"
    ):
        await update.effective_message.reply_text(
            "❌ Players can only be added before the auction starts."
        )
        return

    try:

        db.add_player(
            auction["id"],
            " ".join(context.args[1:])
        )

        await update.effective_message.reply_text(
            "✅ Player added."
        )

    except ValueError as error:

        await update.effective_message.reply_text(
            "❌ " + str(error)
        )


# =========================================================
# REMOVE PLAYER
# =========================================================

async def removeplayer(update, context):

    if len(context.args) < 2:

        await update.effective_message.reply_text(
            "Use `/removeplayer 123456 PLAYER_ID`",
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

    try:

        player_id = int(
            context.args[1]
        )

        ok = db.remove_player(
            auction["id"],
            player_id
        )

    except Exception:

        ok = False

    await update.effective_message.reply_text(
        "✅ Removed."
        if ok
        else "❌ Player not found/pending."
    )


# =========================================================
# RE-AUCTION
# =========================================================

async def reauction(update, context):

    if len(context.args) < 2:

        await update.effective_message.reply_text(
            "Use `/reauction 123456 PLAYER_ID`",
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

    try:

        player_id = int(
            context.args[1]
        )

        ok, message = db.reauction_player(
            auction["id"],
            auction["host_id"],
            player_id
        )

        await update.effective_message.reply_text(
            ("✅ " if ok else "❌ ") + message
        )

    except Exception as error:

        await update.effective_message.reply_text(
            "❌ Invalid player ID."
        )

        log.exception(
            "Reauction error: %s",
            error
        )


# =========================================================
# SKIP / UNSOLD
# =========================================================

async def skip(update, context):

    if not context.args:

        await update.effective_message.reply_text(
            "Use `/skip 123456`",
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

    result = db.mark_unsold_and_next(
        auction["id"]
    )

    if not result:

        await update.effective_message.reply_text(
            "❌ No active player."
        )

        return

    if result["next"]:

        await announce_current(
            auction["id"],
            context
        )

    else:

        await update.effective_message.reply_text(
            "🏁 Auction completed."
        )


async def unsold(update, context):
    await skip(update, context)


# =========================================================
# AUCTION STATS
# =========================================================

async def auctionstats(update, context):

    if not context.args:

        await update.effective_message.reply_text(
            "Use `/auctionstats 123456`",
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

    stats = db.stats(
        auction["id"]
    )

    text = (
        f"📊 *{auction['name']}*\n\n"
        f"Players: {stats['players']}\n"
        f"Sold: {stats['sold']}\n"
        f"Unsold: {stats['unsold']}\n"
        f"Remaining: {stats['pending']}\n"
        f"Participants: {stats['participants']}"
    )

    await update.effective_message.reply_text(
        text,
        parse_mode="Markdown"
    )


# =========================================================
# PARTICIPANTS
# =========================================================

async def participants(update, context):

    if not context.args:

        await update.effective_message.reply_text(
            "Use `/participants 123456`",
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

    with db.conn() as connection:

        rows = connection.execute(
            """
            SELECT
                u.username,
                u.first_name,
                p.balance
            FROM participants p
            JOIN users u
                ON u.telegram_id = p.user_id
            WHERE p.auction_id = ?
            ORDER BY p.joined_at
            """,
            (auction["id"],)
        ).fetchall()

    text = "👥 *PARTICIPANTS*\n\n"

    if rows:

        text += "\n".join(
            f"• @{row['username'] or row['first_name']} "
            f"— {row['balance']:.1f} Cr"
            for row in rows
        )

    else:

        text += "None."

    await update.effective_message.reply_text(
        text,
        parse_mode="Markdown"
    )


# =========================================================
# RESULTS
# =========================================================

async def results(update, context):

    if not context.args:

        await update.effective_message.reply_text(
            "Use `/results 123456`",
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

    rows = db.leaderboard(
        auction["id"]
    )

    if not rows:

        await update.effective_message.reply_text(
            "🏆 No results yet."
        )

        return

    medals = [
        "🥇",
        "🥈",
        "🥉"
    ]

    text = "🏆 *AUCTION RESULTS*\n\n"

    for index, row in enumerate(rows[:10]):

        medal = (
            medals[index]
            if index < 3
            else "▫️"
        )

        text += (
            f"{medal} "
            f"@{row['username'] or row['first_name']} — "
            f"{row['players_won']} players, "
            f"{row['spent']:.1f} Cr\n"
        )

    await update.effective_message.reply_text(
        text,
        parse_mode="Markdown"
    )


# =========================================================
# ADMIN
# =========================================================

async def admin(update, context):

    if not is_admin(
        update.effective_user.id
    ):

        await update.effective_message.reply_text(
            "❌ Admin only."
        )

        return

    text = (
        "🛠 *ADMIN COMMANDS*\n\n"
        "/createauction\n"
        "/startauction CODE\n"
        "/pauseauction CODE\n"
        "/resumeauction CODE\n"
        "/stopauction CODE\n"
        "/addplayer CODE NAME\n"
        "/removeplayer CODE PLAYER_ID\n"
        "/reauction CODE PLAYER_ID\n"
        "/skip CODE\n"
        "/unsold CODE\n"
        "/participants CODE\n"
        "/auctionstats CODE\n"
        "/results CODE\n"
        "/broadcast TEXT"
    )

    await update.effective_message.reply_text(
        text,
        parse_mode="Markdown"
    )


# =========================================================
# BROADCAST
# =========================================================

async def broadcast(update, context):

    if not is_admin(
        update.effective_user.id
    ):

        await update.effective_message.reply_text(
            "❌ Admin only."
        )

        return

    text = " ".join(
        context.args
    ).strip()

    if not text:

        await update.effective_message.reply_text(
            "Use `/broadcast message`",
            parse_mode="Markdown"
        )

        return

    with db.conn() as connection:

        ids = [
            row["telegram_id"]
            for row in connection.execute(
                "SELECT telegram_id FROM users"
            ).fetchall()
        ]

    sent = 0

    for uid in ids:

        try:

            await context.bot.send_message(
                uid,
                text
            )

            sent += 1

        except Exception:

            pass

    await update.effective_message.reply_text(
        f"📣 Sent to {sent} users."
    )


# =========================================================
# ANNOUNCE CURRENT PLAYER
# =========================================================

async def announce_current(
    auction_id,
    context
):

    auction = db.get_auction_by_id(
        auction_id
    )

    if not auction:
        return

    player = db.current_player(
        auction_id
    )

    if not player:

        db.set_state(
            auction_id,
            "COMPLETED"
        )

        await context.bot.send_message(
            auction["host_id"],
            "🏁 Auction completed."
        )

        return

    buttons = [
        [
            InlineKeyboardButton(
                "💰 Bid +0.5 Cr",
                callback_data=f"bid:{auction['code']}"
            )
        ],
        [
            InlineKeyboardButton(
                "📊 Current Bid",
                callback_data=f"bids:{auction['code']}"
            ),
            InlineKeyboardButton(
                "👤 My Balance",
                callback_data=f"bal:{auction['code']}"
            ),
        ],
        [
            InlineKeyboardButton(
                "⏭ Skip Player",
                callback_data=f"skip:{auction['code']}"
            )
        ]
    ]

    text = (
        "🔥 *PLAYER AUCTION STARTED*\n\n"
        f"🔨 *{player['name']}*\n"
        "💰 Current Bid: 2.0 Cr\n"
        "👑 Highest Bidder: None\n"
        f"⏱ Bidding time: {BID_SECONDS}s"
    )

    with db.conn() as connection:

        ids = [
            row["user_id"]
            for row in connection.execute(
                """
                SELECT user_id
                FROM participants
                WHERE auction_id = ?
                """,
                (auction_id,)
            ).fetchall()
        ]

    for uid in ids:

        try:

            await context.bot.send_message(
                uid,
                text,
                parse_mode="Markdown",
                reply_markup=kb(buttons)
            )

        except Exception as error:

            log.warning(
                "Could not notify user %s: %s",
                uid,
                error
            )

    old_task = auction_tasks.get(
        auction_id
    )

    if old_task and not old_task.done():

        old_task.cancel()

    auction_tasks[auction_id] = asyncio.create_task(
        player_timer(
            auction_id,
            context
        )
    )


# =========================================================
# PLAYER TIMER
# =========================================================

async def player_timer(
    auction_id,
    context
):

    try:

        remaining = BID_SECONDS

        while remaining > 0:

            await asyncio.sleep(1)

            auction = db.get_auction_by_id(
                auction_id
            )

            if not auction:
                return

            if auction["state"] in (
                "CANCELLED",
                "COMPLETED"
            ):
                return

            if auction["state"] == "PAUSED":
                continue

            remaining -= 1

        auction = db.get_auction_by_id(
            auction_id
        )

        if not auction:
            return

        if auction["state"] != "RUNNING":
            return

        result = db.sell_current(
            auction_id
        )

        if not result:
            return

        player = result["sold_player"]
        last_bid = result["last"]

        with db.conn() as connection:

            participant_ids = [
                row["user_id"]
                for row in connection.execute(
                    """
                    SELECT user_id
                    FROM participants
                    WHERE auction_id = ?
                    """,
                    (auction_id,)
                ).fetchall()
            ]

        if last_bid:

            with db.conn() as connection:

                winner = connection.execute(
                    """
                    SELECT username, first_name
                    FROM users
                    WHERE telegram_id = ?
                    """,
                    (last_bid["user_id"],)
                ).fetchone()

            winner_name = (
                winner["username"]
                if winner and winner["username"]
                else winner["first_name"]
                if winner
                else "Unknown"
            )

            message = (
                "🔨 *SOLD!*\n\n"
                f"👤 {player['name']}\n"
                f"💰 Sold for: "
                f"{last_bid['amount']:.1f} Cr\n"
                f"👑 Winner: @{winner_name}"
            )

        else:

            message = (
                "⚪ *UNSOLD*\n\n"
                f"👤 {player['name']}\n"
                "No valid bids."
            )

        for uid in participant_ids:

            try:

                await context.bot.send_message(
                    uid,
                    message,
                    parse_mode="Markdown"
                )

            except Exception:
                pass

        if result["next"]:

            await asyncio.sleep(2)

            await announce_current(
                auction_id,
                context
            )

        else:

            db.set_state(
                auction_id,
                "COMPLETED"
            )

            rows = db.leaderboard(
                auction_id
            )

            result_text = (
                "🏁 *AUCTION COMPLETED*\n\n"
                "🏆 *RESULTS*\n"
            )

            for index, row in enumerate(
                rows[:10],
                start=1
            ):

                result_text += (
                    f"{index}. "
                    f"@{row['username'] or row['first_name']} — "
                    f"{row['players_won']} players — "
                    f"{row['spent']:.1f} Cr\n"
                )

            for uid in participant_ids:

                try:

                    await context.bot.send_message(
                        uid,
                        result_text,
                        parse_mode="Markdown"
                    )

                except Exception:
                    pass

    except asyncio.CancelledError:

        return

    except Exception:

        log.exception(
            "Player timer error for auction %s",
            auction_id
        )


# =========================================================
# CALLBACK BUTTON HANDLER
# =========================================================

async def button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    data = query.data or ""

    # -----------------------------------------------------
    # DASHBOARD BUTTONS
    # -----------------------------------------------------

    if data == "create":

        return await create_start(
            update,
            context
        )

    if data == "join":

        await query.message.reply_text(
            "🎯 To join an auction, use:\n\n"
            "`/join 123456`",
            parse_mode="Markdown"
        )

        return

    if data == "live":

        await show_live_callback(
            query
        )

        return

    if data == "team":

        await query.message.reply_text(
            "👤 Use `/team 123456` to view your team.",
            parse_mode="Markdown"
        )

        return

    if data == "balance":

        await query.message.reply_text(
            "💰 Use `/balance 123456` to view your balance.",
            parse_mode="Markdown"
        )

        return

    if data == "history":

        rows = db.user_history(
            query.from_user.id
        )

        if not rows:

            await query.message.reply_text(
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

        await query.message.reply_text(
            text,
            parse_mode="Markdown"
        )

        return

    if data == "help":

        await query.message.reply_text(
            "❓ *HELP*\n\n"
            "/createauction — create auction\n"
            "/join CODE — join auction\n"
            "/live — live auctions\n"
            "/balance CODE — balance\n"
            "/team CODE — team\n"
            "/auction CODE — dashboard\n"
            "/mybids CODE — bid history\n"
            "/history — history",
            parse_mode="Markdown"
        )

        return

    # -----------------------------------------------------
    # VIEW AUCTION
    # -----------------------------------------------------

    if data.startswith("view:"):

        code_value = data.split(
            ":",
            1
        )[1]

        auction = db.get_auction(
            code_value
        )

        if not auction:

            await query.message.reply_text(
                "❌ Auction not found."
            )

            return

        await query.message.reply_text(
            auction_text(auction),
            parse_mode="Markdown"
        )

        return

    # -----------------------------------------------------
    # BID
    # -----------------------------------------------------

    if data.startswith("bid:"):

        code_value = data.split(
            ":",
            1
        )[1]

        auction = db.get_auction(
            code_value
        )

        if not auction:

            await query.message.reply_text(
                "❌ Auction not found."
            )

            return

        participant = db.participant(
            auction["id"],
            query.from_user.id
        )

        if not participant:

            await query.message.reply_text(
                "❌ You are not a participant."
            )

            return

        if auction["state"] != "RUNNING":

            await query.message.reply_text(
                "❌ Bidding is not currently active."
            )

            return

        player = db.current_player(
            auction["id"]
        )

        if not player:

            await query.message.reply_text(
                "❌ No active player."
            )

            return

        try:

            result = db.place_bid(
                auction["id"],
                query.from_user.id
            )

        except Exception as error:

            log.exception(
                "Bid error: %s",
                error
            )

            await query.message.reply_text(
                "❌ Bid failed. Please try again."
            )

            return

        if isinstance(result, tuple):

            ok, message = result

            await query.message.reply_text(
                ("✅ " if ok else "❌ ")
                + str(message)
            )

        else:

            await query.message.reply_text(
                "❌ Bid could not be processed."
            )

        return

    # -----------------------------------------------------
    # CURRENT BIDS
    # -----------------------------------------------------

    if data.startswith("bids:"):

        code_value = data.split(
            ":",
            1
        )[1]

        auction = db.get_auction(
            code_value
        )

        if not auction:

            await query.message.reply_text(
                "❌ Auction not found."
            )

            return

        player = db.current_player(
            auction["id"]
        )

        if not player:

            await query.message.reply_text(
                "ℹ️ No active player."
            )

            return

        rows = db.bid_history(
            auction["id"],
            player["id"]
        )

        text = (
            f"📊 *BID HISTORY*\n\n"
            f"🔨 {player['name']}\n\n"
        )

        if rows:

            text += "\n".join(
                f"{row['amount']:.1f} Cr — "
                f"@{row['username'] or row['first_name']}"
                for row in rows
            )

        else:

            text += "No bids yet."

        await query.message.reply_text(
            text,
            parse_mode="Markdown"
        )

        return

    # -----------------------------------------------------
    # BALANCE BUTTON
    # -----------------------------------------------------

    if data.startswith("bal:"):

        code_value = data.split(
            ":",
            1
        )[1]

        auction = db.get_auction(
            code_value
        )

        if not auction:

            await query.message.reply_text(
                "❌ Auction not found."
            )

            return

        participant = db.participant(
            auction["id"],
            query.from_user.id
        )

        if not participant:

            await query.message.reply_text(
                "❌ You are not a participant."
            )

            return

        await query.message.reply_text(
            f"💰 Your balance: "
            f"*{participant['balance']:.1f} Cr*",
            parse_mode="Markdown"
        )

        return

    # -----------------------------------------------------
    # SKIP BUTTON
    # -----------------------------------------------------

    if data.startswith("skip:"):

        code_value = data.split(
            ":",
            1
        )[1]

        auction = db.get_auction(
            code_value
        )

        if not auction:

            await query.message.reply_text(
                "❌ Auction not found."
            )

            return

        if not can_control(
            auction,
            query.from_user.id
        ):

            await query.message.reply_text(
                "❌ Only the host/admin can skip players."
            )

            return

        result = db.mark_unsold_and_next(
            auction["id"]
        )

        if not result:

            await query.message.reply_text(
                "❌ No active player."
            )

            return

        if result["next"]:

            await query.message.reply_text(
                "⏭ Player skipped."
            )

            await announce_current(
                auction["id"],
                context
            )

        else:

            await query.message.reply_text(
                "🏁 Auction completed."
            )

        return


# =========================================================
# LIVE AUCTION CALLBACK
# =========================================================

async def show_live_callback(query):

    auctions = db.live_auctions()

    if not auctions:

        await query.message.reply_text(
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

    await query.message.reply_text(
        "🔥 *LIVE AUCTIONS*",
        parse_mode="Markdown",
        reply_markup=kb(buttons)
    )


# =========================================================
# CONVERSATION HANDLER
# =========================================================

create_conversation = ConversationHandler(

    entry_points=[
        CommandHandler(
            "createauction",
            create_command
        ),
        CallbackQueryHandler(
            create_start,
            pattern=r"^create$"
        ),
    ],

    states={

        CREATE_NAME: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                create_name
            )
        ],

        CREATE_LIMIT: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                create_limit
            )
        ],

        CREATE_PLAYERS: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                create_players
            )
        ],
    },

    fallbacks=[
        CommandHandler(
            "cancel",
            cancel_create
        )
    ],

    per_user=True,
    per_chat=True,
)


# =========================================================
# REGISTER HANDLERS
# =========================================================

BOT.add_handler(
    create_conversation
)

BOT.add_handler(
    CommandHandler(
        "start",
        start
    )
)

BOT.add_handler(
    CommandHandler(
        "help",
        help_cmd
    )
)

BOT.add_handler(
    CommandHandler(
        "join",
        join
    )
)

BOT.add_handler(
    CommandHandler(
        "live",
        live
    )
)

BOT.add_handler(
    CommandHandler(
        "auction",
        auction_view
    )
)

BOT.add_handler(
    CommandHandler(
        "balance",
        balance
    )
)

BOT.add_handler(
    CommandHandler(
        "team",
        team
    )
)

BOT.add_handler(
    CommandHandler(
        "mybids",
        mybids
    )
)

BOT.add_handler(
    CommandHandler(
        "history",
        history
    )
)

BOT.add_handler(
    CommandHandler(
        "startauction",
        startauction
    )
)

BOT.add_handler(
    CommandHandler(
        "pauseauction",
        pause
    )
)

BOT.add_handler(
    CommandHandler(
        "resumeauction",
        resume
    )
)

BOT.add_handler(
    CommandHandler(
        "stopauction",
        stop
    )
)

BOT.add_handler(
    CommandHandler(
        "addplayer",
        addplayer
    )
)

BOT.add_handler(
    CommandHandler(
        "removeplayer",
        removeplayer
    )
)

BOT.add_handler(
    CommandHandler(
        "reauction",
        reauction
    )
)

BOT.add_handler(
    CommandHandler(
        "skip",
        skip
    )
)

BOT.add_handler(
    CommandHandler(
        "unsold",
        unsold
    )
)

BOT.add_handler(
    CommandHandler(
        "participants",
        participants
    )
)

BOT.add_handler(
    CommandHandler(
        "auctionstats",
        auctionstats
    )
)

BOT.add_handler(
    CommandHandler(
        "results",
        results
    )
)

BOT.add_handler(
    CommandHandler(
        "admin",
        admin
    )
)

BOT.add_handler(
    CommandHandler(
        "broadcast",
        broadcast
    )
)

BOT.add_handler(
    CallbackQueryHandler(
        button
    )
)


# =========================================================
# FASTAPI
# =========================================================

@asynccontextmanager
async def lifespan(app):

    log.info("Starting Telegram bot...")

    await BOT.initialize()
    await BOT.start()

    webhook_url = (
        PUBLIC_URL.rstrip("/")
        + "/telegram/webhook"
    )

    await BOT.bot.set_webhook(
        url=webhook_url,
        secret_token=WEBHOOK_SECRET,
        allowed_updates=Update.ALL_TYPES,
    )

    log.info(
        "Webhook configured: %s",
        webhook_url
    )

    yield

    log.info("Stopping Telegram bot...")

    try:
        await BOT.bot.delete_webhook()
    except Exception:
        pass

    await BOT.stop()
    await BOT.shutdown()


app = FastAPI(
    title="eFootball Auction Bot",
    lifespan=lifespan
)


# =========================================================
# HEALTH CHECK
# =========================================================

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


# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

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

        await BOT.process_update(
            update
        )

        return {
            "ok": True
        }

    except Exception as error:

        log.exception(
            "Webhook processing error: %s",
            error
        )

        raise HTTPException(
            status_code=500,
            detail="Webhook processing failed"
        )
