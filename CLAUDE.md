# CLAUDE.md — Perps Signal Bot

Project rules for Claude Code. Read this fully before writing or changing anything.

## What this project is
A **signal-only** bot for intraday crypto perps (BTC, ETH, SOL) on Katana. It collects
market + sentiment data, scores a trade direction with a transparent rule engine, sends a
daily brief and signal cards to Telegram, logs every signal's outcome, and re-tunes itself
on a schedule using out-of-sample validation.

## Hard rules — never violate
1. **Signals only. Never place, sign, or submit a trade.** No order execution, no broker/DEX
   write calls. The human executes manually on Katana.
2. **Never handle private keys, seed phrases, or wallet credentials.** This bot has no business
   touching them. If a task seems to need them, stop and flag it.
3. **Do not touch the Quant trading project or its Supabase database.** This bot's data lives
   only in its own **Neon** project. No shared tables, no shared connection strings, no reads
   or writes against any other project.
4. **`FLAT` (no trade) is always a valid signal output.** Never force a long/short when the
   rules don't support one.
5. **Respect the risk envelope:** $100 margin per trade, **max 5x leverage** (= $500 notional).
   Every signal is intraday and tagged to close same day. Reject any signal whose stop-loss
   implies a move beyond a safe liquidation distance at the chosen leverage.
6. This is a research tool, not financial advice. Don't add language implying guaranteed profit.

## Tech stack (locked)
- Python 3.12.
- **Database: Neon (serverless Postgres), dedicated free project.** Access via **`psycopg` v3**
  using the Neon **pooled** connection string (`NEON_DATABASE_URL`, host contains `-pooler`).
- Data: Binance public REST/WebSocket (price/volume, no key); Katana Perps API / Katana MCP
  (mark price, funding, OI); Fear & Greed Index (alternative.me).
- Indicators: `pandas-ta`.
- Scheduler: GitHub Actions cron.
- Alerts: Telegram Bot API (`TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`).
- Signals: rule-based now (`signals/rules.py`); LightGBM classifier added in Phase 4,
  gated behind a walk-forward out-of-sample test before it can influence any card.

## DB conventions (psycopg v3)
- All DB access goes through `src/db.py`. Do not scatter connection logic elsewhere.
- Use the pooled URL. Wrap the first query of a run in a short retry — Neon scale-to-zero
  can make the first connect after idle take ~1s.
- Batch each Actions run's DB work into a single connection to conserve free compute-hours.
- Use parameterised queries (`cur.execute(sql, params)`) — never f-string SQL.
- Schema changes go in `migrations/NNN_*.sql`, applied in order. Never edit a committed
  migration; add a new one.

## Storage discipline (Neon free ≈ 0.5 GB)
- `candles`: rolling window only (default 60 days); `prune.yml` trims older rows daily.
- `features`: keep permanently (training data).
- 12-month backfill for ML training lives in a file (Parquet/CSV as a repo asset or Actions
  artifact), NOT in the DB.

## Secrets
- Never hard-code secrets. Read from env: `NEON_DATABASE_URL`, `TELEGRAM_TOKEN`,
  `TELEGRAM_CHAT_ID`. Provide `.env.example` with names only, never values.
- Never commit `.env` or any real credential.

## Build order
Follow the phases in `PERPS_SIGNAL_BOT_BUILD_PLAN_NEON.md`: Phase 0 scaffold → 1 data →
2 indicators/sentiment → 3 rule engine + Telegram → 4 feedback + ML → 5 hardening.
Build one phase at a time; don't skip ahead.

## Style
- Small, readable functions. Type hints. Docstrings on public functions.
- Prefer standard library + the locked deps; ask before adding a new dependency.
