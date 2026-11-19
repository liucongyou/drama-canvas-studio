#!/usr/bin/env bash
set -euo pipefail

APP=/www/wwwroot/yj.pduoduo.vip/app
SERVICE=drama-canvas-yj.service
COMMIT=${1:?usage: $0 <release-commit> <bundle.tar.gz>}
BUNDLE=${2:?usage: $0 <release-commit> <bundle.tar.gz>}
STAMP=$(date +%Y%m%d-%H%M%S)
STAGE="/tmp/voice-ref-import-fix-$COMMIT-$STAMP"
BACKUP="$APP/backups/voice-ref-import-fix-$COMMIT-$STAMP"
SWITCHED=0

test -f "$BUNDLE"
mkdir -p "$STAGE" "$BACKUP"
tar -tzf "$BUNDLE" | grep -Eq '(^/|(^|/)\.\.(/|$))' && {
  echo "unsafe bundle paths" >&2
  exit 1
}
tar -xzf "$BUNDLE" -C "$STAGE"
test -f "$STAGE/import_feed.py"

cp -a "$APP/import_feed.py" "$BACKUP/import_feed.py"
cp -a "$APP/RELEASE_COMMIT" "$BACKUP/RELEASE_COMMIT" 2>/dev/null || true

rollback() {
  set +e
  echo "rollback_started backup=$BACKUP" >&2
  systemctl stop "$SERVICE" 2>/dev/null || true
  install -m 0644 "$BACKUP/import_feed.py" "$APP/import_feed.py"
  if [ -f "$BACKUP/RELEASE_COMMIT" ]; then
    cp -a "$BACKUP/RELEASE_COMMIT" "$APP/RELEASE_COMMIT"
  fi
  chown www:www "$APP/import_feed.py" "$APP/RELEASE_COMMIT" 2>/dev/null || true
  systemctl start "$SERVICE" || true
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

systemctl stop "$SERVICE"
install -m 0644 "$STAGE/import_feed.py" "$APP/import_feed.py"
printf '%s\n' "$COMMIT" > "$APP/RELEASE_COMMIT.next"
mv "$APP/RELEASE_COMMIT.next" "$APP/RELEASE_COMMIT"
chown www:www "$APP/import_feed.py" "$APP/RELEASE_COMMIT"
SWITCHED=1
systemctl start "$SERVICE"

for _ in $(seq 1 60); do
  sleep 1
  if curl -fsS --max-time 5 http://127.0.0.1:8890/readyz >/dev/null; then
    break
  fi
done

systemctl is-active --quiet "$SERVICE"
curl -fsS --max-time 10 http://127.0.0.1:8890/readyz >/dev/null

trap - EXIT
echo "release=$COMMIT"
echo "backup=$BACKUP"
