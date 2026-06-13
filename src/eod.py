"""End-of-day reminder (Phase 5) — list open positions to close before the day rolls.

Run by eod.yml near the end of the UTC trading day:

    python -m src.eod

Intraday positions are graded against their UTC day; this nudges the human to close any
still-open signal manually (the bot never closes anything itself).
"""
from __future__ import annotations

import logging

from . import db
from .notify import telegram

logger = logging.getLogger(__name__)

_OPEN = ("SELECT coin, direction, entry, sl, tp FROM signals "
         "WHERE status = 'OPEN' ORDER BY ts")


def run() -> int:
    """Send a reminder listing open signals. Returns the count (0 = nothing sent)."""
    rows = db.fetch_all(_OPEN)
    if not rows:
        logger.info("no open positions; no reminder sent")
        return 0
    telegram.send_message(telegram.format_eod_reminder(rows))
    logger.info("EOD reminder sent for %d open position(s)", len(rows))
    return len(rows)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        run()
    except Exception:
        logger.exception("EOD reminder failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
