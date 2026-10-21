#!/usr/bin/env python3
"""
NGX Weekly Research
===================
For every ticker in watchlist.txt, runs each prompt in research_prompts.txt
through the Anthropic API with the server-side web search tool, and writes the
answers (with cited sources) to output/ngx-research-YYYY-MM-DD.md.

Files you edit (no code changes needed):
- watchlist.txt              tickers to research, one per line
- research_prompts.txt       the questions asked about each ticker
- research_system_prompt.txt NGX context and accuracy rules for the analyst

Environment variables
---------------------
ANTHROPIC_API_KEY   required
RESEARCH_MODEL      optional  model to use (defaults to MODEL in ngx_daily.py)
MAX_SEARCHES        optional  web searches allowed per prompt (default 8)

Usage
-----
python ngx_research.py             # full run
python ngx_research.py --dry-run   # show what would be asked, no API calls
"""

from __future__ import annotations

import os
import sys
import json
import time
import datetime as dt

import requests

from ngx_daily import ANTHROPIC_URL, MODEL as DAILY_MODEL, WAT, OUTPUT_DIR

MODEL = os.environ.get("RESEARCH_MODEL") or DAILY_MODEL
MAX_TOKENS = 6000
MAX_SEARCHES = int(os.environ.get("MAX_SEARCHES") or 8)
MAX_CONTINUATIONS = 5        # how many times to resume a paused search turn
RETRY_STATUSES = {429, 500, 502, 503, 529}

# Web search tool definition, per Anthropic docs:
# https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool
WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": MAX_SEARCHES,
}


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #

def read_lines(path: str) -> list[str]:
    """Return the file's non-empty lines, skipping # comments."""
    with open(path, "r", encoding="utf-8") as f:
        return [ln.rstrip("\n") for ln in f if not ln.lstrip().startswith("#")]


def load_watchlist(path: str = "watchlist.txt") -> list[str]:
    tickers: list[str] = []
    for ln in read_lines(path):
        t = ln.strip().upper()
        if t and t not in tickers:
            tickers.append(t)
    return tickers


def load_prompts(path: str = "research_prompts.txt") -> list[str]:
    text = "\n".join(read_lines(path))
    prompts = [p.strip() for p in text.split("\n===\n")]
    return [p for p in prompts if p]


def load_system_prompt(path: str = "research_system_prompt.txt") -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------------------- #
# Anthropic call with web search
# --------------------------------------------------------------------------- #

def post(body: dict) -> dict:
    """POST to the Messages API, retrying on rate limits and overloads."""
    api_key = os.environ["ANTHROPIC_API_KEY"]
    for attempt in range(5):
        resp = requests.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            data=json.dumps(body),
            timeout=300,
        )
        if resp.status_code in RETRY_STATUSES and attempt < 4:
            wait = int(resp.headers.get("retry-after") or 0) or 15 * (attempt + 1)
            print(f"    API {resp.status_code}, retrying in {wait}s...")
            time.sleep(wait)
            continue
        if resp.status_code >= 300:
            raise RuntimeError(f"Anthropic API {resp.status_code}: {resp.text[:600]}")
        return resp.json()
    raise RuntimeError("Anthropic API: retries exhausted")


def ask(system_prompt: str, question: str) -> tuple[list[dict], int]:
    """
    Ask one question with web search enabled. Returns the assistant's content
    blocks and the number of searches used. Long search turns can come back
    with stop_reason "pause_turn"; we send the paused turn back to resume it.
    """
    messages = [{"role": "user", "content": question}]
    blocks: list[dict] = []
    searches = 0

    for _ in range(MAX_CONTINUATIONS + 1):
        data = post({
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "system": system_prompt,
            "messages": messages,
            "tools": [WEB_SEARCH_TOOL],
        })
        content = data.get("content", [])
        blocks.extend(content)
        usage = data.get("usage", {}).get("server_tool_use", {}) or {}
        searches += usage.get("web_search_requests", 0)

        if data.get("stop_reason") != "pause_turn":
            break
        # Resume: send everything the assistant has produced so far, unchanged.
        messages = [
            {"role": "user", "content": question},
            {"role": "assistant", "content": blocks},
        ]
    return blocks, searches


def render(blocks: list[dict]) -> tuple[str, list[tuple[str, str]]]:
    """
    Turn content blocks into Markdown text with [n] markers after cited
    passages, plus a numbered list of the sources cited.
    """
    sources: list[tuple[str, str]] = []   # (title, url), in first-cited order
    out: list[str] = []

    for b in blocks:
        if b.get("type") != "text":
            continue
        text = b.get("text", "")
        marks = []
        for c in b.get("citations") or []:
            url = c.get("url")
            if not url:
                continue
            title = c.get("title") or url
            if url not in [u for _, u in sources]:
                sources.append((title, url))
            n = [u for _, u in sources].index(url) + 1
            if f"[{n}]" not in marks:
                marks.append(f"[{n}]")
        out.append(text + ("".join(marks) if marks else ""))

    body = "".join(out).strip()
    return body, sources


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    dry_run = "--dry-run" in sys.argv
    tickers = load_watchlist()
    prompts = load_prompts()
    system_prompt = load_system_prompt()

    if not tickers:
        print("watchlist.txt has no tickers. Add some and run again.")
        return 0
    if not prompts:
        print("research_prompts.txt has no prompts.")
        return 1

    now = dt.datetime.now(WAT)
    print(f"NGX research run: {len(tickers)} ticker(s) x {len(prompts)} prompt(s), model {MODEL}")

    if dry_run:
        for t in tickers:
            for p in prompts:
                print(f"\n--- {t} ---\n{p.replace('[TICKER]', t)}")
        return 0

    sections: list[str] = []
    total_searches = 0
    failures = 0

    for t in tickers:
        print(f"Researching {t}...")
        parts = [f"## {t}\n"]
        for i, p in enumerate(prompts, 1):
            question = p.replace("[TICKER]", t)
            try:
                blocks, searches = ask(system_prompt, question)
                total_searches += searches
                body, sources = render(blocks)
                parts.append(f"### Question {i}\n\n> {question}\n\n{body or '_No answer returned._'}\n")
                if sources:
                    parts.append("**Sources**\n\n" + "\n".join(
                        f"{n}. [{title}]({url})" for n, (title, url) in enumerate(sources, 1)
                    ) + "\n")
                else:
                    parts.append("**Sources:** none cited — treat this answer as unverified.\n")
                print(f"  Q{i}: done ({searches} searches)")
            except Exception as e:   # keep going with the other tickers
                failures += 1
                parts.append(f"### Question {i}\n\n> {question}\n\n**Error:** {e}\n")
                print(f"  Q{i}: FAILED — {e}")
        sections.append("\n".join(parts))

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"ngx-research-{now:%Y-%m-%d}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# NGX Weekly Research — {now:%-d %b %Y}\n\n")
        f.write(
            f"_Generated by {MODEL} with web search ({total_searches} searches). "
            "AI-generated research: check figures against the cited sources "
            "before acting on them. Not investment advice._\n\n"
        )
        f.write("\n---\n\n".join(sections))
    print(f"Wrote {path}  ({total_searches} searches, {failures} failure(s))")

    total = len(tickers) * len(prompts)
    return 1 if failures == total else 0


if __name__ == "__main__":
    sys.exit(main())
