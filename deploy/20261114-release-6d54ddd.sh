#!/usr/bin/env bash
set -euo pipefail

APP=/www/wwwroot/yj.pduoduo.vip/app
COMMIT=6d54ddd
ARCHIVE=/tmp/6d54ddd-dist.tgz
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$APP/backups/frontend-concurrent-generation-$COMMIT-$STAMP"
WORK=$(mktemp -d)
FRONT="$APP/web-vimax/dist"
FRONT_NEXT="$APP/web-vimax/dist.deploy-next"
FRONT_PREV="$APP/web-vimax/dist.deploy-prev"
FRONT_SWITCHED=0

cleanup() {
  rm -rf "$WORK"
}

rollback() {
  set +e
  if [ "$FRONT_SWITCHED" = "1" ]; then
    rm -rf "$FRONT"
    if [ -d "$FRONT_PREV" ]; then
      mv "$FRONT_PREV" "$FRONT"
    fi
  fi
  if [ -f "$BACKUP/RELEASE_COMMIT" ]; then
    cp -a "$BACKUP/RELEASE_COMMIT" "$APP/RELEASE_COMMIT"
  fi
  systemctl restart drama-canvas-yj.service
}

finish() {
  status=$?
  if [ "$status" -ne 0 ]; then
    rollback
  fi
  cleanup
  exit "$status"
}
trap finish EXIT

tar -xzf "$ARCHIVE" -C "$WORK"
test -f "$WORK/dist/index.html"
ENTRY=$(grep -oE '/assets/index-[^" ]+\.js' "$WORK/dist/index.html" | head -1)
test -n "$ENTRY"
test -f "$WORK/dist$ENTRY"

mkdir -p "$BACKUP"
cp -a "$FRONT" "$BACKUP/dist"
cp -a "$APP/RELEASE_COMMIT" "$BACKUP/RELEASE_COMMIT"

rm -rf "$FRONT_NEXT" "$FRONT_PREV"
cp -a "$WORK/dist" "$FRONT_NEXT"
mv "$FRONT" "$FRONT_PREV"
mv "$FRONT_NEXT" "$FRONT"
FRONT_SWITCHED=1

printf '%s\n' "$COMMIT" > "$APP/RELEASE_COMMIT"
chown www:www "$APP/RELEASE_COMMIT"

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
test "$(grep -oE '/assets/index-[^" ]+\.js' "$FRONT/index.html" | head -1)" = "$ENTRY"
test -f "$FRONT$ENTRY"
curl -fsS --max-time 20 https://yjapi.pduoduo.vip/api/auth/session >/dev/null

rm -rf "$FRONT_PREV"
echo "release=$(cat "$APP/RELEASE_COMMIT")"
echo "entry=$ENTRY"
echo "backup=$BACKUP"
