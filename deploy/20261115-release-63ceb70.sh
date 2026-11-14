#!/usr/bin/env bash
set -euo pipefail

APP=/www/wwwroot/yj.pduoduo.vip/app
COMMIT=63ceb70
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$APP/backups/generation-terminal-recovery-$COMMIT-$STAMP"
RUNTIME_FILE=/tmp/canvas-store-63ceb70.py

test -f "$RUNTIME_FILE"
mkdir -p "$BACKUP"
cp -a "$APP/canvas_store.py" "$BACKUP/canvas_store.py"
cp -a "$APP/RELEASE_COMMIT" "$BACKUP/RELEASE_COMMIT"

install -m 0644 "$RUNTIME_FILE" "$APP/canvas_store.py"
printf '%s\n' "$COMMIT" > "$APP/RELEASE_COMMIT"
chown www:www "$APP/canvas_store.py" "$APP/RELEASE_COMMIT"

systemctl restart drama-canvas-yj.service
for attempt in $(seq 1 45); do
  if systemctl is-active --quiet drama-canvas-yj.service \
    && curl -fsS --max-time 5 http://127.0.0.1:8890/api/auth/session >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

systemctl is-active --quiet drama-canvas-yj.service
systemctl is-active --quiet drama-canvas-agent.service
curl -fsS --max-time 20 https://yjapi.pduoduo.vip/api/auth/session >/dev/null

echo "release=$(cat "$APP/RELEASE_COMMIT")"
echo "backup=$BACKUP"
