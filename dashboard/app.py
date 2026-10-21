"""
NGX Research Dashboard (Streamlit)
==================================
Reads the files the daily GitHub Actions job saves under data/. Opening the
dashboard calls no AI. It displays what was already collected, and the
"Refresh prices now" button pulls the latest delayed prices from Kobo Terminal.

Run locally:   streamlit run dashboard/app.py
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = ROOT / "data"
WATCHLIST = Path(__file__).resolve().parent.parent / "watchlist.txt"

st.set_page_config(page_title="NGX Research Desk", page_icon="📈", layout="wide")


# --------------------------------------------------------------------------- #
# Loading (cached until the file changes)
# --------------------------------------------------------------------------- #

def _mtime(name: str) -> float:
    p = DATA / name
    return p.stat().st_mtime if p.exists() else 0.0


@st.cache_data
def _csv(name: str, mtime: float) -> pd.DataFrame:
    p = DATA / name
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p, dtype={"ticker": str})


@st.cache_data
def _json(name: str, mtime: float):
    p = DATA / name
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def csv_(name: str) -> pd.DataFrame:
    return _csv(name, _mtime(name))


def json_(name: str):
    return _json(name, _mtime(name))


@st.cache_data(ttl=600, show_spinner="Pulling latest prices from Kobo Terminal…")
def live_prices(_nonce: int) -> tuple[pd.DataFrame, str]:
    """Latest (30-min delayed) prices straight from Kobo Terminal; cached 10 minutes."""
    import ngx_sources
    rows = ngx_sources.scrape_prices()
    stamp = dt.datetime.now(ngx_sources.WAT).strftime("%H:%M WAT")
    return pd.DataFrame(rows).set_index("ticker"), stamp


def watchlist() -> list[str]:
    if not WATCHLIST.exists():
        return []
    out = []
    for ln in WATCHLIST.read_text(encoding="utf-8").splitlines():
        t = ln.split("#", 1)[0].strip().upper()
        if t and t not in out:
            out.append(t)
    return out


def latest(df: pd.DataFrame) -> pd.DataFrame:
    """Most recent row per ticker."""
    if df.empty:
        return df
    return df.sort_values("date").groupby("ticker", as_index=False).tail(1).set_index("ticker")


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #

def naira(v, digits: int = 2) -> str:
    if v is None or pd.isna(v):
        return "—"
    v = float(v)
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(v) >= div:
            return f"₦{v / div:,.{digits}f}{unit}"
    return f"₦{v:,.{digits}f}"


def pct(v) -> str:
    return "—" if v is None or pd.isna(v) else f"{float(v):+.2f}%"


CAPITAL_RAISE_RE = re.compile(
    r"RIGHTS ISSUE|PUBLIC OFFER|PRIVATE PLACEMENT|CAPITAL RAIS|OFFER FOR SUBSCRIPTION|"
    r"SHARE ISSUANCE|ADDITIONAL SHARES|SUPPLEMENTARY|RECAPITALI", re.I)


def dealing_side(summary: str) -> str:
    s = (summary or "").lower()
    buy = re.search(r"\b(purchas|bought|acquir|buy)", s)
    sell = re.search(r"\b(sold|sale|dispos|sell)", s)
    if buy and not sell:
        return "🟢 Buy"
    if sell and not buy:
        return "🔴 Sell"
    return "Mixed / unclear"


def dealing_value(summary: str) -> str:
    m = re.search(r"total (?:value|consideration)[^₦]{0,40}₦\s?([\d,]+(?:\.\d+)?)", summary or "", re.I)
    return f"₦{m.group(1)}" if m else "—"


def filings_df(store: dict, tickers: list[str] | None = None) -> pd.DataFrame:
    if not store:
        return pd.DataFrame(columns=["date", "ticker", "category", "title", "summary", "pdf"])
    df = pd.DataFrame(store.values())
    if tickers is not None:
        df = df[df["ticker"].isin(tickers)]
    df["is_raise"] = df["title"].fillna("").str.contains(CAPITAL_RAISE_RE)
    return df.sort_values(["date", "id"], ascending=False)


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #

tickers = watchlist()
prices = csv_("prices.csv")
funds = csv_("fundamentals.csv")
market = csv_("market.csv")
fx = csv_("fx.csv")
results = csv_("results.csv")
store = json_("disclosures.json")
profiles = json_("profiles.json")
status = json_("status.json")
swing = csv_("swing.csv")
ideas = json_("trade_ideas.json")

px = latest(prices)
if st.session_state.get("live"):
    try:
        live, stamp = live_prices(st.session_state["live"])
        px = px.copy()
        for col in ["price", "chg_1d", "chg_7d", "mcap", "volume", "name"]:
            px.loc[px.index.intersection(live.index), col] = live[col]
        st.session_state["live_stamp"] = stamp
    except Exception as e:
        st.session_state["live_error"] = str(e)
fd = latest(funds)
filings = filings_df(store, tickers)


sw = swing.set_index("ticker") if not swing.empty else pd.DataFrame()


def sw_label(t: str) -> str | None:
    if sw.empty or t not in sw.index:
        return None
    r = sw.loc[t]
    return f"{'✅ candidate' if r['candidate'] == 'yes' else '—'} ({int(r['passed'])}/11)"


def build_screener() -> pd.DataFrame:
    rows = []
    last_results = (filings[filings["category"] == "Financial Results"]
                    .groupby("ticker")["date"].max() if not filings.empty else pd.Series(dtype=str))
    for t in tickers:
        p = px.loc[t] if t in px.index else None
        f = fd.loc[t] if t in fd.index else None
        pr = profiles.get(t, {})
        rows.append({
            "Ticker": t,
            "Name": (p["name"] if p is not None else (f["name"].title() if f is not None else "")),
            "Sector": pr.get("sector") or "",
            "Price ₦": p["price"] if p is not None else None,
            "1D %": p["chg_1d"] if p is not None else None,
            "7D %": p["chg_7d"] if p is not None else None,
            "30D %": pr.get("chg_30d"),
            "YTD %": pr.get("chg_ytd"),
            "Mkt cap ₦bn": (p["mcap"] / 1e9 if p is not None and pd.notna(p["mcap"]) else
                            (f["mcap"] / 1e9 if f is not None and pd.notna(f["mcap"]) else None)),
            "P/E": f["pe"] if f is not None else None,
            "EPS ₦": f["eps"] if f is not None else None,
            "ROE %": f["roe"] if f is not None else None,
            "Div %": f["div_yield"] if f is not None else None,
            "P/B": f["pb"] if f is not None else None,
            "Aboki signal": f["signal"] if f is not None else None,
            "Score": f["score"] if f is not None else None,
            "Broker consensus": pr.get("consensus"),
            "Last results": last_results.get(t),
            "Swing": sw_label(t),
        })
    df = pd.DataFrame(rows)
    for c in ["Price ₦", "1D %", "7D %", "30D %", "YTD %", "Mkt cap ₦bn", "P/E", "EPS ₦",
              "ROE %", "Div %", "P/B", "Score"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #

st.title("NGX Research Desk")
run_at = status.get("run_at", "")
failed = [k for k, v in status.get("steps", {}).items() if not v.get("ok")]
cap = f"Data last collected {run_at[:16].replace('T', ' ')} WAT" if run_at else "No data collected yet"
cap += " · prices 30-min delayed (Kobo Terminal) · fundamentals and filings from Aboki Forex · personal use only"
st.caption(cap)
b1, b2 = st.columns([1, 5])
if b1.button("Refresh prices now"):
    st.session_state["live"] = int(dt.datetime.now().timestamp() // 600) + 1
    st.rerun()
if st.session_state.get("live_stamp"):
    b2.caption(f"Showing live prices pulled at {st.session_state['live_stamp']} (Kobo Terminal, 30-min delayed). "
               "Price history and the swing screen still use the daily collection.")
if st.session_state.get("live_error"):
    b2.warning("Couldn't pull live prices right now; showing the daily collection.")
if failed:
    st.warning("Some sources failed on the last run and show older data: " + ", ".join(failed))
if not tickers:
    st.error("watchlist.txt is empty.")
    st.stop()

tab_market, tab_swing, tab_screen, tab_ticker, tab_filings = st.tabs(
    ["Market", "Swing", "Screener", "Ticker", "Filings"])

CHECK_LABELS = {
    "chk_data_sanity": "Data OK", "chk_active": "Trading", "chk_price": "Price ≥ ₦2",
    "chk_near_52w_high": "Near 52w high", "chk_liquidity": "Liquidity", "chk_rs_rank": "RS rank",
    "chk_sector_rank": "Sector rank", "chk_roe": "ROE", "chk_eps_growth": "EPS growth",
    "chk_sales_growth": "Sales growth", "chk_up_down_volume": "Up/down vol",
}
MARK = {"pass": "✅", "fail": "❌", "n/a": "➖"}


def show_plan(i: dict) -> None:
    pl, pa, fa = i["plan"], i["plan"]["price_action"], i["facts"]
    c = st.columns(4)
    c[0].metric("Setup", pl["setup"])
    c[1].metric("Entry", f"₦{pl['entry']:,.2f}" if pl["setup"].startswith("Breakout") else
                f"₦{pl['entry_low']:,.2f}–{pl['entry']:,.2f}")
    c[2].metric("Stop", f"₦{pl['stop']:,.2f}", f"-{pl['stop_pct']}%", delta_color="off")
    c[3].metric("Targets 2R / 3R", f"₦{pl['target_2r']:,.2f} / {pl['target_3r']:,.2f}")
    st.markdown(f"**Price action:** ₦{pa['price']:,.2f} · 1D {pct(pa['chg_1d'])} · 7D {pct(pa['chg_7d'])} · "
                f"30D {pct(pa['chg_30d'])} · YTD {pct(pa['chg_ytd'])} · "
                f"{pa['below_52w_high_pct']:.1f}% below the 52-week high ({naira(pa['high_52w'])})"
                + (f" · {pa['range_position_pct']:.0f}% of the 52-week range" if pa.get("range_position_pct") is not None else "")
                + f" · RS rank {pa['rs_rank']:.0f}")
    st.markdown(f"**Plan:** {pl['entry_text']}. Stop ₦{pl['stop']:,.2f}. "
                f"About {pl['shares_per_1m_capital']:,} shares per ₦1m of capital risks {pl['risk_per_trade_pct']}%.")
    facts = []
    if fa.get("eps_growth_pct") is not None:
        facts.append(f"EPS growth {pct(fa['eps_growth_pct'])} ({fa['eps_basis']})")
    if fa.get("sales_growth_pct") is not None:
        facts.append(f"sales growth {pct(fa['sales_growth_pct'])} ({fa['sales_basis']})")
    dd = fa.get("director_dealings_90d") or []
    if dd:
        facts.append(f"insider filings in 90 days: {sum(d['side'] == 'BUY' for d in dd)} buy, "
                     f"{sum(d['side'] == 'SELL' for d in dd)} sell")
    if fa.get("capital_raises_90d"):
        facts.append("capital raise filed: " + "; ".join(r["title"].title() for r in fa["capital_raises_90d"]))
    if fa.get("last_results"):
        lr = fa["last_results"]
        facts.append(f"last results filed {lr['date']}" + (f" ([PDF]({lr['pdf']}))" if lr.get("pdf") else ""))
    if facts:
        st.markdown("**Key facts:** " + " · ".join(facts))
    for n in pl.get("notes", []):
        st.caption("⚠️ " + n)

# ---- Market ---------------------------------------------------------------- #
with tab_market:
    if market.empty:
        st.info("No market wrap collected yet.")
    else:
        m = market.sort_values("date").iloc[-1]
        c = st.columns(4)
        c[0].metric("All-Share Index", f"{m['asi']:,.2f}" if pd.notna(m.get("asi")) else "—",
                    pct(m.get("asi_chg_pct")))
        c[1].metric("Market cap", naira(m.get("mcap")))
        c[2].metric("Advancers / decliners",
                    f"{int(m['advancers']) if pd.notna(m.get('advancers')) else '—'} / "
                    f"{int(m['decliners']) if pd.notna(m.get('decliners')) else '—'}")
        if not fx.empty:
            f = fx.sort_values("date").iloc[-1]
            c[3].metric("USD/NGN (CBN)", f.get("USD/NGN") if pd.notna(f.get("USD/NGN")) else "—")
        st.write(f"**{m['date']}** — {m['summary']}")
        if len(market) > 1:
            st.line_chart(market.set_index("date")["asi"], height=220)

    if not px.empty:
        wl = px[px.index.isin(tickers)].copy()
        wl.index.name = "Ticker"
        g, l = st.columns(2)
        g.subheader("Top gainers today")
        g.dataframe(wl.nlargest(8, "chg_1d")[["name", "price", "chg_1d", "chg_7d"]],
                    column_config={"name": "Name", "price": st.column_config.NumberColumn("Price ₦", format="%.2f"),
                                   "chg_1d": st.column_config.NumberColumn("1D %", format="%+.2f"),
                                   "chg_7d": st.column_config.NumberColumn("7D %", format="%+.2f")},
                    width="stretch")
        l.subheader("Top losers today")
        l.dataframe(wl.nsmallest(8, "chg_1d")[["name", "price", "chg_1d", "chg_7d"]],
                    column_config={"name": "Name", "price": st.column_config.NumberColumn("Price ₦", format="%.2f"),
                                   "chg_1d": st.column_config.NumberColumn("1D %", format="%+.2f"),
                                   "chg_7d": st.column_config.NumberColumn("7D %", format="%+.2f")},
                    width="stretch")

    st.subheader("Watchlist filings — last 7 days")
    if filings.empty:
        st.info("No filings collected yet.")
    else:
        recent = filings[filings["date"] >= (pd.Timestamp.today() - pd.Timedelta(days=7)).strftime("%Y-%m-%d")]
        st.dataframe(recent[["date", "ticker", "category", "title", "pdf"]],
                     column_config={"pdf": st.column_config.LinkColumn("PDF", display_text="Open")},
                     hide_index=True, width="stretch")

# ---- Swing ----------------------------------------------------------------- #
with tab_swing:
    if swing.empty:
        st.info("The swing screen runs with the daily data collection. No results yet.")
    else:
        cands = swing[swing["candidate"] == "yes"]
        st.subheader(f"This week's swing ideas ({len(ideas.get('ideas', []))})")
        st.caption(f"Week of {ideas.get('week_of', '—')} · rule-based plans, refreshed every Friday · "
                   "screen adapted from the MarketSmith checklist (edit swing_criteria.txt) · not investment advice")
        if not ideas.get("ideas"):
            st.info("No weekly ideas yet — they're generated on Fridays (or from a manual run).")
        for i in ideas.get("ideas", []):
            with st.expander(f"${i['ticker']} — {i['name']} · {i['plan']['setup']}", expanded=True):
                show_plan(i)
        st.subheader(f"Today's screen: {len(cands)} candidate(s)")
        view = swing.rename(columns=CHECK_LABELS)
        for lbl in CHECK_LABELS.values():
            view[lbl] = view[lbl].map(lambda v: MARK.get(v, v))
        cols = ["ticker", "name", "sector", "candidate", "passed", "price", "rs_rank", "below_52w_high_pct",
                "avg_value_traded", "roe"] + list(CHECK_LABELS.values())
        only = st.checkbox("Only candidates", value=False)
        st.dataframe(view[view["candidate"] == "yes"][cols] if only else view[cols],
                     column_config={
                         "ticker": "Ticker", "name": "Name", "sector": "Sector", "candidate": "Candidate",
                         "passed": st.column_config.NumberColumn("Passed", format="%d/11"),
                         "price": st.column_config.NumberColumn("Price ₦", format="%.2f"),
                         "rs_rank": st.column_config.NumberColumn("RS rank", format="%.0f"),
                         "below_52w_high_pct": st.column_config.NumberColumn("% below 52w high", format="%.1f"),
                         "avg_value_traded": st.column_config.NumberColumn("Avg value ₦", format="%.0f"),
                         "roe": st.column_config.NumberColumn("ROE %", format="%.1f")},
                     hide_index=True, width="stretch", height=560)
        st.caption("✅ pass · ❌ fail · ➖ not measurable yet. Must pass: data OK, trading, price, near 52-week high, "
                   "liquidity, RS rank, sector rank. Fundamentals (ROE, EPS, sales, up/down volume) must not fail. "
                   "Up/down volume needs 10 trading days of collected history.")

# ---- Screener -------------------------------------------------------------- #
with tab_screen:
    sc = build_screener()
    f1, f2, f3 = st.columns([2, 2, 3])
    sectors = sorted(s for s in sc["Sector"].dropna().unique() if s)
    sel_sec = f1.multiselect("Sector", sectors)
    sel_sig = f2.multiselect("Aboki signal", ["BUY", "HOLD", "SELL"])
    q = f3.text_input("Search ticker or name")
    view = sc
    if sel_sec:
        view = view[view["Sector"].isin(sel_sec)]
    if sel_sig:
        view = view[view["Aboki signal"].isin(sel_sig)]
    if q:
        view = view[view["Ticker"].str.contains(q, case=False) | view["Name"].str.contains(q, case=False)]
    num = {c: st.column_config.NumberColumn(c, format="%.2f") for c in
           ["Price ₦", "P/E", "EPS ₦", "ROE %", "Div %", "P/B", "Mkt cap ₦bn"]}
    num.update({c: st.column_config.NumberColumn(c, format="%+.2f") for c in ["1D %", "7D %", "30D %", "YTD %"]})
    num["Score"] = st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.0f")
    st.dataframe(view, column_config=num, hide_index=True, width="stretch", height=640)
    st.caption(f"{len(view)} of {len(sc)} tickers. Signal and score are Aboki Forex's model, not advice. "
               "None means that source does not cover the ticker (or has no value for it).")

# ---- Ticker ---------------------------------------------------------------- #
with tab_ticker:
    t = st.selectbox("Ticker", tickers, index=tickers.index("GTCO") if "GTCO" in tickers else 0)
    p = px.loc[t] if t in px.index else None
    f = fd.loc[t] if t in fd.index else None
    pr = profiles.get(t, {})
    name = p["name"] if p is not None else (f["name"].title() if f is not None else t)
    st.header(f"{name} ({t})")
    st.caption(" · ".join(x for x in [pr.get("sector"), f"profile updated {pr['updated']}" if pr.get("updated") else None] if x))

    def fnum(key, fmt="{:,.2f}", suffix=""):
        if f is None or pd.isna(f[key]):
            return "—"
        return fmt.format(float(f[key])) + suffix

    c = st.columns(4)
    c[0].metric("Price", naira(p["price"]) if p is not None else "—", pct(p["chg_1d"]) if p is not None else None)
    c[1].metric("7-day", pct(p["chg_7d"]) if p is not None else "—")
    c[2].metric("30-day", pct(pr.get("chg_30d")))
    c[3].metric("Market cap", naira(p["mcap"] if p is not None else (f["mcap"] if f is not None else None)))
    c = st.columns(4)
    c[0].metric("P/E", fnum("pe"))
    c[1].metric("EPS", naira(f["eps"]) if f is not None else "—")
    c[2].metric("ROE", fnum("roe", suffix="%"))
    c[3].metric("Div yield · P/B", f"{fnum('div_yield', suffix='%')} · {fnum('pb')}")
    if pr.get("low_52w"):
        st.caption(f"52-week range {naira(pr['low_52w'])} – {naira(pr['high_52w'])} · "
                   f"YTD {pct(pr.get('chg_ytd'))} · 1Y {pct(pr.get('chg_1y'))}"
                   + (f" · Broker consensus {pr['consensus']} (week of {pr['consensus_week']})"
                      if pr.get("consensus") else ""))

    hist = prices[prices["ticker"] == t].sort_values("date") if not prices.empty else pd.DataFrame()
    if len(hist) > 1:
        st.line_chart(hist.set_index("date")["price"], height=240)
        st.caption(f"Price history collected since {hist['date'].iloc[0]} — it grows by one point each trading day.")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Swing setup")
        idea = next((i for i in ideas.get("ideas", []) if i["ticker"] == t), None)
        if idea:
            st.success(f"On this week's swing list (week of {ideas.get('week_of')}).")
            show_plan(idea)
        if not sw.empty and t in sw.index:
            r = sw.loc[t]
            st.caption(f"Today's screen: {'candidate' if r['candidate'] == 'yes' else 'not a candidate'} · "
                       f"{int(r['passed'])}/11 checks passed")
            st.dataframe(pd.DataFrame({"Check": list(CHECK_LABELS.values()),
                                       "Result": [MARK.get(r[k], r[k]) for k in CHECK_LABELS]}),
                         hide_index=True, width="stretch")
        elif not idea:
            st.caption("Not scored yet.")

    with right:
        st.subheader("Reported results")
        r = results[results["ticker"] == t] if not results.empty else pd.DataFrame()
        if r.empty:
            st.caption("No parsed figures yet (Aboki Forex doesn't summarise every filing).")
        else:
            show = r[["period", "basis", "revenue", "pat", "eps", "filing_date"]].copy()
            show["revenue"] = show["revenue"].map(lambda v: naira(v, 1))
            show["pat"] = show["pat"].map(lambda v: naira(v, 1))
            show["eps"] = show["eps"].map(lambda v: naira(v, 2))
            st.dataframe(show.rename(columns={"period": "Period", "basis": "Basis", "revenue": "Revenue",
                                              "pat": "PAT", "eps": "EPS", "filing_date": "Filed"}),
                         hide_index=True, width="stretch")
            st.caption("As summarised by Aboki Forex from the filings; EPS converted from kobo.")

        tf = filings[filings["ticker"] == t] if not filings.empty else pd.DataFrame()
        st.subheader("Director dealings")
        dd = tf[tf["category"] == "Director Dealing"] if not tf.empty else tf
        if dd.empty:
            st.caption("None collected.")
        else:
            st.dataframe(pd.DataFrame({
                "Filed": dd["date"], "Side": dd["summary"].map(dealing_side),
                "Value": dd["summary"].map(dealing_value), "PDF": dd["pdf"]}),
                column_config={"PDF": st.column_config.LinkColumn("PDF", display_text="Open")},
                hide_index=True, width="stretch")
            st.caption("Buy/sell read from the filing summary wording — open the PDF to confirm.")
        raises = tf[tf["is_raise"]] if not tf.empty else tf
        if not raises.empty:
            st.subheader("Capital raises")
            for _, x in raises.iterrows():
                st.markdown(f"- {x['date']}: [{x['title']}]({x['pdf']})")

        if pr.get("peers"):
            st.subheader("Sector peers")
            peers = [t] + [x for x in pr["peers"] if x != t]
            pv = build_screener().set_index("Ticker")
            rows = [pv.loc[x] if x in pv.index else pd.Series({"Name": "(not in watchlist)"}, name=x) for x in peers]
            st.dataframe(pd.DataFrame(rows)[["Name", "Price ₦", "1D %", "Mkt cap ₦bn", "P/E", "ROE %", "Div %"]],
                         width="stretch")

    st.subheader("All filings")
    if tf.empty:
        st.caption("None collected.")
    else:
        for _, x in tf.head(30).iterrows():
            with st.expander(f"{x['date']} · {x['category']} · {x['title']}"):
                if x.get("summary"):
                    st.text(x["summary"])
                if x.get("pdf"):
                    st.markdown(f"[Open PDF]({x['pdf']})")

# ---- Filings --------------------------------------------------------------- #
with tab_filings:
    if filings.empty:
        st.info("No filings collected yet.")
    else:
        c1, c2, c3 = st.columns(3)
        cats = sorted(filings["category"].dropna().unique())
        sel = c1.multiselect("Category", cats)
        tk = c2.multiselect("Ticker", tickers)
        only_raise = c3.checkbox("Only capital raises")
        v = filings
        if sel:
            v = v[v["category"].isin(sel)]
        if tk:
            v = v[v["ticker"].isin(tk)]
        if only_raise:
            v = v[v["is_raise"]]
        st.dataframe(v[["date", "ticker", "category", "title", "pdf"]],
                     column_config={"pdf": st.column_config.LinkColumn("PDF", display_text="Open")},
                     hide_index=True, width="stretch", height=640)
        st.caption(f"{len(v)} filings for watchlist tickers since collection began.")
