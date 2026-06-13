"""Load the latest feature snapshot (+ close, ATR) for a coin/timeframe from Neon.

Shared by the signal check and the daily brief. Takes an open cursor so the caller controls
the connection.
"""
from __future__ import annotations

from datetime import datetime

from .rules import Snapshot

_LATEST = """
    SELECT f.ts, c.close, f.ema_fast, f.ema_slow, f.ema_long,
           f.rsi, f.macd, f.macd_signal, f.vol_z, f.funding, f.atr
    FROM features f
    JOIN candles c
      ON c.coin = f.coin AND c.timeframe = f.timeframe AND c.open_time = f.ts
    WHERE f.coin = %s AND f.timeframe = %s
    ORDER BY f.ts DESC
    LIMIT 1
"""


def _f(value) -> float | None:
    return float(value) if value is not None else None


def load_latest(cur, coin: str, timeframe: str) -> tuple[datetime, Snapshot, float | None] | None:
    """Return (candle_ts, Snapshot, atr) for the most recent feature row, or None."""
    cur.execute(_LATEST, (coin, timeframe))
    row = cur.fetchone()
    if row is None:
        return None
    ts, close, ema_fast, ema_slow, ema_long, rsi, macd, macd_signal, vol_z, funding, atr = row
    snap = Snapshot(
        close=_f(close),
        ema_fast=_f(ema_fast),
        ema_slow=_f(ema_slow),
        ema_long=_f(ema_long),
        rsi=_f(rsi),
        macd=_f(macd),
        macd_signal=_f(macd_signal),
        vol_z=_f(vol_z),
        funding=_f(funding),
    )
    return ts, snap, _f(atr)
