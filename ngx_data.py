#!/usr/bin/env python3
"""
NGX Daily Data Collector
========================
Pulls free NGX data for every ticker in watchlist.txt and saves it under
data/, where the Streamlit dashboard reads it. No API keys and no Claude.

Writes
------
data/prices.csv         daily price history (one row per ticker per day)
data/fundamentals.csv   daily P/E, EPS, ROE, dividend yield, P/B, score
data/market.csv         daily ASI, market cap and breadth from the market wrap
data/fx.csv             daily CBN official rates
data/disclosures.json   every filing seen, keyed by filing id
data/results.csv        per-period revenue / PAT / EPS parsed from results summaries
data/profiles.json      sector, peers, 52-week range, broker consensus (weekly)
data/status.json        what ran, when, and any source errors

Each source is independent: if one site is down the others still update,
and the error is recorded in data/status.json and shown on the dashboard.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
import datetime as dt
import traceback

import ngx_sources as src

DATA = "data"
PROFILE_MAX_AGE_DAYS = 7
POLITE_DELAY = 1.0   # seconds between per-ticker page requests


# --------------------------------------------------------------------------- #
# File helpers
# --------------------------------------------------------------------------- #

def load_watchlist(path: str = "watchlist.txt") -> list[str]:
    out: list[str] = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            t = ln.split("#", 1)[0].strip().upper()
            if t and t not in out:
                out.append(t)
    return out


def read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True, default=str)
    os.replace(tmp, path)


def upsert_csv(path: str, fields: list[str], rows: list[dict], key: tuple[str, ...]) -> int:
    """Merge rows into a CSV, replacing any existing row with the same key."""
    existing: dict[tuple, dict] = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                existing[tuple(r.get(k, "") for k in key)] = r
    for r in rows:
        clean = {k: ("" if r.get(k) is None else r.get(k)) for k in fields}
        existing[tuple(str(clean[k]) for k in key)] = clean
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ordered = sorted(existing.values(), key=lambda r: tuple(str(r.get(k, "")) for k in key))
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(ordered)
    return len(rows)


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #

PRICE_FIELDS = ["date", "ticker", "name", "price", "chg_1d", "chg_7d", "mcap", "volume", "shares"]
FUND_FIELDS = ["date", "ticker", "name", "signal", "pe", "eps", "roe", "div_yield", "pb", "mcap", "score"]
MARKET_FIELDS = ["date", "asi", "asi_chg_pct", "mcap", "advancers", "decliners", "unchanged", "summary"]
FX_FIELDS = ["date", "USD/NGN", "EUR/NGN", "GBP/NGN", "CNY/NGN"]
RESULT_FIELDS = ["ticker", "period", "basis", "revenue", "pat", "eps", "equity", "assets",
                 "filing_id", "filing_date", "title"]


def step_prices(day: str, tickers: set[str]) -> dict:
    rows = [dict(r, date=day) for r in src.scrape_prices() if r["ticker"] in tickers]
    upsert_csv(f"{DATA}/prices.csv", PRICE_FIELDS, rows, ("date", "ticker"))
    return {"rows": len(rows), "missing": sorted(tickers - {r["ticker"] for r in rows})}


def step_fundamentals(day: str, tickers: set[str]) -> dict:
    rows = [dict(r, date=day) for r in src.scrape_fundamentals() if r["ticker"] in tickers]
    upsert_csv(f"{DATA}/fundamentals.csv", FUND_FIELDS, rows, ("date", "ticker"))
    return {"rows": len(rows), "missing": sorted(tickers - {r["ticker"] for r in rows})}


def step_market(today: dt.date) -> dict:
    wrap = src.scrape_market_wrap(today)
    if not wrap.get("summary"):
        return {"rows": 0, "note": f"No market wrap published yet for {today}"}
    upsert_csv(f"{DATA}/market.csv", MARKET_FIELDS, [wrap], ("date",))
    return {"rows": 1, "source": wrap.get("source")}


def step_fx(day: str) -> dict:
    fx = src.scrape_cbn_fx()
    if not fx:
        return {"rows": 0, "note": "No CBN rates found on the page"}
    upsert_csv(f"{DATA}/fx.csv", FX_FIELDS, [dict(fx, date=day)], ("date",))
    return {"rows": 1, "rates": fx}


def step_disclosures(tickers: set[str], store: dict, scanned: set[str]) -> dict:
    """Merge the main feed, plus each ticker's own page the first time we see it."""
    now = dt.datetime.now(src.WAT).isoformat(timespec="seconds")
    new_ids: list[str] = []

    def merge(items: list[dict]) -> None:
        for d in items:
            if d["ticker"] not in tickers:
                continue
            if d["id"] not in store:
                d["first_seen"] = now
                store[d["id"]] = d
                new_ids.append(d["id"])

    merge(src.scrape_disclosures_feed())
    backfilled = 0
    for t in sorted(tickers - scanned):
        try:
            merge(src.scrape_ticker_disclosures(t))
            scanned.add(t)
            backfilled += 1
        except Exception as e:   # one ticker page failing shouldn't stop the run
            print(f"  disclosures page for {t} failed: {e}")
        time.sleep(POLITE_DELAY)
    return {"new": len(new_ids), "backfilled_tickers": backfilled, "total": len(store),
            "new_ids": new_ids}


