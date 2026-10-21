"""
NGX free data sources — scrapers shared by the daily digest, the data
collector and the research job.

Sources (all public pages, no login or API key):
- Kobo Terminal (formerly NGX Pulse)  https://koboterminal.com/
    * homepage price table: price, 24h %, 7d %, market cap, volume, shares O/S
    * dated market wrap:     /ngx-market-wrap/YYYY-MM-DD
    * stock pages:           /stocks/TICKER  (sector, peers, 52-week range,
                             30d/YTD/1y change, broker consensus)
- Aboki Forex  https://abokiforex.app/
    * /ngx-stocks/fundamentals   P/E, EPS, ROE, dividend yield, P/B, score
    * /ngx-stocks/disclosures    latest ~200 NGX filings with summaries + PDFs
    * /ngx-stocks/disclosures/T  a ticker's latest filings
    * /cbn                       CBN official exchange rates

Kobo's robots.txt allows these pages (it disallows only /api/). Aboki's
robots.txt allows everything. Kobo's terms say free access does not grant
commercial redistribution rights, so keep the outputs for personal use.
"""

from __future__ import annotations

import html as htmllib
import re
import time
import datetime as dt
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

WAT = ZoneInfo("Africa/Lagos")

KOBO = "https://koboterminal.com"
ABOKI = "https://abokiforex.app"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# Old NGX symbols that a source may still use, mapped to the current symbol.
# Aboki Forex still lists UH REIT as UHOMREIT and keeps stale rows for the others.
OLD_SYMBOLS = {"ABBEYBDS": "ABBEYBANK", "UHOMREIT": "UHREIT", "WAPCO": "HBMNG"}

_session = requests.Session()
_session.headers.update(HEADERS)


def fetch(url: str, retries: int = 2) -> str:
    """GET a page, with a short retry for transient errors."""
    for attempt in range(retries + 1):
        try:
            resp = _session.get(url, timeout=40)
            if resp.status_code >= 500 and attempt < retries:
                time.sleep(5)
                continue
            resp.raise_for_status()
            return resp.text
        except requests.RequestException:
            if attempt >= retries:
                raise
            time.sleep(5)
    raise RuntimeError("unreachable")


def fetch_bytes(url: str) -> bytes:
    resp = _session.get(url, timeout=90)
    resp.raise_for_status()
    return resp.content


# --------------------------------------------------------------------------- #
# Number helpers
# --------------------------------------------------------------------------- #

_SUFFIX = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def to_num(text: str | None) -> float | None:
    """'₦1,008.00' -> 1008.0, '-0.40%' -> -0.4, '5.07T' -> 5.07e12, '—' -> None."""
    if text is None:
        return None
    t = text.strip().replace("₦", "").replace(",", "").replace("%", "").strip()
    if not t or t in {"—", "-", "–", "N/A", "n/a"}:
        return None
    mult = 1.0
    if t[-1].upper() in _SUFFIX and re.match(r"^[-+]?\d", t):
        mult = _SUFFIX[t[-1].upper()]
        t = t[:-1]
    try:
        return float(t) * mult
    except ValueError:
        return None


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(20\d{2})")


def parse_date(text: str) -> dt.date | None:
    """'30 Sept 2026', '20 Oct 2026', '20 October 2026' -> date."""
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    mon = _MONTHS.get(m.group(2)[:4].lower()) or _MONTHS.get(m.group(2)[:3].lower())
    if not mon:
        return None
    try:
        return dt.date(int(m.group(3)), mon, int(m.group(1)))
    except ValueError:
        return None


def today_wat() -> dt.date:
    return dt.datetime.now(WAT).date()


# --------------------------------------------------------------------------- #
# Kobo Terminal — prices and market wrap
# --------------------------------------------------------------------------- #

def scrape_prices(html: str | None = None) -> list[dict]:
    """All NGX stocks from the Kobo homepage price table (30-min delayed)."""
    html = html or fetch(KOBO + "/")
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.select("tr"):
        tick = tr.select_one(".stock-ticker")
        price = tr.select_one(".price-cell")
        if not (tick and price):
            continue
        badges = tr.select(".pct-badge")
        name = tr.select_one(".stock-name")
        rows.append({
            "ticker": tick.get_text(strip=True).upper(),
            "name": name.get_text(strip=True) if name else "",
            "price": to_num(price.get_text()),
            "chg_1d": to_num(badges[0].get_text()) if len(badges) > 0 else None,
            "chg_7d": to_num(badges[1].get_text()) if len(badges) > 1 else None,
            "mcap": to_num(_text(tr, ".mcap-cell")),
            "volume": to_num(_text(tr, ".vol-cell")),
            "shares": to_num(_text(tr, ".shares-cell")),
        })
    return rows


def _text(node, selector: str) -> str | None:
    el = node.select_one(selector)
    return el.get_text(strip=True) if el else None


_WRAP_RE = re.compile(
    r"The Nigerian Exchange\b.*?As of \d{1,2}:\d{2} WAT,[^.]*?20\d{2}\.", re.S
)


