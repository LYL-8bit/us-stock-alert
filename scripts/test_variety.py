import sys
sys.path.insert(0, "/opt/news-alert")
import alert_service as a

cfg = a.load_config()
# 对 AAPL/MU/SNDK/TSM 各取最新一条相关申报，展示不同公司/事件类型的解读
demo_tickers = ["AAPL", "MU", "SNDK", "TSM"]
for company in cfg["watchlist"]:
    if company["ticker"] not in demo_tickers:
        continue
    try:
        data = a.fetch_submissions(company["cik"], cfg["sec_user_agent"])
    except Exception as e:
        print(f"{company['ticker']} 抓取失败: {e}")
        continue
    picked = None
    for f in a.iter_recent_filings(data):
        if f["form"] in company["forms"]:
            picked = f
            break
    if not picked:
        print(f"{company['ticker']} 无相关申报")
        continue
    print(f">>> {company['ticker']} {picked['form']} {picked['filingDate']} items={picked.get('items')}")
    try:
        a.process_filing(cfg, company, picked)
    except Exception as e:
        print(f"  处理失败: {e}")
print("=== 全部完成，检查手机 ===")
