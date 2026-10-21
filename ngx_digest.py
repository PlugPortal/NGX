"""
Free digest formatting — turns the day's filings and market wrap into
ready-to-post text without any AI call.

Sections (same order as the old Claude digest):
1. Director dealings  — emoji block per filing
2. Other disclosures  — one tweet each, most market-moving first
3. Market wrap tweet  — plus an FX companion tweet

The filing summaries on Aboki Forex are free text written in many styles,
so fields are only filled when they can be read reliably; anything not
found is left out rather than guessed. Every block keeps the PDF link.
"""

from __future__ import annotations

import re
import datetime as dt

import ngx_sources as src

TWEET_LIMIT = 280
OTHER_ORDER = ["Financial Results", "Corporate Action", "AGM Notice", "Board Meeting",
               "Earnings Forecast", "NGX Notice"]
EMOJI = {"Financial Results": "📊", "Corporate Action": "📣", "AGM Notice": "🗓️",
         "Board Meeting": "🏛️", "Earnings Forecast": "🔮", "NGX Notice": "📌"}
TAGS = {"Financial Results": "#NGXResults", "Corporate Action": "#CorporateAction",
        "AGM Notice": "#AGM", "Board Meeting": "#BoardMeeting",
        "Earnings Forecast": "#EarningsForecast", "NGX Notice": "#NGX"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def x_length(text: str) -> int:
    """Approximate X's character count: emoji and other wide characters count double."""
    return sum(2 if ord(ch) > 0x2FFF else 1 for ch in text)


def fit(prefix: str, body: str, suffix: str, limit: int = TWEET_LIMIT) -> str:
    """Join prefix + body + suffix, trimming body at a word boundary to fit."""
    room = limit - x_length(prefix) - x_length(suffix) - 2
    if x_length(body) > room:
        cut = body
        while x_length(cut) > room - 1 and " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        body = cut.rstrip(",;:") + "…"
    return f"{prefix} {body} {suffix}".strip()


def clean(text: str) -> str:
    t = re.sub(r"\*\*|__", "", text or "")
    t = re.sub(r"^[\s*•\-]+", "", t)
    return re.sub(r"\s+", " ", t).strip()


def bullets(summary: str) -> list[str]:
    return [clean(b) for b in re.split(r"\n\s*[*•]\s*|^\s*[*•]\s*", summary or "") if clean(b)]


def money(v: float) -> str:
    return f"₦{v:,.2f}"


_NUM = r"(\d[\d,]*(?:\.\d+)?)"
_CUR = r"(?:₦|N|NGN)\s?"
_MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"


def find_date(text: str) -> str | None:
    """'October 2, 2026', '30 September 2026', '23/09/2026' -> '2 Oct 2026'."""
    for pat, order in ((rf"({_MONTHS})\s+(\d{{1,2}}),\s*(20\d{{2}})", "mdy"),
                       (rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTHS})\s+(20\d{{2}})", "dmy"),
                       (r"(\d{1,2})/(\d{1,2})/(20\d{2})", "dmy_num")):
        m = re.search(pat, text)
        if not m:
            continue
        try:
            if order == "mdy":
                d = src.parse_date(f"{m.group(2)} {m.group(1)[:4]} {m.group(3)}")
            elif order == "dmy":
                d = src.parse_date(f"{m.group(1)} {m.group(2)[:4]} {m.group(3)}")
            else:
                d = dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            d = None
        if d:
            return f"{d.day} {d:%b %Y}"
    return None


# --------------------------------------------------------------------------- #
# Section 1 — director dealings
# --------------------------------------------------------------------------- #

def parse_dealing(summary: str) -> dict:
    s = clean(summary)
    low = s.lower()
    out: dict = {}
    buy = re.search(r"\b(purchas\w*|bought|acquir\w*|buy)\b", low)
    sell = re.search(r"\b(sold|sale|dispos\w*|sell)\b", low)
    if buy and not sell:
        out["side"] = "BUY"
    elif sell and not buy:
        out["side"] = "SELL"

    m = re.search(_NUM + r"\s+(?:units\s+of\s+)?(?:ordinary\s+)?(?:shares|units|equities|stocks)", s, re.I)
    if m:
        out["shares"] = int(float(m.group(1).replace(",", "")))

    m = (re.search(r"average price of\s*" + _CUR + _NUM, s, re.I)
         or re.search(r"price of\s*" + _CUR + _NUM, s, re.I)
         or re.search(r"\bat\s*" + _CUR + _NUM + r"\s*(?:K\s*)?per (?:share|unit)", s, re.I))
    if m:
        out["price"] = float(m.group(1).replace(",", ""))
        out["avg"] = "average" in m.group(0).lower()

    m = re.search(r"total (?:value|consideration)[^₦N]{0,40}" + _CUR + _NUM, s, re.I)
    if m:
        out["total"] = float(m.group(1).replace(",", ""))

    tx = re.search(r"(?:took place|executed|date of transaction)[^.]*", s, re.I)
    out["date"] = find_date(tx.group(0)) if tx else None
    out["date"] = out["date"] or find_date(s)

    first = bullets(summary)[0] if bullets(summary) else ""
    first = re.sub(r"^[A-Za-z/&' ]{3,40}:\s*", "", first)   # drop labels like "Insider Share Deal:"
    first = re.sub(r"\s+of\s+[A-Z][\w .&]*?Plc\b.*?(?= notified| purchased| sold|$)", "", first)
    m = re.match(r"(?:Insider:\s*)?(.+?\(.+?\))", first)
    if m and len(m.group(1)) < 90:
        out["who"] = m.group(1)
    else:
        m = re.match(r"(.+?),\s*(?:a|an|the)?\s*(.+?)\s+(?:of|at)\s+.+?,\s*(?:purchased|sold|acquired|disposed|notified)", first)
        if m and len(m.group(1)) < 60:
            out["who"] = f"{m.group(1)} ({m.group(2)})"
        else:
            m = re.match(r"(.{10,90}?)\s+(?:purchased|sold|acquired|disposed|notified|had)\b", first)
            if m:
                out["who"] = m.group(1)
    return out


