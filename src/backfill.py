"""One-time history backfill -> file (NOT the database).

Fetches a long window of candles for every coin/timeframe and writes them to one CSV
under ``data/``. Per the storage strategy, the ~12-month history lives in a file (repo
asset or CI artifact), never in Neon — only the rolling window goes to the candles table.

    python -m src.backfill                 # default: 365 days -> data/
    python -m src.backfill --days 540 --out data

The CSV is gitignored by default; commit it deliberately or keep it as a CI artifact.
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
from datetime import datetime, timedelta, timezone

from . import config
from .data import binance

logger = logging.getLogger(__name__)

_HEADER = ["coin", "timeframe", "open_time", "open", "high", "low", "close", "volume"]


def backfill(days: int, out_dir: str) -> str:
    """Write ``days`` of candles for every coin/timeframe to a CSV. Returns the path."""
    os.makedirs(out_dir, exist_ok=True)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    path = os.path.join(out_dir, f"candles_backfill_{days}d.csv")

    total = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(_HEADER)
        for coin in config.COINS:
            for timeframe in config.TIMEFRAMES:
                candles = binance.fetch_range(coin, timeframe, start, end)
                for c in candles:
                    writer.writerow(
                        [c.coin, c.timeframe, c.open_time.isoformat(),
                         c.open, c.high, c.low, c.close, c.volume]
                    )
                total += len(candles)
                logger.info("backfilled %d candles for %s %s", len(candles), coin, timeframe)

    logger.info("wrote %d rows to %s", total, path)
    return path


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Backfill candle history to a CSV file (outside the DB)."
    )
    parser.add_argument("--days", type=int, default=365, help="days of history (default 365)")
    parser.add_argument("--out", default="data", help="output directory (default data/)")
    args = parser.parse_args()

    try:
        backfill(args.days, args.out)
    except Exception:
        logger.exception("backfill failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
