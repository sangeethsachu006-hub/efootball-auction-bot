import os
import json
import asyncio
import logging
from datetime import datetime, timedelta
from threading import Lock

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")

ADMIN_IDS = {
    # Add Telegram numeric user IDs here.
    # Example:
    # 123456789,
}

DATA_FILE = "auction_data.json"

MAX_PARTICIPANTS = 500
MAX_PLAYERS = 500

STARTING_BALANCE = 100.0
MIN_START_BID = 2.0
MIN_INCREMENT = 0.5
MAX_INCREMENT = 2.0

AUCTION_DURATION = 30  # seconds

CURRENCY = "Cr"

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

data_lock = Lock()

# ============================================================
# DEFAULT DATA
# ============================================================

DEFAULT_DATA = {
    "participants": {},
    "players": {},
    "auction": {
        "active": False,
        "player_id": None,
        "highest_bid": 0,
        "highest_bidder": None,
        "started_at": None,
        "ends_at": None,
        "bid_history": [],
    },
    "settings": {
        "auction_duration": AUCTION_DURATION,
    },
}

# ============================================================
# DATABASE
# ============================================================


def load_data():
    with data_lock:
        if not os.path.exists(DATA_FILE):
            save_data(DEFAULT_DATA)
            return json.loads(json.dumps(DEFAULT_DATA))

        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Safety defaults
            data.setdefault("participants", {})
            data.setdefault("players", {})
            data.setdefault("auction", DEFAULT_DATA["auction"].copy())
            data.setdefault("settings", DEFAULT_DATA["settings"].copy())

            return data

        except Exception:
            logger.exception("Could not load database.")
            return json.loads(json.dumps(DEFAULT_DATA))


def save_data(data):
    with data_lock:
        temp_file = DATA_FILE + ".tmp"

        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

        os.replace(temp_file, DATA_FILE)


data = load_data()

# ============================================================
# UTILITY FUNCTIONS
# ============================================================


def is_admin(user_id):
    return user_id in ADMIN_IDS


def format_cr(amount):
    return f"{amount:.1f} Cr"


def get_user(user_id):
    return data["participants"].get(str(user_id))


def get_player(player_id):
    return data["players"].get(str(player_id))


def next_player_id():
    if not data["players"]:
        return 1

    return max(int(x) for x in data["players"].keys()) + 1


def next_player_order():
    return len(data["players"]) + 1


def remaining_balance(user_id):
    user = get_user(user_id)

    if not user:
        return 0

    return round(
        user["budget"] - user["spent"],
        2,
    )


def player_display(player):
    return (
        f"⚽ <b>{player['name']}</b>\n"
        f"🆔 Player ID: <code>{player['id']}</code>"
    )


def participant_display(user):
    return (
        f"👤 <b>{user['name']}</b>\n"
        f"💰 Balance: <b>{format_cr(remaining_balance(user['id']))}</b>\n"
        f"💸 Spent: <b>{format_cr(user['spent'])}</b>\n"
        f"⚽ Players: <b>{len(user['squad'])}</b>"
    )


def auction_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "+0.5 Cr",
                    callback_data="bid_0.5",
                ),
                InlineKeyboardButton(
                    "+1 Cr",
                    callback_data="bid_1",
                ),
            ],
            [
                InlineKeyboardButton(
                    "+1.5 Cr",
                    callback_data="bid_1.5",
                ),
                InlineKeyboardButton(
                    "+2 Cr",
                    callback_data="bid_2",
                ),
            ],
        ]
    )


def auction_status_text():
    auction = data["auction"]

    if not auction["active"]:
        return "🔴 No auction is currently active."

    player = get_player(auction["player_id"])

    if not player:
        return "❌ Auction player not found."

    if auction["highest_bidder"]:
        bidder = get_user(auction["highest_bidder"])
        bidder_name = bidder["name"] if bidder else "Unknown"
    else:
        bidder_name = "No bids yet"

    ends_at = auction["ends_at"]

    try:
        remaining = max(
            0,
            int(
                (
                    datetime.fromisoformat(ends_at)
                    - datetime.now()
                ).total_seconds()
            ),
        )
    except Exception:
        remaining = 0

    return (
        f"🔥 <b>LIVE AUCTION</b>\n\n"
        f"⚽ <b>{player['name']}</b>\n"
        f"🆔 Player ID: <code>{player['id']}</code>\n\n"
        f"💰 Current Bid: <b>{format_cr(auction['highest_bid'])}</b>\n"
        f"👑 Highest Bidder: <b>{bidder_name}</b>\n"
        f"⏱️ Time Left: <b>{remaining}s</b>\n\n"
        f"📈 Bid increment: <b>0.5–2 Cr</b>\n"
        f"💳 Maximum budget/player rules apply."
    )


