import os
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import db


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
PUBLIC_URL = os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required.")

if not PUBLIC_URL:
    raise RuntimeError(
        "PUBLIC_URL or RENDER_EXTERNAL_URL environment variable is required."
    )

PUBLIC_URL = PUBLIC_URL.rstrip("/")
WEBHOOK_PATH = "/telegram/webhook"

STARTING_BALANCE = 100.0


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

log = logging.getLogger("efootball-auction-bot")


# =========================================================
# CREATE AUCTION STATES
# =========================================================

CREATE_NAME = 1
CREATE_PARTICIPANTS = 2
CREATE_PLAYERS = 3


# =========================================================
# HELPERS
# =========================================================

def auction_code():
    return secrets.token_hex(3).upper()


def dashboard_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🏆 Create Auction",
                callback_data="create"
            ),
            InlineKeyboardButton(
                "🎯 Join Auction",
                callback_data="join_help"
            ),
        ],
        [
            InlineKeyboardButton(
                "🔴 Live Auctions",
                callback_data="live"
            ),
            InlineKeyboardButton(
                "💰 My Balance",
                callback_data="balance_help"
            ),
        ],
        [
            InlineKeyboardButton(
                "👥 My Team",
                callback_data="team_help"
            ),
            InlineKeyboardButton(
                "📜 Auction History",
                callback_data="history_help"
            ),
        ],
        [
            InlineKeyboardButton(
                "❓ Help",
                callback_data="help"
            )
        ],
    ])


def auction_keyboard(auction):
    code = auction["code"]

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💰 Bid +0.5 Cr",
                callback_data=f"bid:0.5:{code}"
            ),
            InlineKeyboardButton(
                "💰 Bid +1.0 Cr",
                callback_data=f"bid:1.0:{code}"
            ),
        ],
        [
            InlineKeyboardButton(
                "💰 Bid +1.5 Cr",
                callback_data=f"bid:1.5:{code}"
            ),
            InlineKeyboardButton(
                "💰 Bid +2.0 Cr",
                callback_data=f"bid:2.0:{code}"
            ),
        ],
        [
            InlineKeyboardButton(
                "📊 Current Bid",
                callback_data=f"bids:{code}"
            ),
        ],
        [
            InlineKeyboardButton(
                "💳 My Balance",
                callback_data=f"bal:{code}"
            ),
            InlineKeyboardButton(
                "👥 My Team",
                callback_data=f"team:{code}"
            ),
        ],
    ])


def host_keyboard(auction):
    code = auction["code"]

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "▶️ Start",
                callback_data=f"start:{code}"
            ),
            InlineKeyboardButton(
                "⏸ Pause",
                callback_data=f"pause:{code}"
            ),
        ],
        [
            InlineKeyboardButton(
                "▶️ Resume",
                callback_data=f"resume:{code}"
            ),
            InlineKeyboardButton(
                "🔨 Sell / Next",
                callback_data=f"sell:{code}"
            ),
        ],
        [
            InlineKeyboardButton(
                "📊 Stats",
                callback_data=f"stats:{code}"
            ),
        ],
    ])


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    db.upsert_user(user)

    text = (
        "⚽ *eFootball Auction Bot*\n\n"
        "Virtual credits only. Free to use.\n\n"
        "💰 Starting balance: 100 Cr\n"
        "💵 First bid: 2 Cr\n"
        "📈 Later bid increment: 0.5–2 Cr\n"
        "👥 Max participants per auction: 100\n"
        "⚽ Max players per auction: 500\n\n"
        "Choose an option:"
    )

    if update.message:
        await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=dashboard_keyboard(),
        )


# =========================================================
# HELP
# =========================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        "❓ *HELP*\n\n"
        "/start — Open dashboard\n"
        "/createauction — Create an auction\n"
        "/join CODE — Join an auction\n"
        "/live — Live auctions\n"
        "/balance CODE — Check balance\n"
        "/team CODE — View your team\n"
        "/auction CODE — Auction dashboard\n"
        "/history — Auction history\n\n"
        "💰 First bid is always 2.0 Cr.\n"
        "📈 Later bids can increase by 0.5, 1.0, 1.5 or 2.0 Cr."
    )

    await update.message.reply_text(
        text,
        parse_mode="Markdown"
    )


