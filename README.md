# NGX Daily Digest

Automated daily pipeline that scrapes NGX corporate disclosures and live market
data, formats them with Claude into ready-to-post tweet threads, and saves a
dated digest to this repo (and optionally Notion).

Built for [MintWise NGX](https://github.com/PlugPortal).

## What it does

Every weekday at **15:35 WAT** (just after the 14:30 market close) it:

1. Scrapes the day's filings from `abokiforex.app/ngx-stocks/disclosures`
2. Scrapes live market stats (ASI, market cap, breadth, movers, FX) from `ngxpulse.ng`
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

## Files

| File | Purpose |
|------|---------|
| `ngx_daily.py` | Scraper + Anthropic call + output writer |
| `system_prompt.txt` | Your tweet templates (edit freely) |
| `requirements.txt` | Python deps |
| `.github/workflows/ngx-daily.yml` | Daily schedule + manual trigger |
| `output/` | Generated digests (auto-committed) |
