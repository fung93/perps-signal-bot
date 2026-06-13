# Perps Signal Bot

Signal-only bot for intraday crypto perps (BTC, ETH, SOL) on Katana. It collects market +
sentiment data, scores a direction with a transparent rule engine, and pushes a daily brief
plus signal cards to Telegram. **Execution is manual — the bot never touches funds or keys.**

See [`PERPS_SIGNAL_BOT_BUILD_PLAN_NEON.md`](PERPS_SIGNAL_BOT_BUILD_PLAN_NEON.md) for the full
plan and [`CLAUDE.md`](CLAUDE.md) for the hard rules.

## Status — Phase 0 (scaffold)

Connectivity skeleton only: Neon DB connector, schema migration, and a Telegram heartbeat.
No data collection, indicators, or signals yet.

## One-time setup (manual)

1. **Neon** — create a free project; copy the **pooled** connection string (host contains `-pooler`).
2. **Telegram** — create a bot via [@BotFather](https://t.me/BotFather) for `TELEGRAM_TOKEN`;
   get your `TELEGRAM_CHAT_ID` (e.g. message the bot, then read `getUpdates`).
3. **Env** — `copy .env.example .env` and fill in the three values.
4. **Python 3.12** — `python -m venv .venv && .venv\Scripts\activate`, then
   `pip install -r requirements.txt`. For local `.env` auto-loading: also `pip install python-dotenv`.
5. **Schema** — apply the migration once: `psql "%NEON_DATABASE_URL%" -f migrations/001_init.sql`.

## Verify the pipeline

```
python -m src.heartbeat
```

Pings Neon (`SELECT 1`) and sends a heartbeat message to your Telegram chat. If the message
arrives showing `DB: ok`, the full chain (env → Neon → Telegram) is working.

In CI: set `NEON_DATABASE_URL`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` as repository secrets,
then run the **heartbeat** workflow manually (Actions → heartbeat → Run workflow).
