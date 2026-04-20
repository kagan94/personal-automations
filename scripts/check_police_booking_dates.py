#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.request
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv
from utils.telegram import require_telegram_env, send_msg_to_telegram

load_dotenv(_REPO_ROOT / ".env")
load_dotenv(Path.cwd() / ".env", override=True)

# No certificate verification
_SSL_CTX = ssl._create_unverified_context()

DEFAULT_CUTOFF = "2026-05-27"

def _parse_schedule_dates_payload(raw: str) -> list[date]:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("Expected JSON array")
    out: list[date] = []
    for item in data:
        if isinstance(item, dict) and "date" in item:
            out.append(date.fromisoformat(str(item["date"])))
    return out


def fetch_earliest_possible_booking_date(url: str) -> tuple[date | None, list[date]]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/134.0.0.0 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(req, timeout=90, context=_SSL_CTX) as resp:
        raw = resp.read().decode()
    print(f"API response: {raw}", file=sys.stderr)
    parsed = _parse_schedule_dates_payload(raw)
    earliest: date | None = min(parsed) if parsed else None
    if earliest is not None:
        print( f"Earliest available booking date (from API): {earliest.isoformat()}", file=sys.stderr, )
        print( f"Latest available booking date (from API): {max(parsed).isoformat()}", file=sys.stderr, )
    return earliest, parsed


def main() -> int:
    url = os.environ.get("POLICE_BOOKING_AVAILABLE_TIMES_URL")
    assert url, "POLICE_BOOKING_AVAILABLE_TIMES_URL must be set."
    token, chat_id = require_telegram_env()

    cutoff_s = os.environ.get("EARLIEST_ACCEPTABLE_DATE", DEFAULT_CUTOFF)
    cutoff = date.fromisoformat(cutoff_s)

    _, parsed = fetch_earliest_possible_booking_date(url)
    earlier = [d for d in parsed if d < cutoff]

    if not earlier:
        listed = ", ".join(d.isoformat() for d in sorted(parsed))
        print(f"No dates earlier than {cutoff_s}. Got following dates: {listed or '(none)'}")
        return 0

    earlier.sort()
    msg = (
        "Long-term permit booking at Police office: found earlier slot than "
        f"{cutoff_s}: {', '.join(d.isoformat() for d in earlier)}"
    )
    print(msg)

    send_msg_to_telegram(token, chat_id, msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
