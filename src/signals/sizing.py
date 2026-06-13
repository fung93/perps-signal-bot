"""Position sizing + risk guard for signals.

Fixed $100 margin, leverage <= 5x ($500 max notional). Stop-loss is ATR-based; take-profit
is a fixed reward:risk multiple. A signal is rejected if its stop-loss would sit beyond a
safe distance from the liquidation price at the chosen leverage (hard rule). Leverage is
reduced to keep the stop safe before rejecting outright.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .. import config

ATR_MULT = 1.5       # stop distance = ATR_MULT * ATR
REWARD_RISK = 2.0    # take-profit at 2R
LIQ_SAFETY = 0.5     # stop must sit within this fraction of the liquidation distance

LONG, SHORT = "LONG", "SHORT"


@dataclass(frozen=True)
class Sizing:
    entry: float
    stop_loss: float
    take_profit: float
    leverage: float
    margin_usd: float
    notional_usd: float
    risk_usd: float        # loss if the stop is hit
    rejected: bool
    reason: str


def size(direction: str, entry: float | None, atr: float | None) -> Sizing:
    """Size a LONG/SHORT around ``entry`` using ``atr`` for the stop, applying the liq guard."""
    margin = config.MARGIN_USD
    if entry is None or atr is None or entry <= 0 or atr <= 0:
        return Sizing(entry or 0.0, 0.0, 0.0, 0.0, margin, 0.0, 0.0, True, "invalid entry/atr")

    stop_dist = ATR_MULT * atr
    if direction == LONG:
        stop_loss = entry - stop_dist
        take_profit = entry + REWARD_RISK * stop_dist
    else:
        stop_loss = entry + stop_dist
        take_profit = entry - REWARD_RISK * stop_dist

    # Adverse move that hits the stop, as a fraction of entry.
    stop_frac = stop_dist / entry
    # Isolated-margin liquidation is ~1/leverage away; require the stop within LIQ_SAFETY of
    # that. So leverage <= LIQ_SAFETY / stop_frac — reduce leverage to fit, reject if even 1x
    # leaves the stop beyond the safe distance.
    max_safe_lev = math.floor(LIQ_SAFETY / stop_frac)
    leverage = min(config.MAX_LEVERAGE, max_safe_lev)
    if leverage < 1:
        return Sizing(entry, stop_loss, take_profit, 0.0, margin, 0.0, 0.0, True,
                      f"stop {stop_frac * 100:.1f}% too wide for safe liquidation distance")

    notional = margin * leverage
    return Sizing(entry, stop_loss, take_profit, float(leverage), margin, notional,
                  notional * stop_frac, False,
                  f"ATR stop {stop_frac * 100:.2f}% at {leverage}x")