# =========================================================
# CREATE AUCTION
# =========================================================

async def create_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    return await create_start(update, context)


async def create_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.callback_query:
        await update.callback_query.answer()

        await update.callback_query.message.reply_text(
            "🏆 Enter the auction name:"
        )
    else:
        await update.message.reply_text(
            "🏆 Enter the auction name:"
        )

    return CREATE_NAME


async def create_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    name = update.message.text.strip()

    if not name:
        await update.message.reply_text(
            "❌ Please enter a valid auction name."
        )
        return CREATE_NAME

    if len(name) > 100:
        await update.message.reply_text(
            "❌ Auction name is too long. Maximum 100 characters."
        )
        return CREATE_NAME

    context.user_data["auction_name"] = name

    await update.message.reply_text(
        "👥 Enter maximum participants.\n\n"
        "Minimum: 1\n"
        "Maximum: 100\n\n"
        "Example: 20"
    )

    return CREATE_PARTICIPANTS


async def create_participants(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:
        maximum = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text(
            "❌ Enter a number between 1 and 100."
        )
        return CREATE_PARTICIPANTS

    if maximum < 1 or maximum > 100:
        await update.message.reply_text(
            "❌ Participants must be between 1 and 100."
        )
        return CREATE_PARTICIPANTS

    context.user_data["max_participants"] = maximum

    await update.message.reply_text(
        "⚽ Send the player list.\n\n"
        "One player per line.\n\n"
        "Maximum: 500 players.\n\n"
        "Example:\n"
        "Messi\n"
        "Ronaldo\n"
        "Mbappe\n"
        "Haaland"
    )

    return CREATE_PLAYERS


async def create_players(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    lines = [
        x.strip()
        for x in update.message.text.splitlines()
        if x.strip()
    ]

    if not lines:
        await update.message.reply_text(
            "❌ Please send at least one player."
        )
        return CREATE_PLAYERS

    if len(lines) > 500:
        await update.message.reply_text(
            "❌ Maximum 500 players are allowed."
        )
        return CREATE_PLAYERS

    # Remove duplicates while preserving order
    players = list(dict.fromkeys(lines))

    name = context.user_data["auction_name"]
    maximum = context.user_data["max_participants"]

    user = update.effective_user

    db.upsert_user(user)

    code = auction_code()

    try:
        auction_id = db.create_auction(
            user.id,
            name,
            maximum,
            code,
        )

        db.add_players(
            auction_id,
            players,
        )

        # Host automatically joins
        ok, message = db.join_auction(
            auction_id,
            user.id,
            STARTING_BALANCE,
        )

        if not ok:
            await update.message.reply_text(
                "❌ Could not add host to auction."
            )
            return ConversationHandler.END

    except Exception as error:

        log.exception(
            "Create auction failed: %s",
            error
        )

        await update.message.reply_text(
            "❌ Auction creation failed. Please try again."
        )

        return ConversationHandler.END

    await update.message.reply_text(
        "✅ *Auction Created!*\n\n"
        f"🏆 Name: {name}\n"
        f"🔑 Auction Code: `{code}`\n"
        f"👥 Max Participants: {maximum}\n"
        f"⚽ Players: {len(players)}\n"
        f"💰 Starting Balance: {STARTING_BALANCE:.0f} Cr\n\n"
        f"Share this code with participants:\n"
        f"`/join {code}`\n\n"
        "When everyone joins, use the auction dashboard to start.",
        parse_mode="Markdown",
    )

    context.user_data.clear()

    return ConversationHandler.END


async def create_cancel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data.clear()

    await update.message.reply_text(
        "❌ Auction creation cancelled."
    )

    return ConversationHandler.END


# =========================================================
# JOIN AUCTION
# =========================================================

async def join_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not context.args:
        await update.message.reply_text(
            "Usage:\n/join AUCTION_CODE"
        )
        return

    code = context.args[0].strip().upper()

    auction = db.get_auction(code)

    if not auction:
        await update.message.reply_text(
            "❌ Auction not found."
        )
        return

    user = update.effective_user

    db.upsert_user(user)

    ok, message = db.join_auction(
        auction["id"],
        user.id,
        STARTING_BALANCE,
    )

    if ok:
        await update.message.reply_text(
            "✅ Joined auction!\n\n"
            f"🏆 {auction['name']}\n"
            f"💰 Balance: {STARTING_BALANCE:.1f} Cr\n\n"
            f"Auction Code: `{auction['code']}`",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "❌ " + str(message)
        )


# =========================================================
# LIVE AUCTIONS
# =========================================================

async def live_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    auctions = db.live_auctions()

    if not auctions:
        await update.message.reply_text(
            "🔴 No live auctions currently."
        )
        return

    text = "🔴 *Live Auctions*\n\n"

    for auction in auctions:
        count = db.participant_count(auction["id"])

        text += (
            f"🏆 *{auction['name']}*\n"
            f"🔑 `{auction['code']}`\n"
            f"📌 State: {auction['state']}\n"
            f"👥 Participants: {count}/{auction['max_participants']}\n\n"
        )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
    )


# =========================================================
# AUCTION DASHBOARD
# =========================================================

async def auction_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not context.args:
        await update.message.reply_text(
            "Usage:\n/auction AUCTION_CODE"
        )
        return

    code = context.args[0].strip().upper()

    auction = db.get_auction(code)

    if not auction:
        await update.message.reply_text(
            "❌ Auction not found."
        )
        return

    await send_auction_dashboard(
        update,
        auction,
    )


async def send_auction_dashboard(
    update_or_query,
    auction,
):

    player = db.current_player(
        auction["id"]
    )

    stats = db.stats(
        auction["id"]
    )

    text = (
        f"🏆 *{auction['name']}*\n\n"
        f"🔑 Code: `{auction['code']}`\n"
        f"📌 State: {auction['state']}\n"
        f"👥 Participants: {stats['participants']}/{auction['max_participants']}\n"
        f"⚽ Players: {stats['players']}\n"
        f"✅ Sold: {stats['sold']}\n"
        f"❌ Unsold: {stats['unsold']}\n"
        f"⏳ Pending: {stats['pending']}\n\n"
    )

    if player:
        text += (
            "🔥 *CURRENT PLAYER*\n\n"
            f"⚽ {player['name']}\n"
            f"💰 Starting bid: 2.0 Cr\n\n"
            "First bid is fixed at 2.0 Cr.\n"
            "After that, use the +0.5 / +1 / +1.5 / +2 buttons."
        )

    else:
        text += "⏳ No current player."

    keyboard = auction_keyboard(auction)

    if update_or_query.callback_query:
        await update_or_query.callback_query.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
    else:
        await update_or_query.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )


