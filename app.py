import os
import asyncio
import threading
import logging
from decimal import Decimal, InvalidOperation
from http.server import HTTPServer, BaseHTTPRequestHandler

from fastapi import FastAPI
import uvicorn

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from db import db


# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# Put your Telegram numeric user ID here.
# Example:
# ADMIN_IDS = {123456789}
ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "8080"))

AUCTION_DURATION = 30

MIN_START_BID = Decimal("2.0")
MIN_INCREMENT = Decimal("0.5")
MAX_INCREMENT = Decimal("2.0")
STARTING_BALANCE = Decimal("100.0")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# FASTAPI KEEP-ALIVE SERVER
# ============================================================

api = FastAPI(title="eFootball Auction Bot")


@api.get("/")
async def root():
    return {
        "status": "online",
        "service": "eFootball Auction Bot"
    }


@api.get("/health")
async def health():
    return {
        "status": "healthy"
    }


class KeepAliveHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            b'{"status":"online","service":"eFootball Auction Bot"}'
        )

    def log_message(self, format, *args):
        return


def run_http_server():
    server = HTTPServer(
        (HOST, PORT),
        KeepAliveHandler
    )

    logger.info(
        "Keep-alive server running on port %s",
        PORT
    )

    server.serve_forever()


# ============================================================
# GLOBAL AUCTION LOCK
# ============================================================

auction_lock = asyncio.Lock()

auction_tasks = {}


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def format_cr(value) -> str:
    try:
        number = Decimal(str(value))
        return f"{number:.1f} Cr"
    except Exception:
        return f"{value} Cr"


def parse_amount(value: str):
    try:
        amount = Decimal(value)

        if amount <= 0:
            return None

        return amount.quantize(Decimal("0.1"))

    except (InvalidOperation, ValueError):
        return None


def get_user_name(user) -> str:
    if user.username:
        return f"@{user.username}"

    full_name = user.full_name

    if full_name:
        return full_name

    return str(user.id)


async def safe_edit(query, text, keyboard=None):

    try:
        if keyboard:
            await query.edit_message_text(
                text=text,
                reply_markup=keyboard
            )
        else:
            await query.edit_message_text(
                text=text
            )

    except Exception as e:
        logger.warning(
            "Could not edit Telegram message: %s",
            e
        )


# ============================================================
# AUCTION KEYBOARD
# ============================================================

def get_bid_keyboard(current_bid):

    if current_bid is None:

        keyboard = [
            [
                InlineKeyboardButton(
                    "💰 2 Cr",
                    callback_data="bid:2.0"
                )
            ]
        ]

    else:

        current = Decimal(str(current_bid))

        keyboard = [
            [
                InlineKeyboardButton(
                    f"+0.5 Cr → {current + Decimal('0.5'):.1f}",
                    callback_data="bid:0.5"
                ),
                InlineKeyboardButton(
                    f"+1 Cr → {current + Decimal('1.0'):.1f}",
                    callback_data="bid:1.0"
                )
            ],
            [
                InlineKeyboardButton(
                    f"+1.5 Cr → {current + Decimal('1.5'):.1f}",
                    callback_data="bid:1.5"
                ),
                InlineKeyboardButton(
                    f"+2 Cr → {current + Decimal('2.0'):.1f}",
                    callback_data="bid:2.0"
                )
            ]
        ]

    return InlineKeyboardMarkup(keyboard)


# ============================================================
# AUCTION MESSAGE
# ============================================================

def build_auction_text(auction, player):

    player_name = player.get("name", "Unknown Player")

    position = player.get("position", "N/A")

    rating = player.get("rating", "N/A")

    current_bid = auction.get("current_bid")

    highest_bidder = auction.get("highest_bidder_name")

    if current_bid is None:

        bid_text = (
            f"Starting Bid: "
            f"**{format_cr(MIN_START_BID)}**"
        )

        bidder_text = "No bids yet"

    else:

        bid_text = (
            f"Current Bid: "
            f"**{format_cr(current_bid)}**"
        )

        if highest_bidder:
            bidder_text = (
                f"Highest Bidder: "
                f"**{highest_bidder}**"
            )
        else:
            bidder_text = "No bids yet"

    text = (
        "🔥 **PLAYER AUCTION** 🔥\n\n"
        f"👤 Player: **{player_name}**\n"
        f"📍 Position: **{position}**\n"
        f"⭐ Rating: **{rating}**\n\n"
        f"💰 {bid_text}\n"
        f"👑 {bidder_text}\n\n"
        "⏱️ Auction Duration: **30 seconds**\n"
        "📈 Minimum Increment: **0.5 Cr**\n"
        "📈 Maximum Increment: **2 Cr**\n\n"
        "⚠️ Timer will NOT reset after a bid."
    )

    return text


