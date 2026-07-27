"""Collector entrypoint — ingest candles, compute features, run the signal check.

Run by ``collect.yml`` every 15 minutes (and locally for testing):

    python -m src.collect

Phase 1 ingests Binance candles; Phase 2 computes features; Phase 3 runs the signal check.
"""
from __future__ import annotations

import logging

from . import config, db, features, signal_check
from .data import binance

logger = logging.getLogger(__name__)

# Idempotent upsert: re-ingesting an existing candle just refreshes its values.
_UPSERT_SQL = """
    INSERT INTO candles (coin, timeframe, open_time, open, high, low, close, volume)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (coin, timeframe, open_time) DO UPDATE SET
        open   = EXCLUDED.open,
        high   = EXCLUDED.high,
        low    = EXCLUDED.low,
        close  = EXCLUDED.close,
        volume = EXCLUDED.volume;
"""

# Newest stored candle per series — only rows newer than this (minus a small overlap) need
# writing. Re-upserting all 500 fetched candles every run wasted Neon's transfer quota.
_SELECT_MAX = "SELECT coin, timeframe, max(open_time) FROM candles GROUP BY coin, timeframe"
_OVERLAP_CANDLES = 2
_INTERVAL_SECONDS = {"15m": 900, "1h": 3600, "4h": 14400}


def ingest() -> int:
    """Fetch recent closed candles for every coin/timeframe and upsert the new ones.

    Fetches all series first (network), then in a single Neon connection reads the newest
    stored candle per series and writes only rows newer than it (small overlap included).
    The 500-candle fetch still self-heals gaps: after an outage, everything newer than the
    stored max gets written. Returns the number of rows written.
    """
    fetched: dict[tuple[str, str], list] = {}
    for coin in config.COINS:
        for timeframe in config.TIMEFRAMES:
            candles = binance.fetch_recent(coin, timeframe)
            logger.info("fetched %d candles for %s %s", len(candles), coin, timeframe)
            fetched[(coin, timeframe)] = candles

    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(_SELECT_MAX)
            stored_max = {(r[0], r[1]): r[2] for r in cur.fetchall()}

            rows: list[tuple] = []
            for (coin, timeframe), candles in fetched.items():
                prev = stored_max.get((coin, timeframe))
                if prev is not None:
                    cutoff_s = _OVERLAP_CANDLES * _INTERVAL_SECONDS[timeframe]
                    candles = [c for c in candles
                               if (c.open_time - prev).total_seconds() > -cutoff_s]
                rows.extend(
                    (c.coin, c.timeframe, c.open_time, c.open, c.high, c.low, c.close, c.volume)
                    for c in candles
                )

            if rows:
                cur.executemany(_UPSERT_SQL, rows)
    logger.info("upserted %d candle rows", len(rows))
    return len(rows)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        ingest()
        features.build()
        signal_check.run()
    except Exception:
        logger.exception("collect run failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
