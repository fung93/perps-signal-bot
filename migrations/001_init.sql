-- 001_init.sql — Perps Signal Bot initial schema (Neon / Postgres)
-- Apply once:  psql "$NEON_DATABASE_URL" -f migrations/001_init.sql
-- Safe to re-run: all objects use IF NOT EXISTS.

-- ---------------------------------------------------------------------------
-- candles: raw OHLCV from Binance. Rolling window only (pruned by prune.yml).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS candles (
    coin        TEXT        NOT NULL,                 -- 'BTC' | 'ETH' | 'SOL'
    timeframe   TEXT        NOT NULL,                 -- '15m' | '1h' | '4h'
    open_time   TIMESTAMPTZ NOT NULL,                 -- candle open (UTC)
    open        NUMERIC(20,8) NOT NULL,
    high        NUMERIC(20,8) NOT NULL,
    low         NUMERIC(20,8) NOT NULL,
    close       NUMERIC(20,8) NOT NULL,
    volume      NUMERIC(28,8) NOT NULL,
    PRIMARY KEY (coin, timeframe, open_time)
);

CREATE INDEX IF NOT EXISTS idx_candles_open_time ON candles (open_time);

-- ---------------------------------------------------------------------------
-- features: computed indicators + sentiment per candle. Kept permanently.
-- This is the ML training table (Phase 4).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS features (
    coin        TEXT        NOT NULL,
    timeframe   TEXT        NOT NULL,
    ts          TIMESTAMPTZ NOT NULL,                 -- = candle open_time it was computed on
    rsi         NUMERIC(10,4),
    ema_fast    NUMERIC(20,8),                        -- e.g. EMA20
    ema_slow    NUMERIC(20,8),                        -- e.g. EMA50
    ema_long    NUMERIC(20,8),                        -- e.g. EMA200
    macd        NUMERIC(20,8),
    macd_signal NUMERIC(20,8),
    atr         NUMERIC(20,8),                        -- for SL sizing
    vol_z       NUMERIC(10,4),                        -- volume z-score
    funding     NUMERIC(12,8),                        -- Katana funding rate
    oi          NUMERIC(28,8),                        -- open interest
    fng         INTEGER,                              -- Fear & Greed 0-100
    PRIMARY KEY (coin, timeframe, ts)
);

CREATE INDEX IF NOT EXISTS idx_features_ts ON features (ts);

-- ---------------------------------------------------------------------------
-- signals: every decision the engine emits (including FLAT).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signals (
    id            BIGSERIAL  PRIMARY KEY,
    ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
    coin          TEXT        NOT NULL,
    direction     TEXT        NOT NULL
                  CHECK (direction IN ('LONG','SHORT','FLAT')),
    entry         NUMERIC(20,8),                      -- null when FLAT
    tp            NUMERIC(20,8),
    sl            NUMERIC(20,8),
    leverage      NUMERIC(4,2) CHECK (leverage IS NULL OR leverage <= 5),
    size_usd      NUMERIC(12,2),                      -- margin, default 100
    rule_score    NUMERIC(10,4),                      -- transparent v1 score
    model_version TEXT,                               -- 'rules:v1' or 'ml:<hash>'
    status        TEXT        NOT NULL DEFAULT 'OPEN'
                  CHECK (status IN ('OPEN','HIT_TP','HIT_SL','EOD_CLOSE','SKIPPED')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_signals_ts     ON signals (ts);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals (status);
CREATE INDEX IF NOT EXISTS idx_signals_coin   ON signals (coin);

-- ---------------------------------------------------------------------------
-- outcomes: how each non-FLAT signal resolved. One row per resolved signal.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS outcomes (
    signal_id   BIGINT      PRIMARY KEY REFERENCES signals(id) ON DELETE CASCADE,
    exit_price  NUMERIC(20,8),
    pnl_pct     NUMERIC(10,4),
    pnl_usd     NUMERIC(12,2),
    r_multiple  NUMERIC(10,4),                        -- realised reward in R units
    closed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- params: versioned parameter sets / models with their out-of-sample score.
-- Only one row per kind should be is_active = true at a time.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS params (
    id         BIGSERIAL  PRIMARY KEY,
    kind       TEXT        NOT NULL CHECK (kind IN ('rules','ml')),
    version    TEXT        NOT NULL,
    payload    JSONB       NOT NULL,                  -- thresholds or model metadata
    oos_score  NUMERIC(10,4),                         -- walk-forward out-of-sample score
    is_active  BOOLEAN     NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (kind, version)
);

-- Enforce at most one active set per kind.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_params_active_per_kind
    ON params (kind) WHERE is_active;
