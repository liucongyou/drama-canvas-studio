#!/bin/bash
set -euo pipefail

APP=/www/wwwroot/yj.pduoduo.vip/app
COMMIT=f1ae209
ARCHIVE=/tmp/canvas-agent-f1ae209.tgz
STAGE=/tmp/canvas-agent-f1ae209
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$APP/backups/agent-f1ae209-$STAMP"
MAIN_SERVICE=drama-canvas-yj.service
AGENT_SERVICE=drama-canvas-agent.service
AGENT_ENV=/etc/drama-canvas-agent.env
MAIN_OVERRIDE_DIR=/etc/systemd/system/drama-canvas-yj.service.d

test -f "$ARCHIVE"
rm -rf "$STAGE"
mkdir -p "$STAGE"
tar -xzf "$ARCHIVE" -C "$STAGE"
test -f "$STAGE/server.py"
test -f "$STAGE/agent_gateway.py"
test -f "$STAGE/agent/app/main.py"
test -f "$STAGE/web-vimax/dist/index.html"

mkdir -p "$BACKUP"
cp -a "$APP/server.py" "$BACKUP/server.py"
cp -a "$APP/agent_gateway.py" "$BACKUP/agent_gateway.py" 2>/dev/null || true
cp -a "$APP/agent" "$BACKUP/agent" 2>/dev/null || true
cp -a "$APP/web-vimax/dist" "$BACKUP/dist"

restore() {
  set +e
  systemctl stop "$AGENT_SERVICE" 2>/dev/null || true
  cp -a "$BACKUP/server.py" "$APP/server.py"
  if [ -f "$BACKUP/agent_gateway.py" ]; then
    cp -a "$BACKUP/agent_gateway.py" "$APP/agent_gateway.py"
  else
    rm -f "$APP/agent_gateway.py"
  fi
  rm -rf "$APP/agent"
  if [ -d "$BACKUP/agent" ]; then
    cp -a "$BACKUP/agent" "$APP/agent"
  fi
  rm -rf "$APP/web-vimax/dist"
  cp -a "$BACKUP/dist" "$APP/web-vimax/dist"
  chown -R www:www "$APP/server.py" "$APP/agent_gateway.py" "$APP/agent" "$APP/web-vimax/dist" 2>/dev/null || true
  systemctl daemon-reload
  systemctl restart "$MAIN_SERVICE"
  echo "AGENT_RELEASE_FAILED_AND_RESTORED"
}
trap 'restore' ERR

install -o www -g www -m 0644 "$STAGE/server.py" "$APP/server.py"
install -o www -g www -m 0644 "$STAGE/agent_gateway.py" "$APP/agent_gateway.py"
rm -rf "$APP/agent"
cp -a "$STAGE/agent" "$APP/agent"
rm -rf "$APP/agent/data" "$APP/agent/.venv" "$APP/agent/.pytest_cache"
mkdir -p "$APP/agent/data"
chown -R www:www "$APP/agent"

rm -rf "$APP/web-vimax/dist.next-agent"
mkdir -p "$APP/web-vimax/dist.next-agent"
cp -a "$STAGE/web-vimax/dist/." "$APP/web-vimax/dist.next-agent/"
chown -R www:www "$APP/web-vimax/dist.next-agent"
rm -rf "$APP/web-vimax/dist.previous-agent"
mv "$APP/web-vimax/dist" "$APP/web-vimax/dist.previous-agent"
mv "$APP/web-vimax/dist.next-agent" "$APP/web-vimax/dist"

if [ ! -x "$APP/agent/.venv/bin/python" ]; then
  "$APP/.venv/bin/python" -m venv "$APP/agent/.venv"
fi
"$APP/agent/.venv/bin/pip" install --disable-pip-version-check -q -r "$APP/agent/requirements.txt"

if [ ! -f "$AGENT_ENV" ]; then
  SECRET=$(openssl rand -hex 32)
  umask 077
  cat > "$AGENT_ENV" <<EOF
AGENT_INTERNAL_SECRET=$SECRET
AGENT_SIDECAR_URL=http://127.0.0.1:8895
AGENT_SIGNATURE_WINDOW_SECONDS=60
EOF
fi
chmod 600 "$AGENT_ENV"

mkdir -p "$MAIN_OVERRIDE_DIR"
cat > "$MAIN_OVERRIDE_DIR/agent.conf" <<'EOF'
[Service]
EnvironmentFile=/etc/drama-canvas-agent.env
EOF

cat > "/etc/systemd/system/$AGENT_SERVICE" <<EOF
[Unit]
Description=Drama Canvas Project Agent
After=network-online.target $MAIN_SERVICE
Wants=network-online.target

[Service]
Type=simple
User=www
Group=www
WorkingDirectory=$APP/agent
EnvironmentFile=$AGENT_ENV
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$APP
Environment=CANVAS_BASE_URL=http://127.0.0.1:8890
Environment=CANVAS_AUTH_MODE=accounts
Environment=DB_PATH=$APP/agent/data/agent.db
ExecStart=$APP/agent/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8895
Restart=always
RestartSec=3
KillSignal=SIGINT
TimeoutStopSec=30
UMask=0077
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

chown -R www:www "$APP/agent"
systemctl daemon-reload
systemctl restart "$MAIN_SERVICE"
systemctl enable "$AGENT_SERVICE" >/dev/null
systemctl restart "$AGENT_SERVICE"
printf '%s\n' "$COMMIT" > "$APP/RELEASE_COMMIT"

READY=0
for _ in $(seq 1 120); do
  main_code=$(curl -s -o /dev/null -w '%{http_code}' https://yjapi.pduoduo.vip/api/auth/session || true)
  front_code=$(curl -s -o /dev/null -w '%{http_code}' https://yj.pduoduo.vip/ || true)
  agent_code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8895/api/agent/health || true)
  if systemctl is-active --quiet "$MAIN_SERVICE" \
    && systemctl is-active --quiet "$AGENT_SERVICE" \
    && { [ "$main_code" = "200" ] || [ "$main_code" = "401" ]; } \
    && [ "$front_code" = "200" ] \
    && { [ "$agent_code" = "200" ] || [ "$agent_code" = "401" ]; }; then
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

rm -rf "$APP/web-vimax/dist.previous-agent"
trap - ERR
echo "AGENT_RELEASE=$COMMIT"
echo "BACKUP=$BACKUP"
echo "MAIN_SERVICE=$(systemctl is-active "$MAIN_SERVICE")"
echo "AGENT_SERVICE=$(systemctl is-active "$AGENT_SERVICE")"
echo "MAIN_CODE=$main_code"
echo "FRONT_CODE=$front_code"
echo "AGENT_DIRECT_CODE=$agent_code"
grep -oE 'assets/index-[^" ]+\.js' "$APP/web-vimax/dist/index.html" | head -1
sha256sum "$APP/server.py" "$APP/agent_gateway.py" "$APP/web-vimax/dist/index.html"
