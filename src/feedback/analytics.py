"""Performance analytics over graded outcomes (Phase 4).

Win rate, expectancy, max drawdown, and per-coin / per-direction breakdowns. The weekly
Telegram digest (Phase 5) will format these; for now ``compute()`` returns the summary and
``main()`` logs it.

    python -m src.feedback.analytics
"""
from __future__ import annotations

import logging

from .. import db

logger = logging.getLogger(__name__)

_ROWS = """
    SELECT s.coin, s.direction, o.pnl_usd, o.r_multiple
    FROM signals s JOIN outcomes o ON o.signal_id = s.id
    ORDER BY o.closed_at
"""


def _stats(pnls: list[float], rs: list[float]) -> dict:
    n = len(pnls)
    if n == 0:
        return {"trades": 0, "win_rate": None, "expectancy_usd": None,
                "expectancy_r": None, "total_usd": 0.0, "max_drawdown_usd": 0.0}
    wins = sum(1 for r in rs if r > 0)
    equity = peak = max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "trades": n,
        "win_rate": round(wins / n, 4),
        "expectancy_usd": round(sum(pnls) / n, 4),
        "expectancy_r": round(sum(rs) / n, 4),
        "total_usd": round(sum(pnls), 2),
        "max_drawdown_usd": round(max_dd, 2),
    }


def compute() -> dict:
    """Return overall stats plus per-coin and per-direction breakdowns."""
    rows = db.fetch_all(_ROWS)
    overall = _stats([float(r[2]) for r in rows], [float(r[3]) for r in rows])

    breakdown: dict[str, dict] = {"by_coin": {}, "by_direction": {}}
    for label, idx in (("by_coin", 0), ("by_direction", 1)):
        groups: dict[str, tuple[list, list]] = {}
        for row in rows:
            key = row[idx]
            groups.setdefault(key, ([], []))
            groups[key][0].append(float(row[2]))
            groups[key][1].append(float(row[3]))
        breakdown[label] = {k: _stats(p, r) for k, (p, r) in groups.items()}

    return {"overall": overall, **breakdown}


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        summary = compute()
        logger.info("overall: %s", summary["overall"])
        for coin, st in summary["by_coin"].items():
            logger.info("coin %s: %s", coin, st)
        for direction, st in summary["by_direction"].items():
            logger.info("dir %s: %s", direction, st)
    except Exception:
        logger.exception("analytics failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
