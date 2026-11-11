#!/usr/bin/env bash
set -euo pipefail

APP=/www/wwwroot/yj.pduoduo.vip/app
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$APP/backups/gate-scope-8991b9d-$STAMP"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$BACKUP"
cp -a "$APP/gate_check.py" "$BACKUP/gate_check.py"
cp -a "$APP/web-vimax/dist" "$BACKUP/dist"
cp -a "$APP/RELEASE_COMMIT" "$BACKUP/RELEASE_COMMIT"

unzip -q /tmp/release-8991b9d-20261111.zip -d "$WORK"
install -m 0644 "$WORK/gate_check.py" "$APP/gate_check.py"

rm -rf "$APP/web-vimax/dist.next"
cp -a "$WORK/dist" "$APP/web-vimax/dist.next"
rm -rf "$APP/web-vimax/dist.previous"
mv "$APP/web-vimax/dist" "$APP/web-vimax/dist.previous"
mv "$APP/web-vimax/dist.next" "$APP/web-vimax/dist"

printf '%s\n' '8991b9d' > "$APP/RELEASE_COMMIT"
systemctl restart drama-canvas-yj.service
sleep 2
systemctl is-active drama-canvas-yj.service drama-canvas-agent.service
printf 'BACKUP=%s\n' "$BACKUP"
