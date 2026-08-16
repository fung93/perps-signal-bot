"""Sentiment inputs for the Perps Signal Bot.

Currently the Fear & Greed Index (alternative.me). Funding rate and open interest are
stubbed pending a venue data source — see :func:`funding_oi`.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import requests

from . import katana

logger = logging.getLogger(__name__)

_FNG_URL = "https://api.alternative.me/fng/"
_TIMEOUT_SECONDS = 15


def fear_greed_history(limit: int = 0) -> dict[date, int]:
    """Return ``{UTC date: Fear & Greed value 0-100}``. ``limit=0`` fetches all history.

    Sentiment is enrichment, not core: an API timeout, rate-limit, or maintenance page must
    never take down candle ingestion or signal generation. On any failure this logs and
    returns ``{}``, leaving ``features.fng`` NULL for the affected rows.
    """
    try:
        resp = requests.get(_FNG_URL, params={"limit": limit}, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except (requests.exceptions.RequestException, ValueError) as exc:
        logger.warning("Fear & Greed fetch failed (%s); fng -> NULL", type(exc).__name__)
        return {}

    history: dict[date, int] = {}
    for item in data:
        try:
            day = datetime.fromtimestamp(int(item["timestamp"]), tz=timezone.utc).date()
            history[day] = int(item["value"])
        except (KeyError, TypeError, ValueError):
            continue  # skip malformed entries rather than failing the run
    return history


def latest_fear_greed() -> int | None:
    """Most recent Fear & Greed value, or None if unavailable."""
    history = fear_greed_history(limit=1)
    return next(iter(history.values()), None)


def funding_oi(coin: str) -> tuple[float | None, float | None]:
    """Funding rate and open interest for ``coin``'s Katana perp (via :mod:`src.data.katana`).

    Sourced from Katana's mainnet perps REST (override the host via ``KATANA_API_BASE``).
    Returns ``(None, None)`` if the request fails — the features table tolerates NULL and
    the rule engine treats funding as neutral.
    """
    return katana.fetch_funding_oi(coin)