# ============================================================
# PARTICIPANT COMMANDS
# ============================================================


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🏆 <b>eFootball Auction Bot</b>\n\n"
        "Welcome!\n\n"
        "💰 Starting Budget: <b>100 Cr</b>\n"
        "💵 Starting Bid: <b>2 Cr</b>\n"
        "📈 Bid Increment: <b>0.5–2 Cr</b>\n"
        "⏱️ Auction Duration: <b>30 seconds</b>\n"
        f"👥 Maximum Participants: <b>{MAX_PARTICIPANTS}</b>\n"
        f"⚽ Maximum Players: <b>{MAX_PLAYERS}</b>\n\n"
        "Use /help to see all commands."
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 <b>eFootball Auction Commands</b>\n\n"

        "👤 <b>Participant Commands</b>\n"
        "/register - Register for auction\n"
        "/balance - Check your balance\n"
        "/squad - View your purchased players\n"
        "/players - View auction players\n"
        "/leaderboard - View participants\n"
        "/auction - View current auction\n"
        "/history - View current bid history\n\n"

        "👑 <b>Admin Commands</b>\n"
        "/addplayer Name - Add player\n"
        "/removeplayer ID - Remove player\n"
        "/startauction ID - Start player auction\n"
        "/skip - Skip current player\n"
        "/stop - Stop current auction\n"
        "/cancel - Cancel current auction\n"
        "/participants - View participants\n"
        "/reset - Reset entire auction\n"
        "/announce Message - Send announcement\n\n"

        "💡 <b>Bid</b>\n"
        "You can use the buttons shown below an active auction.\n\n"

        "⚠️ Auction lasts exactly <b>30 seconds</b>.\n"
        "If nobody bids → <b>UNSOLD</b>.\n"
        "If bids exist → highest bidder gets the player."
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
    )


async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    user_id = str(user.id)

    if user_id in data["participants"]:
        await update.message.reply_text(
            "⚠️ You are already registered."
        )
        return

    if len(data["participants"]) >= MAX_PARTICIPANTS:
        await update.message.reply_text(
            "❌ Participant limit reached.\n"
            f"Maximum: {MAX_PARTICIPANTS}"
        )
        return

    participant = {
        "id": user.id,
        "name": user.full_name,
        "username": user.username or "",
        "budget": STARTING_BALANCE,
        "spent": 0.0,
        "squad": [],
        "registered_at": datetime.now().isoformat(),
    }

    data["participants"][user_id] = participant
    save_data(data)

    await update.message.reply_text(
        f"✅ <b>Registration successful!</b>\n\n"
        f"👤 {user.full_name}\n"
        f"💰 Starting Balance: <b>{format_cr(STARTING_BALANCE)}</b>\n\n"
        f"Good luck! 🏆",
        parse_mode="HTML",
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)

    if not user:
        await update.message.reply_text(
            "❌ You are not registered.\nUse /register"
        )
        return

    await update.message.reply_text(
        participant_display(user),
        parse_mode="HTML",
    )


async def squad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)

    if not user:
        await update.message.reply_text(
            "❌ You are not registered."
        )
        return

    if not user["squad"]:
        await update.message.reply_text(
            "⚽ Your squad is currently empty."
        )
        return

    text = (
        f"🏟️ <b>{user['name']}'s Squad</b>\n\n"
    )

    for index, player_id in enumerate(user["squad"], 1):
        player = get_player(player_id)

        if player:
            text += (
                f"{index}. ⚽ {player['name']} — "
                f"{format_cr(player['sold_price'])}\n"
            )

    text += (
        f"\n💰 Remaining: "
        f"<b>{format_cr(remaining_balance(user['id']))}</b>"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
    )


# ============================================================
# PLAYER LIST
# ============================================================


