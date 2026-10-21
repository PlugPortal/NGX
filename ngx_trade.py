#!/usr/bin/env python3
"""
NGX weekly swing ideas (rule-based, no AI)
==========================================
For every ticker that passes the swing screen (data/swing.csv), builds a
price-action summary and a mechanical trade plan, plus the key facts from
its filings. Writes data/trade_ideas.json (read by the dashboard) and
output/ngx-swing-YYYY-MM-DD.md (a weekly note you can read or post).

Trade plan rules (edit the constants below):
- Breakout setup   price within BREAKOUT_ZONE_PCT of the 52-week high:
                   entry = buy-stop just above the 52-week high.
- Pullback setup   price further below the high (but still within the screen's
                   limit): entry zone = current price down to PULLBACK_ZONE_PCT lower.
- Stop             STOP_PCT below entry (O'Neil-style 7–8% rule).
- Targets          2R and 3R, where R = entry − stop.
- Position size    shares that risk RISK_PER_TRADE_PCT of capital, shown per ₦1m.

NGX notes: stocks move at most ±10% a day, many are thinly traded, and
prices here are 30-minute delayed. This is a mechanical plan from public
data for personal research — not investment advice.
"""

from __future__ import annotations

import csv
import re
import json
import os
import sys
import datetime as dt
from collections import defaultdict

from ngx_data import DATA, read_json, write_json
from ngx_swing import read_csv, fnum, yoy_growth
import ngx_sources as src

BREAKOUT_ZONE_PCT = 5
BREAKOUT_BUFFER_PCT = 1
PULLBACK_ZONE_PCT = 3
STOP_PCT = 8
RISK_PER_TRADE_PCT = 1
CAPITAL_EXAMPLE = 1_000_000
OUTPUT_DIR = "output"


def r2(v):
    return None if v is None else round(v, 2)


def plan_for(row: dict, hist: list[dict], profile: dict) -> dict:
    price = fnum(row["price"])
    hi = fnum(row["high_52w"])
    lo = profile.get("low_52w")
    below = fnum(row["below_52w_high_pct"])
    if below is not None and below <= BREAKOUT_ZONE_PCT and hi:
        setup = "Breakout watch"
        entry = hi * (1 + BREAKOUT_BUFFER_PCT / 100)
        entry_text = f"Buy-stop above the 52-week high: ₦{entry:,.2f}"
        entry_low = entry
    else:
        setup = "Pullback in uptrend"
        entry = price
        entry_low = price * (1 - PULLBACK_ZONE_PCT / 100)
        entry_text = f"Entry zone ₦{entry_low:,.2f} – ₦{price:,.2f}"
    stop = entry * (1 - STOP_PCT / 100)
    risk = entry - stop
    t2, t3 = entry + 2 * risk, entry + 3 * risk
    shares = int(CAPITAL_EXAMPLE * RISK_PER_TRADE_PCT / 100 / risk) if risk > 0 else 0

    # price action
    closes = [fnum(h["price"]) for h in hist if fnum(h.get("price"))]
    vols = [fnum(h["volume"]) for h in hist if fnum(h.get("volume")) is not None]
    pa = {
        "price": price, "chg_1d": fnum(hist[-1].get("chg_1d")) if hist else None,
        "chg_7d": fnum(hist[-1].get("chg_7d")) if hist else None,
        "chg_30d": profile.get("chg_30d"), "chg_ytd": profile.get("chg_ytd"), "chg_1y": profile.get("chg_1y"),
        "high_52w": hi, "low_52w": lo, "below_52w_high_pct": below,
        "range_position_pct": r2((price - lo) / (hi - lo) * 100) if hi and lo and hi > lo and price else None,
        "rs_rank": fnum(row["rs_rank"]),
    }
    if len(closes) >= 20:
        ma20 = sum(closes[-20:]) / 20
        pa["vs_20d_avg_pct"] = r2((price - ma20) / ma20 * 100)
    if len(vols) >= 10 and vols[-1] is not None:
        avg = sum(vols[-20:]) / len(vols[-20:])
        pa["volume_vs_avg"] = r2(vols[-1] / avg) if avg else None

    notes = []
    if pa.get("chg_1d") is not None and abs(pa["chg_1d"]) >= 9.5:
        notes.append("Hit the ±10% daily limit last session — expect continuation or a gap; size down.")
    if fnum(row["avg_value_traded"]) and fnum(row["avg_value_traded"]) < 50_000_000:
        notes.append("Thin liquidity (under ₦50m traded a day on average): use limit orders and smaller size.")
    if int(row["history_days"] or 0) < 20:
        notes.append(f"Only {row['history_days']} day(s) of price history collected so far; trend checks build up over time.")

    return {
        "setup": setup, "entry_text": entry_text, "entry": r2(entry), "entry_low": r2(entry_low),
        "stop": r2(stop), "stop_pct": STOP_PCT, "target_2r": r2(t2), "target_3r": r2(t3),
        "shares_per_1m_capital": shares, "risk_per_trade_pct": RISK_PER_TRADE_PCT,
        "price_action": pa, "notes": notes,
    }


