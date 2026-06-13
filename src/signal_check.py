"""Signal check (Phase 3) — evaluate the latest features per coin and emit signals.

For each coin on the signal timeframe: load the latest feature snapshot, dedup against
already-decided candles, score with the rule engine, size LONG/SHORT with the liquidation
guard, persist to the signals table, and push a Telegram card for tradeable signals. FLAT
and rejected setups are logged (status SKIPPED) with no card.

    python -m src.signal_check
"""
from __future__ import annotations

import logging

from . import config, db
from .notify import telegram
from .signals import rules, sizing
from .signals.snapshot import load_latest

logger = logging.getLogger(__name__)

_EXISTS = "SELECT 1 FROM signals WHERE coin = %s AND ts = %s LIMIT 1"

_INSERT = """
    INSERT INTO signals
        (coin, ts, direction, entry, tp, sl, leverage, size_usd, rule_score, model_version, status)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def run() -> int:
    """Evaluate every coin and emit signals. Returns the number of tradeable cards sent."""
    cards: list[str] = []
    with db.connect() as conn:
        with conn.cursor() as cur:
            for coin in config.COINS:
                loaded = load_latest(cur, coin, config.SIGNAL_TIMEFRAME)
                if loaded is None:
                    logger.info("no features yet for %s %s", coin, config.SIGNAL_TIMEFRAME)
                    continue
                ts, snap, atr = loaded

                cur.execute(_EXISTS, (coin, ts))
                if cur.fetchone() is not None:
                    logger.info("%s candle %s already decided", coin, ts)
                    continue

                decision = rules.evaluate(snap)

                if decision.direction == rules.FLAT:
                    cur.execute(_INSERT, (coin, ts, rules.FLAT, None, None, None, None, None,
                                          decision.score, rules.MODEL_VERSION, "SKIPPED"))
                    logger.info("%s FLAT (score %.2f): %s", coin, decision.score, decision.rationale)
                    continue

                sized = sizing.size(decision.direction, entry=snap.close, atr=atr)
                if sized.rejected:
                    cur.execute(_INSERT, (coin, ts, decision.direction, snap.close, None, None,
                                          None, None, decision.score, rules.MODEL_VERSION, "SKIPPED"))
                    logger.info("%s %s rejected: %s", coin, decision.direction, sized.reason)
                    continue

                cur.execute(_INSERT, (coin, ts, decision.direction, sized.entry, sized.take_profit,
                                      sized.stop_loss, sized.leverage, sized.margin_usd,
                                      decision.score, rules.MODEL_VERSION, "OPEN"))
                cards.append(telegram.format_signal_card(coin, decision, sized))
                logger.info("%s %s @ %.2f (score %.2f, %.0fx)", coin, decision.direction,
                            sized.entry, decision.score, sized.leverage)

    for text in cards:
        telegram.send_message(text)
    logger.info("signal check complete: %d card(s) sent", len(cards))
    return len(cards)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        run()
    except Exception:
        logger.exception("signal check failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
