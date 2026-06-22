"""Build the features table from stored candles (Phase 2).

For each coin/timeframe, reads the candle window from Neon, computes indicators
(:mod:`src.indicators.compute`), attaches sentiment (Fear & Greed; funding/OI once wired),
and upserts into the features table.

    python -m src.features
"""
from __future__ import annotations

import logging
import math

import pandas as pd

from . import config, db
from .data import sentiment
from .indicators import compute

logger = logging.getLogger(__name__)

_SELECT_CANDLES = (
    "SELECT open_time, open, high, low, close, volume "
    "FROM candles WHERE coin = %s AND timeframe = %s ORDER BY open_time ASC"
)

_UPSERT_FEATURE = """
    INSERT INTO features
        (coin, timeframe, ts, rsi, ema_fast, ema_slow, ema_long,
         macd, macd_signal, atr, vol_z, adx, funding, oi, fng)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (coin, timeframe, ts) DO UPDATE SET
        rsi=EXCLUDED.rsi, ema_fast=EXCLUDED.ema_fast, ema_slow=EXCLUDED.ema_slow,
        ema_long=EXCLUDED.ema_long, macd=EXCLUDED.macd, macd_signal=EXCLUDED.macd_signal,
        atr=EXCLUDED.atr, vol_z=EXCLUDED.vol_z, adx=EXCLUDED.adx, funding=EXCLUDED.funding,
        oi=EXCLUDED.oi, fng=EXCLUDED.fng;
"""

_OHLCV = ["open", "high", "low", "close", "volume"]


def _clean(value) -> float | None:
    """Convert pandas NaN to None (SQL NULL); otherwise return a plain float."""
    try:
        if value is None or math.isnan(value):
            return None
    except TypeError:
        return value
    return float(value)


def build() -> int:
    """Compute and upsert features for every coin/timeframe. Returns rows written."""
    fng_by_date = sentiment.fear_greed_history(limit=200)
    rows: list[tuple] = []

    with db.connect() as conn:
        with conn.cursor() as cur:
            for coin in config.COINS:
                funding, oi = sentiment.funding_oi(coin)
                for timeframe in config.TIMEFRAMES:
                    cur.execute(_SELECT_CANDLES, (coin, timeframe))
                    data = cur.fetchall()
                    if len(data) <= compute.RSI_LEN:
                        logger.info("skip %s %s: only %d candles", coin, timeframe, len(data))
                        continue

                    open_times = [r[0] for r in data]
                    df = pd.DataFrame(data, columns=["open_time", *_OHLCV])
                    for col in _OHLCV:
                        df[col] = df[col].astype(float)

                    feats = compute.compute_features(df)
                    written = 0
                    for i, ts in enumerate(open_times):
                        if pd.isna(feats["rsi"].iloc[i]):
                            continue  # not enough warmup yet
                        rows.append((
                            coin, timeframe, ts,
                            _clean(feats["rsi"].iloc[i]),
                            _clean(feats["ema_fast"].iloc[i]),
                            _clean(feats["ema_slow"].iloc[i]),
                            _clean(feats["ema_long"].iloc[i]),
                            _clean(feats["macd"].iloc[i]),
                            _clean(feats["macd_signal"].iloc[i]),
                            _clean(feats["atr"].iloc[i]),
                            _clean(feats["vol_z"].iloc[i]),
                            _clean(feats["adx"].iloc[i]),
                            funding, oi, fng_by_date.get(ts.date()),
                        ))
                        written += 1
                    logger.info("computed %d feature rows for %s %s", written, coin, timeframe)

            if rows:
                cur.executemany(_UPSERT_FEATURE, rows)
    logger.info("upserted %d feature rows", len(rows))
    return len(rows)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        build()
    except Exception:
        logger.exception("feature build failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
