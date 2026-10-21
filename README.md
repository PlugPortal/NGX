# NGX Daily Digest

Automated daily pipeline that scrapes NGX corporate disclosures and live market
data, formats them with Claude into ready-to-post tweet threads, and saves a
dated digest to this repo (and optionally Notion).

Built for [MintWise NGX](https://github.com/PlugPortal).

## What it does

Every weekday at **15:35 WAT** (just after the 14:30 market close) it:

1. Scrapes the day's filings from `abokiforex.app/ngx-stocks/disclosures`
2. Scrapes the market wrap from Kobo Terminal (formerly `ngxpulse.ng`) and CBN FX from Aboki Forex
3. Sends both to the Anthropic API with your fixed formatting template
4. Writes `output/ngx-digest-YYYY-MM-DD.md` with three sections:
   - **Director dealings** — your emoji block format
   - **Other disclosures** — one ≤280-char tweet each
   - **Market wrap** — a session-summary tweet + an FX companion tweet
5. Commits the digest back to the repo, and (optionally) pushes it to Notion for review

## Why these sources

`ngxgroup.com` and its disclosures page are JavaScript-gated and can't be scraped
without a headless browser. `abokiforex.app` and `ngxpulse.ng` render the same data
server-side as plain HTML, so they're reliable for automation.

## Setup (one-time)

1. **Create the repo** — push these files to a new repo, e.g. `PlugPortal/ngx-daily`.

2. **Add your Anthropic API key as a secret:**
   - Repo → Settings → Secrets and variables → Actions → New repository secret
   - Name: `ANTHROPIC_API_KEY`, Value: your key from console.anthropic.com

3. **(Optional) Enable Notion push:**
   - Add secrets `NOTION_TOKEN` (an internal integration token) and `NOTION_PAGE_ID`
     (the parent page to file digests under).
   - Add a repository *variable* `PUSH_TO_NOTION` = `1`.
   - Share the target Notion page with your integration.

4. **Allow the workflow to commit:**
   - Settings → Actions → General → Workflow permissions → "Read and write permissions".

That's it. The schedule runs automatically. To run on demand, go to the **Actions**
tab → NGX Daily Digest → Run workflow.

## Run locally

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python ngx_daily.py
# digest appears in output/
```

## Cost

Each run is one API call (~2–4k input tokens, ~2k output). On Opus that's a few
US cents per weekday; switch `MODEL` in `ngx_daily.py` to `claude-sonnet-4-6` to cut
that by roughly 5x with no real quality loss for this task.

## Customising the format

All formatting lives in `system_prompt.txt`. Edit the templates there — the Python
code never needs to change to adjust how tweets look.

## Posting to X/Twitter (optional next step)

This pipeline stops at a reviewed digest by design (so nothing posts unchecked).
To auto-post, add a step that reads `output/ngx-digest-*.md` and calls the X API v2
`POST /2/tweets` endpoint per block. Keep the manual-review default until you trust
the output.

## Daily data + research dashboard

A second workflow, **NGX Daily Data**, runs every weekday at 20:15 WAT (after
Kobo Terminal publishes the day's market wrap). It:

1. Collects free data for every ticker in `watchlist.txt` into `data/`:
   prices (Kobo Terminal), P/E, EPS, ROE, dividend yield, P/B and score
   (Aboki Forex), the market wrap, CBN FX, every NGX filing (director dealings,
   capital raises, results), per-period figures from results summaries, and
   sector / peers / 52-week range / broker consensus (refreshed weekly).
2. Runs Claude research **only for tickers that filed new results** (up to 10 a
   day by default). Claude gets the filing itself (key pages, or the scanned PDF),
   the collected context, and web search, and answers the two questions in
   `research_prompts.txt`. Answers are saved to `data/research/TICKER.json`.
3. Commits `data/` to the repo.

The Streamlit dashboard in `dashboard/app.py` only reads `data/`. Opening it
never calls Claude or any data site.

### Deploy the dashboard (one-time)

1. Go to https://share.streamlit.io → **Create app** → deploy from GitHub.
2. Repository `PlugPortal/NGX`, branch `main`, main file path `dashboard/app.py`.
3. Deploy. Streamlit copies each new commit, so the dashboard updates after
   every daily run.

Streamlit Community Cloud allows **one private app** per account. An app from a
public repo is public by default — see "Keep it private" below.

### Keep it private

Kobo Terminal's terms say free access doesn't grant redistribution rights, and
this repo is public, so anything in `data/` is visible on GitHub. To keep it to
yourself: make the repo private (Settings → General → Danger Zone → Change
visibility), then the Streamlit app deployed from it is private by default.

### Research controls (optional repository variables)

| Variable | Default | What it does |
|---|---|---|
| `MAX_RESEARCH_PER_RUN` | 10 | Tickers with new results researched per day; the rest wait for the next run |
| `RESEARCH_BACKFILL_PER_RUN` | 0 | Extra never-researched tickers per day, largest first |
| `RESEARCH_MODEL` | model in `ngx_daily.py` | Model used for research |
| `MAX_SEARCHES` | 6 | Web searches per question |

To research specific tickers now: **Actions** → NGX Daily Data → Run workflow →
type tickers (e.g. `GTCO,ZENITHBANK`). Tick "Collect data only" to skip Claude.

**Cost:** web search is $10 per 1,000 searches plus tokens; filing pages and
search results count as input tokens. Each research run logs its token use.
Check current rates at https://platform.claude.com/docs/en/about-claude/pricing.

Preview what research is due, without calling the API:

```bash
python ngx_research.py --dry-run
```

Run the dashboard on your own computer:

```bash
pip install -r requirements.txt -r dashboard/requirements.txt
streamlit run dashboard/app.py
```

## Files

| File | Purpose |
|------|---------|
| `ngx_daily.py` | Scraper + Anthropic call + output writer |
| `system_prompt.txt` | Your tweet templates (edit freely) |
| `requirements.txt` | Python deps |
| `.github/workflows/ngx-daily.yml` | Daily schedule + manual trigger |
| `ngx_sources.py` | Scrapers for Kobo Terminal and Aboki Forex (shared) |
| `ngx_data.py` | Daily free-data collector → `data/` |
| `ngx_research.py` | Claude research for tickers with new results |
| `watchlist.txt` | Tickers tracked |
| `research_prompts.txt` | Questions asked about each ticker |
| `research_system_prompt.txt` | NGX context and accuracy rules for research |
| `dashboard/app.py` | Streamlit dashboard |
| `.github/workflows/ngx-data.yml` | Daily data + research schedule + manual trigger |
| `data/` | Collected data and research (auto-committed) |
| `output/` | Generated digests (auto-committed) |
