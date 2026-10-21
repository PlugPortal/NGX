#!/usr/bin/env python3
"""
NGX Research (event-driven)
===========================
Runs the prompts in research_prompts.txt through Claude for a ticker only
when that ticker has filed new financial results since its last research.
Results are saved to data/research/TICKER.json for the dashboard.

For each ticker it gives Claude:
- the results filing itself (key pages of the PDF; scanned PDFs are sent
  as a PDF so Claude can read the images),
- Aboki Forex's summary of the filing,
- the ticker's recent filings from data/disclosures.json (director
  dealings, rights issues, etc.), its price and fundamentals snapshot,
  sector and peers,
- and live web search for anything else.

Run after ngx_data.py.

Environment variables
---------------------
ANTHROPIC_API_KEY          required (unless nothing needs research)
RESEARCH_MODEL             optional  model (defaults to MODEL in ngx_daily.py)
MAX_SEARCHES               optional  web searches per question (default 6)
MAX_RESEARCH_PER_RUN       optional  tickers researched per run (default 10)
RESEARCH_BACKFILL_PER_RUN  optional  extra never-researched tickers per run,
                                     largest first, even with no new filing (default 0)
RESEARCH_TICKERS           optional  comma-separated tickers to research now,
                                     regardless of filings (manual run input)

Usage
-----
python ngx_research.py             # research whatever is due
python ngx_research.py --dry-run   # list what is due and why, no API calls
"""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import re
import sys
import time
import datetime as dt

import requests

import ngx_sources as src
from ngx_data import DATA, load_watchlist, read_json, write_json
from ngx_daily import ANTHROPIC_URL, MODEL as DAILY_MODEL

MODEL = os.environ.get("RESEARCH_MODEL") or DAILY_MODEL
MAX_TOKENS = 6000
MAX_SEARCHES = int(os.environ.get("MAX_SEARCHES") or 6)
MAX_PER_RUN = int(os.environ.get("MAX_RESEARCH_PER_RUN") or 10)
BACKFILL_PER_RUN = int(os.environ.get("RESEARCH_BACKFILL_PER_RUN") or 0)
MAX_CONTINUATIONS = 5
RETRY_STATUSES = {429, 500, 502, 503, 529}

PDF_TEXT_CHAR_BUDGET = 45_000   # text sent from a text-based PDF
PDF_SCAN_MAX_PAGES = 20         # pages sent from a scanned PDF
PDF_MAX_BYTES = 15_000_000

# Web search tool, per https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES}

RESEARCH_DIR = f"{DATA}/research"


# --------------------------------------------------------------------------- #
# Prompts
# --------------------------------------------------------------------------- #

