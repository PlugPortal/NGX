# NGX Research Desk

Free, automated NGX market data, a daily tweet digest, a swing-trade screen with
weekly rule-based trade ideas, and a Streamlit dashboard. **No AI and no API keys**
— everything runs on GitHub Actions and Streamlit Community Cloud's free tiers.

Built for [MintWise NGX](https://github.com/PlugPortal). Personal research only — not investment advice.

## What runs, and when

| Workflow | When (WAT) | What it does |
|---|---|---|
| **NGX Daily Data** | Weekdays 20:15 | Collects free data for every ticker in `watchlist.txt` into `data/`, runs the swing screen, and on Fridays writes the weekly swing ideas |
| **NGX Daily Digest** | Weekdays 20:30 | Turns the day's filings, market wrap and CBN FX into ready-to-post tweet text in `output/ngx-digest-YYYY-MM-DD.md` |

Both run after Kobo Terminal stamps the day's market wrap "As of 20:00 WAT", and the
digest only uses a wrap dated today. Run either by hand from **Actions** → workflow →
**Run workflow**.

## Sources (free, public pages)

- **Kobo Terminal** (formerly ngxpulse.ng) — prices (30-min delayed), market wrap, sector,
  peers, 52-week range, broker consensus
- **Aboki Forex** — P/E, EPS, ROE, dividend yield, P/B, every NGX filing with summaries and
  PDF links, CBN official FX

Kobo's terms say free access doesn't grant redistribution rights, so keep the data for
personal use (see "Keep it private").

## Swing screen and weekly ideas

`ngx_swing.py` scores every ticker daily against `swing_criteria.txt`, an NGX version of the
MarketSmith checklist. You can edit the numbers there; no code changes are needed.

| MarketSmith | NGX version |
|---|---|
| Price ≥ $15 | Price ≥ ₦2 |
| Within 15% of 52-week high | Same |
| 50-day avg volume ≥ 400k | Avg daily value traded ≥ ₦10m |
| RS Rating ≥ 80 | RS rank ≥ 70 among the watchlist (weighted 1Y/YTD/30D/7D returns) |
| Industry group top 40 | NGX sector in the top half by 30-day return |
| ROE ≥ 17% | ROE ≥ 15% |
| EPS / sales growth ≥ 25% | ≥ 15%, latest period vs same period last year (when the filing summary has figures) |
| Up/down volume ≥ 1 | Same, once 10 trading days are collected |
| SMR, Acc/Dis, estimates, surprise, IPO date | Dropped — no free NGX data |
| — | Added: data-sanity check (implausible moves) and must have traded recently |

On Fridays, `ngx_trade.py` writes a plan for every candidate to `data/trade_ideas.json` and
`output/ngx-swing-YYYY-MM-DD.md`: price action, setup (breakout or pullback), entry,
an 8% stop, 2R/3R targets, position size per ₦1m at 1% risk, and key facts from filings.
The rules are at the top of `ngx_trade.py`.

Price history starts building from the first run, one point per trading day, so the
history-based checks (up/down volume, 20-day trend) fill in over the first few weeks.

## Dashboard (Streamlit)

`dashboard/app.py` has five tabs: Market, Swing, Screener, Ticker and Filings. It reads
`data/`, and its **Refresh prices now** button pulls the latest delayed prices from Kobo.

### Deploy (one-time)

1. Go to https://share.streamlit.io → **Create app** → deploy from GitHub.
2. Repository `PlugPortal/NGX`, branch `main`, main file path `dashboard/app.py`.
3. Deploy. Streamlit copies each new commit, so the dashboard updates after every daily run.

### Keep it private

This repo is public, so `data/` is visible on GitHub. To keep it to yourself, make the repo
private (Settings → General → Danger Zone → Change visibility). An app deployed from a
private repo is private by default. Streamlit Community Cloud allows **one private app** per account.

## Setup

1. **Workflow permissions:** Settings → Actions → General → Workflow permissions →
   "Read and write permissions".
2. **Optional Notion push for the digest:** secrets `NOTION_TOKEN` and `NOTION_PAGE_ID`,
   repository variable `PUSH_TO_NOTION` = `1`.
3. **Tickers:** edit `watchlist.txt`, one per line.

## Run locally

```bash
pip install -r requirements.txt -r dashboard/requirements.txt
python ngx_data.py      # collect data + swing screen
python ngx_trade.py     # weekly ideas
python ngx_daily.py     # digest
streamlit run dashboard/app.py
```

## Files

| File | Purpose |
|------|---------|
| `ngx_sources.py` | Scrapers for Kobo Terminal and Aboki Forex (shared) |
| `ngx_data.py` | Daily data collector → `data/` |
| `ngx_swing.py` | Swing screen → `data/swing.csv` |
| `swing_criteria.txt` | Swing screen thresholds (edit freely) |
| `ngx_trade.py` | Weekly rule-based trade ideas |
| `ngx_daily.py` + `ngx_digest.py` | Daily digest and its tweet templates |
| `watchlist.txt` | Tickers tracked |
| `dashboard/app.py` | Streamlit dashboard |
| `.github/workflows/` | Schedules |
| `data/`, `output/` | Collected data, ideas and digests (auto-committed) |
