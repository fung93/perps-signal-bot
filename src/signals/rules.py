"""Transparent v1 rule engine: score a feature snapshot into LONG / SHORT / FLAT.

Trend-following with momentum and volume confirmation, plus a funding-crowding filter.
Every component is explicit and contributes to a signed score (positive = long bias).
FLAT is always a valid output. Thresholds live here for now; Phase 4 retuning will move
them into the params table.
"""
from __future__ import annotations

from dataclasses import dataclass

MODEL_VERSION = "rules:v1"

LONG, SHORT, FLAT = "LONG", "SHORT", "FLAT"

# Scoring thresholds / weights.
ENTER_THRESHOLD = 2.5       # |score| required to leave FLAT
RSI_OVERBOUGHT = 75.0       # don't open new longs above this (no chasing)
RSI_OVERSOLD = 25.0         # don't open new shorts below this
VOL_CONFIRM_Z = 0.5         # volume z-score that counts as confirmation
FUNDING_CROWDED = 0.0005    # |funding rate| above which that side is "crowded"


@dataclass(frozen=True)
class Snapshot:
    """The inputs the rule engine reads for one coin at one candle."""

    close: float | None
    ema_fast: float | None
    ema_slow: float | None
    ema_long: float | None
    rsi: float | None
    macd: float | None
    macd_signal: float | None
    vol_z: float | None
    funding: float | None = None
    adx: float | None = None


@dataclass(frozen=True)
class Decision:
    direction: str
    score: float
    rationale: str


def _sign(x: float) -> float:
    return (x > 0) - (x < 0)


def regime(s: Snapshot) -> str:
    """Coarse trend label from the EMA stack and price (for the daily brief)."""
    if None in (s.close, s.ema_fast, s.ema_slow, s.ema_long):
        return "unknown"
    if s.ema_fast > s.ema_slow > s.ema_long and s.close > s.ema_fast:
        return "uptrend"
    if s.ema_fast < s.ema_slow < s.ema_long and s.close < s.ema_fast:
        return "downtrend"
    return "range"


def evaluate(s: Snapshot) -> Decision:
    """Score a snapshot into a Decision. Returns FLAT when conviction is insufficient."""
    required = (s.close, s.ema_fast, s.ema_slow, s.ema_long, s.rsi, s.macd, s.macd_signal)
    if any(v is None for v in required):
        return Decision(FLAT, 0.0, "insufficient indicator warmup")

    score = 0.0
    reasons: list[str] = []

    # 1) Trend alignment — EMA stack with price confirmation.
    if s.ema_fast > s.ema_slow > s.ema_long and s.close > s.ema_fast:
        score += 2.0
        reasons.append("EMA stack bullish")
    elif s.ema_fast < s.ema_slow < s.ema_long and s.close < s.ema_fast:
        score -= 2.0
        reasons.append("EMA stack bearish")
    else:
        reasons.append("EMA mixed")

    # 2) Momentum — MACD vs its signal line.
    if s.macd > s.macd_signal:
        score += 1.0
        reasons.append("MACD bullish")
    else:
        score -= 1.0
        reasons.append("MACD bearish")

    # 3) RSI directional bias.
    score += 0.5 if s.rsi >= 50.0 else -0.5

    # 4) Volume confirmation — amplifies conviction in the prevailing direction.
    if s.vol_z is not None and s.vol_z >= VOL_CONFIRM_Z and score != 0:
        score += 0.5 * _sign(score)
        reasons.append("volume confirms")

    # 5) Funding crowding filter (skipped when funding is unavailable).
    if s.funding is not None:
        if s.funding > FUNDING_CROWDED:
            score -= 0.5
            reasons.append("funding crowded long")
        elif s.funding < -FUNDING_CROWDED:
            score += 0.5
            reasons.append("funding crowded short")

    # Decide, with an overextension veto so we never chase.
    direction = FLAT
    if score >= ENTER_THRESHOLD and s.rsi < RSI_OVERBOUGHT:
        direction = LONG
    elif score <= -ENTER_THRESHOLD and s.rsi > RSI_OVERSOLD:
        direction = SHORT
    elif abs(score) >= ENTER_THRESHOLD:
        reasons.append("vetoed: RSI overextended")

    return Decision(direction, round(score, 4), "; ".join(reasons))