async def players(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not data["players"]:
        await update.message.reply_text(
            "⚽ No players have been added yet."
        )
        return

    text = "⚽ <b>Auction Players</b>\n\n"

    for player_id, player in data["players"].items():
        status = player["status"]

        if status == "sold":
            status_text = "✅ SOLD"
        elif status == "unsold":
            status_text = "❌ UNSOLD"
        elif status == "auctioned":
            status_text = "🔥 LIVE"
        else:
            status_text = "⏳ PENDING"

        text += (
            f"<code>{player_id}</code>. "
            f"<b>{player['name']}</b> — {status_text}\n"
        )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
    )


# ============================================================
# LEADERBOARD
# ============================================================


async def leaderboard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    participants = list(
        data["participants"].values()
    )

    if not participants:
        await update.message.reply_text(
            "No participants registered."
        )
        return

    participants.sort(
        key=lambda x: len(x["squad"]),
        reverse=True,
    )

    text = "🏆 <b>AUCTION LEADERBOARD</b>\n\n"

    for index, user in enumerate(participants, 1):
        text += (
            f"{index}. <b>{user['name']}</b>\n"
            f"   ⚽ Players: {len(user['squad'])}\n"
            f"   💰 Balance: {format_cr(remaining_balance(user['id']))}\n\n"
        )

        if index >= 50:
            break

    await update.message.reply_text(
        text,
        parse_mode="HTML",
    )


# ============================================================
# CURRENT AUCTION
# ============================================================


async def auction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not data["auction"]["active"]:
        await update.message.reply_text(
            "🔴 No active auction."
        )
        return

    await update.message.reply_text(
        auction_status_text(),
        parse_mode="HTML",
        reply_markup=auction_keyboard(),
    )


# ============================================================
# BID PROCESSING
# ============================================================


async def process_bid(
    update: Update,
    increment: float,
):
    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    user = get_user(user_id)

    if not user:
        await query.answer(
            "❌ Register first using /register",
            show_alert=True,
        )
        return

    auction_data = data["auction"]

    if not auction_data["active"]:
        await query.answer(
            "❌ Auction has ended.",
            show_alert=True,
        )
        return

    now = datetime.now()

    try:
        end_time = datetime.fromisoformat(
            auction_data["ends_at"]
        )
    except Exception:
        await query.answer(
            "❌ Auction timer error.",
            show_alert=True,
        )
        return

    if now >= end_time:
        await finish_auction(
            context=query.bot,
        )

        await query.answer(
            "⏱️ Auction time is over.",
            show_alert=True,
        )
        return

    current_bid = float(
        auction_data["highest_bid"]
    )

    # First bid
    if current_bid == 0:
        new_bid = MIN_START_BID
        actual_increment = MIN_START_BID

        # Button increments are applied after starting bid
        if increment > MIN_INCREMENT:
            new_bid = MIN_START_BID + increment
            actual_increment = increment

    else:
        new_bid = round(
            current_bid + increment,
            2,
        )

        actual_increment = increment

    # Increment validation
    if current_bid > 0:
        if actual_increment < MIN_INCREMENT:
            await query.answer(
                "❌ Minimum increment is 0.5 Cr.",
                show_alert=True,
            )
            return

        if actual_increment > MAX_INCREMENT:
            await query.answer(
                "❌ Maximum increment is 2 Cr.",
                show_alert=True,
            )
            return

    # Cannot bid against yourself
    if (
        auction_data["highest_bidder"] == user_id
    ):
        await query.answer(
            "⚠️ You are already the highest bidder.",
            show_alert=True,
        )
        return

    # Balance validation
    balance_left = remaining_balance(user_id)

    if new_bid > balance_left:
        await query.answer(
            f"❌ Insufficient balance.\n"
            f"Available: {format_cr(balance_left)}",
            show_alert=True,
        )
        return

    # Save bid
    auction_data["highest_bid"] = new_bid
    auction_data["highest_bidder"] = user_id

    auction_data["bid_history"].append(
        {
            "user_id": user_id,
            "name": user["name"],
            "amount": new_bid,
            "increment": actual_increment,
            "time": datetime.now().isoformat(),
        }
    )

    save_data(data)

    await query.message.edit_text(
        auction_status_text(),
        parse_mode="HTML",
        reply_markup=auction_keyboard(),
    )

    await query.answer(
        f"✅ Bid placed: {format_cr(new_bid)}"
    )


