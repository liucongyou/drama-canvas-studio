#!/usr/bin/env bash
set -Eeuo pipefail

COMMIT="${1:?usage: release.sh <commit> <bundle.tar.gz>}"
BUNDLE="${2:?usage: release.sh <commit> <bundle.tar.gz>}"
APP=/www/wwwroot/yj.pduoduo.vip/app
PSQL=/www/server/pgsql/bin/psql
PG_DUMP=/www/server/pgsql/bin/pg_dump
STAMP=$(date +%Y%m%d-%H%M%S)
STAGE="$APP/releases/worker-plane-$COMMIT-$STAMP"
BACKUP="$APP/backups/worker-plane-$COMMIT-$STAMP"
WORKER_ENV=/etc/drama-canvas-worker-plane.env
API_DROPIN=/etc/systemd/system/drama-canvas-yj.service.d/worker-plane.conf
WORKER_SERVICE=/etc/systemd/system/drama-canvas-worker.service
RUNTIME_FILES=(
  server.py
  canvas_store.py
  canvas_repository.py
  generation_outcome.py
  telemetry.py
  generation_queue.py
  generation_worker.py
  schema_migrations.py
  operational_metrics.py
  observability.py
  pg_compat.py
  tenant_context.py
  tenant_registry.py
  tenant_security.py
  provider_protocols.py
  data_repair_media_versions.py
)
SUCCESS=0

test -f "$BUNDLE"
set -a
. /etc/drama-canvas-pg.env
set +a
test "${CANVAS_DB_BACKEND:-}" = "postgres"
test -n "${DATABASE_URL:-}"

if tar -tzf "$BUNDLE" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
  echo "bundle contains an unsafe path" >&2
  exit 1
fi

mkdir -p "$STAGE" "$BACKUP"
tar -xzf "$BUNDLE" -C "$STAGE"
test "$(cat "$STAGE/RELEASE_COMMIT")" = "$COMMIT"
(
  cd "$STAGE"
  sha256sum -c SHA256SUMS
)

"$APP/.venv/bin/python" -m py_compile \
  "$STAGE/runtime/server.py" \
  "$STAGE/runtime/canvas_store.py" \
  "$STAGE/runtime/generation_queue.py" \
  "$STAGE/runtime/generation_worker.py" \
  "$STAGE/runtime/schema_migrations.py" \
  "$STAGE/runtime/operational_metrics.py" \
  "$STAGE/runtime/observability.py" \
  "$STAGE/runtime/scripts/run-schema-migrations.py" \
  "$STAGE/runtime/scripts/verify-release-health.py"

entry_asset=$(grep -oE '/assets/index-[A-Za-z0-9_-]+\.js' \
  "$STAGE/dist/index.html" | head -n 1)
test -n "$entry_asset"
test -f "$STAGE/dist${entry_asset}"
EXPECTED_INDEX_SHA=$(sha256sum "$STAGE/dist/index.html" | awk '{print $1}')

active_tasks=0
for schema in $("$PSQL" "$DATABASE_URL" -Atc \
  "SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'tenant_%' ORDER BY schema_name"); do
  task_table=$("$PSQL" "$DATABASE_URL" -Atc \
    "SELECT to_regclass('$schema.generation_tasks') IS NOT NULL")
  if [ "$task_table" = "t" ]; then
    count=$("$PSQL" "$DATABASE_URL" -Atc \
      "SELECT count(*) FROM $schema.generation_tasks WHERE status IN ('queued','submitting','running')")
    active_tasks=$((active_tasks + count))
  fi
done
if [ "$active_tasks" -ne 0 ]; then
  echo "refusing cutover with $active_tasks active generation tasks" >&2
  exit 1
fi

