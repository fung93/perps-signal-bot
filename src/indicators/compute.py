"""Technical indicators for the Perps Signal Bot, computed with pandas.

Pure functions over price/volume Series, plus :func:`compute_features` which builds the
indicator columns for a candle frame. Implemented in-house (no pandas-ta) so behaviour is
identical on Python 3.11 (local) and 3.12 (CI).

Wilder's smoothing (RSI, ATR) uses an EWM with alpha = 1/length; EMAs and MACD use
standard spans with adjust=False.
"""
from __future__ import annotations

import pandas as pd

# Trend EMAs per the build plan; MACD/RSI/ATR use standard lengths.
RSI_LEN = 14
EMA_FAST = 20
EMA_SLOW = 50
EMA_LONG = 200
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
ATR_LEN = 14
VOL_Z_LEN = 20


def ema(series: pd.Series, length: int) -> pd.Series:
    """Exponential moving average (standard, adjust=False)."""
    return series.ewm(span=length, min_periods=length, adjust=False).mean()


def rsi(close: pd.Series, length: int = RSI_LEN) -> pd.Series:
    """Wilder's Relative Strength Index."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(
    close: pd.Series, fast: int = MACD_FAST, slow: int = MACD_SLOW, signal: int = MACD_SIGNAL
) -> tuple[pd.Series, pd.Series]:
    """MACD line (EMA fast - EMA slow) and its signal line."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, min_periods=signal, adjust=False).mean()
    return macd_line, signal_line


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = ATR_LEN) -> pd.Series:
    """Average True Range (Wilder's), used for stop-loss sizing."""
    prev_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return true_range.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()


def volume_zscore(volume: pd.Series, length: int = VOL_Z_LEN) -> pd.Series:
    """Rolling z-score of volume over ``length`` candles."""
    mean = volume.rolling(length).mean()
    std = volume.rolling(length).std(ddof=0)
    return (volume - mean) / std


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute indicator columns for an ascending-by-open_time candle frame.

    ``df`` must have numeric columns: high, low, close, volume. Returns a frame (same row
    order and index as ``df``) with rsi, ema_fast/slow/long, macd, macd_signal, atr, vol_z.
    Early rows are NaN until each indicator has enough warmup.
    """
    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]
    macd_line, signal_line = macd(close)
    return pd.DataFrame(
        {
            "rsi": rsi(close),
            "ema_fast": ema(close, EMA_FAST),
            "ema_slow": ema(close, EMA_SLOW),
            "ema_long": ema(close, EMA_LONG),
            "macd": macd_line,
            "macd_signal": signal_line,
            "atr": atr(high, low, close),
            "vol_z": volume_zscore(volume),
        }
    )