def _wrap_from_text(text: str) -> str | None:
    m = _WRAP_RE.search(text)
    return re.sub(r"\s+", " ", m.group(0)).strip() if m else None


def scrape_market_wrap(day: dt.date | None = None) -> dict:
    """
    The day's market summary paragraph. Tries Kobo's dated wrap page first,
    then the homepage, and only returns a summary whose date matches `day`
    so the digest never reports yesterday's session as today's.
    """
    day = day or today_wat()
    summary = None
    source = None
    for url in (f"{KOBO}/ngx-market-wrap/{day:%Y-%m-%d}", KOBO + "/"):
        try:
            text = BeautifulSoup(fetch(url), "html.parser").get_text(" ", strip=True)
        except requests.RequestException:
            continue
        s = _wrap_from_text(text)
        if s and parse_date(s) == day:
            summary, source = s, url
            break
    out = {"date": day.isoformat(), "summary": summary, "source": source}
    if summary:
        out.update(parse_wrap_figures(summary))
    return out


def parse_wrap_figures(summary: str) -> dict:
    """Pull ASI close/change, market cap and breadth out of the wrap sentence."""
    f: dict = {}
    m = re.search(r"\(([-+]?\d+(?:\.\d+)?)%\)\s*to close at\s*([\d,]+(?:\.\d+)?)", summary)
    if m:
        f["asi_chg_pct"] = float(m.group(1))
        f["asi"] = to_num(m.group(2))
    m = re.search(r"to ₦([\d,.]+)\s*(trillion|billion)", summary)
    if m:
        f["mcap"] = to_num(m.group(1)) * (1e12 if m.group(2) == "trillion" else 1e9)
    for key, pat in (("advancers", r"(\d+) stocks advanced"), ("decliners", r"(\d+) declined"),
                     ("unchanged", r"(\d+) closed unchanged")):
        m = re.search(pat, summary)
        if m:
            f[key] = int(m.group(1))
    return f


def scrape_profile(ticker: str) -> dict:
    """Sector, peers, 52-week range, performance and broker consensus."""
    text = BeautifulSoup(fetch(f"{KOBO}/stocks/{ticker}"), "html.parser").get_text(" ", strip=True)
    p: dict = {"ticker": ticker}

    def grab(pat: str, flags=0):
        m = re.search(pat, text, flags)
        return m.groups() if m else None

    g = grab(r"is listed under the (.+?) sector")
    p["sector"] = g[0].strip() if g else None
    g = grab(r"stocks on the Nigerian Exchange, including ([A-Z0-9 ,]+?)\.")
    p["peers"] = [x.strip() for x in g[0].split(",") if x.strip()] if g else []
    g = grab(r"52-week range is ₦([\d,]+(?:\.\d+)?) to ₦([\d,]+(?:\.\d+)?)")
    if g:
        p["low_52w"], p["high_52w"] = to_num(g[0]), to_num(g[1])
    for key, label in (("chg_30d", "30-day change"), ("chg_ytd", "year-to-date change"),
                       ("chg_1y", "1-year change")):
        g = grab(label + r" of ([-+]?[\d.]+)%")
        p[key] = float(g[0]) if g else None
    g = grab(r"Week of (\d{1,2} \w+ 20\d{2}) (BUY|HOLD|SELL)")
    if g:
        p["consensus"], p["consensus_week"] = g[1], g[0]
        g2 = grab(r"(\d+) BROKERAGE RATINGS (\d+) buy (\d+) hold (\d+) sell")
        if g2:
            p["ratings"] = {"n": int(g2[0]), "buy": int(g2[1]), "hold": int(g2[2]), "sell": int(g2[3])}
    return p


# --------------------------------------------------------------------------- #
# Aboki Forex — fundamentals, disclosures, CBN FX
# --------------------------------------------------------------------------- #

_FUND_COLS = ["pe", "eps", "roe", "div_yield", "pb", "mcap"]


def scrape_fundamentals(html: str | None = None) -> list[dict]:
    """Snapshot fundamentals for ~125 NGX stocks."""
    html = html or fetch(ABOKI + "/ngx-stocks/fundamentals")
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for tr in soup.select("tr[data-symbol]"):
        tds = tr.find_all("td")
        if len(tds) < 10:
            continue
        sig = tds[2].get_text(strip=True).upper() or None
        rec = {"ticker": tds[0].get_text(strip=True).upper(),
               "name": tds[1].get_text(strip=True), "signal": sig}
        for col, td in zip(_FUND_COLS, tds[3:9]):
            rec[col] = to_num(td.get_text())
        score = tr.select_one(".score-val")
        rec["score"] = to_num(score.get_text()) if score else None
        out.append(rec)
    return out


_CATEGORY_ALIASES = {
    "director dealings": "Director Dealing",
    "directorsdealings": "Director Dealing",
    "financial statements": "Financial Results",
    "earningforcast": "Earnings Forecast",
    "corporate actions": "Corporate Action",
}


def norm_category(cat: str) -> str:
    c = re.sub(r"\s+", " ", cat or "").strip()
    return _CATEGORY_ALIASES.get(c.lower(), c)