def facts_for(ticker: str, store: dict, results: list[dict]) -> dict:
    cutoff = (dt.date.today() - dt.timedelta(days=90)).isoformat()
    mine = sorted((d for d in store.values() if d["ticker"] == ticker), key=lambda d: d["date"], reverse=True)
    from ngx_digest import parse_dealing
    dealings = []
    for d in mine:
        if d["category"] == "Director Dealing" and d["date"] >= cutoff:
            p = parse_dealing(d.get("summary", ""))
            dealings.append({"date": d["date"], "side": p.get("side", "?"), "shares": p.get("shares"),
                             "price": p.get("price"), "pdf": d.get("pdf")})
    raises = [{"date": d["date"], "title": d["title"], "pdf": d.get("pdf")} for d in mine
              if d["date"] >= cutoff and re.search(
                  r"RIGHTS ISSUE|PUBLIC OFFER|PRIVATE PLACEMENT|CAPITAL RAIS|OFFER FOR SUBSCRIPTION|RECAPITALI",
                  d["title"], re.I)]
    last_results = next(({"date": d["date"], "title": d["title"], "summary": d.get("summary", ""),
                          "pdf": d.get("pdf")} for d in mine if d["category"] == "Financial Results"), None)
    upcoming = [{"date": d["date"], "title": d["title"], "pdf": d.get("pdf")} for d in mine
                if d["category"] in ("Board Meeting", "AGM Notice") and d["date"] >= cutoff][:3]
    eps_g, eps_b = yoy_growth(results, "eps")
    sales_g, sales_b = yoy_growth(results, "revenue")
    return {"last_results": last_results, "eps_growth_pct": r2(eps_g), "eps_basis": eps_b,
            "sales_growth_pct": r2(sales_g), "sales_basis": sales_b,
            "director_dealings_90d": dealings, "capital_raises_90d": raises, "meetings_90d": upcoming}


def fmt(v, suffix="%", sign=True):
    if v is None:
        return "n/a"
    return f"{v:+.2f}{suffix}" if sign else f"{v:,.2f}{suffix}"


def markdown(ideas: list[dict], week: str) -> str:
    out = [f"# NGX Swing Ideas — week of {week}", "",
           "_Rule-based plans from public data (prices 30-min delayed). Personal research, not investment advice._", ""]
    if not ideas:
        out.append("No tickers passed the swing screen this week.")
    for i in ideas:
        pl, pa, f = i["plan"], i["plan"]["price_action"], i["facts"]
        out += [f"## ${i['ticker']} — {i['name']} ({i['sector']})", "",
                f"**Setup:** {pl['setup']} · RS rank {pa['rs_rank']:.0f} · {i['passed']}/11 checks passed", "",
                f"**Price action:** ₦{pa['price']:,.2f} · 1D {fmt(pa['chg_1d'])} · 7D {fmt(pa['chg_7d'])} · "
                f"30D {fmt(pa['chg_30d'])} · YTD {fmt(pa['chg_ytd'])} · "
                f"{fmt(pa['below_52w_high_pct'], '%', False)} below the 52-week high (₦{pa['high_52w']:,.2f})", "",
                f"**Trade plan:** {pl['entry_text']} · Stop ₦{pl['stop']:,.2f} (−{pl['stop_pct']}%) · "
                f"Targets ₦{pl['target_2r']:,.2f} (2R) / ₦{pl['target_3r']:,.2f} (3R) · "
                f"~{pl['shares_per_1m_capital']:,} shares per ₦1m capital at {pl['risk_per_trade_pct']}% risk", ""]
        facts = []
        if f["eps_growth_pct"] is not None:
            facts.append(f"EPS growth {fmt(f['eps_growth_pct'])} ({f['eps_basis']})")
        if f["sales_growth_pct"] is not None:
            facts.append(f"Sales growth {fmt(f['sales_growth_pct'])} ({f['sales_basis']})")
        if f["last_results"]:
            facts.append(f"Last results filed {f['last_results']['date']}: {f['last_results']['title'].title()}")
        if f["director_dealings_90d"]:
            b = sum(d["side"] == "BUY" for d in f["director_dealings_90d"])
            s = sum(d["side"] == "SELL" for d in f["director_dealings_90d"])
            facts.append(f"Insider filings (90 days): {b} buy, {s} sell")
        if f["capital_raises_90d"]:
            facts.append("Capital raise filed: " + "; ".join(r["title"].title() for r in f["capital_raises_90d"]))
        if facts:
            out += ["**Key facts:** " + " · ".join(facts), ""]
        for n in pl["notes"]:
            out.append(f"- ⚠️ {n}")
        out.append("")
    return "\n".join(out)


def run(write_markdown: bool = True) -> dict:
    rows = read_csv("swing.csv")
    profiles = read_json(f"{DATA}/profiles.json", {})
    store = read_json(f"{DATA}/disclosures.json", {})
    prices = defaultdict(list)
    for r in read_csv("prices.csv"):
        prices[r["ticker"]].append(r)
    results = defaultdict(list)
    for r in read_csv("results.csv"):
        results[r["ticker"]].append(r)

    ideas = []
    for row in rows:
        if row.get("candidate") != "yes" or not fnum(row.get("price")):
            continue
        t = row["ticker"]
        hist = sorted(prices[t], key=lambda h: h["date"])
        ideas.append({"ticker": t, "name": row["name"], "sector": row["sector"], "passed": int(row["passed"]),
                      "plan": plan_for(row, hist, profiles.get(t, {})),
                      "facts": facts_for(t, store, results[t])})
    now = dt.datetime.now(src.WAT)
    week = (now.date() - dt.timedelta(days=now.weekday())).isoformat()
    write_json(f"{DATA}/trade_ideas.json", {"generated": now.isoformat(timespec="seconds"),
                                            "week_of": week, "ideas": ideas})
    path = None
    if write_markdown:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        path = f"{OUTPUT_DIR}/ngx-swing-{now:%Y-%m-%d}.md"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(markdown(ideas, week))
    return {"ideas": len(ideas), "file": path}


if __name__ == "__main__":
    print(run())
    sys.exit(0)
