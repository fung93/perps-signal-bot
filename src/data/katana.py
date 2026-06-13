"""Katana perps market data — funding rate and open interest.

Katana's perpetuals are powered by Vertex. This reads per-market tickers over HTTP so the
bot can use them in CI (the Katana MCP is only available to interactive tooling, not the
unattended workflows). Set ``KATANA_API_BASE`` to the perps REST host; if it is unset or a
request fails, callers get ``(None, None)`` and the pipeline runs without funding/OI — the
features table tolerates NULL and the rule engine treats funding as neutral.

Gateways differ in JSON style (camelCase ``currentFundingRate``/``openInterest`` or
snake_case ``funding_rate``/``open_interest``; a list or a symbol-keyed dict). All are
handled, and the tickers payload is fetched once per process.
"""
from __future__ import annotations

import logging

import requests

from .. import config

logger = logging.getLogger(__name__)

_TICKERS_PATH = "/v2/tickers"
_TIMEOUT_SECONDS = 15

# Canonical coin -> Katana perp market symbol.
_MARKETS = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD"}

_UNSET = object()
_tickers_cache = _UNSET   # tickers payload, cached for the process (one fetch per run)


def _fetch_tickers():
    """GET the tickers payload once. None if no base URL is set or the request fails."""
    global _tickers_cache
    if _tickers_cache is not _UNSET:
        return _tickers_cache
    base = config.get_env("KATANA_API_BASE")
    if not base:
        _tickers_cache = None
        return None
    try:
        resp = requests.get(f"{base.rstrip('/')}{_TICKERS_PATH}", timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        _tickers_cache = resp.json()
    except requests.exceptions.RequestException as exc:
        logger.warning("Katana tickers fetch failed (%s); funding/OI -> NULL", type(exc).__name__)
        _tickers_cache = None
    return _tickers_cache


def _find_market(payload, market: str):
    """Find the entry for ``market`` whether tickers come as a list or a symbol-keyed dict."""
    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict) and market in (
                row.get("market"), row.get("ticker_id"), row.get("symbol")
            ):
                return row
        return None
    if isinstance(payload, dict):
        if isinstance(payload.get(market), dict):
            return payload[market]
        for key in ("tickers", "data", "result"):
            if isinstance(payload.get(key), (list, dict)):
                found = _find_market(payload[key], market)
                if found is not None:
                    return found
    return None


def _pick(row: dict, *keys) -> float | None:
    """First present, parseable numeric value among ``keys``."""
    for key in keys:
        value = row.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def fetch_funding_oi(coin: str) -> tuple[float | None, float | None]:
    """Return (funding_rate, open_interest) for ``coin``'s Katana perp, or (None, None)."""
    market = _MARKETS.get(coin)
    if market is None:
        return None, None
    payload = _fetch_tickers()
    if payload is None:
        return None, None
    row = _find_market(payload, market)
    if row is None:
        logger.warning("Katana ticker for %s not found in payload", market)
        return None, None
    funding = _pick(row, "currentFundingRate", "funding_rate", "fundingRate", "lastFundingRate")
    oi = _pick(row, "openInterest", "open_interest")
    return funding, oi
