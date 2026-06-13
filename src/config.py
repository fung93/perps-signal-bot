"""Central configuration for the Perps Signal Bot.

Environment-driven settings live here so secrets and tunables sit in one place.
Values are read from the process environment. In local dev a ``.env`` file is loaded
automatically if ``python-dotenv`` is installed; GitHub Actions injects env vars directly,
so the dependency is optional.
"""
from __future__ import annotations

import os

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
MAX_SIGNALS_PER_DAY: int = _int_env("MAX_SIGNALS_PER_DAY", 3)  # cap tradeable signals / UTC day


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