# ============================================================
# REGISTER PARTICIPANT
# ============================================================

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    if not user:
        return

    try:

        existing = db.get_participant(user.id)

        if existing:

            balance = existing.get(
                "balance",
                STARTING_BALANCE
            )

            await update.message.reply_text(
                "✅ You are already registered!\n\n"
                f"👤 {get_user_name(user)}\n"
                f"💰 Balance: {format_cr(balance)}"
            )

            return

        participants = db.get_all_participants()

        if len(participants) >= 500:

            await update.message.reply_text(
                "❌ Participant limit reached.\n"
                "Maximum participants: 500"
            )

            return

        db.add_participant(
            user.id,
            user.username,
            user.full_name,
            float(STARTING_BALANCE)
        )

        await update.message.reply_text(
            "✅ **Registration Successful!**\n\n"
            f"👤 {get_user_name(user)}\n"
            f"💰 Starting Budget: {format_cr(STARTING_BALANCE)}\n\n"
            "You can now participate in the auction."
        )

    except Exception as e:

        logger.exception("Registration error")

        await update.message.reply_text(
            f"❌ Registration failed.\n\n{e}"
        )


# ============================================================
# BALANCE
# ============================================================

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    if not user:
        return

    participant = db.get_participant(user.id)

    if not participant:

        await update.message.reply_text(
            "❌ You are not registered.\n"
            "Use /register first."
        )

        return

    current_balance = participant.get(
        "balance",
        STARTING_BALANCE
    )

    spent = (
        Decimal(str(STARTING_BALANCE))
        - Decimal(str(current_balance))
    )

    await update.message.reply_text(
        "💰 **YOUR BALANCE**\n\n"
        f"Starting Budget: {format_cr(STARTING_BALANCE)}\n"
        f"Spent: {format_cr(spent)}\n"
        f"Remaining: {format_cr(current_balance)}"
    )


# ============================================================
# SQUAD
# ============================================================

