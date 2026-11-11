#!/usr/bin/env bash
set -euo pipefail

APP=/www/wwwroot/yj.pduoduo.vip/app
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$APP/backups/reference-id-e288b00-$STAMP"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$BACKUP"
cp -a "$APP/server.py" "$BACKUP/server.py"
cp -a "$APP/provider_client.py" "$BACKUP/provider_client.py"
cp -a "$APP/explicit_references.py" "$BACKUP/explicit_references.py"
cp -a "$APP/agent/app/runtime.py" "$BACKUP/agent-runtime.py"
cp -a "$APP/web-vimax/dist" "$BACKUP/dist"
cp -a "$APP/RELEASE_COMMIT" "$BACKUP/RELEASE_COMMIT"

unzip -q /tmp/e288b00.zip -d "$WORK"

install -m 0644 "$WORK/server.py" "$APP/server.py"
install -m 0644 "$WORK/provider_client.py" "$APP/provider_client.py"
install -m 0644 "$WORK/explicit_references.py" "$APP/explicit_references.py"
install -m 0644 "$WORK/runtime.py" "$APP/agent/app/runtime.py"

rm -rf "$APP/web-vimax/dist.next"
cp -a "$WORK/dist" "$APP/web-vimax/dist.next"
rm -rf "$APP/web-vimax/dist.previous"
mv "$APP/web-vimax/dist" "$APP/web-vimax/dist.previous"
mv "$APP/web-vimax/dist.next" "$APP/web-vimax/dist"

printf '%s\n' 'e288b00' > "$APP/RELEASE_COMMIT"

systemctl restart drama-canvas-agent.service
systemctl restart drama-canvas-yj.service
sleep 2
systemctl is-active drama-canvas-agent.service drama-canvas-yj.service
printf 'BACKUP=%s\n' "$BACKUP"
