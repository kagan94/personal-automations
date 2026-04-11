#!/usr/bin/env python3
"""Fetch and parse Trip.ee cheap flight offers (HTML)."""
from __future__ import annotations

import html
import re
import ssl
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT)) 

from bs4 import BeautifulSoup, Tag
from dotenv import load_dotenv
from utils.telegram import require_telegram_env, send_msg_to_telegram

load_dotenv(_REPO_ROOT / ".env")
load_dotenv(Path.cwd() / ".env", override=True)

_SSL_CTX = ssl.create_default_context()

PAGE_URLS = (
    "https://trip.ee/odavad-lennupiletid",
    "https://trip.ee/odavad-lennupiletid?filter=&page=2",
)
FETCH_TIMEOUT_S = 60.0

# Case-insensitive substring match against any destination tag; row is dropped if any tag matches.
EXCLUDED_DESTINATIONS: set[str] = {
    "Montenegro",
    "London",
}

# Rows with a parsed € price strictly above this are dropped (unknown price is kept).
MAX_OFFER_PRICE_EUR = 600


def _route_words_from_text(text: str) -> str:
    """Keep space-separated tokens whose first letter is uppercase; skip hyphenated words (e.g. Edasi-tagasi)."""
    kept: list[str] = []
    for raw in text.split():
        if "-" in raw:
            continue
        w = raw.strip(".,;:!?()[]\"'")
        if w and w[0].isupper():
            kept.append(raw)
    return " ".join(kept)


def _route_and_price_from_heading(heading: str) -> tuple[str, str | None, int | None]:
    """Return route text, € token (if any), and numeric price for sorting."""
    title = " ".join(heading.split())
    tokens = title.split()
    price_idx: int | None = None
    price_word: str | None = None
    for i, raw in enumerate(tokens):
        if "€" in raw:
            price_idx = i
            price_word = raw.strip(".,;:!?()[]\"'")
            break
    before_price = " ".join(tokens[:price_idx]) if price_idx is not None else title
    route = _route_words_from_text(before_price)
    if not route:
        route = before_price or title
    price: int | None = None
    if price_word:
        dm = re.search(r"\d+", price_word)
        if dm:
            price = int(dm.group(0))
    return route, price_word, price


@dataclass(frozen=True)
class FlightOfferRow:
    heading: str
    href: str
    """Path or absolute URL of the offer link."""
    destinations: tuple[str, ...]
    """Texts from destination tags (e.g. Kreeta, Kreeka), in page order."""

    @property
    def destination(self) -> str:
        """First destination tag, or empty if none."""
        return self.destinations[0] if self.destinations else ""


def _sort_price_eur(row: FlightOfferRow) -> int:
    _, _, price = _route_and_price_from_heading(row.heading)
    return price if price is not None else 10**9


def _row_has_excluded_destination(row: FlightOfferRow) -> bool:
    needles = {n.casefold() for n in EXCLUDED_DESTINATIONS}
    for tag in row.destinations:
        t = tag.casefold()
        if any(n in t for n in needles):
            return True
    return False


def _row_price_over_max(row: FlightOfferRow) -> bool:
    _, _, price = _route_and_price_from_heading(row.heading)
    return price is not None and price > MAX_OFFER_PRICE_EUR


def _route_from_tallinn(row: FlightOfferRow) -> bool:
    route, _, _ = _route_and_price_from_heading(row.heading)
    return route.casefold().startswith("tallinnast")


def _route_from_riga(row: FlightOfferRow) -> bool:
    route, _, _ = _route_and_price_from_heading(row.heading)
    return route.casefold().startswith("riiast")


def _telegram_line(i: int, row: FlightOfferRow) -> str:
    route, price_word, _ = _route_and_price_from_heading(row.heading)
    url = _trip_offer_url(row.href)
    safe_route = html.escape(route)
    safe_href = html.escape(url, quote=True)
    link_html = f'<a href="{safe_href}">link</a>'
    if price_word:
        safe_price = html.escape(price_word)
        return f"{i}. {safe_route}, {safe_price}, {link_html}"
    return f"{i}. {safe_route}, {link_html}"