# ============================================================
# CALLBACK BUTTONS
# ============================================================


async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if query.data.startswith("bid_"):
        value = query.data.replace(
            "bid_",
            "",
        )

        try:
            increment = float(value)
        except ValueError:
            await query.answer(
                "Invalid bid.",
                show_alert=True,
            )
            return

        await process_bid(
            update,
            increment,
        )


# ============================================================
# ADMIN: ADD PLAYER
# ============================================================


async def add_player(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ Admin only."
        )
        return

    if len(data["players"]) >= MAX_PLAYERS:
        await update.message.reply_text(
            f"❌ Maximum {MAX_PLAYERS} players allowed."
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage:\n/addplayer Lionel Messi"
        )
        return

    name = " ".join(context.args).strip()

    if len(name) > 100:
        await update.message.reply_text(
            "❌ Player name is too long."
        )
        return

    player_id = next_player_id()

    data["players"][str(player_id)] = {
        "id": player_id,
        "name": name,
        "status": "pending",
        "sold_to": None,
        "sold_price": 0,
        "created_at": datetime.now().isoformat(),
    }

    save_data(data)

    await update.message.reply_text(
        f"✅ Player added.\n\n"
        f"⚽ <b>{name}</b>\n"
        f"🆔 ID: <code>{player_id}</code>",
        parse_mode="HTML",
    )


# ============================================================
# ADMIN: REMOVE PLAYER
# ============================================================


async def remove_player(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ Admin only."
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage:\n/removeplayer 1"
        )
        return

    try:
        player_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid player ID."
        )
        return

    player = get_player(player_id)

    if not player:
        await update.message.reply_text(
            "❌ Player not found."
        )
        return

    if player["status"] == "sold":
        await update.message.reply_text(
            "❌ Sold players cannot be removed."
        )
        return

    if (
        data["auction"]["active"]
        and data["auction"]["player_id"] == player_id
    ):
        await update.message.reply_text(
            "❌ Cannot remove the currently auctioned player."
        )
        return

    del data["players"][str(player_id)]

    save_data(data)

    await update.message.reply_text(
        "✅ Player removed."
    )


# ============================================================
# ADMIN: START AUCTION
# ============================================================


async def start_auction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ Admin only."
        )
        return

    if data["auction"]["active"]:
        await update.message.reply_text(
            "⚠️ Another auction is already active."
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage:\n/startauction PLAYER_ID"
        )
        return

    try:
        player_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid player ID."
        )
        return

    player = get_player(player_id)

    if not player:
        await update.message.reply_text(
            "❌ Player not found."
        )
        return

    if player["status"] != "pending":
        await update.message.reply_text(
            f"❌ Player status is already {player['status']}."
        )
        return

    # Check participants
    if len(data["participants"]) == 0:
        await update.message.reply_text(
            "❌ No participants registered."
        )
        return

    now = datetime.now()
    ends = now + timedelta(
        seconds=AUCTION_DURATION
    )

    data["auction"] = {
        "active": True,
        "player_id": player_id,
        "highest_bid": 0,
        "highest_bidder": None,
        "started_at": now.isoformat(),
        "ends_at": ends.isoformat(),
        "bid_history": [],
    }

    player["status"] = "auctioned"

    save_data(data)

    text = (
        "🔥 <b>AUCTION STARTED!</b>\n\n"
        f"⚽ <b>{player['name']}</b>\n"
        f"🆔 Player ID: <code>{player_id}</code>\n\n"
        f"💰 Starting Bid: <b>{format_cr(MIN_START_BID)}</b>\n"
        f"📈 Minimum Increment: <b>{format_cr(MIN_INCREMENT)}</b>\n"
        f"📈 Maximum Increment: <b>{format_cr(MAX_INCREMENT)}</b>\n"
        f"⏱️ Time: <b>{AUCTION_DURATION} seconds</b>\n\n"
        "👇 Place your bid:"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=auction_keyboard(),
    )

    # Schedule automatic completion
    context.application.create_task(
        auction_timer(
            context,
            player_id,
            ends,
        )
    )


# ============================================================
# AUCTION TIMER
# ============================================================


