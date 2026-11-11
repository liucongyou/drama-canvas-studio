#!/usr/bin/env bash
set -euo pipefail

APP=/www/wwwroot/yj.pduoduo.vip/app
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$APP/backups/reference-id-gateway-e288b00-$STAMP"
mkdir -p "$BACKUP"
cp -a "$APP/agent_gateway.py" "$BACKUP/agent_gateway.py"
install -m 0644 /tmp/agent_gateway.e288b00.py "$APP/agent_gateway.py"
systemctl restart drama-canvas-yj.service
sleep 2
systemctl is-active drama-canvas-yj.service
printf 'BACKUP=%s\n' "$BACKUP"
