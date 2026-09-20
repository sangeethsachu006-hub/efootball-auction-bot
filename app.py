import os
import asyncio
import threading
import logging
from decimal import Decimal, InvalidOperation
from http.server import HTTPServer, BaseHTTPRequestHandler

from fastapi import FastAPI
import uvicorn

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from db import db


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_IDS = set()

for value in os.getenv("ADMIN_IDS", "").split(","):
    value = value.strip()

    if value.isdigit():
        ADMIN_IDS.add(int(value))


PORT = int(os.getenv("PORT", "8080"))

HOST = "0.0.0.0"

AUCTION_DURATION = 30

MAX_PLAYERS = 500
MAX_PARTICIPANTS = 500

STARTING_BALANCE = Decimal("100.0")

MIN_START_BID = Decimal("2.0")

MIN_INCREMENT = Decimal("0.5")

MAX_INCREMENT = Decimal("2.0")


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="eFootball Auction Bot",
    version="1.0.0"
)


@app.get("/")
async def home():

    return {
        "status": "online",
        "service": "eFootball Auction Bot"
    }


@app.get("/health")
async def health():

    return {
        "status": "healthy"
    }


# ============================================================
# HTTP SERVER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "application/json"
        )

        self.end_headers()

        self.wfile.write(
            b'{"status":"online"}'
        )

    def log_message(self, format, *args):
        return


def run_http_server():

    server = HTTPServer(
        (HOST, PORT),
        HealthHandler
    )

    logger.info(
        "HTTP server running on port %s",
        PORT
    )

    server.serve_forever()


# ============================================================
# AUCTION STATE
# ============================================================

auction_lock = asyncio.Lock()

auction_tasks = {}


# ============================================================
# HELPERS
# ============================================================

def is_admin(user_id):

    return user_id in ADMIN_IDS


def money(value):

    try:

        amount = Decimal(
            str(value)
        )

        return f"{amount:.1f} Cr"

    except Exception:

        return f"{value} Cr"


def user_display_name(user):

    if user.username:

        return f"@{user.username}"

    if user.full_name:

        return user.full_name

    return str(user.id)


def get_player_value(player, key, default=None):

    if not player:

        return default

    try:

        return player[key]

    except Exception:

        return default


# ============================================================
# BID BUTTONS
# ============================================================

def bid_keyboard(current_bid):

    # First bid
    if current_bid is None:

        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "💰 BID 2 Cr",
                    callback_data="bid:2.0"
                )
            ]
        ])

    current = Decimal(
        str(current_bid)
    )

    return InlineKeyboardMarkup([
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
    ])


# ============================================================
# AUCTION MESSAGE
# ============================================================

def auction_message(auction, player):

    player_name = get_player_value(
        player,
        "name",
        "Unknown Player"
    )

    position = get_player_value(
        player,
        "position",
        "N/A"
    )

    rating = get_player_value(
        player,
        "rating",
        "N/A"
    )

    current_bid = get_player_value(
        auction,
        "current_bid"
    )

    highest_bidder = get_player_value(
        auction,
        "highest_bidder_name"
    )

    if current_bid is None:

        bid_text = "Starting Bid: **2.0 Cr**"

        bidder_text = "No bids yet"

    else:

        bid_text = (
            f"Current Bid: **{money(current_bid)}**"
        )

        if highest_bidder:

            bidder_text = (
                f"Highest Bidder: **{highest_bidder}**"
            )

        else:

            bidder_text = "No bids yet"

    return (
        "🔥 **eFOOTBALL PLAYER AUCTION** 🔥\n\n"

        f"👤 Player: **{player_name}**\n"
        f"📍 Position: **{position}**\n"
        f"⭐ Rating: **{rating}**\n\n"

        f"💰 {bid_text}\n"
        f"👑 {bidder_text}\n\n"

        "⏱️ **30 seconds only**\n"
        "📈 Minimum increment: **0.5 Cr**\n"
        "📈 Maximum increment: **2 Cr**\n\n"

        "⚠️ The timer does NOT reset when someone bids."
    )


# ============================================================
# /START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🤖 **eFootball Auction Bot**\n\n"

        "Welcome!\n\n"

        "Use /register to join the auction.\n"
        "Use /balance to check your budget.\n"
        "Use /squad to see your players.\n"
        "Use /auction to see the current auction.\n"
        "Use /leaderboard to see rankings.\n\n"

        "Use /help for all commands.",
        parse_mode="Markdown"
    )


