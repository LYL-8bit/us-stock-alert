#!/bin/bash
set -e

# 创建无登录权限的服务用户
id newsbot >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin newsbot

mkdir -p /opt/news-alert
install -m 644 /tmp/alert_service.py /opt/news-alert/alert_service.py
install -m 600 /tmp/config.yaml /opt/news-alert/config.yaml
install -m 644 /tmp/requirements.txt /opt/news-alert/requirements.txt
install -m 644 /tmp/news-alert.service /etc/systemd/system/news-alert.service

# venv + 依赖（清华镜像）
if [ ! -d /opt/news-alert/venv ]; then
  python3 -m venv /opt/news-alert/venv
fi
/opt/news-alert/venv/bin/pip install -q --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
/opt/news-alert/venv/bin/pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r /opt/news-alert/requirements.txt

chown -R newsbot:newsbot /opt/news-alert
systemctl daemon-reload

echo "DEPLOY_OK"
/opt/news-alert/venv/bin/python -c "import requests, yaml, socks; print('deps ok')"
rm -f /tmp/alert_service.py /tmp/config.yaml /tmp/requirements.txt /tmp/news-alert.service
