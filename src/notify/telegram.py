"""Telegram alerts for the Perps Signal Bot.

Sends messages via the Telegram Bot API (a simple HTTPS POST), which fits the stateless
GitHub Actions cron model better than a long-running bot process. Reads ``TELEGRAM_TOKEN``
and ``TELEGRAM_CHAT_ID`` from the environment (see :mod:`src.config`).
"""
from __future__ import annotations

import logging

import requests

from .. import config

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"
_TIMEOUT_SECONDS = 15


def send_message(
    text: str,
    *,
    parse_mode: str | None = "Markdown",
    disable_preview: bool = True,
) -> dict:
    """Send ``text`` to the configured chat and return the Telegram API response JSON.

    Pass ``parse_mode=None`` to send plain text (safest for arbitrary dynamic content).
    Raises ``requests.HTTPError`` if the API call fails.
    """
    token = config.require_env("TELEGRAM_TOKEN")
    chat_id = config.require_env("TELEGRAM_CHAT_ID")
    payload: dict[str, object] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": disable_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    resp = requests.post(
        f"{_API_BASE}/bot{token}/sendMessage",
        json=payload,
        timeout=_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.json()


def format_signal_card(coin: str, decision, sizing) -> str:
    """Render a tradeable signal as a Telegram card (``decision`` and ``sizing`` results)."""
    side = "🟢 LONG" if decision.direction == "LONG" else "🔴 SHORT"
    return (
        f"*{coin} · {side}*  ({config.SIGNAL_TIMEFRAME})\n"
        f"Entry: `{sizing.entry:,.2f}`\n"
        f"TP: `{sizing.take_profit:,.2f}`    SL: `{sizing.stop_loss:,.2f}`\n"
        f"Leverage: {sizing.leverage:.0f}x   Margin: ${sizing.margin_usd:,.0f} "
        f"(notional ${sizing.notional_usd:,.0f})\n"
        f"Risk if stopped: ${sizing.risk_usd:,.2f}\n"
        f"_Why:_ {decision.rationale} (score {decision.score:+.1f})\n"
        f"⏱ Intraday — close by EOD. Research signal, not financial advice."
    )


def format_daily_brief(rows: list[dict], fng: int | None) -> str:
    """Render the daily market brief from per-coin summary rows."""
    lines = [
        f"*Daily brief* · {config.SIGNAL_TIMEFRAME} regime",
        f"Fear & Greed: {fng if fng is not None else 'n/a'}",
        "",
    ]
    for r in rows:
        lines.append(
            f"*{r['coin']}* — {r['regime']}, bias {r['bias']}, RSI {r['rsi']:.0f}\n"
            f"  EMA20 `{r['ema_fast']:,.2f}` / EMA50 `{r['ema_slow']:,.2f}` / "
            f"EMA200 `{r['ema_long']:,.2f}`"
        )
    lines.append("")
    lines.append("_Research only, not financial advice._")
    return "\n".join(lines)
