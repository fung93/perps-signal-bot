"""Collector entrypoint — ingest recent closed candles into Neon.

Run by ``collect.yml`` every 15 minutes (and locally for testing):

    python -m src.collect

Phase 1 ingests Binance candles only. Later phases extend this same run with indicator
computation (Phase 2) and signal checks (Phase 3).
"""
from __future__ import annotations

import logging

from . import config, db
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


def ingest() -> int:
    """Fetch recent closed candles for every coin/timeframe and upsert them.

    Fetches all series first (network), then writes everything in a single Neon
    connection to conserve compute-hours. Returns the number of rows written.
    """
    rows: list[tuple] = []
    for coin in config.COINS:
        for timeframe in config.TIMEFRAMES:
            candles = binance.fetch_recent(coin, timeframe)
            logger.info("fetched %d candles for %s %s", len(candles), coin, timeframe)
            rows.extend(
                (c.coin, c.timeframe, c.open_time, c.open, c.high, c.low, c.close, c.volume)
                for c in candles
            )

    if not rows:
        logger.warning("no candles fetched; nothing to write")
        return 0

    with db.connect() as conn:
        with conn.cursor() as cur:
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
    except Exception:
        logger.exception("collect run failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
