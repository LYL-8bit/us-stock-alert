import json, urllib.request
TICKERS = ["AAPL", "MRVL", "MU", "SNDK", "TSM"]
req = urllib.request.Request(
    "https://www.sec.gov/files/company_tickers.json",
    headers={"User-Agent": "yourname your-email@example.com"},
)
data = json.load(urllib.request.urlopen(req, timeout=20))
m = {}
for row in data.values():
    t = row["ticker"].upper()
    if t in TICKERS:
        m[t] = (str(row["cik_str"]).zfill(10), row["title"])
for t in TICKERS:
    if t in m:
        print(f"{t}\t{m[t][0]}\t{m[t][1]}")
    else:
        print(f"{t}\tNOT_FOUND")