# ============================================================
# /HELP
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🤖 **eFOOTBALL AUCTION BOT**\n\n"

        "👥 **Participant Commands**\n\n"
        "/register - Register\n"
        "/balance - Check balance\n"
        "/squad - View squad\n"
        "/players - Player list\n"
        "/auction - Current auction\n"
        "/leaderboard - Leaderboard\n"
        "/history ID - Bid history\n\n"

        "👑 **Admin Commands**\n\n"
        "/addplayer Name Position Rating\n"
        "/startauction PLAYER_ID\n"
        "/skipauction\n"
        "/stopauction\n"
        "/participants\n"
        "/stats\n"
        "/reset CONFIRM\n\n"

        "⚙️ **RULES**\n\n"
        "👥 Maximum participants: 500\n"
        "⚽ Maximum players: 500\n"
        "💰 Budget: 100 Cr\n"
        "💰 Starting bid: 2 Cr\n"
        "📈 Minimum increment: 0.5 Cr\n"
        "📈 Maximum increment: 2 Cr\n"
        "⏱️ Auction duration: 30 seconds\n"
        "🔄 Timer never resets",
        parse_mode="Markdown"
    )


# ============================================================
# /REGISTER
# ============================================================

async def register(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    try:

        existing = db.get_participant(
            user.id
        )

        if existing:

            balance_value = existing.get(
                "balance",
                100
            )

            await update.message.reply_text(
                "✅ **Already Registered**\n\n"
                f"👤 {user_display_name(user)}\n"
                f"💰 Balance: {money(balance_value)}",
                parse_mode="Markdown"
            )

            return

        participants = db.get_all_participants()

        if len(participants) >= MAX_PARTICIPANTS:

            await update.message.reply_text(
                "❌ Participant limit reached.\n"
                "Maximum: 500"
            )

            return

        db.add_participant(
            user.id,
            user.username,
            user.full_name,
            float(STARTING_BALANCE)
        )

        await update.message.reply_text(
            "🎉 **REGISTRATION SUCCESSFUL**\n\n"
            f"👤 {user_display_name(user)}\n"
            "💰 Budget: **100 Cr**\n\n"
            "You can now participate in auctions.",
            parse_mode="Markdown"
        )

    except Exception as e:

        logger.exception(
            "Registration error"
        )

        await update.message.reply_text(
            f"❌ Registration failed.\n\n{e}"
        )


# ============================================================
# /BALANCE
# ============================================================

async def balance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    participant = db.get_participant(
        user.id
    )

    if not participant:

        await update.message.reply_text(
            "❌ You are not registered.\n\n"
            "Use /register first."
        )

        return

    balance_value = participant.get(
        "balance",
        100
    )

    spent = (
        Decimal("100")
        - Decimal(str(balance_value))
    )

    await update.message.reply_text(
        "💰 **YOUR WALLET**\n\n"
        f"Starting Budget: **100 Cr**\n"
        f"Spent: **{money(spent)}**\n"
        f"Remaining: **{money(balance_value)}**",
        parse_mode="Markdown"
    )


# ============================================================
# /SQUAD
# ============================================================

async def squad(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    participant = db.get_participant(
        user.id
    )

    if not participant:

        await update.message.reply_text(
            "❌ Please use /register first."
        )

        return

    players = db.get_squad(
        user.id
    )

    if not players:

        await update.message.reply_text(
            "⚽ **YOUR SQUAD**\n\n"
            "No players purchased yet.",
            parse_mode="Markdown"
        )

        return

    lines = [
        "⚽ **YOUR SQUAD**\n"
    ]

    total = Decimal("0")

    for index, player in enumerate(
        players,
        1
    ):

        price = Decimal(
            str(
                player.get(
                    "purchase_price",
                    0
                )
            )
        )

        total += price

        lines.append(
            f"{index}. **{player.get('name', 'Unknown')}** "
            f"— {money(price)}"
        )

    lines.append(
        f"\n💰 Total Spent: **{money(total)}**"
    )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown"
    )


# ============================================================
# /LEADERBOARD
# ============================================================

async def leaderboard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:

        leaders = db.get_leaderboard()

        if not leaders:

            await update.message.reply_text(
                "📊 No participants yet."
            )

            return

        lines = [
            "🏆 **LEADERBOARD**\n"
        ]

        for index, person in enumerate(
            leaders,
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
                f"💰 {money(balance_value)}"
            )

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown"
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Error: {e}"
        )


# ============================================================
# /PLAYERS
# ============================================================

async def players(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:

        all_players = db.get_all_players()

        if not all_players:

            await update.message.reply_text(
                "⚽ No players added yet."
            )

            return

        lines = [
            f"⚽ **PLAYERS ({len(all_players)})**\n"
        ]

        for player in all_players[:100]:

            player_id = player.get(
                "id",
                "?"
            )

            name = player.get(
                "name",
                "Unknown"
            )

            position = player.get(
                "position",
                "N/A"
            )

            rating = player.get(
                "rating",
                "N/A"
            )

            status = player.get(
                "status",
                "available"
            )

            lines.append(
                f"#{player_id} — "
                f"**{name}** | "
                f"{position} | "
                f"{rating} | "
                f"{status}"
            )

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown"
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Error: {e}"
        )


