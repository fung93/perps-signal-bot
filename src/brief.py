"""Daily market brief (Phase 3) — regime + sentiment snapshot to Telegram.

Run by brief.yml at 08:00 MYT (00:00 UTC):

    python -m src.brief
"""
from __future__ import annotations

import logging

from . import config, db
from .data import sentiment
from .notify import telegram
from .signals import rules
from .signals.snapshot import load_latest

logger = logging.getLogger(__name__)


def build_rows() -> list[dict]:
    """One summary row per coin: regime, rule bias, RSI, and the EMA levels."""
    rows: list[dict] = []
    with db.connect() as conn:
        with conn.cursor() as cur:
            for coin in config.COINS:
                loaded = load_latest(cur, coin, config.SIGNAL_TIMEFRAME)
                if loaded is None:
                    logger.info("no features for %s", coin)
                    continue
                _ts, snap, _atr = loaded
                if snap.rsi is None or snap.ema_long is None:
                    continue
                decision = rules.evaluate(snap)
                rows.append({
                    "coin": coin,
                    "regime": rules.regime(snap),
                    "bias": decision.direction,
                    "rsi": snap.rsi,
                    "ema_fast": snap.ema_fast,
                    "ema_slow": snap.ema_slow,
                    "ema_long": snap.ema_long,
                })
    return rows


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        rows = build_rows()
        fng = sentiment.latest_fear_greed()
        telegram.send_message(telegram.format_daily_brief(rows, fng))
        logger.info("daily brief sent for %d coins", len(rows))
    except Exception:
        logger.exception("daily brief failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