def read_lines(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [ln.rstrip("\n") for ln in f if not ln.lstrip().startswith("#")]


def load_prompts(path: str = "research_prompts.txt") -> list[str]:
    text = "\n".join(read_lines(path))
    return [p.strip() for p in text.split("\n===\n") if p.strip()]


def load_system_prompt(path: str = "research_system_prompt.txt") -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------------------- #
# What is due
# --------------------------------------------------------------------------- #

def latest_results_filing(store: dict, ticker: str) -> dict | None:
    res = [d for d in store.values()
           if d["ticker"] == ticker and d.get("category") == "Financial Results"]
    return max(res, key=lambda d: (d.get("date", ""), d["id"])) if res else None


def due_tickers(watchlist: list[str], store: dict) -> list[tuple[str, str, dict | None]]:
    """Return (ticker, reason, trigger_filing) in priority order."""
    forced = [t.strip().upper() for t in (os.environ.get("RESEARCH_TICKERS") or "").split(",") if t.strip()]
    out: list[tuple[str, str, dict | None]] = []
    for t in forced:
        out.append((t, "manual request", latest_results_filing(store, t)))

    new_results, never = [], []
    for t in watchlist:
        if t in forced:
            continue
        done = read_json(f"{RESEARCH_DIR}/{t}.json", None)
        filing = latest_results_filing(store, t)
        if filing and (not done or done.get("trigger", {}).get("id") != filing["id"]):
            # only react to filings seen recently, not old ones found by backfill
            if done or filing.get("date", "") >= (dt.date.today() - dt.timedelta(days=14)).isoformat():
                new_results.append((t, f"new results filed {filing['date']}", filing))
                continue
        if not done:
            never.append((t, "not yet researched", filing))

    new_results.sort(key=lambda x: x[2]["date"], reverse=True)
    out += new_results
    out = out[:max(MAX_PER_RUN, len(forced))]

    if BACKFILL_PER_RUN and never:
        caps = _market_caps()
        never.sort(key=lambda x: caps.get(x[0], 0), reverse=True)
        out += never[:BACKFILL_PER_RUN]
    return out


def _market_caps() -> dict[str, float]:
    caps: dict[str, float] = {}
    try:
        with open(f"{DATA}/prices.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("mcap"):
                    caps[r["ticker"]] = float(r["mcap"])
    except FileNotFoundError:
        pass
    return caps


# --------------------------------------------------------------------------- #
# Context for Claude
# --------------------------------------------------------------------------- #

KEY_PAGE_WORDS = (
    "profit or loss", "comprehensive income", "income statement", "earnings per share",
    "revenue", "gross earnings", "interest income", "turnover", "gross profit",
    "operating profit", "profit before tax", "statement of financial position",
    "net interest", "insurance revenue", "cost of sales",
)


def pdf_blocks(url: str | None) -> tuple[list[dict], str]:
    """
    Turn a filing PDF into content blocks: key text pages if the PDF has a
    text layer, otherwise the first pages as a PDF document (scans).
    """
    if not url:
        return [], "no PDF link"
    try:
        from pypdf import PdfReader, PdfWriter
        raw = src.fetch_bytes(url)
        reader = PdfReader(io.BytesIO(raw))
        pages = [(i, (p.extract_text() or "")) for i, p in enumerate(reader.pages)]
    except Exception as e:
        return [], f"PDF could not be read ({type(e).__name__})"

    text_chars = sum(len(t.strip()) for _, t in pages)
    if text_chars > 1500:
        scored = sorted(pages, key=lambda p: -sum(p[1].lower().count(w) for w in KEY_PAGE_WORDS))
        keep, used = [], 0
        for i, t in scored:
            if used + len(t) > PDF_TEXT_CHAR_BUDGET:
                continue
            keep.append((i, t))
            used += len(t)
        keep.sort()
        body = "\n\n".join(f"--- page {i + 1} ---\n{t.strip()}" for i, t in keep)
        note = f"text of {len(keep)} of {len(pages)} pages"
        return [{"type": "text", "text": f"Extracted text from the results filing ({note}):\n\n{body}"}], note

    # Scanned PDF: send the first pages so Claude can read them as images.
    writer = PdfWriter()
    for p in reader.pages[:PDF_SCAN_MAX_PAGES]:
        writer.add_page(p)
    buf = io.BytesIO()
    writer.write(buf)
    data = buf.getvalue()
    if len(data) > PDF_MAX_BYTES:
        return [], "scanned PDF too large to send"
    note = f"scanned PDF, first {min(len(reader.pages), PDF_SCAN_MAX_PAGES)} of {len(reader.pages)} pages"
    return [{"type": "document",
             "source": {"type": "base64", "media_type": "application/pdf",
                        "data": base64.b64encode(data).decode()}}], note


def latest_row(path: str, ticker: str) -> dict | None:
    row = None
    try:
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["ticker"] == ticker and (row is None or r["date"] >= row["date"]):
                    row = r
    except FileNotFoundError:
        pass
    return row


def _naira(v: str | None) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "n/a"
    for unit, div in (("trillion", 1e12), ("billion", 1e9), ("million", 1e6)):
        if abs(x) >= div:
            return f"₦{x / div:,.2f} {unit}"
    return f"₦{x:,.2f}"


def context_text(ticker: str, store: dict, filing: dict | None) -> str:
    cutoff = (dt.date.today() - dt.timedelta(days=120)).isoformat()
    recent = sorted((d for d in store.values() if d["ticker"] == ticker and d.get("date", "") >= cutoff),
                    key=lambda d: d["date"], reverse=True)[:25]
    profile = read_json(f"{DATA}/profiles.json", {}).get(ticker, {})
    parts = [f"Context collected from free sources on {dt.date.today():%d %b %Y} (verify before relying on it):"]
    price = latest_row(f"{DATA}/prices.csv", ticker)
    if price:
        parts.append(f"- Kobo Terminal price {price['date']}: ₦{price['price']}, 1d {price['chg_1d']}%, "
                     f"7d {price['chg_7d']}%, market cap {_naira(price['mcap'])}")
    fund = latest_row(f"{DATA}/fundamentals.csv", ticker)
    if fund:
        parts.append(f"- Aboki Forex fundamentals {fund['date']}: P/E {fund['pe']}, EPS ₦{fund['eps']}, "
                     f"ROE {fund['roe']}%, dividend yield {fund['div_yield']}%, P/B {fund['pb']}")
    if profile:
        parts.append(f"- Sector: {profile.get('sector')}; NGX peers listed by Kobo: {', '.join(profile.get('peers', []))}")
    if filing:
        parts.append(f"\nResults filing that triggered this research ({filing['date']}): {filing['title']}\n"
                     f"PDF: {filing.get('pdf')}\nAboki Forex summary of it:\n{filing.get('summary') or '(none)'}")
    if recent:
        parts.append("\nRecent NGX filings for this ticker (last 120 days, newest first):")
        for d in recent:
            s = re.sub(r"\s+", " ", d.get("summary") or "")[:400]
            parts.append(f"- {d['date']} [{d['category']}] {d['title']}" + (f" — {s}" if s else ""))
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Anthropic call with web search
# --------------------------------------------------------------------------- #

def post(body: dict) -> dict:
    api_key = os.environ["ANTHROPIC_API_KEY"]
    for attempt in range(5):
        resp = requests.post(
            ANTHROPIC_URL,
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            data=json.dumps(body), timeout=300,
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


def ask(system_prompt: str, content: list[dict]) -> tuple[list[dict], dict]:
    """One question with web search; resumes paused turns. Returns blocks and usage."""
    messages = [{"role": "user", "content": content}]
    blocks: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "web_search_requests": 0}
    for _ in range(MAX_CONTINUATIONS + 1):
        data = post({"model": MODEL, "max_tokens": MAX_TOKENS, "system": system_prompt,
                     "messages": messages, "tools": [WEB_SEARCH_TOOL]})
        blocks.extend(data.get("content", []))
        u = data.get("usage", {})
        usage["input_tokens"] += u.get("input_tokens", 0)
        usage["output_tokens"] += u.get("output_tokens", 0)
        usage["web_search_requests"] += (u.get("server_tool_use") or {}).get("web_search_requests", 0)
        if data.get("stop_reason") != "pause_turn":
            break
        messages = [{"role": "user", "content": content}, {"role": "assistant", "content": blocks}]
    return blocks, usage


def render(blocks: list[dict]) -> tuple[str, list[dict]]:
    """Markdown with [n] markers after cited passages, plus the numbered sources."""
    sources: list[dict] = []
    urls: list[str] = []
    out: list[str] = []
    for b in blocks:
        if b.get("type") != "text":
            continue
        marks = []
        for c in b.get("citations") or []:
            url = c.get("url")
            if not url:
                continue
            if url not in urls:
                urls.append(url)
                sources.append({"title": c.get("title") or url, "url": url})
            m = f"[{urls.index(url) + 1}]"
            if m not in marks:
                marks.append(m)
        out.append(b.get("text", "") + "".join(marks))
    return "".join(out).strip(), sources


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def research_ticker(ticker: str, reason: str, filing: dict | None, store: dict,
                    prompts: list[str], system_prompt: str) -> dict:
    ctx = context_text(ticker, store, filing)
    doc_blocks, pdf_note = pdf_blocks(filing.get("pdf") if filing else None)
    answers = []
    for i, p in enumerate(prompts):
        question = p.replace("[TICKER]", ticker)
        content: list[dict] = []
        if i == 0:
            content += doc_blocks
            content.append({"type": "text", "text": ctx})
        else:
            # Later questions get the context plus the first answer, not the PDF again.
            content.append({"type": "text", "text": ctx + "\n\nYour earlier research note on this ticker:\n"
                            + answers[0]["answer"]})
        content.append({"type": "text", "text": f"Question: {question}"})
        blocks, usage = ask(system_prompt, content)
        answer, sources = render(blocks)
        answers.append({"question": question, "answer": answer or "_No answer returned._",
                        "sources": sources, "usage": usage})
        print(f"  Q{i + 1}: {usage['web_search_requests']} searches, "
              f"{usage['input_tokens']:,} in / {usage['output_tokens']:,} out tokens")
    return {
        "ticker": ticker,
        "updated": dt.datetime.now(src.WAT).isoformat(timespec="seconds"),
        "model": MODEL,
        "reason": reason,
        "trigger": ({k: filing.get(k) for k in ("id", "date", "title", "pdf")} if filing else {}),
        "filing_input": pdf_note,
        "answers": answers,
    }


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    watchlist = load_watchlist()
    store = read_json(f"{DATA}/disclosures.json", {})
    due = due_tickers(watchlist, store)
    print(f"Research due: {len(due)} ticker(s), model {MODEL}")
    for t, reason, _ in due:
        print(f"  {t}: {reason}")
    if dry_run or not due:
        return 0

    prompts = load_prompts()
    system_prompt = load_system_prompt()
    os.makedirs(f"{RESEARCH_DIR}/archive", exist_ok=True)
    failures = 0
    for t, reason, filing in due:
        print(f"Researching {t} ({reason})...")
        try:
            rec = research_ticker(t, reason, filing, store, prompts, system_prompt)
            old = read_json(f"{RESEARCH_DIR}/{t}.json", None)
            if old:
                write_json(f"{RESEARCH_DIR}/archive/{t}-{old['updated'][:10]}.json", old)
            write_json(f"{RESEARCH_DIR}/{t}.json", rec)
        except Exception as e:   # keep going with the other tickers
            failures += 1
            print(f"  FAILED: {e}")
    print(f"Research done: {len(due) - failures} ok, {failures} failed")
    return 1 if failures and failures == len(due) else 0


if __name__ == "__main__":
    sys.exit(main())
