#!/usr/bin/env bash
# Live end-to-end test (no AI). Output goes to test_out/ and is committed to claude/probe-data.
set -x
mkdir -p test_out
python ngx_data.py 2>&1 | tee test_out/ngx_data.log
python ngx_trade.py 2>&1 | tee test_out/trade.log
cp output/ngx-swing-*.md test_out/ 2>/dev/null
python ngx_daily.py 2>&1 | tee test_out/digest.log
cp output/ngx-digest-*.md test_out/ 2>/dev/null
pip install -q -r dashboard/requirements.txt playwright
python - <<'PY' 2>&1 | tee test_out/apptest.log
from streamlit.testing.v1 import AppTest
at = AppTest.from_file("dashboard/app.py", default_timeout=120).run()
print("exceptions:", [e.value for e in at.exception]); print("errors:", [e.value for e in at.error])
print("tabs:", len(at.tabs))
at.button[0].click().run()
print("after refresh exceptions:", [e.value for e in at.exception], "warnings:", [w.value for w in at.warning])
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
    pg.get_by_role("button", name="Refresh prices now").click(); pg.wait_for_timeout(9000)
    pg.screenshot(path="test_out/1_market.png", full_page=True)
    for i, name in enumerate(["Swing", "Ticker"], 2):
        pg.get_by_role("tab", name=name).click(); pg.wait_for_timeout(4000)
        if name == "Ticker":
            pg.get_by_test_id("stSelectbox").click(); pg.keyboard.type("ZENITHBANK"); pg.keyboard.press("Enter"); pg.wait_for_timeout(5000)
        pg.screenshot(path=f"test_out/{i}_{name.lower()}.png", full_page=True)
    print("shots ok")
PY
true
