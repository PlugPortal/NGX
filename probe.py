import json, os, re, sys, datetime as dt
import requests
os.makedirs("probe_out", exist_ok=True)
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
meta = {}
pages = {
 "aboki_fund": "https://abokiforex.app/ngx-stocks/fundamentals",
 "aboki_signals": "https://abokiforex.app/ngx-stocks",
 "aboki_disc": "https://abokiforex.app/ngx-stocks/disclosures",
 "aboki_disc_GTCO": "https://abokiforex.app/ngx-stocks/disclosures/GTCO",
 "aboki_disc_DANGCEM": "https://abokiforex.app/ngx-stocks/disclosures/DANGCEM",
 "aboki_robots": "https://abokiforex.app/robots.txt",
 "kobo_home": "https://koboterminal.com/",
 "kobo_stock": "https://koboterminal.com/stocks/ZENITHBANK",
 "kobo_api_doc": "https://koboterminal.com/api",
 "kobo_api_stocks_nokey": "https://koboterminal.com/api/ngxdata/stocks",
 "kobo_api_market_nokey": "https://koboterminal.com/api/ngxdata/market",
 "kobo_robots": "https://koboterminal.com/robots.txt",
 "ngxpulse": "https://ngxpulse.ng/",
}
for k, u in pages.items():
    try:
        r = requests.get(u, headers=H, timeout=40)
        meta[k] = {"url": u, "status": r.status_code, "final": r.url, "ctype": r.headers.get("content-type"), "len": len(r.text),
                   "history": [h.status_code for h in r.history]}
        open(f"probe_out/{k}.html", "w", encoding="utf-8").write(r.text)
    except Exception as e:
        meta[k] = {"url": u, "error": str(e)}
# run existing scrapers
try:
    import ngx_daily as d
    disc_html = open("probe_out/aboki_disc.html", encoding="utf-8").read()
    labels = sorted(set(re.findall(r"\d{1,2}\s+[A-Z][a-z]{2}\s+20\d{2}", disc_html)))
    meta["scrape"] = {"today_label": d.today_str(), "date_labels_in_page": labels[:40],
        "today_count": len(d.scrape_disclosures(disc_html, d.today_str())),
        "market": d.scrape_market(open("probe_out/ngxpulse.html", encoding="utf-8").read())}
    from bs4 import BeautifulSoup
    t = BeautifulSoup(disc_html, "html.parser").get_text("\n", strip=True)
    open("probe_out/aboki_disc_text.txt","w",encoding="utf-8").write(t)
    open("probe_out/aboki_disc_GTCO_text.txt","w",encoding="utf-8").write(BeautifulSoup(open("probe_out/aboki_disc_GTCO.html",encoding="utf-8").read(),"html.parser").get_text("\n",strip=True))
    open("probe_out/aboki_fund_text.txt","w",encoding="utf-8").write(BeautifulSoup(open("probe_out/aboki_fund.html",encoding="utf-8").read(),"html.parser").get_text("\n",strip=True))
    open("probe_out/kobo_home_text.txt","w",encoding="utf-8").write(BeautifulSoup(open("probe_out/kobo_home.html",encoding="utf-8").read(),"html.parser").get_text("\n",strip=True))
    open("probe_out/kobo_stock_text.txt","w",encoding="utf-8").write(BeautifulSoup(open("probe_out/kobo_stock.html",encoding="utf-8").read(),"html.parser").get_text("\n",strip=True))
except Exception as e:
    meta["scrape_error"] = repr(e)
# try one results PDF
try:
    g = open("probe_out/aboki_disc_GTCO.html", encoding="utf-8").read()
    pdfs = re.findall(r'href="([^"]+\.pdf[^"]*)"', g, flags=re.I) + re.findall(r'href="(https?://doclib[^"]+)"', g)
    meta["gtco_pdf_links"] = pdfs[:15]
    if pdfs:
        r = requests.get(pdfs[0], headers=H, timeout=60)
        meta["pdf0"] = {"status": r.status_code, "ctype": r.headers.get("content-type"), "bytes": len(r.content)}
        open("probe_out/pdf0.pdf", "wb").write(r.content)
        from pypdf import PdfReader
        rd = PdfReader("probe_out/pdf0.pdf")
        meta["pdf0"]["pages"] = len(rd.pages)
        open("probe_out/pdf0.txt","w",encoding="utf-8").write("\n\n".join((p.extract_text() or "") for p in rd.pages[:6]))
except Exception as e:
    meta["pdf_error"] = repr(e)
json.dump(meta, open("probe_out/meta.json", "w"), indent=2, default=str)
print(json.dumps(meta, indent=2, default=str)[:3000])
