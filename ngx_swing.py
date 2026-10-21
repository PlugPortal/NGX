#!/usr/bin/env python3
"""
NGX swing-trade screen
======================
Scores every watchlist ticker against swing_criteria.txt using only the
free data in data/, and writes data/swing.csv (one row per ticker with
each check's value and pass / fail / n/a) and data/swing_meta.json.

Run after ngx_data.py (ngx_data.py runs it automatically).

What each MarketSmith item became on NGX:
- EPS / RS ratings      -> RS rank among the watchlist (weighted 1Y/YTD/30D/7D returns)
- SMR, Acc/Dis ratings  -> dropped (proprietary; no free NGX equivalent)
- EPS / sales growth    -> latest period vs same period last year, from parsed
                           results summaries (n/a when a filing has no figures)
- EPS estimates, surprise, 3-year growth, IPO date -> dropped (no free data)
- Industry group rank   -> NGX sector rank by average 30-day return
- Price >= $15          -> price >= ₦2
- 50-day avg volume     -> average daily value traded over collected days
- Up/down volume ratio  -> volume on up days / down days (once 10+ days collected)
- RS line new high      -> dropped until price history is long enough
- ROE >= 17%            -> ROE >= 15% (Aboki Forex)
"""

from __future__ import annotations

import csv
import json
import os
import re
import datetime as dt
from collections import defaultdict

DATA = "data"


def load_criteria(path: str = "swing_criteria.txt") -> dict[str, float]:
    out = {}
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.split("#", 1)[0].strip()
            if "=" in ln:
                k, v = (x.strip() for x in ln.split("=", 1))
                out[k] = float(v)
    return out


