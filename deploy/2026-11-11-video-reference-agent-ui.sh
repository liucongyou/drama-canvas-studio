#!/bin/bash
set -euo pipefail

APP=/www/wwwroot/yj.pduoduo.vip/app
COMMIT=f55e36e
ARCHIVE=/tmp/release-20261111-f55e36e.tgz
EXPECTED_SHA=c75f0a33e6629792fe4ba10b6ef2ae632d22ed635c0eab3e0827cd9069706787
ENTRY=assets/index-DV6uACSx.js
STAGE=/tmp/release-20261111-f55e36e
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$APP/backups/video-ref-agent-ui-$COMMIT-$STAMP"
MAIN_SERVICE=drama-canvas-yj.service
AGENT_SERVICE=drama-canvas-agent.service

test "$(sha256sum "$ARCHIVE" | awk '{print $1}')" = "$EXPECTED_SHA"
rm -rf "$STAGE"
mkdir -p "$STAGE"
tar -xzf "$ARCHIVE" -C "$STAGE"
test -f "$STAGE/provider_client.py"
test -f "$STAGE/server.py"
test -f "$STAGE/web-vimax/dist/index.html"
grep -q "$ENTRY" "$STAGE/web-vimax/dist/index.html"
test -f "$STAGE/web-vimax/dist/$ENTRY"

mkdir -p "$BACKUP"
cp -a "$APP/provider_client.py" "$BACKUP/provider_client.py"
cp -a "$APP/server.py" "$BACKUP/server.py"
cp -a "$APP/web-vimax/dist" "$BACKUP/dist"

restore() {
  set +e
  cp -a "$BACKUP/provider_client.py" "$APP/provider_client.py"
  cp -a "$BACKUP/server.py" "$APP/server.py"
  rm -rf "$APP/web-vimax/dist"
  cp -a "$BACKUP/dist" "$APP/web-vimax/dist"
  chown -R www:www "$APP/provider_client.py" "$APP/server.py" "$APP/web-vimax/dist"
  systemctl restart "$MAIN_SERVICE"
  echo "VIDEO_REFERENCE_AGENT_UI_RELEASE_FAILED_AND_RESTORED"
}
trap restore ERR

install -o www -g www -m 0644 "$STAGE/provider_client.py" "$APP/provider_client.py"
install -o www -g www -m 0644 "$STAGE/server.py" "$APP/server.py"

rm -rf "$APP/web-vimax/dist.next"
mkdir -p "$APP/web-vimax/dist.next"
cp -a "$STAGE/web-vimax/dist/." "$APP/web-vimax/dist.next/"
chown -R www:www "$APP/web-vimax/dist.next"
rm -rf "$APP/web-vimax/dist.previous"
mv "$APP/web-vimax/dist" "$APP/web-vimax/dist.previous"
mv "$APP/web-vimax/dist.next" "$APP/web-vimax/dist"

systemctl restart "$MAIN_SERVICE"
systemctl restart "$AGENT_SERVICE"
printf '%s\n' "$COMMIT" > "$APP/RELEASE_COMMIT"

READY=0
for _ in $(seq 1 120); do
  main_code=$(curl -s -o /dev/null -w '%{http_code}' https://yjapi.pduoduo.vip/api/auth/session || true)
  front_code=$(curl -s -o /dev/null -w '%{http_code}' https://yj.pduoduo.vip/ || true)
  entry_code=$(curl -s -o /dev/null -w '%{http_code}' "https://yj.pduoduo.vip/$ENTRY" || true)
  if systemctl is-active --quiet "$MAIN_SERVICE" \
    && systemctl is-active --quiet "$AGENT_SERVICE" \
    && { [ "$main_code" = "200" ] || [ "$main_code" = "401" ]; } \
    && [ "$front_code" = "200" ] \
    && [ "$entry_code" = "200" ]; then
    READY=1
    break
  fi
  sleep 1
done

if [ "$READY" != "1" ]; then
  restore
  trap - ERR
  exit 1
fi

rm -rf "$APP/web-vimax/dist.previous"
trap - ERR
echo "VIDEO_REFERENCE_AGENT_UI_RELEASE=$COMMIT"
echo "BACKUP=$BACKUP"
echo "MAIN_SERVICE=$(systemctl is-active "$MAIN_SERVICE")"
echo "AGENT_SERVICE=$(systemctl is-active "$AGENT_SERVICE")"
echo "MAIN_CODE=$main_code"
echo "FRONT_CODE=$front_code"
echo "ENTRY_CODE=$entry_code"
grep -oE 'assets/index-[^" ]+\.js' "$APP/web-vimax/dist/index.html" | head -1
sha256sum "$APP/provider_client.py" "$APP/server.py" "$APP/web-vimax/dist/index.html"
