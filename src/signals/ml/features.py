"""ML dataset construction: stationary features + forward-looking labels.

Labels are self-supervised from the historical price path: for each candle, simulate a long
with the same ATR stop and R-multiple target the sizing module uses, and label whether it
would have hit the target before the stop (or closed up) within the same UTC day. Shared
feature engineering keeps training and prediction consistent.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ...indicators import compute
from ..sizing import ATR_MULT, REWARD_RISK

FEATURE_COLUMNS = [
    "f_rsi", "f_px_fast", "f_fast_slow", "f_slow_long", "f_macd", "f_macd_hist", "f_vol_z",
]


def engineer(feats: pd.DataFrame, close: pd.Series) -> pd.DataFrame:
    """ATR-normalised, mostly-stationary features from indicator columns + close."""
    atr = feats["atr"]
    return pd.DataFrame({
        "f_rsi": feats["rsi"] / 100.0,
        "f_px_fast": (close - feats["ema_fast"]) / atr,
        "f_fast_slow": (feats["ema_fast"] - feats["ema_slow"]) / atr,
        "f_slow_long": (feats["ema_slow"] - feats["ema_long"]) / atr,
        "f_macd": feats["macd"] / atr,
        "f_macd_hist": (feats["macd"] - feats["macd_signal"]) / atr,
        "f_vol_z": feats["vol_z"],
    })


def engineer_snapshot(snap, atr: float | None) -> list[float] | None:
    """Feature vector for a single live Snapshot (order matches FEATURE_COLUMNS), or None."""
    vals = (snap.close, snap.ema_fast, snap.ema_slow, snap.ema_long,
            snap.rsi, snap.macd, snap.macd_signal, snap.vol_z, atr)
    if any(v is None for v in vals) or atr <= 0:
        return None
    return [
        snap.rsi / 100.0,
        (snap.close - snap.ema_fast) / atr,
        (snap.ema_fast - snap.ema_slow) / atr,
        (snap.ema_slow - snap.ema_long) / atr,
        snap.macd / atr,
        (snap.macd - snap.macd_signal) / atr,
        snap.vol_z,
    ]


def label_long(df: pd.DataFrame, atr: pd.Series) -> pd.Series:
    """1 if a long entered at this close hits target before stop (or closes up) same day."""
    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    days = [t.date() for t in df["open_time"]]
    a = atr.to_numpy()
    n = len(df)
    y = np.full(n, np.nan)
    for i in range(n):
        if np.isnan(a[i]) or a[i] <= 0:
            continue
        entry = close[i]
        sl = entry - ATR_MULT * a[i]
        tp = entry + REWARD_RISK * ATR_MULT * a[i]
        outcome = np.nan
        j = i + 1
        while j < n and days[j] == days[i]:
            if low[j] <= sl:
                outcome = 0.0
                break
            if high[j] >= tp:
                outcome = 1.0
                break
            j += 1
        else:
            # No stop/target hit. If the day rolled over (j<n) it's complete -> label by
            # last same-day close; if data simply ran out, leave NaN (incomplete day).
            if j < n:
                outcome = 1.0 if close[j - 1] > entry else 0.0
        y[i] = outcome
    return pd.Series(y, index=df.index)


def build_dataset(candles: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """From one coin's candle history return (X, y, open_time) with NaN rows dropped."""
    df = candles.sort_values("open_time").reset_index(drop=True)
    feats = compute.compute_features(df)
    frame = engineer(feats, df["close"])
    frame["__y"] = label_long(df, feats["atr"]).to_numpy()
    frame["__ts"] = df["open_time"].to_numpy()
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna()
    return frame[FEATURE_COLUMNS], frame["__y"], frame["__ts"]
