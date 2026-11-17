#!/usr/bin/env bash
set -euo pipefail

SOURCE=${1:?usage: $0 <pg_compat.py> <release-commit>}
COMMIT=${2:?usage: $0 <pg_compat.py> <release-commit>}
APP=/www/wwwroot/yj.pduoduo.vip/app
PY="$APP/.venv/bin/python"
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$APP/backups/pg-pool-ping-$COMMIT-$STAMP"

test -f "$SOURCE"
test -x "$PY"
"$PY" -m py_compile "$SOURCE"

mkdir -p "$BACKUP"
cp -a "$APP/pg_compat.py" "$BACKUP/pg_compat.py"
cp -a "$APP/RELEASE_COMMIT" "$BACKUP/RELEASE_COMMIT"

rollback() {
  cp -a "$BACKUP/pg_compat.py" "$APP/pg_compat.py"
  cp -a "$BACKUP/RELEASE_COMMIT" "$APP/RELEASE_COMMIT"
  chown www:www "$APP/pg_compat.py" "$APP/RELEASE_COMMIT"
  systemctl restart \
    drama-canvas-yj.service \
    drama-canvas-worker.service \
    drama-canvas-agent.service || true
  echo "rollback_completed backup=$BACKUP" >&2
}

on_error() {
  local status=$?
  if [ "$status" -ne 0 ]; then
    rollback
  fi
  exit "$status"
}
trap on_error EXIT

install -o www -g www -m 0644 "$SOURCE" "$APP/pg_compat.py"
printf '%s\n' "$COMMIT" > "$APP/RELEASE_COMMIT"
chown www:www "$APP/RELEASE_COMMIT"

systemctl restart \
  drama-canvas-yj.service \
  drama-canvas-worker.service \
  drama-canvas-agent.service

ready=false
for _ in $(seq 1 45); do
  sleep 1
  if curl -fsS --max-time 3 http://127.0.0.1:8890/readyz |
    "$PY" -c '
import json
import sys

data = json.load(sys.stdin)
checks = data.get("checks") or {}
raise SystemExit(0 if (
    data.get("ok") is True
    and (checks.get("database") or {}).get("ok") is True
    and (checks.get("migrations") or {}).get("ok") is True
    and (checks.get("workerFleet") or {}).get("ok") is True
) else 1)
'; then
    ready=true
    break
  fi
done

if [ "$ready" != true ]; then
  echo "readyz_failed backup=$BACKUP" >&2
  exit 1
fi

test "$(tr -d '\r\n' < "$APP/RELEASE_COMMIT")" = "$COMMIT"
systemctl is-active --quiet \
  drama-canvas-yj.service \
  drama-canvas-worker.service \
  drama-canvas-agent.service

trap - EXIT
echo "release_ok commit=$COMMIT backup=$BACKUP"