# =========================================================
# BALANCE
# =========================================================

async def balance_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not context.args:
        await update.message.reply_text(
            "Usage:\n/balance AUCTION_CODE"
        )
        return

    code = context.args[0].strip().upper()

    auction = db.get_auction(code)

    if not auction:
        await update.message.reply_text(
            "❌ Auction not found."
        )
        return

    participant = db.participant(
        auction["id"],
        update.effective_user.id
    )

    if not participant:
        await update.message.reply_text(
            "❌ You are not a participant."
        )
        return

    await update.message.reply_text(
        f"💰 *Your Balance*\n\n"
        f"{participant['balance']:.1f} Cr",
        parse_mode="Markdown",
    )


# =========================================================
# TEAM
# =========================================================

async def team_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not context.args:
        await update.message.reply_text(
            "Usage:\n/team AUCTION_CODE"
        )
        return

    code = context.args[0].strip().upper()

    auction = db.get_auction(code)

    if not auction:
        await update.message.reply_text(
            "❌ Auction not found."
        )
        return

    holdings = db.my_holdings(
        auction["id"],
        update.effective_user.id
    )

    if not holdings:
        await update.message.reply_text(
            "👥 You haven't purchased any players yet."
        )
        return

    text = "👥 *MY TEAM*\n\n"

    total = 0

    for index, row in enumerate(holdings, start=1):
        text += (
            f"{index}. ⚽ {row['name']} — "
            f"{row['price']:.1f} Cr\n"
        )
        total += row["price"]

    text += (
        f"\n💰 Total spent: {total:.1f} Cr"
    )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
    )


