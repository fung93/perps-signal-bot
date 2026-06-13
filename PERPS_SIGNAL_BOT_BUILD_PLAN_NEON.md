# Intraday Crypto Perps Signal System — Build Plan (Neon edition)

> A self-improving **signal** bot for BTC / ETH / SOL intraday perps on Katana.
> Generates long/short signals with entry, TP, SL, and sizing; pushes them to Telegram.
> **Execution is manual** — the bot never touches funds or keys.
> Entire stack runs on free tiers. Database is **Neon** (serverless Postgres),
> isolated from the existing Quant trading and dashboard projects on Supabase.

---

## 0. Scope & guardrails (read first)

- **What it does:** collects market + sentiment data, scores a trade direction with a transparent rule engine, sends a daily brief + signal cards via Telegram, logs every signal's outcome, and re-tunes itself on a schedule using out-of-sample validation.
- **What it does NOT do:** place orders, hold private keys, or move money. Signals only.
- **Per-trade params:** $100 margin, **max 5x leverage** (= $500 max notional). Intraday only — every position is flagged to close same day.
- **"Self-learning" definition:** a measured feedback loop, NOT online self-retraining on noise. New parameters/models are promoted **only if they beat the incumbent on data they have never seen** (walk-forward). `FLAT` (no trade) is always a valid output.
- **Isolation:** this project is its own Neon project. It does not read from or write to the Quant trading Supabase project. No shared tables, no shared credentials.
- This is a research tool. The capital is risk capital. Not financial advice.

---

## 1. Locked technical decisions

| Concern | Decision |
|---|---|
| Primary price feed | Binance public REST + WebSocket (no key for market data) — global price discovery |
| Execution-venue feed | Katana Perps API / Katana MCP — mark price, funding, OI for the market actually traded |
| Sentiment | Fear & Greed Index (alternative.me) + funding rate + open interest |
| Indicators | `pandas-ta` (RSI, EMA fast/slow, MACD, volume, **ATR for SL sizing**) |
| **Database** | **Neon** (serverless Postgres, dedicated free project). Scale-to-zero; wakes on connect. |
| DB access | `psycopg` (v3) or SQLAlchemy via Neon **pooled** connection string |
| Scheduler / compute | **GitHub Actions** cron (v1). Oracle Cloud Always Free ARM VM only if WebSocket streaming is needed later. |
| Signal layer v1 | Rule-based (`signals/rules.py`) — transparent, auditable |
| Signal layer v2 (Phase 4) | LightGBM classifier on the same feature table, gated behind walk-forward test |
| Alerts | Telegram Bot API |
| Language | Python 3.12 |
| Repo | New dedicated GitHub repo (separate from Quant trading + dashboard repos) |

### Why Neon here
- Same Postgres engine as Supabase — SQL, schema, and ORM code port directly.
- Free tier gives many more projects than Supabase's 2, so this bot lives on its own without touching Quant trading.
- Scale-to-zero is fine for a 15-minute cadence: the DB sleeps when idle and wakes on the next connection (~sub-second), which also conserves the free compute-hours.
- Free storage is ~0.5 GB — same order as Supabase, so the storage strategy in §4 still matters.

---

## 2. Repo structure

```
perps-signal-bot/
├── CLAUDE.md                  # project rules for Claude Code (copy §0 + §1)
├── README.md
├── requirements.txt           # psycopg[binary], pandas, pandas-ta, requests, python-telegram-bot, lightgbm (P4)
├── .env.example               # NEON_DATABASE_URL (pooled), TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
├── .github/workflows/
│   ├── collect.yml            # every 15m: ingest + indicators + signal check
│   ├── brief.yml              # daily 08:00 MYT: market brief
│   ├── reconcile.yml          # grade open/closed signal outcomes
│   ├── prune.yml              # daily: trim raw candles to rolling window (storage control)
│   └── retune.yml             # weekly: walk-forward re-optimization
├── migrations/
│   └── 001_init.sql           # plain SQL migrations, applied via psql/CI
├── src/
│   ├── config.py              # coins, timeframes, thresholds (env-driven)
│   ├── db.py                  # Neon connection (pooled URL), helpers, retry on cold-start
│   ├── data/
│   │   ├── binance.py         # OHLCV + volume
│   │   ├── katana.py          # mark price, funding, OI (API/MCP)
│   │   └── sentiment.py       # Fear & Greed, funding/OI aggregation
│   ├── indicators/compute.py  # RSI, EMA, MACD, ATR, volume features
│   ├── signals/
│   │   ├── rules.py           # v1 transparent scoring engine
│   │   ├── sizing.py          # $100 / ≤5x / ATR-based SL / R-multiple TP
│   │   └── ml/                # Phase 4: features.py, train.py, predict.py
│   ├── feedback/
│   │   ├── grade.py           # did the signal hit TP / SL / EOD?
│   │   └── analytics.py       # win rate, expectancy, drawdown, per-setup
│   └── notify/telegram.py     # brief + signal cards + weekly digest
└── tests/
```

---

## 3. Database schema (Neon / Postgres)