def dealing_block(d: dict, company: str) -> str:
    p = parse_dealing(d.get("summary", ""))
    side = {"BUY": "🟢 BUY", "SELL": "🔴 SELL"}.get(p.get("side"), "⚪ DEALING")
    lines = ["#DirectorsDealing", side, f"🏭 {company}"]
    lines.append(f"📅 {p['date']}" if p.get("date") else f"📅 Filed {src.parse_date(d['date']).day} "
                 f"{src.parse_date(d['date']):%b %Y}" if d.get("date") else "📅 —")
    if p.get("who"):
        lines.append(f"👤 {p['who']}")
    if p.get("shares"):
        verb = {"BUY": "Bought", "SELL": "Sold"}.get(p.get("side"), "Dealt")
        lines.append(f"🧾 {verb}: {p['shares']:,} ordinary shares")
    if p.get("price"):
        lines.append(f"💰 {'Average price' if p.get('avg') else 'Price'}: {money(p['price'])} per share")
    total = p.get("total") or (p["shares"] * p["price"] if p.get("shares") and p.get("price") else None)
    if total:
        approx = "" if p.get("total") or not p.get("avg") else "≈ "
        lines.append(f"💼 Total Value: {approx}{money(total)}")
    lines.append(f"🇳🇬 #{d['ticker']} #DirectorsDealing")
    if d.get("pdf"):
        lines.append(f"🔗 {d['pdf']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Section 2 — other disclosures
# --------------------------------------------------------------------------- #

def disclosure_tweet(d: dict) -> str:
    cat = d["category"]
    bl = bullets(d.get("summary", ""))
    if cat == "Financial Results" and bl:
        # Aboki results summaries start "Q3 2026 | Unaudited" then the figures line.
        body = " — ".join(bl[:2])
    elif bl:
        body = bl[0]
    else:
        body = d.get("title", "").title()
    prefix = f"{EMOJI.get(cat, '📌')} ${d['ticker']}:"
    suffix = f"{TAGS.get(cat, '#NGX')} #NigerianStocks 🇳🇬"
    return fit(prefix, body, suffix)


# --------------------------------------------------------------------------- #
# Section 3 — market wrap + FX
# --------------------------------------------------------------------------- #

def market_tweet(wrap: dict) -> str:
    s = wrap.get("summary")
    if not s:
        return "📊 Today's NGX market wrap was not published at run time."
    parts = []
    if wrap.get("asi"):
        parts.append(f"ASI {wrap['asi']:,.2f} ({wrap.get('asi_chg_pct', 0):+.2f}%)")
    if wrap.get("mcap"):
        parts.append(f"Mkt cap ₦{wrap['mcap'] / 1e12:,.2f}tn")
    if wrap.get("advancers") is not None:
        parts.append(f"Breadth {wrap['advancers']}▲ {wrap.get('decliners', '?')}▼")
    m = re.search(r"([A-Z0-9]+) led gainers \(([-+]?[\d.]+)%", s)
    if m:
        parts.append(f"Top gainer ${m.group(1)} {m.group(2)}%")
    m = re.search(r"([A-Z0-9]+) paced losers \(([-+]?[\d.]+)%", s)
    if m:
        parts.append(f"Top loser ${m.group(1)} {m.group(2)}%")
    d = src.parse_date(s)
    head = f"📊 NGX close {d.day} {d:%b %Y}:" if d else "📊 NGX close:"
    return fit(head, " | ".join(parts), "#NGX #NigerianStocks 🇳🇬")


def fx_tweet(fx: dict) -> str:
    flags = {"USD/NGN": "🇺🇸 USD", "EUR/NGN": "🇪🇺 EUR", "GBP/NGN": "🇬🇧 GBP"}
    parts = []
    for k, label in flags.items():
        v = src.to_num(fx.get(k))
        if v:
            parts.append(f"{label} ₦{v:,.2f}")
    return ("FX (CBN official): " + " | ".join(parts)) if parts else "FX: CBN rates unavailable at run time."


# --------------------------------------------------------------------------- #
# Whole digest
# --------------------------------------------------------------------------- #

def build(disclosures: list[dict], wrap: dict, fx: dict, names: dict[str, str]) -> str:
    def company(t: str, title: str) -> str:
        n = names.get(t)
        if n:
            return n
        m = re.match(r"(.+?PLC)", title or "", re.I)
        return m.group(1).title() if m else t

    dd = [d for d in disclosures if d["category"] == "Director Dealing"]
    other = [d for d in disclosures if d["category"] != "Director Dealing"]
    other.sort(key=lambda d: OTHER_ORDER.index(d["category"]) if d["category"] in OTHER_ORDER else 99)

    out = ["DIRECTOR DEALINGS", ""]
    out.append("\n—\n".join(dealing_block(d, company(d["ticker"], d["title"])) for d in dd)
               if dd else "No director dealings filed today.")
    out += ["", "OTHER DISCLOSURES", ""]
    out.append("\n\n".join(disclosure_tweet(d) for d in other) if other else "No other disclosures filed today.")
    out += ["", "MARKET WRAP", "", market_tweet(wrap), fx_tweet(fx)]
    return "\n".join(out)
