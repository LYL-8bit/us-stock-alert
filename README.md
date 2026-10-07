# 美股实时 8-K 预警服务

事件驱动的美股重大事项实时预警。监控 SEC EDGAR 申报，命中 watchlist 后用 Grok 做中文解读，经 Bark 即时推送到 iPhone。

- **延迟**：从申报出现到手机推送约 10~20 秒
- **运行**：7×24 常驻，systemd 守护、崩溃自愈、开机自启
- **部署位置**：PVE 容器 101（Debian 12 / x86_64），`/opt/news-alert/`

---

## 架构

```
systemd: news-alert.service
  └─ 每 12s 轮询 SEC EDGAR submissions JSON（直连，带 User-Agent）
       └─ 命中 watchlist CIK + 关注表单类型
            ├─ 抓申报正文（直连）
            ├─ Grok 解读（走本地 Xray SOCKS5 代理，3 次重试）
            ├─ SQLite 去重（seen.db，首次运行播种不轰炸）
            └─ Bark 推送（直连优先，失败降级走代理）
  └─ 每轮 ping 一次 UptimeKuma 心跳

systemd: xray.service
  └─ 代理出站，本地监听 127.0.0.1:10808 SOCKS5
     仅 Grok 调用走它；SEC 抓取与 Bark 推送均直连
```

**网络要点**：该机器对 SEC、Bark 可直连，但 Grok（api.x.ai）需经代理访问，所以只有 Grok 这一步走代理。检测与推送这两条时效关键路径不依赖代理。

---

## 文件结构

| 文件 | 说明 |
|---|---|
| `alert_service.py` | 主服务：轮询 / 解读 / 去重 / 推送 |
| `config.yaml` | 配置：watchlist、轮询间隔、Bark、代理、心跳（**含敏感信息，不进 git**）|
| `news-alert.service` | 主服务 systemd 单元 |
| `xray-config.json` | Xray 客户端配置（**含代理凭据，不进 git**）|
| `xray.service` | Xray systemd 单元 |
| `requirements.txt` | Python 依赖：requests、requests[socks]、PyYAML |
| `seen.db` | SQLite 去重库（自动生成）|
| `/etc/news-alert.env` | Grok API 密钥（`chmod 600`，由 systemd 注入，**不进 git**）|
| `scripts/` | 手动调试脚本：端到端推送测试、CIK 查询、重放最新申报（需在部署机 `/opt/news-alert` 下运行）|

仓库里带 `.example` 后缀的是脱敏模板，部署时复制成实名文件再填。

---

## 常用运维

所有命令在容器 101 内以 root 执行。

```bash
# 看实时日志
journalctl -u news-alert -f -o cat

# 看最近 50 行
journalctl -u news-alert -n 50 --no-pager

# 重启 / 停止 / 状态
systemctl restart news-alert
systemctl stop news-alert
systemctl status news-alert

# 改配置后重启生效
nano /opt/news-alert/config.yaml && systemctl restart news-alert
```

### 增删监控标的

编辑 `config.yaml` 的 `watchlist`。需要公司的 CIK（10 位，前补零）。查 CIK：

```bash
curl -s -A "你的名字 你的邮箱" https://www.sec.gov/files/company_tickers.json \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print([ (r['ticker'],str(r['cik_str']).zfill(10)) for r in d.values() if r['ticker'] in ['NVDA','AVGO'] ])"
```

- 美国本土公司用表单 `["8-K","8-K/A"]`
- 外国发行人（如台积电）用 `["6-K","20-F"]`

改完 `systemctl restart news-alert`。新标的的历史申报会在重启时被播种（不推送），之后只推新的。

### 换推送铃声

`config.yaml` 里 `bark.sound`，可选：`healthnotification`、`bell`、`glass`、`chime`、`birdsong`、`calypso`、`horn`、`newsflash`、`telegraph`、`silence` 等。改完重启。

### 验证"有消息能否及时推"

从 seen.db 删掉某条最近申报，运行中的服务会在下一轮把它当新申报推送：

```bash
sqlite3 /opt/news-alert/seen.db \
  "DELETE FROM seen WHERE accession=(SELECT accession FROM seen LIMIT 1);"
journalctl -u news-alert -f -o cat   # 观察检测+推送
```

---

## 从零部署（参考）

```bash
apt update && apt install -y python3-venv python3-pip unzip sqlite3
useradd --system --no-create-home --shell /usr/sbin/nologin newsbot

# Xray（二进制需自行下载放到 /usr/local/bin/xray）
mkdir -p /usr/local/etc/xray
cp xray-config.json /usr/local/etc/xray/config.json   # 由 .example 填好
cp xray.service /etc/systemd/system/
systemctl enable --now xray

# 主服务
mkdir -p /opt/news-alert && cp alert_service.py config.yaml requirements.txt /opt/news-alert/
python3 -m venv /opt/news-alert/venv
/opt/news-alert/venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r /opt/news-alert/requirements.txt
echo 'GROK_API_KEY=你的key' > /etc/news-alert.env && chmod 600 /etc/news-alert.env && chown newsbot:newsbot /etc/news-alert.env
cp news-alert.service /etc/systemd/system/
chown -R newsbot:newsbot /opt/news-alert
systemctl daemon-reload && systemctl enable --now news-alert
```

---

## 故障排查

| 现象 | 排查 |
|---|---|
| 收不到推送 | `journalctl -u news-alert -n 50`；确认 `xray` 在跑 `systemctl status xray` |
| 推送显示"AI 解读暂不可用" | Grok 代理异常：`systemctl restart xray`；测试 `curl --socks5-hostname 127.0.0.1:10808 https://api.x.ai/v1/models` 应返回 401 |
| 服务反复重启 | `journalctl -u news-alert -n 80` 看 traceback；多半是 config.yaml 格式或 /etc/news-alert.env 缺失 |
| UptimeKuma 报 Down | 服务挂了或心跳 URL 改了，先看主服务状态 |

---

## 安全

- Grok 密钥只在 `/etc/news-alert.env`（`chmod 600`），由 systemd 注入环境变量，不落代码、不进 git
- 服务以无登录权限的 `newsbot` 用户运行
- 代理凭据、Bark key 在 `.gitignore` 内，不进仓库
