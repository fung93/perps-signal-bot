"""Weekly performance digest (Phase 5) — push the analytics summary to Telegram.

Run by digest.yml weekly:

    python -m src.digest
"""
from __future__ import annotations

import logging

from .feedback import analytics
from .notify import telegram

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        summary = analytics.compute()
        telegram.send_message(telegram.format_digest(summary))
        logger.info("weekly digest sent (%d trades)", summary["overall"]["trades"])
    except Exception:
        logger.exception("weekly digest failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