# =========================================================
# HISTORY
# =========================================================

async def history_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    rows = db.user_history(
        update.effective_user.id
    )

    if not rows:
        await update.message.reply_text(
            "📜 No auction history yet."
        )
        return

    text = "📜 *AUCTION HISTORY*\n\n"

    for row in rows:
        text += (
            f"🏆 {row['name']}\n"
            f"🔑 {row['code']}\n"
            f"📌 {row['state']}\n"
            f"⚽ Players won: {row['players_won']}\n"
            f"💰 Spent: {row['spent']:.1f} Cr\n\n"
        )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
    )


# =========================================================
# CALLBACK BUTTONS
# =========================================================

async def button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    data = query.data or ""

    # -----------------------------------------------------
    # DASHBOARD
    # -----------------------------------------------------

    if data == "create":

        return await create_start(
            update,
            context
        )

    if data == "join_help":

        await query.message.reply_text(
            "Use:\n\n/join AUCTION_CODE\n\n"
            "Example:\n/join ABC123"
        )

        return

    if data == "live":

        auctions = db.live_auctions()

        if not auctions:
            await query.message.reply_text(
                "🔴 No live auctions."
            )
            return

        text = "🔴 *LIVE AUCTIONS*\n\n"

        for auction in auctions:
            count = db.participant_count(
                auction["id"]
            )

            text += (
                f"🏆 {auction['name']}\n"
                f"🔑 `{auction['code']}`\n"
                f"📌 {auction['state']}\n"
                f"👥 {count}/{auction['max_participants']}\n\n"
            )

        await query.message.reply_text(
            text,
            parse_mode="Markdown",
        )

        return

    if data == "balance_help":

        await query.message.reply_text(
            "Use:\n\n/balance AUCTION_CODE"
        )

        return

    if data == "team_help":

        await query.message.reply_text(
            "Use:\n\n/team AUCTION_CODE"
        )

        return

    if data == "history_help":

        await query.message.reply_text(
            "Use:\n\n/history"
        )

        return

    if data == "help":

        await query.message.reply_text(
            "❓ *HELP*\n\n"
            "/start\n"
            "/createauction\n"
            "/join CODE\n"
            "/live\n"
            "/balance CODE\n"
            "/team CODE\n"
            "/auction CODE\n"
            "/history",
            parse_mode="Markdown",
        )

        return

    # -----------------------------------------------------
    # BID
    # -----------------------------------------------------
