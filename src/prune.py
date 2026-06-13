"""Prune the candles table to its rolling retention window (storage discipline).

candles keeps only a rolling window (default 90 days); features are kept permanently for
training. Run daily by prune.yml. 90 days clears EMA-200 warmup on every timeframe and
exceeds the collector's deepest fetch (4h x 500 candles ~= 83 days), so prune and the
collector don't fight over the tail.

    python -m src.prune
"""
from __future__ import annotations

import logging

from . import config, db

logger = logging.getLogger(__name__)

_DELETE = "DELETE FROM candles WHERE open_time < now() - make_interval(days => %s)"


def run() -> int:
    """Delete candles older than the retention window. Returns the number of rows removed."""
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(_DELETE, (config.CANDLE_RETENTION_DAYS,))
            deleted = cur.rowcount
    logger.info("pruned %d candle rows older than %d days",
                deleted, config.CANDLE_RETENTION_DAYS)
    return deleted


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        run()
    except Exception:
        logger.exception("prune failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
