#!/usr/bin/env bash
# Live end-to-end test. Output goes to test_out/ and is committed to claude/probe-data.
set -x
mkdir -p test_out
git fetch -q --depth 1 origin claude/probe-data && git checkout FETCH_HEAD -- data && python ngx_data.py 2>&1 | tee test_out/ngx_data.log
python - <<'PY' 2>&1 | tee test_out/daily_check.log
import json, datetime as dt, ngx_sources as s, ngx_daily as d
today = dt.datetime.now(s.WAT).date()
out = {"today": str(today)}
out["todays_disclosures"] = len(d.todays_disclosures(today))
out["todays_market"] = d.todays_market(today)
out["wrap_2026_10_20"] = s.scrape_market_wrap(dt.date(2026, 10, 20))
out["fx"] = s.scrape_cbn_fx()
for name, url in (("cbn", s.ABOKI + "/cbn"), ("wrap_page", s.KOBO + "/ngx-market-wrap/2026-10-20")):
    try:
        open(f"test_out/{name}.html", "w", encoding="utf-8").write(s.fetch(url))
    except Exception as e:
        out[name + "_error"] = repr(e)
print(json.dumps(out, indent=1, ensure_ascii=False, default=str))
PY
python ngx_research.py --dry-run 2>&1 | tee test_out/research_dry.log
pip install -q streamlit playwright
python - <<'PY' 2>&1 | tee test_out/apptest.log
from streamlit.testing.v1 import AppTest
at = AppTest.from_file("dashboard/app.py", default_timeout=120).run()
print("exceptions:", [e.value for e in at.exception])
print("errors:", [e.value for e in at.error]); print("warnings:", [w.value for w in at.warning])
print("titles:", [h.value for h in at.header], "tabs:", len(at.tabs))
at.selectbox[0].set_value("ZENITHBANK").run()
print("after select exceptions:", [e.value for e in at.exception])
PY
python -m playwright install --with-deps chromium > /dev/null 2>&1
(streamlit run dashboard/app.py --server.headless true --server.port 8501 > test_out/streamlit.log 2>&1 &)
sleep 15
python - <<'PY' 2>&1 | tee test_out/shots.log
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1440, "height": 1000})
    pg.goto("http://localhost:8501"); pg.wait_for_timeout(9000)
    pg.screenshot(path="test_out/1_market.png", full_page=True)
    for i, name in enumerate(["Screener", "Ticker", "Filings"], 2):
        pg.get_by_role("tab", name=name).click(); pg.wait_for_timeout(4000)
        if name == "Ticker":
            pg.get_by_test_id("stSelectbox").click(); pg.keyboard.type("ZENITHBANK"); pg.keyboard.press("Enter"); pg.wait_for_timeout(5000)
        pg.screenshot(path=f"test_out/{i}_{name.lower()}.png", full_page=True)
    print("shots ok")
PY
true