if data.startswith("bid:"):

        parts = data.split(":")

        if len(parts) != 3:

            await query.message.reply_text(
                "❌ Invalid bid."
            )

            return

        try:

            increment = float(parts[1])
            code_value = parts[2]

        except ValueError:

            await query.message.reply_text(
                "❌ Invalid bid amount."
            )

            return

        if increment < 0.5 or increment > 2.0:

            await query.message.reply_text(
                "❌ Bid increment must be between 0.5 Cr and 2.0 Cr."
            )

            return

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

            result = db.bid(
                auction["id"],
                query.from_user.id,
                increment
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

            ok = result[0]
            message = result[1]

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

        code = data.split(
            ":",
            1
        )[1]

        auction = db.get_auction(code)

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
                "❌ No active player."
            )

            return

        rows = db.bid_history(
            auction["id"],
            player["id"]
        )

        if not rows:

            await query.message.reply_text(
                f"📊 No bids yet for {player['name']}."
            )

            return

        text = (
            f"📊 *BID HISTORY*\n\n"
            f"⚽ {player['name']}\n\n"
        )

        for row in reversed(rows):

            username = (
                row["username"]
                or row["first_name"]
                or "Player"
            )

            text += (
                f"👤 {username} — "
                f"{row['amount']:.1f} Cr\n"
            )

        await query.message.reply_text(
            text,
            parse_mode="Markdown",
        )

        return

    # -----------------------------------------------------
    # BALANCE BUTTON
    # -----------------------------------------------------

    if data.startswith("bal:"):

        code = data.split(
            ":",
            1
        )[1]

        auction = db.get_auction(code)

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
            f"💰 Your balance:\n\n"
            f"{participant['balance']:.1f} Cr"
        )

        return
    #
    i# -----------------------------------------------------
    # TEAM BUTTON
    # -----------------------------------------------------

    if data.startswith("team:"):

        code = data.split(
            ":",
            1
        )[1]

        auction = db.get_auction(code)

        if not auction:

            await query.message.reply_text(
                "❌ Auction not found."
            )

            return

        holdings = db.my_holdings(
            auction["id"],
            query.from_user.id
        )

        if not holdings:

            await query.message.reply_text(
                "👥 Your team is empty."
            )

            return

        text = "👥 *MY TEAM*\n\n"

        total = 0

        for index, row in enumerate(
            holdings,
            start=1
        ):

            text += (
                f"{index}. ⚽ {row['name']} — "
                f"{row['price']:.1f} Cr\n"
            )

            total += row["price"]

        text += (
            f"\n💰 Total spent: {total:.1f} Cr"
        )

        await query.message.reply_text(
            text,
            parse_mode="Markdown",
        )

        return

    # -----------------------------------------------------
    # START AUCTION
    # -----------------------------------------------------

    if data.startswith("start:"):

        code = data.split(
            ":",
            1
        )[1]

        auction = db.get_auction(code)

        if not auction:

            await query.message.reply_text(
                "❌ Auction not found."
            )

            return

        ok, message = db.start_auction(
            auction["id"],
            query.from_user.id
        )

        await query.message.reply_text(
            ("✅ " if ok else "❌ ")
            + str(message)
        )

        if ok:

            updated = db.get_auction(code)

            await send_auction_dashboard(
                update,
                updated
            )

        return
        # -----------------------------------------------------
    # PAUSE
    # -----------------------------------------------------

    if data.startswith("pause:"):

        code = data.split(
            ":",
            1
        )[1]

        auction = db.get_auction(code)

        if not auction:

            await query.message.reply_text(
                "❌ Auction not found."
            )

            return

        if auction["host_id"] != query.from_user.id:

            await query.message.reply_text(
                "❌ Only the host can pause."
            )

            return

        if auction["state"] != "RUNNING":

            await query.message.reply_text(
                "❌ Auction is not running."
            )

            return

        db.set_state(
            auction["id"],
            "PAUSED"
        )

        await query.message.reply_text(
            "⏸ Auction paused."
        )

        return

    # -----------------------------------------------------
    # RESUME
    # -----------------------------------------------------

    if data.startswith("resume:"):

        code = data.split(
            ":",
            1
        )[1]

        auction = db.get_auction(code)

        if not auction:

            await query.message.reply_text(
                "❌ Auction not found."
            )

            return

        if auction["host_id"] != query.from_user.id:

            await query.message.reply_text(
                "❌ Only the host can resume."
            )

            return

        if auction["state"] != "PAUSED":

            await query.message.reply_text(
                "❌ Auction is not paused."
            )

            return

        db.set_state(
            auction["id"],
            "RUNNING"
        )

        await query.message.reply_text(
            "▶️ Auction resumed."
        )

        return

    # -----------------------------------------------------
    # SELL / NEXT PLAYER
    # -----------------------------------------------------

    if data.startswith("sell:"):

        code = data.split(
            ":",
            1
        )[1]

        auction = db.get_auction(code)

        if not auction:

            await query.message.reply_text(
                "❌ Auction not found."
            )

            return

        if auction["host_id"] != query.from_user.id:

            await query.message.reply_text(
                "❌ Only the host can sell/next."
            )

            return

        result = db.sell_current(
            auction["id"]
        )

        if not result:

            await query.message.reply_text(
                "❌ No current player."
            )

            return

        sold_player = result["sold_player"]
        last = result["last"]
        next_player = result["next"]

        if last:

            await query.message.reply_text(
                f"🔨 *SOLD!*\n\n"
                f"⚽ {sold_player['name']}\n"
                f"💰 {last['amount']:.1f} Cr\n"
                f"👤 User ID: {last['user_id']}",
                parse_mode="Markdown",
            )

        else:

            await query.message.reply_text(
                f"❌ *UNSOLD*\n\n"
                f"⚽ {sold_player['name']}",
                parse_mode="Markdown",
            )

        if next_player:

            updated = db.get_auction(code)

            await send_auction_dashboard(
                update,
                updated
            )

        else:

            await query.message.reply_text(
                "🏁 *Auction completed!*",
                parse_mode="Markdown",
            )

        return
        # -----------------------------------------------------
    # STATS
    # -----------------------------------------------------

    if data.startswith("stats:"):

        code = data.split(
            ":",
            1
        )[1]

        auction = db.get_auction(code)

        if not auction:

            await query.message.reply_text(
                "❌ Auction not found."
            )

            return

        if auction["host_id"] != query.from_user.id:

            await query.message.reply_text(
                "❌ Only the host can view this."
            )

            return

        stats = db.stats(
            auction["id"]
        )

        await query.message.reply_text(
            f"📊 *AUCTION STATS*\n\n"
            f"⚽ Players: {stats['players']}\n"
            f"✅ Sold: {stats['sold']}\n"
            f"❌ Unsold: {stats['unsold']}\n"
            f"⏳ Pending: {stats['pending']}\n"
            f"👥 Participants: {stats['participants']}",
            parse_mode="Markdown",
        )

        return


