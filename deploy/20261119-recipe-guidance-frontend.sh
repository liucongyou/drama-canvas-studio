#!/usr/bin/env bash
set -euo pipefail

APP=/www/wwwroot/yj.pduoduo.vip/app
COMMIT=${1:?usage: $0 <release-commit> <bundle.tar.gz>}
BUNDLE=${2:?usage: $0 <release-commit> <bundle.tar.gz>}
STAMP=$(date +%Y%m%d-%H%M%S)
STAGE="/tmp/recipe-guidance-frontend-$COMMIT-$STAMP"
BACKUP="$APP/backups/recipe-guidance-frontend-$COMMIT-$STAMP"
SWITCHED=0

test -f "$BUNDLE"
mkdir -p "$STAGE" "$BACKUP"
tar -tzf "$BUNDLE" | grep -Eq '(^/|(^|/)\.\.(/|$))' && {
  echo "unsafe bundle paths" >&2
  exit 1
}
tar -xzf "$BUNDLE" -C "$STAGE"
test -f "$STAGE/dist/index.html"

cp -a "$APP/web-vimax/dist" "$BACKUP/dist"
cp -a "$APP/RELEASE_COMMIT" "$BACKUP/RELEASE_COMMIT" 2>/dev/null || true

rollback() {
  set +e
  echo "rollback_started backup=$BACKUP" >&2
  rm -rf "$APP/web-vimax/dist.rollback"
  mv "$APP/web-vimax/dist" "$APP/web-vimax/dist.rollback"
  cp -a "$BACKUP/dist" "$APP/web-vimax/dist"
  if [ -f "$BACKUP/RELEASE_COMMIT" ]; then
    cp -a "$BACKUP/RELEASE_COMMIT" "$APP/RELEASE_COMMIT"
  fi
  chown -R www:www "$APP/web-vimax/dist" "$APP/RELEASE_COMMIT" 2>/dev/null || true
  echo "rollback_completed" >&2
}

finish() {
  status=$?
  if [ "$status" -ne 0 ] && [ "$SWITCHED" = "1" ]; then
    rollback
  fi
  exit "$status"
}
trap finish EXIT

rm -rf "$APP/web-vimax/dist.next"
cp -a "$STAGE/dist" "$APP/web-vimax/dist.next"
rm -rf "$APP/web-vimax/dist.previous"
mv "$APP/web-vimax/dist" "$APP/web-vimax/dist.previous"
mv "$APP/web-vimax/dist.next" "$APP/web-vimax/dist"
SWITCHED=1

printf '%s\n' "$COMMIT" > "$APP/RELEASE_COMMIT.next"
mv "$APP/RELEASE_COMMIT.next" "$APP/RELEASE_COMMIT"
chown -R www:www "$APP/web-vimax/dist" "$APP/RELEASE_COMMIT"

curl -fsS --max-time 10 http://127.0.0.1:8890/readyz >/dev/null
curl -fsS --max-time 10 https://yj.pduoduo.vip/ >/dev/null
grep -q 'assets/index-' "$APP/web-vimax/dist/index.html"

trap - EXIT
rm -rf "$APP/web-vimax/dist.previous"
echo "release=$COMMIT"
echo "backup=$BACKUP"
