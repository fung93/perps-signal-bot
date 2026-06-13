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
