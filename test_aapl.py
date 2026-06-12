import sys
sys.path.insert(0, "/opt/news-alert")
import alert_service as a
cfg = a.load_config()
for company in cfg["watchlist"]:
    if company["ticker"] != "AAPL":
        continue
    data = a.fetch_submissions(company["cik"], cfg["sec_user_agent"])
    for f in a.iter_recent_filings(data):
        if f["form"] in company["forms"]:
            print(f">>> AAPL {f['form']} {f['filingDate']} items={f.get('items')}")
            a.process_filing(cfg, company, f)
            break
print("=== 完成 ===")