def step_results(store: dict) -> dict:
    rows = []
    for d in store.values():
        if d.get("category") != "Financial Results":
            continue
        for r in src.parse_results_summary(d.get("summary", "")):
            rows.append(dict(r, ticker=d["ticker"], filing_id=d["id"],
                             filing_date=d["date"], title=d["title"]))
    # oldest filing first so a later filing's restated figures win the key
    rows.sort(key=lambda r: r["filing_date"])
    upsert_csv(f"{DATA}/results.csv", RESULT_FIELDS, rows, ("ticker", "period", "basis"))
    return {"rows": len(rows)}


def step_profiles(tickers: set[str], profiles: dict, max_fetch: int) -> dict:
    today = dt.datetime.now(src.WAT).date()
    due = []
    for t in sorted(tickers):
        upd = profiles.get(t, {}).get("updated")
        age = (today - dt.date.fromisoformat(upd)).days if upd else 10**6
        if age >= PROFILE_MAX_AGE_DAYS:
            due.append((age, t))
    due.sort(reverse=True)
    fetched, failed = 0, []
    for _, t in due[:max_fetch]:
        try:
            p = src.scrape_profile(t)
            p["updated"] = today.isoformat()
            profiles[t] = p
            fetched += 1
        except Exception as e:
            failed.append(t)
            print(f"  profile for {t} failed: {e}")
        time.sleep(POLITE_DELAY)
    return {"fetched": fetched, "failed": failed, "due": len(due)}


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def run_step(status: dict, name: str, fn, *args):
    t0 = time.time()
    try:
        res = fn(*args)
        status["steps"][name] = {"ok": True, **res, "seconds": round(time.time() - t0, 1)}
        print(f"{name}: ok {json.dumps({k: v for k, v in res.items() if k != 'new_ids'}, default=str)[:300]}")
        return res
    except Exception as e:
        status["steps"][name] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:500]}
        print(f"{name}: FAILED\n{traceback.format_exc()}")
        return None


def main() -> int:
    tickers = set(load_watchlist())
    now = dt.datetime.now(src.WAT)
    today = now.date()
    day = today.isoformat()
    print(f"NGX data run {now:%Y-%m-%d %H:%M} WAT for {len(tickers)} tickers")

    store = read_json(f"{DATA}/disclosures.json", {})
    meta = read_json(f"{DATA}/meta.json", {"scanned_disclosure_pages": []})
    scanned = set(meta.get("scanned_disclosure_pages", []))
    profiles = read_json(f"{DATA}/profiles.json", {})

    status = {"run_at": now.isoformat(timespec="seconds"), "tickers": len(tickers), "steps": {}}
    run_step(status, "prices", step_prices, day, tickers)
    run_step(status, "fundamentals", step_fundamentals, day, tickers)
    run_step(status, "market_wrap", step_market, today)
    run_step(status, "fx", step_fx, day)
    disc = run_step(status, "disclosures", step_disclosures, tickers, store, scanned)
    run_step(status, "results", step_results, store)
    run_step(status, "profiles", step_profiles, tickers, profiles,
             int(os.environ.get("PROFILE_FETCH_LIMIT") or 200))

    write_json(f"{DATA}/disclosures.json", store)
    write_json(f"{DATA}/profiles.json", profiles)
    write_json(f"{DATA}/meta.json", {"scanned_disclosure_pages": sorted(scanned)})
    write_json(f"{DATA}/status.json", status)
    if disc:
        write_json(f"{DATA}/new_filings.json", disc["new_ids"])

    ok = [s for s in status["steps"].values() if s.get("ok")]
    print(f"Done: {len(ok)}/{len(status['steps'])} steps succeeded")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