# ============================================================
# /ADPLAYER
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
            "Usage:\n\n"
            "/addplayer Player_Name Position Rating\n\n"
            "Example:\n"
            "/addplayer Messi RWF 105"
        )

        return

    name = context.args[0].replace(
        "_",
        " "
    )

    position = context.args[1]

    try:

        rating = int(
            context.args[2]
        )

    except ValueError:

        await update.message.reply_text(
            "❌ Rating must be a number."
        )

        return

    try:

        all_players = db.get_all_players()

        if len(all_players) >= MAX_PLAYERS:

            await update.message.reply_text(
                "❌ Maximum 500 players reached."
            )

            return

        db.add_player(
            name=name,
            position=position,
            rating=rating
        )

        await update.message.reply_text(
            "✅ **PLAYER ADDED**\n\n"
            f"👤 {name}\n"
            f"📍 Position: {position}\n"
            f"⭐ Rating: {rating}",
            parse_mode="Markdown"
        )

    except Exception as e:

        logger.exception(
            "Add player error"
        )

        await update.message.reply_text(
            f"❌ Failed to add player.\n\n{e}"
        )


# ============================================================
# /STARTAUCTION
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

    if not context.args:

        await update.message.reply_text(
            "Usage:\n"
            "/startauction PLAYER_ID"
        )

        return

    try:

        player_id = int(
            context.args[0]
        )

    except ValueError:

        await update.message.reply_text(
            "❌ Invalid player ID."
        )

        return

    async with auction_lock:

        active = db.get_active_auction()

        if active:

            await update.message.reply_text(
                "⚠️ An auction is already running."
            )

            return

        player = db.get_player(
            player_id
        )

        if not player:

            await update.message.reply_text(
                "❌ Player not found."
            )

            return

        try:

            auction = db.start_auction(
                player_id=player_id,
                duration=30,
                min_bid=2.0
            )

        except Exception as e:

            await update.message.reply_text(
                f"❌ Could not start auction.\n\n{e}"
            )

            return

        message = await update.message.reply_text(
            auction_message(
                auction,
                player
            ),
            parse_mode="Markdown",
            reply_markup=bid_keyboard(
                None
            )
        )

        auction_id = auction.get(
            "id"
        )

        task = asyncio.create_task(
            auction_timer(
                update.effective_chat.id,
                message.message_id,
                auction_id,
                player_id,
                context.application
            )
        )

        auction_tasks[
            auction_id
        ] = task


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

        # EXACT 30 SECOND WAIT
        await asyncio.sleep(
            AUCTION_DURATION
        )

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

                winner = result.get(
                    "winner_name",
                    "Unknown"
                )

                winning_bid = result.get(
                    "winning_bid",
                    0
                )

                final_text = (
                    "🔨 **AUCTION CLOSED**\n\n"
                    f"⚽ Player: **{player.get('name')}**\n\n"
                    "🟢 **SOLD**\n\n"
                    f"👑 Winner: **{winner}**\n"
                    f"💰 Price: **{money(winning_bid)}**"
                )

            else:

                final_text = (
                    "🔨 **AUCTION CLOSED**\n\n"
                    f"⚽ Player: **{player.get('name')}**\n\n"
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
                    "Message edit failed: %s",
                    e
                )

            try:

                await application.bot.send_message(
                    chat_id=chat_id,
                    text=final_text,
                    parse_mode="Markdown"
                )

            except Exception as e:

                logger.warning(
                    "Result message failed: %s",
                    e
                )

    except asyncio.CancelledError:

        logger.info(
            "Auction timer cancelled."
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
# BID CALLBACK
# ============================================================

async def bid_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user = query.from_user

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

    valid_increments = {
        Decimal("0.5"),
        Decimal("1.0"),
        Decimal("1.5"),
        Decimal("2.0")
    }

    if increment not in valid_increments:

        await query.answer(
            "Invalid increment.",
            show_alert=True
        )

        return

    async with auction_lock:

        participant = db.get_participant(
            user.id
        )

        if not participant:

            await query.answer(
                "Register first using /register.",
                show_alert=True
            )

            return

        auction = db.get_active_auction()

        if not auction:

            await query.answer(
                "Auction has ended.",
                show_alert=True
            )

            return

        player = db.get_player(
            auction.get("player_id")
        )

        current_bid = auction.get(
            "current_bid"
        )

        highest_bidder_id = auction.get(
            "highest_bidder_id"
        )

        # Cannot bid against yourself
        if highest_bidder_id == user.id:

            await query.answer(
                "You are already the highest bidder.",
                show_alert=True
            )

            return

        # ----------------------------------------------------
        # FIRST BID
        # ----------------------------------------------------

        if current_bid is None:

            if increment != Decimal("2.0"):

                await query.answer(
                    "First bid must be exactly 2 Cr.",
                    show_alert=True
                )

                return

            new_bid = Decimal("2.0")

        # ----------------------------------------------------
        # NEXT BID
        # ----------------------------------------------------

        else:

            current = Decimal(
                str(current_bid)
            )

            new_bid = (
                current + increment
            )

            difference = (
                new_bid - current
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

        # ----------------------------------------------------
        # CHECK BALANCE
        # ----------------------------------------------------

        balance_value = Decimal(
            str(
                participant.get(
                    "balance",
                    100
                )
            )
        )

        if new_bid > balance_value:

            await query.answer(
                f"Insufficient balance.\n"
                f"You have {money(balance_value)}.",
                show_alert=True
            )

            return

        # ----------------------------------------------------
        # PLACE BID
        # ----------------------------------------------------

        try:

            result = db.place_bid(
                auction_id=auction.get("id"),
                user_id=user.id,
                amount=float(new_bid),
                username=user.username,
                full_name=user.full_name
            )

        except Exception as e:

            logger.exception(
                "Place bid error"
            )

            await query.answer(
                "Could not place bid.",
                show_alert=True
            )

            return

        if not result:

            await query.answer(
                "Bid rejected.",
                show_alert=True
            )

            return

        # ----------------------------------------------------
        # REFRESH AUCTION
        # ----------------------------------------------------

        updated = db.get_active_auction()

        if not updated:

            await query.answer(
                "Auction already closed.",
                show_alert=True
            )

            return

        # ----------------------------------------------------
        # UPDATE MESSAGE
        # ----------------------------------------------------

        try:

            await query.edit_message_text(
                text=auction_message(
                    updated,
                    player
                ),
                parse_mode="Markdown",
                reply_markup=bid_keyboard(
                    updated.get("current_bid")
                )
            )

        except Exception as e:

            logger.warning(
                "Could not update auction message: %s",
                e
            )

        await query.answer(
            f"Bid placed: {money(new_bid)}"
        )


# ============================================================
# /AUCTION
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
            "❌ Player not found."
        )

        return

    await update.message.reply_text(
        auction_message(
            auction,
            player
        ),
        parse_mode="Markdown",
        reply_markup=bid_keyboard(
            auction.get("current_bid")
        )
    )


# ============================================================
# /HISTORY
# ============================================================

async def history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

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
            f"{money(amount)}"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown"
    )


