#!/usr/bin/env python3
"""
美股实时 8-K 预警服务
- 逐公司轮询 SEC EDGAR submissions JSON（直连）
- 命中 watchlist → 抓申报正文 → Grok 解读（走本地 SOCKS5 代理）→ Bark 推送（直连）
- SQLite 去重，首次运行只播种不轰炸
- 单点失败隔离：SEC/Grok/Bark 任一失败不影响主循环
"""
import os
import re
import sys
import time
import json
import sqlite3
import logging
import html as html_mod
from datetime import datetime, timezone, timedelta

import yaml
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
DB_PATH = os.path.join(BASE_DIR, "seen.db")

# 8-K item 代码 → 说人话的中文含义
ITEM_MEANINGS = {
    "1.01": "签了重大合同", "1.02": "终止重大合同", "1.03": "破产/接管",
    "2.01": "完成收购或卖资产", "2.02": "发布财报/业绩", "2.03": "新增大额借债",
    "2.04": "债务提前到期", "2.05": "重组裁员成本", "2.06": "资产减值",
    "3.01": "退市风险", "3.02": "增发新股", "3.03": "股东权利变更",
    "4.01": "更换审计师", "4.02": "过往财报不可信",
    "5.01": "控制权易主", "5.02": "高管/董事变动", "5.03": "公司章程修改",
    "5.07": "股东投票结果", "7.01": "对外披露消息", "8.01": "其他重大事件",
    "9.01": "附财报或附件",
}

# 表单类型 → 说人话
FORM_NAMES = {
    "8-K": "重大事项公告", "8-K/A": "重大事项补充",
    "6-K": "外国公司临时报告", "20-F": "外国公司年报",
}


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ==================== 去重存储 ====================

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS seen (accession TEXT PRIMARY KEY, ts TEXT)"
    )
    conn.commit()
    return conn


def is_seen(conn, accession):
    cur = conn.execute("SELECT 1 FROM seen WHERE accession=?", (accession,))
    return cur.fetchone() is not None


