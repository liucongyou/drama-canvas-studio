#!/bin/bash
set -euo pipefail

APP=/www/wwwroot/yj.pduoduo.vip/app
ARCHIVE=/tmp/drama-canvas-release-dae014b.tgz
PKG=/tmp/release-dae014b
EXPECTED_SHA=dcf06f486fdd34f35023602c72514f3ea28dbbbba5b67f5f1af678bd13095158
COMMIT=dae014b
ENTRY=assets/index-DdlSQlY6.js
SERVICE=drama-canvas-yj.service
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$APP/backups/$COMMIT-$STAMP"
FILES=(
  server.py
  canvas_store.py
  generation_outcome.py
  provider_protocols.py
  provider_catalog.py
  media_storage.py
  tos_storage.py
)

test -f "$ARCHIVE"
test "$(sha256sum "$ARCHIVE" | awk '{print $1}')" = "$EXPECTED_SHA"

rm -rf "$PKG"
mkdir -p "$PKG"
tar -xzf "$ARCHIVE" -C "$PKG"

test "$(tr -d '[:space:]' < "$PKG/RELEASE_COMMIT")" = "$COMMIT"
grep -q "$ENTRY" "$PKG/web-vimax/dist/index.html"
for name in "${FILES[@]}"; do
  test -f "$PKG/$name"
done

"$APP/.venv/bin/python" -m py_compile \
  "$PKG/server.py" \
  "$PKG/canvas_store.py" \
  "$PKG/generation_outcome.py" \
  "$PKG/provider_protocols.py" \
  "$PKG/provider_catalog.py" \
  "$PKG/media_storage.py" \
  "$PKG/tos_storage.py"

mkdir -p "$BACKUP/backend" "$BACKUP/dist" "$BACKUP/db"
for name in "${FILES[@]}"; do
  if [ -f "$APP/$name" ]; then
    cp -a "$APP/$name" "$BACKUP/backend/$name"
  fi
done
cp -a "$APP/web-vimax/dist/." "$BACKUP/dist/"

for db in "$APP"/data/tenants/*/canvas.db; do
  tenant=$(basename "$(dirname "$db")")
  sqlite3 "$db" ".backup '$BACKUP/db/$tenant-canvas.db'"
  sqlite3 "$BACKUP/db/$tenant-canvas.db" "PRAGMA quick_check;" | grep -qx ok
done

restore() {
  set +e
  for name in "${FILES[@]}"; do
    if [ -f "$BACKUP/backend/$name" ]; then
      cp -a "$BACKUP/backend/$name" "$APP/$name"
      chown www:www "$APP/$name"
      chmod 664 "$APP/$name"
    else
      rm -f "$APP/$name"
    fi
  done
  rm -rf "$APP/web-vimax/dist"
  cp -a "$BACKUP/dist" "$APP/web-vimax/dist"
  chown -R www:www "$APP/web-vimax/dist"
  systemctl restart "$SERVICE"
  set -e
}

for name in "${FILES[@]}"; do
  install -o www -g www -m 664 "$PKG/$name" "$APP/$name"
done

rm -rf "$APP/web-vimax/dist.next"
mkdir -p "$APP/web-vimax/dist.next"
cp -a "$PKG/web-vimax/dist/." "$APP/web-vimax/dist.next/"
chown -R www:www "$APP/web-vimax/dist.next"
rm -rf "$APP/web-vimax/dist.previous"
mv "$APP/web-vimax/dist" "$APP/web-vimax/dist.previous"
mv "$APP/web-vimax/dist.next" "$APP/web-vimax/dist"

cd "$APP"
systemctl restart "$SERVICE"
sleep 8

if ! systemctl is-active --quiet "$SERVICE"; then
  restore
  exit 1
fi
if ! curl --fail --silent --show-error https://yj.pduoduo.vip/ >/dev/null; then
  restore
  exit 1
fi
if ! curl --fail --silent --show-error "https://yj.pduoduo.vip/$ENTRY" >/dev/null; then
  restore
  exit 1
fi

session_status=$(
  curl --silent \
    --output /dev/null \
    --write-out '%{http_code}' \
    https://yjapi.pduoduo.vip/api/auth/session
)
if [ "$session_status" != 200 ] && [ "$session_status" != 401 ]; then
  restore
  exit 1
fi

journalctl -u "$SERVICE" --since "2 minutes ago" --no-pager \
  | grep -E 'Traceback|schema.*(fail|error)|recovery.*(fail|error)' \
  && { restore; exit 1; } || true

echo "RELEASE=$COMMIT"
echo "BACKUP=$BACKUP"
echo "ENTRY=$ENTRY"
echo "SERVICE=$(systemctl is-active "$SERVICE")"
echo "SESSION_STATUS=$session_status"
sha256sum "${FILES[@]}" "$APP/web-vimax/dist/index.html"