All tables in this Neon project only. `migrations/001_init.sql`:

- `candles` — coin, timeframe, open_time, o/h/l/c, volume. **Rolling window only** (see §4).
- `features` — coin, timeframe, ts, rsi, ema_fast, ema_slow, macd, atr, vol_z, funding, oi, fng. **Kept long-term** (this is the ML training table).
- `signals` — id, ts, coin, direction (LONG/SHORT/FLAT), entry, tp, sl, leverage, size_usd, rule_score, model_version, status (OPEN/HIT_TP/HIT_SL/EOD_CLOSE).
- `outcomes` — signal_id, exit_price, pnl_pct, pnl_usd, r_multiple, closed_at.
- `params` — versioned parameter sets + their out-of-sample score (audit trail for re-tuning).

Connection notes for `db.py`:
- Use the Neon **pooled** connection string (host contains `-pooler`) for Actions/serverless.
- Wrap the first query in a short retry — a cold scale-to-zero wake can make the very first connect take ~1s.
- Keep each Actions run's DB work batched in one connection to minimise compute-hours.

---

## 4. Storage strategy (Neon free ~0.5 GB — plan for it from day one)

- **Raw `candles`:** keep only a rolling window (e.g. last 60 days) needed to recompute indicators. `prune.yml` deletes older rows daily.
- **`features`:** keep permanently — small (a few hundred K rows over a year for 3 coins) and required for Phase 4 training.
- **Phase 4 backfill:** the one-time ~12-month history needed to train the classifier lives **outside** the DB — export to a Parquet/CSV file committed to the repo or stored as a GitHub Actions artifact, loaded only at train time. Don't let a year of raw candles sit in Neon.
- This keeps you comfortably under 0.5 GB indefinitely.

---

## 5. Phases

### Phase 0 — Scaffold *(pipeline visible from day one)*
- New GitHub repo; new **Neon project**; Telegram bot via @BotFather.
- `CLAUDE.md` with §0 + §1 rules. `.env.example` with the three secrets.
- `migrations/001_init.sql` applied to Neon; `db.py` connects via pooled URL with cold-start retry.
- A `/ping` Telegram command + heartbeat log so you can watch the pipeline breathe.

### Phase 1 — Data pipeline
- `binance.py`: pull 15m / 1h / 4h candles for BTC, ETH, SOL → `candles`.
- One-time backfill ~12 months → exported to file (per §4), not the DB.
- `collect.yml` scheduled every 15m.

### Phase 2 — Indicators + sentiment
- `indicators/compute.py`: RSI(14), EMA(20/50/200), MACD, ATR(14), volume z-score on each closed candle → `features`.
- `sentiment.py`: Fear & Greed + Katana funding + OI → `features`.

### Phase 3 — Rule engine + Telegram *(first fully working system)*
- `rules.py` scoring, e.g.: trend alignment (EMA stack) + RSI not overextended + volume confirmation + funding not crowded → score → LONG / SHORT / FLAT.
- `sizing.py`: SL = entry ± (k × ATR); TP at fixed R multiple; size = $100, leverage ≤ 5x; **reject signal if SL implies a move beyond safe liquidation distance at chosen leverage**.
- `telegram.py`:
  - **Daily brief (08:00 MYT):** regime per coin, key levels, funding/sentiment snapshot, watchlist.
  - **Signal card:** coin · LONG/SHORT · entry zone · TP · SL · leverage · $ size · one-line rationale · "close by EOD" tag.

### Phase 4 — Feedback loop + ML classifier
- `feedback/grade.py`: mark each signal HIT_TP / HIT_SL / EOD_CLOSE; write `outcomes`.
- `feedback/analytics.py`: win rate, expectancy, max drawdown, per-setup breakdown.
- `signals/ml/`: LightGBM classifier trained on `features` + the file-based backfill → P(profitable long/short within day).
- **Promotion gate:** `retune.yml` runs weekly walk-forward; a new param set or model is written to `params` and activated **only if it beats the live one out-of-sample**. Otherwise it's logged and discarded.

### Phase 5 — Hardening
- Kill-switch + max signals/day.
- `FLAT` respected as a real decision (no forced trades).
- EOD "close everything" reminder to Telegram.
- Weekly performance digest.
- Liquidation-distance sanity check on every card.

---

## 6. Suggested timeline (with Claude Code)
- **Weekend 1:** Phases 0–3 → working signal system.
- **A few evenings:** Phases 4–5.
- **Then weeks of paper/observation-only** before risking real capital on any signal.

---

## 7. Free-tier notes
- GitHub Actions cron can drift a few minutes; the 15m floor is fine for intraday — don't fight for sub-minute precision.
- Neon scale-to-zero + 15m cadence is a good match; batch DB work per run to conserve compute-hours.
- ~0.5 GB Neon storage is the real ceiling — the §4 strategy (prune raw candles, keep features, backfill in a file) is what keeps you under it.
- Oracle Always Free (4 ARM cores / 24 GB, available 2026) is the upgrade path **only** if you later want continuous WebSocket streaming; you could also move Postgres onto it at that point and drop Neon.