rollback() {
  status=$1
  trap - EXIT
  set +e
  echo "release failed; restoring previous API/Worker release" >&2
  systemctl stop drama-canvas-worker.service drama-canvas-yj.service || true
  for file in "${RUNTIME_FILES[@]}"; do
    if grep -Fxq "$file" "$BACKUP/missing-files.txt"; then
      rm -f "$APP/$file"
    elif [ -f "$BACKUP/$file" ]; then
      install -m 0644 "$BACKUP/$file" "$APP/$file"
    fi
  done
  if [ -d "$BACKUP/migrations" ]; then
    rm -rf "$APP/migrations"
    cp -a "$BACKUP/migrations" "$APP/migrations"
  elif [ -f "$BACKUP/migrations-missing" ]; then
    rm -rf "$APP/migrations"
  fi
  if [ -d "$BACKUP/scripts" ]; then
    cp -a "$BACKUP/scripts/." "$APP/scripts/"
  fi
  rm -rf "$APP/web-vimax/dist"
  if [ -d "$BACKUP/dist" ]; then
    cp -a "$BACKUP/dist" "$APP/web-vimax/dist"
  fi
  if [ -f "$BACKUP/RELEASE_COMMIT" ]; then
    install -m 0644 "$BACKUP/RELEASE_COMMIT" "$APP/RELEASE_COMMIT"
  fi
  if [ -f "$BACKUP/worker-plane.env" ]; then
    install -m 0644 "$BACKUP/worker-plane.env" "$WORKER_ENV"
  else
    rm -f "$WORKER_ENV"
  fi
  if [ -f "$BACKUP/api-dropin.conf" ]; then
    install -m 0644 "$BACKUP/api-dropin.conf" "$API_DROPIN"
  else
    rm -f "$API_DROPIN"
  fi
  if [ -f "$BACKUP/worker.service" ]; then
    install -m 0644 "$BACKUP/worker.service" "$WORKER_SERVICE"
  else
    rm -f "$WORKER_SERVICE"
  fi
  systemctl daemon-reload || true
  systemctl start drama-canvas-yj.service || true
  systemctl start drama-canvas-agent.service || true
  if [ -f "$BACKUP/worker-was-active" ]; then
    systemctl start drama-canvas-worker.service || true
  fi
  echo "database pre-migration backup: $BACKUP/database.dump" >&2
  echo "release stage: $STAGE" >&2
  exit "$status"
}
trap 'status=$?; if [ "$status" -ne 0 ] && [ "$SUCCESS" -ne 1 ]; then rollback "$status"; fi' EXIT

printf '' > "$BACKUP/missing-files.txt"
for file in "${RUNTIME_FILES[@]}"; do
  if [ -f "$APP/$file" ]; then
    cp -a "$APP/$file" "$BACKUP/$file"
  else
    printf '%s\n' "$file" >> "$BACKUP/missing-files.txt"
  fi
done
cp -a "$APP/web-vimax/dist" "$BACKUP/dist"
cp -a "$APP/RELEASE_COMMIT" "$BACKUP/RELEASE_COMMIT"
if [ -d "$APP/migrations" ]; then
  cp -a "$APP/migrations" "$BACKUP/migrations"
else
  touch "$BACKUP/migrations-missing"
fi
if [ -d "$APP/scripts" ]; then
  cp -a "$APP/scripts" "$BACKUP/scripts"
fi
if [ -f "$WORKER_ENV" ]; then cp -a "$WORKER_ENV" "$BACKUP/worker-plane.env"; fi
if [ -f "$API_DROPIN" ]; then cp -a "$API_DROPIN" "$BACKUP/api-dropin.conf"; fi
if [ -f "$WORKER_SERVICE" ]; then cp -a "$WORKER_SERVICE" "$BACKUP/worker.service"; fi
if systemctl is-active --quiet drama-canvas-worker.service; then
  touch "$BACKUP/worker-was-active"
fi

systemctl stop drama-canvas-worker.service 2>/dev/null || true
systemctl stop drama-canvas-yj.service

"$PG_DUMP" --format=custom --no-owner --no-acl \
  --file="$BACKUP/database.dump" "$DATABASE_URL"

if [ ! -f "$WORKER_ENV" ]; then
  install -m 0644 "$STAGE/system/worker-plane.env" "$WORKER_ENV"
fi
mkdir -p "$(dirname "$API_DROPIN")"
install -m 0644 "$STAGE/system/api-worker-plane.conf" "$API_DROPIN"
install -m 0644 "$STAGE/system/drama-canvas-worker.service" "$WORKER_SERVICE"
systemctl daemon-reload

