#!/usr/bin/env python3
"""
NGX Daily Automation Pipeline
=============================
Scrapes NGX corporate disclosures + the market wrap + CBN FX, formats them
into ready-to-post tweet text with fixed templates (ngx_digest.py — no AI,
no API key), and writes a dated Markdown file (and optionally Notion).

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
import ngx_digest

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

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
    return [d for d in src.scrape_disclosures_feed() if d["date"] == today.isoformat()]


def todays_market(today: dt.date) -> dict:
    """Today's market wrap (Kobo Terminal, only if dated today) and CBN official FX."""
    try:
        wrap = src.scrape_market_wrap(today)
    except Exception as e:
        print(f"  market wrap failed: {e}")
        wrap = {}
    try:
        fx = src.scrape_cbn_fx()
    except Exception as e:
        print(f"  FX failed: {e}")
        fx = {}
    return {"wrap": wrap, "fx": fx}


def company_names() -> dict[str, str]:
    try:
        return {r["ticker"]: r["name"] for r in src.scrape_prices()}
    except Exception:
        return {}


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
    print(f"  market wrap: {'found' if market['wrap'].get('summary') else 'not published yet'}; "
          f"FX: {', '.join(market['fx']) or 'none'}")

    if not disclosures and not market["wrap"].get("summary"):
        print("Nothing to format today. Exiting cleanly.")
        return 0

    formatted = ngx_digest.build(disclosures, market["wrap"], market["fx"], company_names())

    path = write_markdown(date_label, formatted)
    print(f"Wrote {path}")

    if os.environ.get("PUSH_TO_NOTION") == "1":
        push_to_notion(date_label, formatted)

    return 0


if __name__ == "__main__":
    sys.exit(main())
