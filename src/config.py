"""Central configuration for the Perps Signal Bot.

Environment-driven settings live here so secrets and tunables sit in one place.
Values are read from the process environment. In local dev a ``.env`` file is loaded
automatically if ``python-dotenv`` is installed; GitHub Actions injects env vars directly,
so the dependency is optional.
"""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

# Optional local convenience: load .env if python-dotenv is present. No-op otherwise.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ModuleNotFoundError:
    pass


# --- Project scope (locked: BTC / ETH / SOL, intraday) ---
COINS: tuple[str, ...] = ("BTC", "ETH", "SOL")
TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h")
SIGNAL_TIMEFRAME: str = "1h"   # timeframe the rule engine evaluates for entries

# --- Risk envelope (hard rules — see CLAUDE.md) ---
MARGIN_USD: float = 100.0
MAX_LEVERAGE: int = 5
MAX_NOTIONAL_USD: float = MARGIN_USD * MAX_LEVERAGE  # $500


# --- Operational controls (Phase 5 hardening) ---
def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


KILL_SWITCH: bool = _flag("KILL_SWITCH")               # when true, emit no signals at all
MAX_SIGNALS_PER_DAY: int = _int_env("MAX_SIGNALS_PER_DAY", 3)  # cap tradeable signals / day
# BTC/ETH/SOL move together, so simultaneous same-direction entries are one position at N
# times the size, not N independent bets (2026-09-03: three LONGs on one candle, all stopped
# out). Capping at 2 cut max drawdown 23% for identical expectancy in the backtest.
MAX_SIGNALS_PER_BAR: int = _int_env("MAX_SIGNALS_PER_BAR", 2)  # same-direction cap / candle
CANDLE_RETENTION_DAYS: int = _int_env("CANDLE_RETENTION_DAYS", 90)  # candles rolling window (prune.py)
READINESS_MIN_TRADES: int = _int_env("READINESS_MIN_TRADES", 30)  # graded trades before the "data ready" alert
ADX_MIN: float = float(os.environ.get("ADX_MIN") or 30)  # min ADX trend strength to emit (backtest-validated)


# --- Active hours (local time): only emit signals while you're around to act ---
LOCAL_TZ: str = "Asia/Kuala_Lumpur"
ACTIVE_HOUR_START: int = 8     # inclusive — 08:00 local
ACTIVE_HOUR_END: int = 22      # exclusive — 22:00 local


def in_active_hours(now: datetime | None = None) -> bool:
    """True if the current (or given) time falls within the local active window."""
    tz = ZoneInfo(LOCAL_TZ)
    moment = now.astimezone(tz) if now else datetime.now(tz)
    return ACTIVE_HOUR_START <= moment.hour < ACTIVE_HOUR_END


def require_env(name: str) -> str:
    """Return a required environment variable, or raise with a clear message."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Set it in your environment or .env (see .env.example)."
        )
    return value


def get_env(name: str, default: str | None = None) -> str | None:
    """Return an optional environment variable, or ``default`` if unset."""
    return os.environ.get(name, default)
