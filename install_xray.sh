#!/bin/bash
set -e
mkdir -p /usr/local/etc/xray
install -m 755 /tmp/xray /usr/local/bin/xray
install -m 600 /tmp/xray-config.json /usr/local/etc/xray/config.json
install -m 644 /tmp/xray.service /etc/systemd/system/xray.service
systemctl daemon-reload
systemctl enable xray >/dev/null 2>&1
systemctl restart xray
sleep 2
systemctl is-active xray && echo "XRAY_ACTIVE" || echo "XRAY_FAILED"
/usr/local/bin/xray version | head -1
rm -f /tmp/xray /tmp/xray-config.json /tmp/xray.service
