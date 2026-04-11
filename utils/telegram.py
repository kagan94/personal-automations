"""Telegram Bot API sendMessage helper."""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request

# Match police booking script (some environments have TLS issues with verify)
_SSL_CTX = ssl._create_unverified_context()

# https://core.telegram.org/bots/api#sendmessage
_TELEGRAM_MAX_MESSAGE_LENGTH = 4096
_CONTINUATION_PREFIX = "(continued)\n"


def require_telegram_env() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    assert token and chat_id, "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set."
    return token, chat_id


def _split_for_telegram(text: str) -> list[str]:
    """Split text into parts that each fit Telegram's message length limit."""
    if len(text) <= _TELEGRAM_MAX_MESSAGE_LENGTH:
        return [text]
    more_budget = _TELEGRAM_MAX_MESSAGE_LENGTH - len(_CONTINUATION_PREFIX)
    chunks: list[str] = []
    rest = text
    while rest:
        budget = _TELEGRAM_MAX_MESSAGE_LENGTH if not chunks else more_budget
        if len(rest) <= budget:
            chunks.append(_CONTINUATION_PREFIX + rest if chunks else rest)
            break
        piece = rest[:budget]
        nl = piece.rfind("\n")
        if nl > budget * 2 // 3:
            cut = nl + 1
        else:
            cut = budget
        segment = rest[:cut]
        rest = rest[cut:]
        chunks.append(segment if not chunks else _CONTINUATION_PREFIX + segment)
    return chunks


def send_msg_to_telegram(
    token: str,
    chat_id: str,
    text: str,
    *,
    timeout_s: float = 30.0,
    parse_mode: str | None = None,
) -> None:
    api = f"https://api.telegram.org/bot{token}/sendMessage"
    for part in _split_for_telegram(text):
        payload: dict[str, str] = {"chat_id": chat_id, "text": part}
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            api,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s, context=_SSL_CTX) as resp:
                resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            raise RuntimeError(f"Telegram API HTTP {e.code}: {detail}") from e