set -a
. "$WORKER_ENV"
set +a
CANVAS_SCHEMA_MIGRATIONS_REQUIRED=1 \
CANVAS_MULTI_TENANT=1 \
CANVAS_TENANT_DATA_ROOT="$APP/data" \
"$APP/.venv/bin/python" "$STAGE/runtime/scripts/run-schema-migrations.py" \
  --url "$DATABASE_URL" \
  --apply \
  --report "$BACKUP/schema-migrations.json"
CANVAS_SCHEMA_MIGRATIONS_REQUIRED=1 \
CANVAS_MULTI_TENANT=1 \
CANVAS_TENANT_DATA_ROOT="$APP/data" \
"$APP/.venv/bin/python" "$STAGE/runtime/scripts/run-schema-migrations.py" \
  --url "$DATABASE_URL" \
  --check \
  --report "$BACKUP/schema-migrations-check.json"

for file in "${RUNTIME_FILES[@]}"; do
  install -m 0644 "$STAGE/runtime/$file" "$APP/$file"
done
rm -rf "$APP/migrations"
cp -a "$STAGE/runtime/migrations" "$APP/migrations"
cp -a "$STAGE/runtime/scripts/." "$APP/scripts/"
rm -rf "$APP/web-vimax/dist"
mv "$STAGE/dist" "$APP/web-vimax/dist"
printf '%s\n' "$COMMIT" > "$APP/RELEASE_COMMIT"
chown -R www:www "$APP/web-vimax/dist" "$APP/migrations"
for file in "${RUNTIME_FILES[@]}"; do
  chown www:www "$APP/$file"
done
chown www:www "$APP/RELEASE_COMMIT"

systemctl start drama-canvas-agent.service
systemctl enable drama-canvas-worker.service
systemctl start drama-canvas-worker.service
systemctl start drama-canvas-yj.service

health_file="$BACKUP/health.json"
ready_file="$BACKUP/ready.json"
for attempt in $(seq 1 90); do
  if curl -fsS --max-time 5 http://127.0.0.1:8890/healthz \
    -o "$health_file"; then
    break
  fi
  sleep 1
done
test -s "$health_file"
"$APP/.venv/bin/python" - "$health_file" "$COMMIT" <<'PY'
import json
import sys

health = json.load(open(sys.argv[1], encoding="utf-8"))
if not health.get("ok") or health.get("releaseCommit") != sys.argv[2]:
    raise SystemExit(f"unexpected health response: {health}")
PY

ready=0
for attempt in $(seq 1 90); do
  if curl -fsS --max-time 5 http://127.0.0.1:8890/readyz \
    -o "$ready_file"; then
    if "$APP/.venv/bin/python" - "$ready_file" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if report.get("ok") else 1)
PY
    then
      ready=1
      break
    fi
  fi
  sleep 1
done
test "$ready" -eq 1

systemctl is-active --quiet drama-canvas-agent.service
systemctl is-active --quiet drama-canvas-worker.service
systemctl is-active --quiet drama-canvas-yj.service
test "$(sha256sum "$APP/web-vimax/dist/index.html" | awk '{print $1}')" = "$EXPECTED_INDEX_SHA"
public_ready=0
for attempt in $(seq 1 30); do
  if curl -fsS --max-time 20 \
    https://yjapi.pduoduo.vip/api/auth/session >/dev/null; then
    public_ready=1
    break
  fi
  sleep 2
done
test "$public_ready" -eq 1

CANVAS_DB_BACKEND=postgres \
CANVAS_MULTI_TENANT=1 \
CANVAS_TENANT_DATA_ROOT="$APP/data" \
"$APP/.venv/bin/python" "$APP/scripts/verify-release-health.py" \
  --base http://127.0.0.1:8890 \
  --database-url "$DATABASE_URL" \
  --expected-commit "$COMMIT" \
  --output "$BACKUP/release-health.json"

SUCCESS=1
echo "release=$COMMIT"
echo "backup=$BACKUP"
echo "stage=$STAGE"
