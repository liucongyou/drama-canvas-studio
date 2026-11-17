#!/usr/bin/env bash
set -euo pipefail

SOURCE=${1:?usage: $0 <dist-dir> <release-commit>}
COMMIT=${2:?usage: $0 <dist-dir> <release-commit>}
APP=/www/wwwroot/yj.pduoduo.vip/app
FRONT="$APP/web-vimax/dist"
NEXT="$APP/web-vimax/dist.deploy-next"
PREV="$APP/web-vimax/dist.deploy-prev"
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$APP/backups/canvas-ux-stability-$COMMIT-$STAMP"
SWITCHED=0

test -f "$SOURCE/index.html"
ENTRY=$(grep -oE '/assets/index-[^" ]+\.js' "$SOURCE/index.html" | head -1)
test -n "$ENTRY"
test -f "$SOURCE$ENTRY"

mkdir -p "$BACKUP"
cp -a "$FRONT" "$BACKUP/dist"
cp -a "$APP/RELEASE_COMMIT" "$BACKUP/RELEASE_COMMIT"

rollback() {
  set +e
  if [ "$SWITCHED" = "1" ]; then
    rm -rf "$FRONT"
    mv "$PREV" "$FRONT"
    cp -a "$BACKUP/RELEASE_COMMIT" "$APP/RELEASE_COMMIT"
    chown -R www:www "$FRONT"
    chown www:www "$APP/RELEASE_COMMIT"
  fi
  echo "rollback_completed backup=$BACKUP" >&2
}

finish() {
  status=$?
  if [ "$status" -ne 0 ]; then
    rollback
  fi
  rm -rf "$NEXT"
  exit "$status"
}
trap finish EXIT

rm -rf "$NEXT" "$PREV"
cp -a "$SOURCE" "$NEXT"
chown -R www:www "$NEXT"
mv "$FRONT" "$PREV"
mv "$NEXT" "$FRONT"
SWITCHED=1

printf '%s\n' "$COMMIT" > "$APP/RELEASE_COMMIT"
chown www:www "$APP/RELEASE_COMMIT"

test "$(grep -oE '/assets/index-[^" ]+\.js' "$FRONT/index.html" | head -1)" = "$ENTRY"
curl -fsS --max-time 20 https://yj.pduoduo.vip/ >/dev/null
curl -fsS --max-time 20 https://yjapi.pduoduo.vip/api/auth/session >/dev/null
curl -fsS --max-time 20 "https://yj.pduoduo.vip$ENTRY" >/dev/null

rm -rf "$PREV"
trap - EXIT
echo "release=$COMMIT"
echo "entry=$ENTRY"
echo "backup=$BACKUP"