def _telegram_body(
    rows_tallinn: list[FlightOfferRow],
    rows_riga: list[FlightOfferRow],
    rows_other: list[FlightOfferRow],
) -> str:
    """Tallinn, Riga (Riiast), then other departures; numbering restarts in each section."""
    chunks: list[str] = []

    def append_section(title: str, section_rows: list[FlightOfferRow]) -> None:
        if not section_rows:
            return
        if chunks:
            chunks.append("")
        chunks.append(html.escape(title))
        chunks.append(
            "\n".join(_telegram_line(i, r) for i, r in enumerate(section_rows, start=1)),
        )

    append_section("Tallinn", rows_tallinn)
    append_section("Riga", rows_riga)
    append_section("Other:", rows_other)
    return "\n".join(chunks)


def _class_tokens(class_attr: Any) -> list[str]:
    if not class_attr:
        return []
    if isinstance(class_attr, str):
        return class_attr.split()
    return list(class_attr)


def _has_class_prefix(tokens: list[str], prefix: str) -> bool:
    return any(prefix in t for t in tokens)


def _trip_offer_url(href: str) -> str:
    href = href.strip()
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return urljoin("https://trip.ee/", href)


def fetch_html(url: str, timeout_s: float = 60.0) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/134.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "et-EE,et;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_s, context=_SSL_CTX) as resp:
        return resp.read().decode()


def _parse_flight_offer_block(block: Tag) -> FlightOfferRow | None:
    title_a = block.find(
        "a",
        class_=lambda c: _has_class_prefix(_class_tokens(c), "FlightOfferRow_Title__"),
    )
    if not title_a or not title_a.get("href"):
        return None
    heading = title_a.get_text(strip=True)
    href = str(title_a["href"]).strip()
    tags_div = block.find(
        class_=lambda c: _has_class_prefix(_class_tokens(c), "FlightOfferRow_Tags__"),
    )
    dest_names: list[str] = []
    if tags_div:
        for a in tags_div.find_all("a"):
            if not _has_class_prefix(_class_tokens(a.get("class")), "Tag_Destination__"):
                continue
            span = a.find(
                class_=lambda c: _has_class_prefix(_class_tokens(c), "Tag_Title__"),
            )
            text = span.get_text(strip=True) if span else a.get_text(strip=True)
            if text:
                dest_names.append(text)
    return FlightOfferRow(
        heading=heading,
        href=href,
        destinations=tuple(dest_names),
    )


def parse_flight_offer_rows(html: str) -> list[FlightOfferRow]:
    """Extract offer rows from Trip.ee HTML (CSS module hashes may change; prefixes are stable)."""
    soup = BeautifulSoup(html, "html.parser")
    blocks = soup.find_all(
        class_=lambda c: _has_class_prefix(_class_tokens(c), "FlightOfferRow_Content__"),
    )
    rows: list[FlightOfferRow] = []
    for block in blocks:
        row = _parse_flight_offer_block(block)
        if row is not None:
            rows.append(row)
    return rows


def main() -> int:
    token, chat_id = require_telegram_env()

    rows: list[FlightOfferRow] = []
    for url in PAGE_URLS:
        rows.extend(parse_flight_offer_rows(fetch_html(url, timeout_s=FETCH_TIMEOUT_S)))

    rows.sort(key=lambda r: (_sort_price_eur(r), r.heading))
    rows = [
        r
        for r in rows
        if not _row_has_excluded_destination(r) and not _row_price_over_max(r)
    ]

    if not rows:
        send_msg_to_telegram(token, chat_id, "Trip.ee: no flight offers parsed.")
        print("Sent empty notice to Telegram.", file=sys.stderr)
        return 0

    rows_tallinn = [r for r in rows if _route_from_tallinn(r)]
    rows_riga = [r for r in rows if _route_from_riga(r)]
    rows_other = [r for r in rows if not _route_from_tallinn(r) and not _route_from_riga(r)]
    body = _telegram_body(rows_tallinn, rows_riga, rows_other)
    header = html.escape("Trip.ee cheap flight offers")
    send_msg_to_telegram(
        token,
        chat_id,
        f"{header}\n{body}",
        parse_mode="HTML",
    )
    print(f"Sent {len(rows)} flight offers to Telegram.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
