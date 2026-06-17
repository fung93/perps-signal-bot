"""One-time "data is ready to evaluate" alert.

Sends a Telegram message the first time enough signals have been graded to evaluate the
strategy (``config.READINESS_MIN_TRADES``). A flag in the meta table makes it fire exactly
once. Invoked by the daily brief, so it checks once a day and pings within a day of crossing.
"""
from __future__ import annotations

import logging

from . import config, db
from .feedback import analytics
from .notify import telegram

logger = logging.getLogger(__name__)

_FLAG = "readiness_notified"
_GET = "SELECT 1 FROM meta WHERE key = %s LIMIT 1"
_SET = ("INSERT INTO meta (key, value) VALUES (%s, 'true') "
        "ON CONFLICT (key) DO UPDATE SET value = 'true', updated_at = now()")


def _already_notified() -> bool:
    return bool(db.fetch_all(_GET, (_FLAG,)))


def _mark_notified() -> None:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(_SET, (_FLAG,))


def check_and_notify() -> bool:
    """Alert once the graded sample reaches the threshold. Returns True if a message was sent."""
    summary = analytics.compute()
    overall = summary["overall"]
    trades = overall["trades"]

    if trades < config.READINESS_MIN_TRADES:
        logger.info("readiness: %d/%d graded trades", trades, config.READINESS_MIN_TRADES)
        return False
    if _already_notified():
        return False

    # Send first, then set the flag — so a send failure simply retries next day.
    telegram.send_message(
        "*Data milestone reached*\n"
        f"{trades} graded signals collected — the sample is now large enough to evaluate.\n"
        f"Win rate {overall['win_rate'] * 100:.0f}%  ·  "
        f"expectancy ${overall['expectancy_usd']:.2f} ({overall['expectancy_r']:+.2f}R)  ·  "
        f"max drawdown ${overall['max_drawdown_usd']:.2f}\n"
        "Review expectancy and regime coverage before considering real capital. "
        "Not financial advice."
    )
    _mark_notified()
    logger.info("readiness alert sent at %d graded trades", trades)
    return True
