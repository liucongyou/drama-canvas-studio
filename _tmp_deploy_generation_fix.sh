#!/bin/bash
set -euo pipefail

APP=/www/wwwroot/yj.pduoduo.vip/app
DIST="$APP/web-vimax/dist"
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$APP/backups/generation-fix-$STAMP"

mkdir -p "$BACKUP"
cp "$APP/server.py" "$BACKUP/server.py"
cp "$APP/provider_client.py" "$BACKUP/provider_client.py"
cp "$APP/canvas_store.py" "$BACKUP/canvas_store.py"
cp "$APP/network_security.py" "$BACKUP/network_security.py"
cp -a "$DIST" "$BACKUP/dist"

cp /tmp/server.py "$APP/server.py"
cp /tmp/provider_client.py "$APP/provider_client.py"
cp /tmp/canvas_store.py "$APP/canvas_store.py"
cp /tmp/network_security.py "$APP/network_security.py"

rm -rf "$DIST.next"
mkdir -p "$DIST.next"
tar -xzf /tmp/frontend-dist.tgz -C "$DIST.next" --strip-components=1

mv "$DIST" "$DIST.previous"
mv "$DIST.next" "$DIST"

systemctl restart drama-canvas-yj.service
if ! systemctl is-active --quiet drama-canvas-yj.service; then
  rm -rf "$DIST"
  mv "$DIST.previous" "$DIST"
  cp "$BACKUP/server.py" "$APP/server.py"
  cp "$BACKUP/provider_client.py" "$APP/provider_client.py"
  cp "$BACKUP/canvas_store.py" "$APP/canvas_store.py"
  cp "$BACKUP/network_security.py" "$APP/network_security.py"
  systemctl restart drama-canvas-yj.service
  exit 1
fi

rm -rf "$DIST.previous"
echo "$BACKUP"
