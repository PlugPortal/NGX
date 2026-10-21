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
- Disclosures:  https://abokiforex.app/ngx-stocks/disclosures
- Market wrap:  https://koboterminal.com/ngx-market-wrap/YYYY-MM-DD (formerly ngxpulse.ng)
- FX:           https://abokiforex.app/cbn  (CBN official rates)
Parsers live in ngx_sources.py.

The NGX Group site (ngxgroup.com) is JavaScript-gated and not scrapeable
without a headless browser, so we route through the mirrors above.

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

import ngx_sources as src

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-opus-4-8"          # swap to claude-sonnet-4-6 for lower cost
MAX_TOKENS = 4000
WAT = ZoneInfo("Africa/Lagos")

OUTPUT_DIR = "output"


# --------------------------------------------------------------------------- #
# Scraping (shared parsers live in ngx_sources.py)
# --------------------------------------------------------------------------- #

def today_str() -> str:
    """NGX trading date in '25 Jun 2026' style, used in the digest title."""
    return dt.datetime.now(WAT).strftime("%-d %b %Y")


def todays_disclosures(today: dt.date) -> list[dict]:
    """Today's filings from the Aboki Forex feed, in the shape the prompt expects."""
    return [
        {"ticker": d["ticker"], "category": d["category"], "date": today_str(),
         "title": d["title"], "details": " ".join((d.get("summary") or "").split())}
        for d in src.scrape_disclosures_feed() if d["date"] == today.isoformat()
    ]


def todays_market(today: dt.date) -> dict:
    """Today's market wrap (Kobo Terminal) and CBN official FX (Aboki Forex)."""
    try:
        summary = src.scrape_market_wrap(today).get("summary")
    except Exception as e:
        print(f"  market wrap failed: {e}")
        summary = None
    try:
        fx = src.scrape_cbn_fx()
    except Exception as e:
        print(f"  FX failed: {e}")
        fx = {}
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

    today = dt.datetime.now(WAT).date()

    print("Fetching disclosures...")
    disclosures = todays_disclosures(today)
    print(f"  found {len(disclosures)} disclosure(s) dated {date_label}")

    print("Fetching market data...")
    market = todays_market(today)
    print(f"  market wrap: {'found' if 'unavailable' not in market['summary'] else 'not published yet'}; "
          f"FX: {', '.join(market['fx']) or 'none'}")

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
