"""Binance public market data — OHLCV candles for the Perps Signal Bot.

Pulls klines from Binance's public REST API (no key needed for market data) — the
global price-discovery feed — and maps them to the bot's canonical (coin, timeframe)
space. Used by both the 15-minute collector and the one-time history backfill.

Only **closed** candles are returned; the in-progress candle is dropped.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

# Public market-data hosts, tried in order. The read-only mirror
# data-api.binance.vision is reachable where api.binance.com is geo-blocked — notably
# US-based CI runners, which get HTTP 451 from the main host. Set BINANCE_API_BASE to
# force a single host.
_DEFAULT_HOSTS = ("https://data-api.binance.vision", "https://api.binance.com")
_active_base: str | None = None   # working host, remembered for this process

_KLINES_PATH = "/api/v3/klines"
_MAX_LIMIT = 1000          # Binance hard cap of klines per request
_TIMEOUT_SECONDS = 15
_PAGE_PAUSE_SECONDS = 0.25  # be gentle on the public API when paging a backfill

# Canonical coin -> Binance spot symbol (USDT pairs = global price discovery).
_SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"}

# Binance uses these exact interval strings, so our timeframes map 1:1.
_INTERVALS = {"15m": "15m", "1h": "1h", "4h": "4h"}


@dataclass(frozen=True)
class Candle:
    """One OHLCV candle, keyed by (coin, timeframe, open_time) like the DB table."""

    coin: str
    timeframe: str
    open_time: datetime   # candle open, timezone-aware UTC
    open: float
    high: float
    low: float
    close: float
    volume: float


def _resolve(coin: str, timeframe: str) -> tuple[str, str]:
    """Translate canonical coin/timeframe to Binance symbol/interval, validating both."""
    try:
        return _SYMBOLS[coin], _INTERVALS[timeframe]
    except KeyError as exc:
        raise ValueError(f"Unsupported coin/timeframe: {coin} {timeframe}") from exc


def _candidate_hosts() -> tuple[str, ...]:
    """Hosts to try, in order. BINANCE_API_BASE (if set) forces a single host."""
    override = os.environ.get("BINANCE_API_BASE")
    if override:
        return (override.rstrip("/"),)
    return _DEFAULT_HOSTS


def _get(path: str, params: dict) -> requests.Response:
    """GET ``path`` from the first reachable Binance host, falling back on failure.

    Connection errors and HTTP 451 (geo-block) advance to the next host; the first host
    that works is remembered for the rest of the process.
    """
    global _active_base
    hosts = _candidate_hosts()
    ordered = hosts
    if _active_base in hosts:
        ordered = (_active_base, *(h for h in hosts if h != _active_base))

    last_error: Exception | None = None
    for base in ordered:
        try:
            resp = requests.get(f"{base}{path}", params=params, timeout=_TIMEOUT_SECONDS)
        except requests.exceptions.RequestException as exc:
            last_error = exc
            logger.warning(
                "Binance host %s unreachable (%s); trying next", base, type(exc).__name__
            )
            continue
        if resp.status_code == 451:  # geo-blocked — try the mirror
            last_error = requests.HTTPError(f"451 geo-block from {base}")
            logger.warning("Binance host %s returned 451 (geo-block); trying next", base)
            continue
        resp.raise_for_status()
        _active_base = base
        return resp
    raise ConnectionError(f"All Binance hosts failed; last error: {last_error}")


def _request_klines(
    symbol: str,
    interval: str,
    *,
    limit: int,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> list[list]:
    """Raw klines request. Returns Binance's list-of-arrays payload."""
    params: dict[str, object] = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_ms is not None:
        params["startTime"] = start_ms
    if end_ms is not None:
        params["endTime"] = end_ms
    return _get(_KLINES_PATH, params).json()


def _to_candle(coin: str, timeframe: str, kline: list) -> Candle:
    """Map one Binance kline array to a Candle.

    Kline layout: [open_time_ms, open, high, low, close, volume, close_time_ms, ...].
    """
    open_ms = int(kline[0])
    return Candle(
        coin=coin,
        timeframe=timeframe,
        open_time=datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc),
        open=float(kline[1]),
        high=float(kline[2]),
        low=float(kline[3]),
        close=float(kline[4]),
        volume=float(kline[5]),
    )


def _closed(kline: list, now_ms: int) -> bool:
    """True if the candle's close time has passed (i.e. it is final)."""
    return int(kline[6]) <= now_ms


def fetch_recent(coin: str, timeframe: str, *, limit: int = 200) -> list[Candle]:
    """Fetch the most recent **closed** candles for one coin/timeframe.

    A generous overlap (default 200 candles) lets the 15-minute collector self-heal any
    gaps from missed runs — re-writing existing rows is a harmless upsert.
    """
    symbol, interval = _resolve(coin, timeframe)
    raw = _request_klines(symbol, interval, limit=min(limit, _MAX_LIMIT))
    now_ms = int(time.time() * 1000)
    return [_to_candle(coin, timeframe, k) for k in raw if _closed(k, now_ms)]


def fetch_range(
    coin: str, timeframe: str, start: datetime, end: datetime | None = None
) -> list[Candle]:
    """Fetch every **closed** candle in [start, end], paging past the 1000-row cap.

    Used by the backfill. ``start``/``end`` should be timezone-aware UTC.
    """
    symbol, interval = _resolve(coin, timeframe)
    end = end or datetime.now(timezone.utc)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    now_ms = int(time.time() * 1000)

    out: list[Candle] = []
    cursor = start_ms
    while cursor < end_ms:
        raw = _request_klines(
            symbol, interval, limit=_MAX_LIMIT, start_ms=cursor, end_ms=end_ms
        )
        if not raw:
            break
        out.extend(_to_candle(coin, timeframe, k) for k in raw if _closed(k, now_ms))
        next_cursor = int(raw[-1][0]) + 1  # advance just past the last open_time
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(raw) < _MAX_LIMIT:
            break  # last page — no more data in range
        logger.debug("%s %s: paged to %d candles", coin, timeframe, len(out))
        time.sleep(_PAGE_PAUSE_SECONDS)
    return out