# =========================================================
# TELEGRAM APPLICATION
# =========================================================

BOT = Application.builder().token(
    BOT_TOKEN
).build()


# =========================================================
# CREATE CONVERSATION
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

        CREATE_PARTICIPANTS: [
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                create_participants
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
            create_cancel
        )
    ],
)


BOT.add_handler(create_conversation)

BOT.add_handler(
    CommandHandler(
        "start",
        start
    )
)

BOT.add_handler(
    CommandHandler(
        "help",
        help_command
    )
)

BOT.add_handler(
    CommandHandler(
        "join",
        join_command
    )
)

BOT.add_handler(
    CommandHandler(
        "live",
        live_command
    )
)

BOT.add_handler(
    CommandHandler(
        "auction",
        auction_command
    )
)

BOT.add_handler(
    CommandHandler(
        "balance",
        balance_command
    )
)

BOT.add_handler(
    CommandHandler(
        "team",
        team_command
    )
)

BOT.add_handler(
    CommandHandler(
        "history",
        history_command
    )
)

BOT.add_handler(
    CallbackQueryHandler(
        button
    )
)

# =========================================================
# FASTAPI LIFESPAN
# =========================================================

@asynccontextmanager
async def lifespan(app):

    # IMPORTANT:
    # Create all SQLite tables before Telegram starts.
    db.init_db()

    log.info(
        "Starting Telegram bot..."
    )

    await BOT.initialize()
    await BOT.start()

    webhook_url = (
        PUBLIC_URL
        + WEBHOOK_PATH
    )

    await BOT.bot.set_webhook(
        url=webhook_url,
        allowed_updates=Update.ALL_TYPES,
        secret_token=(
            WEBHOOK_SECRET
            if WEBHOOK_SECRET
            else None
        ),
    )

    log.info(
        "Webhook configured: %s",
        webhook_url
    )

    yield

    try:

        await BOT.stop()
        await BOT.shutdown()

    except Exception:

        log.exception(
            "Telegram shutdown failed"
        )


# =========================================================
# FASTAPI APP
# =========================================================

app = FastAPI(
    title="eFootball Auction Bot",
    version="1.0.0",
    lifespan=lifespan,
)


# =========================================================
# HEALTH
# =========================================================

@app.get("/")
async def root():

    return {
        "status": "ok",
        "service": "efootball-auction-bot",
    }


@app.get("/health")
async def health():

    return {
        "status": "healthy"
    }


# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

@app.post(WEBHOOK_PATH)
async def telegram_webhook(
    request: Request
):

    if WEBHOOK_SECRET:

        incoming_secret = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token"
        )

        if incoming_secret != WEBHOOK_SECRET:

            raise HTTPException(
                status_code=403,
                detail="Invalid webhook secret",
            )

    data = await request.json()

    update = Update.de_json(
        data,
        BOT.bot
    )

    await BOT.update_queue.put(
        update
    )

    return {
        "ok": True
}