def read_csv(name: str) -> list[dict]:
    p = f"{DATA}/{name}"
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def fnum(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def period_key(period: str) -> tuple[str, int] | None:
    """'Q3 2026' -> ('Q3', 2026); '9M 2026' -> ('9M', 2026)."""
    m = re.match(r"(Q[1-4]|H[12]|[369]M|FY)\s+(20\d{2})$", period.strip())
    return (m.group(1), int(m.group(2))) if m else None


def yoy_growth(rows: list[dict], field: str) -> tuple[float | None, str]:
    """Latest period vs the same period a year earlier. Prefers quarters."""
    by = {}
    for r in rows:
        k = period_key(r["period"])
        v = fnum(r.get(field))
        if k and v is not None:
            by[k] = v
    best = None
    for (kind, year), v in by.items():
        prev = by.get((kind, year - 1))
        if prev is None or prev == 0:
            continue
        rank = (year, {"Q": 3, "H": 2, "9": 1, "6": 1, "3": 1, "F": 0}.get(kind[0], 0))
        if best is None or rank > best[0]:
            best = (rank, (v - prev) / abs(prev) * 100, f"{kind} {year} vs {kind} {year - 1}")
    return (best[1], best[2]) if best else (None, "no comparable periods")


def pct_rank(values: dict[str, float]) -> dict[str, float]:
    items = sorted(values.items(), key=lambda kv: kv[1])
    n = len(items)
    return {t: (i / (n - 1) * 100 if n > 1 else 100.0) for i, (t, _) in enumerate(items)}


def run() -> dict:
    c = load_criteria()
    from ngx_data import load_watchlist, read_json
    tickers = load_watchlist()
    profiles = read_json(f"{DATA}/profiles.json", {})

    prices = defaultdict(list)
    for r in read_csv("prices.csv"):
        prices[r["ticker"]].append(r)
    for t in prices:
        prices[t].sort(key=lambda r: r["date"])
    funds = {}
    for r in sorted(read_csv("fundamentals.csv"), key=lambda r: r["date"]):
        funds[r["ticker"]] = r
    results = defaultdict(list)
    for r in read_csv("results.csv"):
        results[r["ticker"]].append(r)

    # RS composite and sector strength
    comp = {}
    for t in tickers:
        p, pr = (prices[t][-1] if prices[t] else {}), profiles.get(t, {})
        if pr.get("chg_30d") is not None and abs(pr["chg_30d"]) > c.get("max_plausible_30d_move_pct", 150):
            continue   # implausible move: keep it out of the RS ranking
        parts = [(pr.get("chg_1y"), .4), (pr.get("chg_ytd"), .3), (pr.get("chg_30d"), .2), (fnum(p.get("chg_7d")), .1)]
        have = [(v, w) for v, w in parts if v is not None]
        if have:
            comp[t] = sum(v * w for v, w in have) / sum(w for _, w in have)
    rs = pct_rank(comp)
    sec_ret = defaultdict(list)
    for t in tickers:
        pr = profiles.get(t, {})
        if pr.get("sector") and pr.get("chg_30d") is not None:
            sec_ret[pr["sector"]].append(pr["chg_30d"])
    sec_avg = {s: sum(v) / len(v) for s, v in sec_ret.items()}
    order = sorted(sec_avg, key=sec_avg.get, reverse=True)
    sec_rank_pct = {s: (i + 1) / len(order) * 100 for i, s in enumerate(order)}

    rows = []
    for t in tickers:
        hist = prices[t]
        last = hist[-1] if hist else {}
        pr = profiles.get(t, {})
        f = funds.get(t, {})
        price = fnum(last.get("price"))
        hi = max([x for x in [pr.get("high_52w")] + [fnum(h.get("price")) for h in hist] if x is not None], default=None)
        below_hi = (hi - price) / hi * 100 if hi and price else None
        vals = [fnum(h["price"]) * fnum(h["volume"]) for h in hist[-50:]
                if fnum(h.get("price")) and fnum(h.get("volume")) is not None]
        avg_val = sum(vals) / len(vals) if vals else None
        up = sum(fnum(h["volume"]) or 0 for h in hist[-50:] if (fnum(h.get("chg_1d")) or 0) > 0)
        dn = sum(fnum(h["volume"]) or 0 for h in hist[-50:] if (fnum(h.get("chg_1d")) or 0) < 0)
        ud = (up / dn if dn else None) if len(hist) >= 10 else None
        eps_g, eps_basis = yoy_growth(results[t], "eps")
        if eps_g is None:
            eps_g, eps_basis = yoy_growth(results[t], "pat")
            eps_basis = (eps_basis + " (PAT)") if eps_g is not None else eps_basis
        sales_g, sales_basis = yoy_growth(results[t], "revenue")
        roe = fnum(f.get("roe"))
        sec = pr.get("sector")

        def chk(value, ok):
            return "n/a" if value is None else ("pass" if ok(value) else "fail")

        c30, c7 = pr.get("chg_30d"), fnum(last.get("chg_7d"))
        checks = {
            "data_sanity": chk(c30, lambda v: abs(v) <= c.get("max_plausible_30d_move_pct", 150)),
            "active": ("n/a" if c30 is None and c7 is None else
                       "pass" if not c.get("require_recent_price_change", 1) or (c30 or 0) != 0 or (c7 or 0) != 0
                       else "fail"),
            "price": chk(price, lambda v: v >= c["min_price"]),
            "near_52w_high": chk(below_hi, lambda v: v <= c["max_below_52w_high_pct"]),
            "liquidity": chk(avg_val, lambda v: v >= c["min_avg_value_traded_naira"]),
            "rs_rank": chk(rs.get(t), lambda v: v >= c["min_rs_rank"]),
            "sector_rank": chk(sec_rank_pct.get(sec), lambda v: v <= c["max_sector_rank_pct"]),
            "roe": chk(roe, lambda v: v >= c["min_roe_pct"]),
            "eps_growth": chk(eps_g, lambda v: v >= c["min_eps_growth_pct"]),
            "sales_growth": chk(sales_g, lambda v: v >= c["min_sales_growth_pct"]),
            "up_down_volume": chk(ud, lambda v: v >= c["min_up_down_volume_ratio"]),
        }
        must = ["data_sanity", "active", "price", "near_52w_high", "liquidity", "rs_rank", "sector_rank"]
        fundamental = ["roe", "eps_growth", "sales_growth", "up_down_volume"]
        candidate = (all(checks[k] == "pass" for k in must)
                     and not any(checks[k] == "fail" for k in fundamental))
        rows.append({
            "ticker": t, "name": last.get("name", ""), "sector": sec or "",
            "candidate": "yes" if candidate else "no",
            "passed": sum(v == "pass" for v in checks.values()),
            "price": price, "below_52w_high_pct": below_hi, "high_52w": hi,
            "avg_value_traded": avg_val, "rs_rank": rs.get(t),
            "sector_rank_pct": sec_rank_pct.get(sec), "roe": roe,
            "eps_growth_pct": eps_g, "eps_basis": eps_basis,
            "sales_growth_pct": sales_g, "sales_basis": sales_basis,
            "up_down_volume": ud, "history_days": len(hist),
            **{f"chk_{k}": v for k, v in checks.items()},
        })

    rows.sort(key=lambda r: (r["candidate"] != "yes", -(r["rs_rank"] or -1)))
    fields = list(rows[0].keys()) if rows else ["ticker"]
    with open(f"{DATA}/swing.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 2) if isinstance(v, float) else ("" if v is None else v)) for k, v in r.items()})
    meta = {"run_at": dt.datetime.now().isoformat(timespec="seconds"),
            "candidates": [r["ticker"] for r in rows if r["candidate"] == "yes"],
            "criteria": c, "sector_ranking": [{"sector": s, "avg_30d": round(sec_avg[s], 2)} for s in order]}
    with open(f"{DATA}/swing_meta.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)
    return {"candidates": len(meta["candidates"]), "scored": len(rows)}


if __name__ == "__main__":
    print(run())
