import sys, sqlite3
sys.path.insert(0, "/opt/news-alert")
import alert_service as a

cfg = a.load_config()
# 取 MRVL 最新一条相关申报，从 seen.db 删除，让运行中的服务把它当"新申报"
target = None
for company in cfg["watchlist"]:
    if company["ticker"] != "MRVL":
        continue
    data = a.fetch_submissions(company["cik"], cfg["sec_user_agent"])
    for f in a.iter_recent_filings(data):
        if f["form"] in company["forms"]:
            target = f["accession"]
            break
conn = sqlite3.connect("/opt/news-alert/seen.db")
conn.execute("DELETE FROM seen WHERE accession=?", (target,))
conn.commit()
import datetime
print(f"已从 seen.db 删除 {target}（UTC {datetime.datetime.utcnow().strftime('%H:%M:%S')}）")
print("运行中的服务将在下一轮轮询(<=12s)把它当新申报检测并推送")