async def auction_timer(
    context,
    player_id,
    end_time,
):
    seconds = max(
        0,
        (
            end_time - datetime.now()
        ).total_seconds(),
    )

    await asyncio.sleep(seconds)

    auction_data = data["auction"]

    if not auction_data["active"]:
        return

    if auction_data["player_id"] != player_id:
        return

    await finish_auction(
        context.bot,
    )


# ============================================================
# FINISH AUCTION
# ============================================================


async def finish_auction(bot):
    auction_data = data["auction"]

    if not auction_data["active"]:
        return

    player_id = auction_data["player_id"]

    player = get_player(player_id)

    if not player:
        data["auction"]["active"] = False
        save_data(data)
        return

    highest_bidder_id = auction_data[
        "highest_bidder"
    ]

    highest_bid = float(
        auction_data["highest_bid"]
    )

    if (
        highest_bidder_id is not None
        and highest_bid >= MIN_START_BID
    ):
        user = get_user(highest_bidder_id)

        if user:
            user["spent"] = round(
                user["spent"] + highest_bid,
                2,
            )

            user["squad"].append(
                player_id
            )

            player["status"] = "sold"
            player["sold_to"] = highest_bidder_id
            player["sold_price"] = highest_bid

            result_text = (
                "🔨 <b>SOLD!</b>\n\n"
                f"⚽ <b>{player['name']}</b>\n"
                f"👑 Winner: <b>{user['name']}</b>\n"
                f"💰 Price: <b>{format_cr(highest_bid)}</b>\n"
                f"💳 Remaining Balance: "
                f"<b>{format_cr(remaining_balance(user['id']))}</b>"
            )
        else:
            player["status"] = "unsold"

            result_text = (
                "❌ <b>UNSOLD</b>\n\n"
                f"⚽ {player['name']}"
            )

    else:
        player["status"] = "unsold"
        player["sold_to"] = None
        player["sold_price"] = 0

        result_text = (
            "❌ <b>UNSOLD</b>\n\n"
            f"⚽ <b>{player['name']}</b>\n"
            "No valid bids were placed."
        )

    # Close auction
    data["auction"] = {
        "active": False,
        "player_id": None,
        "highest_bid": 0,
        "highest_bidder": None,
        "started_at": None,
        "ends_at": None,
        "bid_history": [],
    }

    save_data(data)

    # Send result to all registered users
    chat_ids = list(data["participants"].keys())

    for chat_id in chat_ids:
        try:
            await bot.send_message(
                chat_id=int(chat_id),
                text=result_text,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning(
                "Could not send result to %s: %s",
                chat_id,
                e,
            )


# ============================================================
# ADMIN: STOP
# ============================================================


async def stop_auction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ Admin only."
        )
        return

    if not data["auction"]["active"]:
        await update.message.reply_text(
            "❌ No active auction."
        )
        return

    await finish_auction(
        context.bot
    )

    await update.message.reply_text(
        "🛑 Auction stopped and processed."
    )


# ============================================================
# ADMIN: CANCEL
# ============================================================


async def cancel_auction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ Admin only."
        )
        return

    auction_data = data["auction"]

    if not auction_data["active"]:
        await update.message.reply_text(
            "❌ No active auction."
        )
        return

    player = get_player(
        auction_data["player_id"]
    )

    if player:
        player["status"] = "pending"

    data["auction"] = {
        "active": False,
        "player_id": None,
        "highest_bid": 0,
        "highest_bidder": None,
        "started_at": None,
        "ends_at": None,
        "bid_history": [],
    }

    save_data(data)

    await update.message.reply_text(
        "🛑 Auction cancelled.\n"
        "No balance has been deducted."
    )


# ============================================================
# ADMIN: SKIP
# ============================================================


async def skip_player(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ Admin only."
        )
        return

    if not data["auction"]["active"]:
        await update.message.reply_text(
            "❌ No active auction."
        )
        return

    player = get_player(
        data["auction"]["player_id"]
    )

    if player:
        player["status"] = "unsold"

    data["auction"] = {
        "active": False,
        "player_id": None,
        "highest_bid": 0,
        "highest_bidder": None,
        "started_at": None,
        "ends_at": None,
        "bid_history": [],
    }

    save_data(data)

    await update.message.reply_text(
        "⏭️ Player skipped and marked UNSOLD."
    )