def _filing_id(pdf: str | None, ticker: str, date: str, title: str) -> str:
    m = re.search(r"/(\d+)_[^/]*$", pdf or "")
    return m.group(1) if m else f"{ticker}|{date}|{title}"[:200]


def parse_disclosure_cards(html: str, ticker: str | None = None) -> list[dict]:
    """
    Parse Aboki disclosure cards. Works for the main feed (cards carry
    data-symbol) and per-ticker pages (pass the ticker).
    """
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for card in soup.select("div.disclosure-card"):
        tick = (card.get("data-symbol") or ticker or "").upper()
        badge = card.select_one(".type-badge")
        date_el = card.select_one(".card-date")
        title_el = card.select_one(".card-title")
        summ_el = card.select_one(".card-summary")
        pdf_el = card.select_one("a[href$='.pdf'], a.pdf-link, a[href*='doclib']")
        if not (tick and badge and date_el):
            continue
        d = parse_date(date_el.get_text(" ", strip=True))
        title = title_el.get_text(" ", strip=True) if title_el else ""
        summary = htmllib.unescape(summ_el.get_text("\n", strip=False)).strip() if summ_el else ""
        pdf = pdf_el.get("href") if pdf_el else None
        date_iso = d.isoformat() if d else ""
        out.append({
            "id": _filing_id(pdf, tick, date_iso, title),
            "ticker": tick,
            "category": norm_category(badge.get_text(" ", strip=True)),
            "date": date_iso,
            "title": title,
            "summary": summary,
            "pdf": pdf,
        })
    return out


def scrape_disclosures_feed() -> list[dict]:
    return parse_disclosure_cards(fetch(ABOKI + "/ngx-stocks/disclosures"))


def scrape_ticker_disclosures(ticker: str) -> list[dict]:
    return parse_disclosure_cards(fetch(f"{ABOKI}/ngx-stocks/disclosures/{ticker}"), ticker)


def scrape_cbn_fx() -> dict:
    """CBN official rates for USD, EUR, GBP and CNY to NGN, from Aboki's /cbn page."""
    text = BeautifulSoup(fetch(ABOKI + "/cbn"), "html.parser").get_text(" ", strip=True)
    fx = {}
    names = {"USD": r"(?:USD|US Dollar|Dollar)", "EUR": r"(?:EUR|Euro)",
             "GBP": r"(?:GBP|Pound(?: Sterling)?)", "CNY": r"(?:CNY|Yuan|Renminbi)"}
    for cur, pat in names.items():
        m = re.search(pat + r"[^₦\d]{0,40}₦?\s?([\d,]{3,}\.\d{1,4})", text)
        if m:
            fx[f"{cur}/NGN"] = "₦" + m.group(1)
    return fx


# --------------------------------------------------------------------------- #
# Results summaries -> per-period figures
# --------------------------------------------------------------------------- #

_PERIOD_RE = re.compile(r"^[•*\-]?\s*((?:Q[1-4]|H[12]|[369]M|9M|FY)\s*20\d{2})\s*\|\s*(.+)$")
_FIG_RE = re.compile(r"(Revenue|Gross Earnings|Turnover|Gross Premium|PBT|PAT|EPS|Equity|Assets)\s*:\s*(\(?[-₦\d.,]+[a-zA-Z]*\)?)")


def _money(s: str) -> float | None:
    """'₦920.7m' -> 920.7e6, '(₦105.8m)' -> -105.8e6, '(1.88k)' EPS kobo -> -0.0188 naira."""
    neg = s.strip().startswith("(") or s.strip().startswith("-")
    t = s.strip("() -").replace("₦", "").replace(",", "")
    m = re.match(r"([\d.]+)\s*([a-zA-Z]*)", t)
    if not m:
        return None
    v = float(m.group(1))
    unit = m.group(2).lower()
    v *= {"k": 1e3, "m": 1e6, "mn": 1e6, "bn": 1e9, "b": 1e9, "tn": 1e12, "t": 1e12}.get(unit, 1)
    return -v if neg else v


def parse_results_summary(summary: str) -> list[dict]:
    """
    Turn Aboki's results summary bullets into rows, e.g.
      • Q3 2026 | Unaudited
      • Revenue: ₦920.7m | PAT: (₦105.8m) | EPS: (1.88k) | ...
    EPS is reported in kobo ('k'); it is converted to naira.
    Returns [] when the summary has no figures.
    """
    rows: list[dict] = []
    cur: dict | None = None
    for raw in (summary or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _PERIOD_RE.match(line)
        if m:
            cur = {"period": re.sub(r"\s+", " ", m.group(1)), "basis": m.group(2).strip()}
            rows.append(cur)
            continue
        if cur is None:
            continue
        for label, val in _FIG_RE.findall(line):
            key = {"gross earnings": "revenue", "turnover": "revenue",
                   "gross premium": "revenue"}.get(label.lower(), label.lower())
            v = _money(val)
            if key == "eps" and v is not None:
                v = v / 1e3 / 100 if val.rstrip(")").lower().endswith("k") else v
            cur.setdefault(key, v)
    return [r for r in rows if len(r) > 2]
