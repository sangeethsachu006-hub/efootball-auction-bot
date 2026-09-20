# eFootball Auction Bot

A free Telegram bot for virtual eFootball player auctions.

## Rules
- Virtual credits only; no real-money transactions, gambling, or cash prizes.
- Maximum 100 participants per auction.
- Maximum 500 players per auction.
- Base price: 2 Cr.
- Minimum increment: 0.5 Cr.
- Default starting balance: 100 Cr per participant.
- Default bidding timer: 30 seconds.
- SQLite persistence.
- Atomic SQLite transactions protect bids and balances from race conditions.

## Deploy on Render
1. Create a Render Web Service from this repository.
2. Build: `pip install -r requirements.txt`
3. Start: `uvicorn app:app --host 0.0.0.0 --port $PORT`
4. Add environment variables from `.env.example`.
5. `PUBLIC_URL` should be the Render service URL.
6. `ADMIN_IDS` is a comma-separated list of Telegram numeric user IDs.
7. Deploy and send `/start` to the bot.

## Host flow
`/createauction` -> enter name -> participant limit -> paste player list.

A creator becomes the auction host. The host can manage that auction. Admins can manage any auction.

## Player management
Use `/addplayer <auction_id> <player name>` before the auction starts. The player cap is 500.

## Auction states
DRAFT -> LOBBY -> RUNNING <-> PAUSED -> COMPLETED
DRAFT/LOBBY can also be CANCELLED.

## Important
Render free services can sleep when idle. Telegram webhooks wake the service when Telegram sends an update, but cold starts can add delay.