def mark_seen(conn, accession):
    conn.execute(
        "INSERT OR IGNORE INTO seen (accession, ts) VALUES (?, ?)",
        (accession, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def db_count(conn):
    return conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]


# ==================== SEC 抓取（直连）====================

def fetch_submissions(cik, ua, timeout=15):
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    r = requests.get(url, headers={"User-Agent": ua, "Accept-Encoding": "gzip"}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def iter_recent_filings(data):
    """把 submissions JSON 的并行数组转成逐条 dict。"""
    recent = data.get("filings", {}).get("recent", {})
    n = len(recent.get("accessionNumber", []))
    for i in range(n):
        yield {
            "accession": recent["accessionNumber"][i],
            "form": recent["form"][i],
            "filingDate": recent["filingDate"][i],
            "reportDate": recent.get("reportDate", [""] * n)[i],
            "items": recent.get("items", [""] * n)[i],
            "primaryDocument": recent.get("primaryDocument", [""] * n)[i],
            "primaryDocDescription": recent.get("primaryDocDescription", [""] * n)[i],
        }


def filing_url(cik, accession, primary_doc):
    acc_nodash = accession.replace("-", "")
    cik_int = int(cik)
    return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{primary_doc}"


def fetch_filing_text(url, ua, timeout=15, max_chars=7000):
    try:
        r = requests.get(url, headers={"User-Agent": ua}, timeout=timeout)
        r.raise_for_status()
        text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", r.text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = html_mod.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as e:
        logging.warning(f"抓取申报正文失败 {url}: {e}")
        return ""


def describe_items(items_str):
    """转成说人话的事件标签，去掉技术代码。"""
    if not items_str:
        return ""
    codes = [c.strip() for c in items_str.split(",") if c.strip()]
    seen = []
    for c in codes:
        meaning = ITEM_MEANINGS.get(c, "其他事项")
        if meaning not in seen:
            seen.append(meaning)
    return "、".join(seen)


# ==================== Grok 解读（走代理）====================

def grok_interpret(cfg, ticker, name_cn, form, items_desc, filing_text):
    # 优先从环境变量读密钥（由 systemd EnvironmentFile 注入），不落配置文件
    api_key = os.environ.get("GROK_API_KEY") or cfg.get("grok", {}).get("api_key", "")
    if not api_key or api_key.startswith("PUT_YOUR"):
        raise ValueError("未配置 GROK_API_KEY")
    proxy = cfg["grok"].get("proxy")
    proxies = {"http": proxy, "https": proxy} if proxy else None

    item_line = f"申报涉及：{items_desc}\n" if items_desc else ""
    body = filing_text if filing_text else "（正文抓取失败，请仅依据表单类型与申报项判断）"
    prompt = (
        f"你是帮散户做美股日内决策的助手，面向**不懂金融术语**的普通人。\n"
        f"{name_cn}({ticker}) 刚向 SEC 提交了一份申报。\n"
        f"{item_line}"
        f"正文摘录：\n{body}\n\n"
        f"请严格按下面格式输出，每行一项，说大白话、不用专业术语、不超过200字：\n"
        f"事件：用6到12个字概括发生了什么（像新闻标题，给手机通知用）\n"
        f"方向：利好 或 利空 或 中性\n"
        f"操作：给出明确的操作倾向，只能从这四个里选一个——「考虑买入」「考虑卖出/减仓」「持有观望」「不必操作」，后面可补一句简短条件或理由（如'若开盘回调可低吸''等财报电话会确认'）\n"
        f"解读：2句话讲清楚发生了什么、以及为什么会这样影响股价，像跟朋友说话一样自然\n"
        f"确定性：高 或 中 或 低（指这个操作判断有多靠谱）\n"
        f"数字：列出关键的财务或指引数字（如营收、EPS、增速），没有就写 无"
    )
    payload = {
        "model": cfg["grok"].get("model", "grok-4.3"),
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 400,
        "temperature": 0.2,
    }
    # 代理偶有抖动，重试 3 次，避免高价值消息（如财报）因单次超时丢掉解读
    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(
                "https://api.x.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                proxies=proxies,
                timeout=45,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            last_err = e
            logging.warning(f"Grok 第{attempt+1}/3 次失败，重试: {e}")
            time.sleep(2)
    raise last_err


# ==================== Bark 推送（直连）====================

def bark_push(cfg, title, body, url=None):
    # 注意：故意不设置 payload["url"]，这样点击通知会打开 Bark 查看详情，
    # 而不是直接跳浏览器；原文链接放在 body 文本里，在 Bark 内可点。
    bark_url = cfg["bark"]["url"].rstrip("/")
    proxy = cfg["grok"].get("proxy")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    payload = {
        "title": title,
        "body": body,
        "group": cfg["bark"].get("group", "美股预警"),
        "level": "timeSensitive",
        "sound": cfg["bark"].get("sound", "bell"),
        "isArchive": 1,
    }
    # api.day.app 国内直连偶有瞬时超时：先直连2次（快），仍失败则走代理（稳），不丢警报
    attempts = [(None, "直连"), (None, "直连"), (proxies, "代理")]
    for px, label in attempts:
        try:
            r = requests.post(bark_url, json=payload, timeout=10, proxies=px)
            r.raise_for_status()
            return True
        except Exception as e:
            logging.warning(f"Bark {label}失败，重试: {e}")
    logging.error("Bark 推送彻底失败（直连+代理均失败）")
    return False


# ==================== 处理单条新申报 ====================

def parse_interp(text):
    """把 Grok 的结构化输出解析成字段，容错：缺字段不报错。"""
    fields = {"事件": "", "方向": "", "操作": "", "解读": "", "确定性": "", "数字": ""}
    for line in text.splitlines():
        line = line.strip()
        for k in fields:
            for sep in ("：", ":"):
                if line.startswith(k + sep):
                    fields[k] = line[len(k) + 1:].strip()
                    break
    return fields


def process_filing(cfg, company, f):
    t0 = time.monotonic()
    ticker = company["ticker"]
    name_cn = company.get("name_cn", ticker)
    cik = company["cik"]
    items_desc = describe_items(f.get("items", ""))
    form_name = FORM_NAMES.get(f["form"], f["form"])
    url = filing_url(cik, f["accession"], f["primaryDocument"]) if f.get("primaryDocument") else None

    logging.info(f"🆕 检测到新申报 {ticker} {f['form']} {f['accession']} items={f.get('items','')}")

    filing_text = fetch_filing_text(url, cfg["sec_user_agent"]) if url else ""

    interp_raw = ""
    grok_ok = True
    try:
        interp_raw = grok_interpret(cfg, ticker, name_cn, f["form"], items_desc, filing_text)
    except Exception as e:
        logging.error(f"Grok 解读失败: {e}")
        grok_ok = False

    fd = f.get("filingDate", "")
    fd_short = fd[5:] if len(fd) == 10 else fd  # 06-11

    if grok_ok:
        fields = parse_interp(interp_raw)
        dir_word = fields["方向"] or "中性"
        emoji = "🟢" if "利好" in dir_word else "🔴" if "利空" in dir_word else "⚪"
        event = fields["事件"] or (items_desc.split("、")[0] if items_desc else form_name)

        # 操作倾向对应一个醒目图标
        action = fields["操作"] or "持有观望"
        if "买" in action:
            act_icon = "🟩 建议"
        elif "卖" in action or "减" in action:
            act_icon = "🟥 建议"
        elif "观望" in action:
            act_icon = "🟨 建议"
        else:
            act_icon = "⬜ 建议"

        title = f"{emoji} {name_cn} {ticker} · {event}"

        body_lines = []
        # 操作建议放最顶部，手机一眼就看到
        body_lines.append(f"{act_icon}：{action}")
        body_lines.append("")
        if fields["解读"]:
            body_lines.append(fields["解读"])
        else:
            body_lines.append(interp_raw.strip())  # 解析失败兜底用原文
        info = f"📊 {dir_word}"
        if fields["确定性"]:
            info += f" ｜ 把握{fields['确定性']}"
        body_lines.append("")
        body_lines.append(info)
        if fields["数字"] and fields["数字"] != "无":
            body_lines.append(f"🔢 {fields['数字']}")
        tag = f"🗂 {form_name}"
        if items_desc:
            tag += f" · {items_desc}"
        body_lines.append(tag)
        body_lines.append(f"🕐 {fd_short}")
        if url:
            body_lines.append(f"🔗 原文：{url}")
        body = "\n".join(body_lines)
    else:
        # 解读失败：仍把"哪家公司出了什么类型的事"推给你，附原文自己看
        emoji = "⚠️"
        title = f"⚠️ {name_cn} {ticker} · {items_desc or form_name}"
        body = (
            f"AI 解读暂时不可用（代理或接口异常），但有新申报：\n\n"
            f"🗂 {form_name}"
            + (f" · {items_desc}" if items_desc else "")
            + f"\n🕐 {fd_short}"
            + (f"\n🔗 原文：{url}" if url else "")
        )

    ok = bark_push(cfg, title, body, url)
    elapsed = time.monotonic() - t0
    if ok:
        logging.info(f"✅ 已推送 {ticker} 「{title}」 处理耗时 {elapsed:.1f}s")
    else:
        logging.error(f"❌ 推送失败 {ticker} {f['accession']} 耗时 {elapsed:.1f}s")


# ==================== 心跳 ====================

def heartbeat(cfg):
    hb = cfg.get("heartbeat", {}).get("uptimekuma_push_url")
    if hb:
        try:
            requests.get(hb, timeout=8)
        except Exception:
            pass


# ==================== 主循环 ====================

def main():
    setup_logging()
    cfg = load_config()
    conn = init_db()

    poll_interval = cfg.get("poll_interval", 12)
    ua = cfg["sec_user_agent"]
    watchlist = cfg["watchlist"]

    # 冷启动播种：首次运行把所有现存申报标记为已读，不轰炸
    seeding = db_count(conn) == 0
    if seeding:
        logging.info("首次运行：播种现存申报（不推送）...")

    # 新鲜度阈值：只对最近 N 天的申报推送（防止旧申报回填触发）
    fresh_days = cfg.get("fresh_days", 2)

    startup_pushed = False
    loop_count = 0
    while True:
        loop_count += 1
        for company in watchlist:
            try:
                data = fetch_submissions(company["cik"], ua)
            except Exception as e:
                logging.warning(f"抓取 {company['ticker']} submissions 失败: {e}")
                continue

            for f in iter_recent_filings(data):
                if f["form"] not in company["forms"]:
                    continue
                acc = f["accession"]
                if is_seen(conn, acc):
                    continue

                if seeding:
                    mark_seen(conn, acc)
                    continue

                # 新鲜度检查
                try:
                    fd = datetime.strptime(f["filingDate"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) - fd > timedelta(days=fresh_days):
                        mark_seen(conn, acc)
                        continue
                except Exception:
                    pass

                try:
                    process_filing(cfg, company, f)
                except Exception as e:
                    logging.error(f"处理 {company['ticker']} {acc} 失败: {e}")
                finally:
                    mark_seen(conn, acc)

            time.sleep(0.3)  # SEC 限速友好

        if seeding:
            logging.info(f"播种完成，已记录 {db_count(conn)} 条历史申报，进入监控模式")
            seeding = False
            if cfg.get("notify_on_start", True):
                bark_push(cfg, "✅ 美股预警已启动",
                          f"监控 {len(watchlist)} 个标的：" + "、".join(c["ticker"] for c in watchlist))

        heartbeat(cfg)
        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