# ============================================================
# /PARTICIPANTS
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
            f"{money(balance_value)}"
        )

    await update.message.reply_text(
        "\n".join(lines[:101]),
        parse_mode="Markdown"
    )


# ============================================================
# /STATS
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
            f"💰 Bids: "
            f"{data.get('bids', 0)}\n"
            f"🟢 Sold: "
            f"{data.get('sold', 0)}\n"
            f"🔴 Unsold: "
            f"{data.get('unsold', 0)}",
            parse_mode="Markdown"
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Could not load statistics.\n\n{e}"
        )


# ============================================================
# /SKIPAUCTION
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

        db.cancel_auction(
            auction_id
        )

        await update.message.reply_text(
            "⏭️ Auction skipped."
        )


# ============================================================
# /STOPAUCTION
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
# /RESET
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
            "⚠️ This will delete all auction data.\n\n"
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
# ERROR HANDLER
# ============================================================

async def telegram_error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.exception(
        "Telegram error:",
        exc_info=context.error
    )


# ============================================================
# BUILD TELEGRAM APPLICATION
# ============================================================

def build_bot():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN environment variable is missing."
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Participant commands

    application.add_handler(
        CommandHandler(
            "start",
            start
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
            "players",
            players
        )
    )

    application.add_handler(
        CommandHandler(
            "auction",
            current_auction
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
            "history",
            history
        )
    )

    # Admin commands

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

    # Bid buttons

    application.add_handler(
        CallbackQueryHandler(
            bid_callback,
            pattern=r"^bid:"
        )
    )

    application.add_error_handler(
        telegram_error_handler
    )

    return application


# ============================================================
# RUN BOT
# ============================================================

def run_bot():

    bot = build_bot()

    logger.info(
        "Telegram bot starting..."
    )

    bot.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print("🚀 Starting eFootball Auction Bot...")

    api_thread = threading.Thread(
        target=lambda: uvicorn.run(
            app,
            host="0.0.0.0",
            port=PORT
        ),
        daemon=True
    )

    api_thread.start()

    print("🌐 FastAPI server started")
    print("🤖 Starting Telegram bot...")

    run_bot()