# ============================================================
# PARTICIPANTS ADMIN
# ============================================================


async def participants(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ Admin only."
        )
        return

    users = data["participants"].values()

    if not users:
        await update.message.reply_text(
            "No participants."
        )
        return

    text = (
        f"👥 <b>Participants</b>\n"
        f"Total: <b>{len(data['participants'])}</b>\n\n"
    )

    for index, user in enumerate(users, 1):
        text += (
            f"{index}. <b>{user['name']}</b>\n"
            f"💰 {format_cr(remaining_balance(user['id']))}\n"
            f"⚽ {len(user['squad'])} players\n\n"
        )

        if index >= 100:
            break

    await update.message.reply_text(
        text,
        parse_mode="HTML",
    )


# ============================================================
# HISTORY
# ============================================================


async def history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    history_data = data["auction"]["bid_history"]

    if not history_data:
        await update.message.reply_text(
            "📜 No bids yet."
        )
        return

    text = "📜 <b>Bid History</b>\n\n"

    for index, bid in enumerate(
        reversed(history_data),
        1,
    ):
        text += (
            f"{index}. <b>{bid['name']}</b> — "
            f"{format_cr(bid['amount'])}\n"
        )

        if index >= 50:
            break

    await update.message.reply_text(
        text,
        parse_mode="HTML",
    )


# ============================================================
# ADMIN ANNOUNCEMENT
# ============================================================


async def announce(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ Admin only."
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage:\n/announce Your message"
        )
        return

    message = " ".join(context.args)

    text = (
        "📢 <b>AUCTION ANNOUNCEMENT</b>\n\n"
        f"{message}"
    )

    for chat_id in data["participants"].keys():
        try:
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=text,
                parse_mode="HTML",
            )
        except Exception:
            pass

    await update.message.reply_text(
        "✅ Announcement sent."
    )


# ============================================================
# ADMIN RESET
# ============================================================


async def reset(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "❌ Admin only."
        )
        return

    # Confirmation requirement
    if not context.args or context.args[0].lower() != "confirm":
        await update.message.reply_text(
            "⚠️ This will reset the entire auction database.\n\n"
            "To confirm:\n"
            "/reset confirm"
        )
        return

    global data

    data = json.loads(
        json.dumps(DEFAULT_DATA)
    )

    save_data(data)

    await update.message.reply_text(
        "♻️ <b>Complete auction reset.</b>\n\n"
        "Participants, players, squads and auction history "
        "have been reset.",
        parse_mode="HTML",
    )


# ============================================================
# ERROR HANDLER
# ============================================================


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.exception(
        "Exception while handling update:",
        exc_info=context.error,
    )


# ============================================================
# MAIN
# ============================================================


def main():
    if (
        not BOT_TOKEN
        or BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE"
    ):
        raise ValueError(
            "Please add your Telegram Bot Token "
            "to BOT_TOKEN."
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # General
    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(
        CommandHandler("register", register)
    )

    application.add_handler(
        CommandHandler("balance", balance)
    )

    application.add_handler(
        CommandHandler("squad", squad)
    )

    application.add_handler(
        CommandHandler("players", players)
    )

    application.add_handler(
        CommandHandler("leaderboard", leaderboard)
    )

    application.add_handler(
        CommandHandler("auction", auction)
    )

    application.add_handler(
        CommandHandler("history", history)
    )

    # Admin
    application.add_handler(
        CommandHandler("addplayer", add_player)
    )

    application.add_handler(
        CommandHandler("removeplayer", remove_player)
    )

    application.add_handler(
        CommandHandler("startauction", start_auction)
    )

    application.add_handler(
        CommandHandler("stop", stop_auction)
    )

    application.add_handler(
        CommandHandler("cancel", cancel_auction)
    )

    application.add_handler(
        CommandHandler("skip", skip_player)
    )

    application.add_handler(
        CommandHandler("participants", participants)
    )

    application.add_handler(
        CommandHandler("announce", announce)
    )

    application.add_handler(
        CommandHandler("reset", reset)
    )

    # Buttons
    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    application.add_error_handler(
        error_handler
    )

    print(
        "🔥 eFootball Auction Bot started..."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