async def squad(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    if not user:
        return

    participant = db.get_participant(user.id)

    if not participant:

        await update.message.reply_text(
            "❌ Please register first using /register."
        )

        return

    players = db.get_squad(user.id)

    if not players:

        await update.message.reply_text(
            "⚽ **YOUR SQUAD**\n\n"
            "No players purchased yet."
        )

        return

    lines = [
        "⚽ **YOUR SQUAD**\n"
    ]

    total_spent = Decimal("0")

    for index, player in enumerate(players, 1):

        price = Decimal(
            str(player.get("purchase_price", 0))
        )

        total_spent += price

        lines.append(
            f"{index}. **{player.get('name', 'Unknown')}**\n"
            f"   💰 {format_cr(price)}"
        )

    lines.append(
        f"\n💵 Total Spent: {format_cr(total_spent)}"
    )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# LEADERBOARD
# ============================================================

async def leaderboard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    leaders = db.get_leaderboard()

    if not leaders:

        await update.message.reply_text(
            "📊 No participants registered yet."
        )

        return

    lines = [
        "🏆 **LEADERBOARD**\n"
    ]

    for index, player in enumerate(leaders, 1):

        name = (
            player.get("username")
            or player.get("full_name")
            or str(player.get("user_id"))
        )

        balance_value = player.get("balance", 0)

        lines.append(
            f"{index}. {name} — "
            f"{format_cr(balance_value)}"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# PLAYERS LIST
# ============================================================

async def players(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    player_list = db.get_all_players()

    if not player_list:

        await update.message.reply_text(
            "📋 No players available."
        )

        return

    lines = [
        "📋 **PLAYER LIST**\n"
    ]

    for player in player_list[:100]:

        status = player.get(
            "status",
            "available"
        )

        lines.append(
            f"• {player.get('name', 'Unknown')} "
            f"— {player.get('position', 'N/A')} "
            f"— {status}"
        )

    if len(player_list) > 100:

        lines.append(
            f"\nShowing first 100 of "
            f"{len(player_list)} players."
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# ADD PLAYER
# ============================================================

async def addplayer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not is_admin(user.id):

        await update.message.reply_text(
            "❌ Admin only."
        )

        return

    if len(context.args) < 3:

        await update.message.reply_text(
            "Usage:\n"
            "/addplayer Player Name Position Rating\n\n"
            "Example:\n"
            "/addplayer Lionel_Messi RWF 105"
        )

        return

    name = context.args[0]
    position = context.args[1]

    try:
        rating = int(context.args[2])
    except ValueError:

        await update.message.reply_text(
            "❌ Rating must be a number."
        )

        return

    try:

        all_players = db.get_all_players()

        if len(all_players) >= 500:

            await update.message.reply_text(
                "❌ Maximum 500 players allowed."
            )

            return

        db.add_player(
            name=name.replace("_", " "),
            position=position,
            rating=rating
        )

        await update.message.reply_text(
            "✅ Player added successfully!\n\n"
            f"👤 {name.replace('_', ' ')}\n"
            f"📍 {position}\n"
            f"⭐ {rating}"
        )

    except Exception as e:

        logger.exception("Add player error")

        await update.message.reply_text(
            f"❌ Failed to add player.\n\n{e}"
        )


# ============================================================
# START AUCTION
# ============================================================

async def startauction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not is_admin(user.id):

        await update.message.reply_text(
            "❌ Admin only."
        )

        return

    async with auction_lock:

        try:

            active = db.get_active_auction()

            if active:

                await update.message.reply_text(
                    "⚠️ An auction is already running."
                )

                return

            if not context.args:

                await update.message.reply_text(
                    "Usage:\n"
                    "/startauction PLAYER_ID"
                )

                return

            try:
                player_id = int(context.args[0])
            except ValueError:

                await update.message.reply_text(
                    "❌ Invalid player ID."
                )

                return

            player = db.get_player(player_id)

            if not player:

                await update.message.reply_text(
                    "❌ Player not found."
                )

                return

            auction = db.start_auction(
                player_id=player_id,
                duration=AUCTION_DURATION,
                min_bid=float(MIN_START_BID)
            )

            message = await update.message.reply_text(
                build_auction_text(
                    auction,
                    player
                ),
                parse_mode="Markdown",
                reply_markup=get_bid_keyboard(None)
            )

            auction_message_id = message.message_id

            auction_id = auction.get("id")

            chat_id = update.effective_chat.id

            task = asyncio.create_task(
                auction_timer(
                    chat_id,
                    auction_message_id,
                    auction_id,
                    player_id,
                    context.application
                )
            )

            auction_tasks[auction_id] = task

        except Exception as e:

            logger.exception(
                "Start auction error"
            )

            await update.message.reply_text(
                f"❌ Could not start auction.\n\n{e}"
            )


# ============================================================
# AUCTION TIMER
# ============================================================

async def auction_timer(
    chat_id,
    message_id,
    auction_id,
    player_id,
    application
):

    try:

        await asyncio.sleep(AUCTION_DURATION)

        async with auction_lock:

            auction = db.get_active_auction()

            if not auction:
                return

            if auction.get("id") != auction_id:
                return

            if auction.get("player_id") != player_id:
                return

            result = db.finish_auction(
                auction_id
            )

            player = db.get_player(
                player_id
            )

            if not result:
                return

            status = result.get(
                "status",
                "UNSOLD"
            )

            if status == "SOLD":

                winner_name = result.get(
                    "winner_name",
                    "Unknown"
                )

                winning_bid = result.get(
                    "winning_bid",
                    0
                )

                final_text = (
                    "🔨 **AUCTION CLOSED**\n\n"
                    f"👤 Player: **{player.get('name')}**\n\n"
                    "🟢 **SOLD**\n\n"
                    f"👑 Winner: **{winner_name}**\n"
                    f"💰 Final Price: "
                    f"**{format_cr(winning_bid)}**"
                )

            else:

                final_text = (
                    "🔨 **AUCTION CLOSED**\n\n"
                    f"👤 Player: **{player.get('name')}**\n\n"
                    "🔴 **UNSOLD**\n\n"
                    "No valid bids were received."
                )

            try:

                await application.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=final_text,
                    parse_mode="Markdown"
                )

            except Exception as e:

                logger.warning(
                    "Could not edit closed auction: %s",
                    e
                )

            # Send result separately
            try:

                await application.bot.send_message(
                    chat_id=chat_id,
                    text=final_text,
                    parse_mode="Markdown"
                )

            except Exception as e:

                logger.warning(
                    "Could not send auction result: %s",
                    e
                )

    except asyncio.CancelledError:

        logger.info(
            "Auction timer cancelled: %s",
            auction_id
        )

    except Exception:

        logger.exception(
            "Auction timer error"
        )

    finally:

        auction_tasks.pop(
            auction_id,
            None
        )


# ============================================================
# PLACE BID
# ============================================================

async def bid_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user = query.from_user

    if not query.data.startswith("bid:"):
        return

    try:

        increment = Decimal(
            query.data.split(":")[1]
        )

    except Exception:

        await query.answer(
            "Invalid bid.",
            show_alert=True
        )

        return

    if increment not in (
        Decimal("0.5"),
        Decimal("1.0"),
        Decimal("1.5"),
        Decimal("2.0")
    ):

        await query.answer(
            "Invalid increment.",
            show_alert=True
        )

        return

    async with auction_lock:

        try:

            participant = db.get_participant(
                user.id
            )

            if not participant:

                await query.answer(
                    "Please use /register first.",
                    show_alert=True
                )

                return

            auction = db.get_active_auction()

            if not auction:

                await query.answer(
                    "Auction has already ended.",
                    show_alert=True
                )

                return

            player_id = auction.get(
                "player_id"
            )

            player = db.get_player(
                player_id
            )

            current_bid = auction.get(
                "current_bid"
            )

            # ------------------------------------------------
            # FIRST BID
            # ------------------------------------------------

            if current_bid is None:

                if increment != Decimal("2.0"):

                    await query.answer(
                        "First bid must be 2 Cr.",
                        show_alert=True
                    )

                    return

                bid_amount = Decimal("2.0")

            # ------------------------------------------------
            # NEXT BID
            # ------------------------------------------------

            else:

                current = Decimal(
                    str(current_bid)
                )

                bid_amount = (
                    current + increment
                )

            # ------------------------------------------------
            # VALIDATE INCREMENT
            # ------------------------------------------------

            if current_bid is not None:

                difference = (
                    bid_amount
                    - Decimal(str(current_bid))
                )

                if difference < MIN_INCREMENT:

                    await query.answer(
                        "Minimum increment is 0.5 Cr.",
                        show_alert=True
                    )

                    return

                if difference > MAX_INCREMENT:

                    await query.answer(
                        "Maximum increment is 2 Cr.",
                        show_alert=True
                    )

                    return

            # ------------------------------------------------
            # CHECK SELF OUTBID
            # ------------------------------------------------

            highest_bidder_id = auction.get(
                "highest_bidder_id"
            )

            if highest_bidder_id == user.id:

                await query.answer(
                    "You are already the highest bidder.",
                    show_alert=True
                )

                return

            # ------------------------------------------------
            # CHECK BALANCE
            # ------------------------------------------------

            balance_value = Decimal(
                str(
                    participant.get(
                        "balance",
                        STARTING_BALANCE
                    )
                )
            )

            if bid_amount > balance_value:

                await query.answer(
                    f"Insufficient balance.\n"
                    f"You have {format_cr(balance_value)}",
                    show_alert=True
                )

                return

            # ------------------------------------------------
            # PLACE BID IN DATABASE
            # ------------------------------------------------

            result = db.place_bid(
                auction_id=auction.get("id"),
                user_id=user.id,
                amount=float(bid_amount),
                username=user.username,
                full_name=user.full_name
            )

            if not result:

                await query.answer(
                    "Bid could not be placed.",
                    show_alert=True
                )

                return

            # ------------------------------------------------
            # REFRESH AUCTION
            # ------------------------------------------------

            updated_auction = db.get_active_auction()

            if not updated_auction:

                await query.answer(
                    "Auction already ended.",
                    show_alert=True
                )

                return

            # ------------------------------------------------
            # UPDATE MESSAGE
            # ------------------------------------------------

            await safe_edit(
                query,
                build_auction_text(
                    updated_auction,
                    player
                ),
                get_bid_keyboard(
                    updated_auction.get(
                        "current_bid"
                    )
                )
            )

            await query.answer(
                f"Bid placed: {format_cr(bid_amount)}",
                show_alert=False
            )

        except Exception as e:

            logger.exception(
                "Bid error"
            )

            await query.answer(
                "An error occurred while placing bid.",
                show_alert=True
            )


# ============================================================
# BID HISTORY
# ============================================================

async def history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    if not context.args:

        await update.message.reply_text(
            "Usage:\n"
            "/history AUCTION_ID"
        )

        return

    try:

        auction_id = int(
            context.args[0]
        )

    except ValueError:

        await update.message.reply_text(
            "❌ Invalid auction ID."
        )

        return

    bids = db.get_bid_history(
        auction_id
    )

    if not bids:

        await update.message.reply_text(
            "📜 No bids found."
        )

        return

    lines = [
        "📜 **BID HISTORY**\n"
    ]

    for index, bid in enumerate(
        bids,
        1
    ):

        bidder = (
            bid.get("username")
            or bid.get("full_name")
            or str(bid.get("user_id"))
        )

        amount = bid.get(
            "amount",
            0
        )

        lines.append(
            f"{index}. {bidder} — "
            f"{format_cr(amount)}"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# CURRENT AUCTION
# ============================================================

async def current_auction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    auction = db.get_active_auction()

    if not auction:

        await update.message.reply_text(
            "ℹ️ No auction is currently running."
        )

        return

    player = db.get_player(
        auction.get("player_id")
    )

    if not player:

        await update.message.reply_text(
            "❌ Player information unavailable."
        )

        return

    await update.message.reply_text(
        build_auction_text(
            auction,
            player
        ),
        parse_mode="Markdown",
        reply_markup=get_bid_keyboard(
            auction.get("current_bid")
        )
    )


# ============================================================
# SKIP AUCTION
# ============================================================

async def skipauction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not is_admin(user.id):

        await update.message.reply_text(
            "❌ Admin only."
        )

        return

    async with auction_lock:

        auction = db.get_active_auction()

        if not auction:

            await update.message.reply_text(
                "ℹ️ No active auction."
            )

            return

        auction_id = auction.get(
            "id"
        )

        task = auction_tasks.get(
            auction_id
        )

        if task:

            task.cancel()

        result = db.cancel_auction(
            auction_id
        )

        await update.message.reply_text(
            "⏭️ Current auction skipped."
        )


# ============================================================
# STOP AUCTION
# ============================================================

async def stopauction(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not is_admin(user.id):

        await update.message.reply_text(
            "❌ Admin only."
        )

        return

    async with auction_lock:

        auction = db.get_active_auction()

        if not auction:

            await update.message.reply_text(
                "ℹ️ No active auction."
            )

            return

        auction_id = auction.get(
            "id"
        )

        task = auction_tasks.get(
            auction_id
        )

        if task:

            task.cancel()

        db.cancel_auction(
            auction_id
        )

        await update.message.reply_text(
            "🛑 Auction stopped."
        )


# ============================================================
# PARTICIPANTS
# ============================================================

async def participants(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not is_admin(user.id):

        await update.message.reply_text(
            "❌ Admin only."
        )

        return

    people = db.get_all_participants()

    if not people:

        await update.message.reply_text(
            "No participants registered."
        )

        return

    lines = [
        f"👥 **PARTICIPANTS: {len(people)}**\n"
    ]

    for index, person in enumerate(
        people,
        1
    ):

        name = (
            person.get("username")
            or person.get("full_name")
            or str(person.get("user_id"))
        )

        balance_value = person.get(
            "balance",
            0
        )

        lines.append(
            f"{index}. {name} — "
            f"{format_cr(balance_value)}"
        )

    await update.message.reply_text(
        "\n".join(lines[:101])
    )


# ============================================================
# STATS
# ============================================================

async def stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not is_admin(user.id):

        await update.message.reply_text(
            "❌ Admin only."
        )

        return

    try:

        data = db.get_statistics()

        await update.message.reply_text(
            "📊 **AUCTION STATISTICS**\n\n"
            f"👥 Participants: "
            f"{data.get('participants', 0)}\n"
            f"⚽ Players: "
            f"{data.get('players', 0)}\n"
            f"🔨 Auctions: "
            f"{data.get('auctions', 0)}\n"
            f"💰 Total Bids: "
            f"{data.get('bids', 0)}\n"
            f"🟢 Sold: "
            f"{data.get('sold', 0)}\n"
            f"🔴 Unsold: "
            f"{data.get('unsold', 0)}"
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Could not load statistics.\n\n{e}"
        )


# ============================================================
# RESET DATABASE
# ============================================================

async def reset(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not is_admin(user.id):

        await update.message.reply_text(
            "❌ Admin only."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "⚠️ This will reset the auction database.\n\n"
            "Use:\n"
            "/reset CONFIRM"
        )

        return

    if context.args[0].upper() != "CONFIRM":

        await update.message.reply_text(
            "❌ Reset cancelled."
        )

        return

    async with auction_lock:

        for task in list(
            auction_tasks.values()
        ):

            task.cancel()

        auction_tasks.clear()

        db.reset_database()

        await update.message.reply_text(
            "♻️ Database reset successfully."
        )


# ============================================================
# HELP
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        "🤖 **eFootball Auction Bot**\n\n"

        "👤 **Participant Commands**\n"
        "/register - Register for auction\n"
        "/balance - Check balance\n"
        "/squad - View squad\n"
        "/leaderboard - Leaderboard\n"
        "/auction - Current auction\n"
        "/players - Player list\n"
        "/history ID - Bid history\n\n"

        "👑 **Admin Commands**\n"
        "/addplayer NAME POSITION RATING\n"
        "/startauction PLAYER_ID\n"
        "/skipauction\n"
        "/stopauction\n"
        "/participants\n"
        "/stats\n"
        "/reset CONFIRM\n\n"

        "⚙️ **Auction Rules**\n"
        "• Maximum Players: 500\n"
        "• Maximum Participants: 500\n"
        "• Starting Budget: 100 Cr\n"
        "• Starting Bid: 2 Cr\n"
        "• Minimum Increment: 0.5 Cr\n"
        "• Maximum Increment: 2 Cr\n"
        "• Auction Duration: 30 seconds\n"
        "• Timer never resets after bidding"
    )

    await update.message.reply_text(
        text,
        parse_mode="Markdown"
    )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.exception(
        "Telegram bot error",
        exc_info=context.error
    )


# ============================================================
# TELEGRAM BOT
# ============================================================

def create_bot():

    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":

        raise RuntimeError(
            "BOT_TOKEN is not configured."
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # -----------------------------------------
    # PARTICIPANT COMMANDS
    # -----------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            help_command
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_command
        )
    )

    application.add_handler(
        CommandHandler(
            "register",
            register
        )
    )

    application.add_handler(
        CommandHandler(
            "balance",
            balance
        )
    )

    application.add_handler(
        CommandHandler(
            "squad",
            squad
        )
    )

    application.add_handler(
        CommandHandler(
            "leaderboard",
            leaderboard
        )
    )

    application.add_handler(
        CommandHandler(
            "players",
            players
        )
    )

    application.add_handler(
        CommandHandler(
            "history",
            history
        )
    )

    application.add_handler(
        CommandHandler(
            "auction",
            current_auction
        )
    )

    # -----------------------------------------
    # ADMIN COMMANDS
    # -----------------------------------------

    application.add_handler(
        CommandHandler(
            "addplayer",
            addplayer
        )
    )

    application.add_handler(
        CommandHandler(
            "startauction",
            startauction
        )
    )

    application.add_handler(
        CommandHandler(
            "skipauction",
            skipauction
        )
    )

    application.add_handler(
        CommandHandler(
            "stopauction",
            stopauction
        )
    )

    application.add_handler(
        CommandHandler(
            "participants",
            participants
        )
    )

    application.add_handler(
        CommandHandler(
            "stats",
            stats
        )
    )

    application.add_handler(
        CommandHandler(
            "reset",
            reset
        )
    )

    # -----------------------------------------
    # BID BUTTONS
    # -----------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            bid_callback,
            pattern=r"^bid:"
        )
    )

    application.add_error_handler(
        error_handler
    )

    return application


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info(
        "Starting eFootball Auction Bot..."
    )

    # Start HTTP server in background thread
    http_thread = threading.Thread(
        target=run_http_server,
        daemon=True
    )

    http_thread.start()

    # Create Telegram bot
    application = create_bot()

    logger.info(
        "Telegram bot started."
    )

    # Start polling
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
