import sys
sys.path.insert(0, "/opt/news-alert")
import alert_service as a

cfg = a.load_config()

# 找出 watchlist 里最新的一条相关申报，跑完整链路
best = None
for company in cfg["watchlist"]:
    try:
        data = a.fetch_submissions(company["cik"], cfg["sec_user_agent"])
    except Exception as e:
        print("抓取失败", company["ticker"], e)
        continue
    for f in a.iter_recent_filings(data):
        if f["form"] not in company["forms"]:
            continue
        if best is None or f["filingDate"] > best[1]["filingDate"]:
            best = (company, f)
        break  # 最新优先，每公司取第一条匹配即可

if not best:
    print("没找到可测试的申报")
    sys.exit(1)

company, f = best
print(f"=== 测试申报: {company['ticker']} {f['form']} 申报日{f['filingDate']} items={f.get('items')} ===")
print("跑 SEC抓正文 → Grok解读(走代理) → Bark推送 ...")
a.process_filing(cfg, company, f)
print("=== 完成，检查手机 ===")
