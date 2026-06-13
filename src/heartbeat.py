"""Heartbeat entrypoint — confirms the pipeline is alive end to end.

Run locally or from CI to exercise the full Phase 0 chain: environment -> Neon DB ->
Telegram. This is the "watch the pipeline breathe" check.

    python -m src.heartbeat
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from . import db
from .notify import telegram

logger = logging.getLogger(__name__)


def run() -> None:
    """Ping the DB and send a Telegram heartbeat reporting the result."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    try:
        db_ok = db.ping()
    except Exception:
        # The heartbeat's job is to report failure, not crash before notifying.
        db_ok = False
        logger.exception("DB ping failed")

    status = "✅ alive" if db_ok else "⚠️ DB unreachable"
    text = (
        "*Perps Signal Bot — heartbeat*\n"
        f"{status}\n"
        f"DB: {'ok' if db_ok else 'FAIL'}\n"
        f"Time: {now}"
    )
    telegram.send_message(text)
    logger.info("Heartbeat sent: db_ok=%s at %s", db_ok, now)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        run()
    except Exception:
        logger.exception("Heartbeat failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
