#!/usr/bin/env python3
"""
NGX Daily Automation Pipeline
=============================
Scrapes NGX corporate disclosures + live market data, sends them to the
Anthropic API for formatting into ready-to-post tweet threads, and writes
the output to a dated Markdown file (and optionally Notion).

Author: MintWise NGX (github.com/PlugPortal)

Sources
-------
- Disclosures:  https://abokiforex.app/ngx-stocks/disclosures   (server-rendered HTML)
- Market data:  https://ngxpulse.ng/                            (server-rendered HTML)

The NGX Group site (ngxgroup.com) is JavaScript-gated and not scrapeable
without a headless browser, so we route through the two mirrors above.

Environment variables (set as GitHub Actions secrets)
-----------------------------------------------------
ANTHROPIC_API_KEY   required
NOTION_TOKEN        optional  (only if PUSH_TO_NOTION=1)
NOTION_PAGE_ID      optional  (parent page to append the daily digest under)
PUSH_TO_NOTION      optional  ("1" to enable Notion push)
"""

from __future__ import annotations

import os
import sys
import re
import json
import datetime as dt
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

DISCLOSURES_URL = "https://abokiforex.app/ngx-stocks/disclosures"
MARKET_URL = "https://ngxpulse.ng/"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-opus-4-8"          # swap to claude-sonnet-4-6 for lower cost
MAX_TOKENS = 4000
WAT = ZoneInfo("Africa/Lagos")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

OUTPUT_DIR = "output"


# --------------------------------------------------------------------------- #
# Scraping
# --------------------------------------------------------------------------- #

def fetch(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def today_str() -> str:
    """NGX trading date in '25 Jun 2026' style (matches abokiforex labels)."""
    return dt.datetime.now(WAT).strftime("%-d %b %Y")


CATEGORIES = [
    "Financial Results", "Board Meeting", "AGM Notice", "Corporate Action",
    "Director Dealing", "NGX Notice", "Earnings Forecast",
    "Extra-Ordinary General Meeting (EGM)",
]
# Each filing's header renders on ONE line: "TICKER  Category  25 Jun 2026"
_CAT_ALT = "|".join(re.escape(c) for c in CATEGORIES)
HEADER_RE = re.compile(
    r"^(?P<ticker>[A-Z0-9]+)\s+(?P<cat>" + _CAT_ALT +
    r")\s+(?P<date>\d{1,2}\s+[A-Z][a-z]{2}\s+20\d{2})$"
)


def scrape_disclosures(html: str, target_date: str) -> list[dict]:
    """
    Parse the abokiforex disclosures feed and return only entries whose date
    label matches target_date (e.g. '25 Jun 2026').

    Each filing block is: a header line ('TICKER Category 25 Jun 2026'),
    a bold title line, then bullet detail lines, ending at 'View PDF' or the
    next header. We walk the flattened text and reconstruct each block.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    entries: list[dict] = []
    i = 0
    while i < len(lines):
        m = HEADER_RE.match(lines[i])
        if not m:
            i += 1
            continue

        ticker, category, date = m.group("ticker"), m.group("cat"), m.group("date")
        title = lines[i + 1] if i + 1 < len(lines) else ""
        details: list[str] = []
        j = i + 2
        while j < len(lines):
            ln = lines[j]
            if ln.startswith("View PDF") or HEADER_RE.match(ln):
                break
            details.append(ln.lstrip("*•- ").strip())
            j += 1

        if date == target_date:
            entries.append({
                "ticker": ticker,
                "category": category,
                "date": date,
                "title": title,
                "details": " ".join(d for d in details if d).strip(),
            })
        i = j
    return entries


def scrape_market(html: str) -> dict:
    """Pull the headline market-summary paragraph + FX block from NGX Pulse."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    def grab(pattern: str) -> str | None:
        m = re.search(pattern, text)
        return m.group(0).strip() if m else None

    summary = grab(
        r"The Nigerian Exchange is .*?As of \d{1,2}:\d{2} WAT,.*?20\d{2}\."
    )

    fx = {}
    for cur in ("USD", "EUR", "GBP", "CNY"):
        m = re.search(rf"{cur}\s*/\s*NGN\s*₦([\d,]+\.\d{{2}})", text)
        if m:
            fx[f"{cur}/NGN"] = "₦" + m.group(1)

    return {"summary": summary or "Market summary unavailable.", "fx": fx}


# --------------------------------------------------------------------------- #
# Anthropic formatting
# --------------------------------------------------------------------------- #

def load_system_prompt() -> str:
    with open("system_prompt.txt", "r", encoding="utf-8") as f:
        return f.read()


def call_anthropic(system_prompt: str, user_payload: str) -> str:
    api_key = os.environ["ANTHROPIC_API_KEY"]
    body = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_payload}],
    }
    resp = requests.post(
        ANTHROPIC_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        data=json.dumps(body),
        timeout=120,
    )
    if resp.status_code >= 300:
        # Surface the API's own error message instead of an opaque HTTPError.
        raise RuntimeError(
            f"Anthropic API {resp.status_code}: {resp.text[:600]}"
        )
    data = resp.json()
    text = "".join(
        block["text"] for block in data.get("content", []) if block.get("type") == "text"
    ).strip()
    if not text:
        raise RuntimeError(f"Anthropic returned no text. Raw: {json.dumps(data)[:600]}")
    return text


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

def write_markdown(date_label: str, content: str) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    fname = dt.datetime.now(WAT).strftime("%Y-%m-%d")
    path = os.path.join(OUTPUT_DIR, f"ngx-digest-{fname}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# NGX Daily Digest — {date_label}\n\n")
        f.write(content + "\n")
    return path


def push_to_notion(date_label: str, content: str) -> None:
    """Append the digest as a child page under NOTION_PAGE_ID."""
    token = os.environ.get("NOTION_TOKEN")
    parent = os.environ.get("NOTION_PAGE_ID")
    if not (token and parent):
        print("Notion env not set; skipping Notion push.")
        return

    # Notion blocks cap at 2000 chars; chunk the content.
    chunks = [content[i:i + 1900] for i in range(0, len(content), 1900)]
    children = [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": c}}]},
        }
        for c in chunks
    ]
    body = {
        "parent": {"page_id": parent},
        "properties": {
            "title": {"title": [{"text": {"content": f"NGX Digest — {date_label}"}}]}
        },
        "children": children,
    }
    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        data=json.dumps(body),
        timeout=60,
    )
    if resp.status_code >= 300:
        print(f"Notion push failed: {resp.status_code} {resp.text[:300]}")
    else:
        print("Pushed digest to Notion.")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    date_label = today_str()
    print(f"NGX daily run for {date_label}")

    print("Fetching disclosures...")
    disclosures = scrape_disclosures(fetch(DISCLOSURES_URL), date_label)
    print(f"  found {len(disclosures)} disclosure(s) dated {date_label}")

    print("Fetching market data...")
    market = scrape_market(fetch(MARKET_URL))

    if not disclosures and "unavailable" in market["summary"]:
        print("Nothing to format today. Exiting cleanly.")
        return 0

    payload = json.dumps(
        {"date": date_label, "market": market, "disclosures": disclosures},
        ensure_ascii=False,
        indent=2,
    )

    print("Calling Anthropic API...")
    formatted = call_anthropic(load_system_prompt(), payload)

    path = write_markdown(date_label, formatted)
    print(f"Wrote {path}")

    if os.environ.get("PUSH_TO_NOTION") == "1":
        push_to_notion(date_label, formatted)

    return 0


if __name__ == "__main__":
    sys.exit(main())
