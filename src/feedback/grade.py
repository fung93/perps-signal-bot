"""Grade open signals against subsequent candles (Phase 4 feedback).

For each OPEN signal, walk the signal-timeframe candles within the same UTC day to see
whether the stop or the target was hit first, else close at end-of-day. Writes the outcome
and flips the signal's status. Intraday only — each position is graded against its own day.

    python -m src.feedback.grade
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .. import config, db
from ..signals.rules import LONG

logger = logging.getLogger(__name__)

_OPEN = ("SELECT id, coin, ts, direction, entry, tp, sl, leverage, size_usd "
         "FROM signals WHERE status = 'OPEN' ORDER BY ts")
_FWD = ("SELECT high, low, close FROM candles "
        "WHERE coin = %s AND timeframe = %s AND open_time > %s AND open_time < %s "
        "ORDER BY open_time ASC")
_OUTCOME = """
    INSERT INTO outcomes (signal_id, exit_price, pnl_pct, pnl_usd, r_multiple, closed_at)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (signal_id) DO UPDATE SET
        exit_price = EXCLUDED.exit_price, pnl_pct = EXCLUDED.pnl_pct,
        pnl_usd = EXCLUDED.pnl_usd, r_multiple = EXCLUDED.r_multiple,
        closed_at = EXCLUDED.closed_at
"""
_SET_STATUS = "UPDATE signals SET status = %s WHERE id = %s"


def _eod(ts: datetime) -> datetime:
    """End of the local active day (config.ACTIVE_HOUR_END) for ts — the close boundary."""
    local = ts.astimezone(ZoneInfo(config.LOCAL_TZ))
    end = local.replace(hour=config.ACTIVE_HOUR_END, minute=0, second=0, microsecond=0)
    if local.hour >= config.ACTIVE_HOUR_END:
        end += timedelta(days=1)
    return end.astimezone(timezone.utc)


def _resolve(direction, entry, tp, sl, fwd, day_over):
    """Return (status, exit_price) or None if still open. SL takes priority on ties."""
    for high, low, close in fwd:
        if direction == LONG:
            if low <= sl:
                return "HIT_SL", sl
            if high >= tp:
                return "HIT_TP", tp
        else:
            if high >= sl:
                return "HIT_SL", sl
            if low <= tp:
                return "HIT_TP", tp
    if day_over:
        return "EOD_CLOSE", (fwd[-1][2] if fwd else entry)
    return None


def run() -> int:
    """Grade every OPEN signal whose outcome can be determined. Returns the count graded."""
    now = datetime.now(timezone.utc)
    graded = 0
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(_OPEN)
            signals = cur.fetchall()
            for sid, coin, ts, direction, entry, tp, sl, leverage, size_usd in signals:
                entry, tp, sl = float(entry), float(tp), float(sl)
                lev = float(leverage or 1)
                margin = float(size_usd or config.MARGIN_USD)
                eod = _eod(ts)
                cur.execute(_FWD, (coin, config.SIGNAL_TIMEFRAME, ts, eod))
                fwd = [(float(h), float(low), float(c)) for h, low, c in cur.fetchall()]
                resolved = _resolve(direction, entry, tp, sl, fwd, now >= eod)
                if resolved is None:
                    continue
                status, exit_price = resolved
                if direction == LONG:
                    pnl_pct = (exit_price - entry) / entry
                    r_mult = (exit_price - entry) / (entry - sl)
                else:
                    pnl_pct = (entry - exit_price) / entry
                    r_mult = (entry - exit_price) / (sl - entry)
                pnl_usd = margin * lev * pnl_pct
                cur.execute(_OUTCOME, (sid, exit_price, pnl_pct, pnl_usd, r_mult, now))
                cur.execute(_SET_STATUS, (status, sid))
                graded += 1
                logger.info("graded signal %d (%s %s): %s exit %.2f r=%.2f",
                            sid, coin, direction, status, exit_price, r_mult)
    logger.info("grade run complete: %d graded", graded)
    return graded


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        run()
    except Exception:
        logger.exception("grade run failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
