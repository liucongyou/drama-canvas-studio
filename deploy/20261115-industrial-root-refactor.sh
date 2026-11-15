#!/usr/bin/env bash
set -Eeuo pipefail

COMMIT="${1:?usage: release.sh <commit> <bundle.tar.gz>}"
BUNDLE="${2:?usage: release.sh <commit> <bundle.tar.gz>}"
APP=/www/wwwroot/yj.pduoduo.vip/app
PSQL=/www/server/pgsql/bin/psql
PG_DUMP=/www/server/pgsql/bin/pg_dump
STAMP=$(date +%Y%m%d-%H%M%S)
STAGE="$APP/releases/industrial-$COMMIT-$STAMP"
BACKUP="$APP/backups/industrial-$COMMIT-$STAMP"
RUNTIME_FILES=(
  server.py
  canvas_store.py
  canvas_repository.py
  generation_outcome.py
  telemetry.py
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
  "$STAGE/runtime/canvas_repository.py" \
  "$STAGE/runtime/generation_outcome.py" \
  "$STAGE/runtime/telemetry.py" \
  "$STAGE/runtime/scripts/run-local-migrations.py" \
  "$STAGE/runtime/scripts/verify-release-health.py"

entry_asset=$(grep -oE '/assets/index-[A-Za-z0-9_-]+\.js' \
  "$STAGE/dist/index.html" | head -n 1)
test -n "$entry_asset"
test -f "$STAGE/dist${entry_asset}"
EXPECTED_INDEX_SHA=$(sha256sum "$STAGE/dist/index.html" | awk '{print $1}')

active_tasks=0
for schema in $("$PSQL" "$DATABASE_URL" -Atc \
  "SELECT schema_name FROM information_schema.schemata "
  "WHERE schema_name LIKE 'tenant_%' ORDER BY schema_name"); do
  task_table=$("$PSQL" "$DATABASE_URL" -Atc \
    "SELECT to_regclass('$schema.generation_tasks') IS NOT NULL")
  if [ "$task_table" = "t" ]; then
    count=$("$PSQL" "$DATABASE_URL" -Atc \
      "SELECT count(*) FROM $schema.generation_tasks "
      "WHERE status IN ('queued','submitting','running')")
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
  echo "release failed; rolling code back to the previous version" >&2
  systemctl stop drama-canvas-yj.service drama-canvas-agent.service
  for file in "${RUNTIME_FILES[@]}"; do
    if grep -Fxq "$file" "$BACKUP/missing-files.txt"; then
      rm -f "$APP/$file"
    elif [ -f "$BACKUP/$file" ]; then
      install -m 0644 "$BACKUP/$file" "$APP/$file"
    fi
  done
  rm -rf "$APP/web-vimax/dist"
  if [ -d "$BACKUP/dist" ]; then
    cp -a "$BACKUP/dist" "$APP/web-vimax/dist"
  fi
  if [ -f "$BACKUP/RELEASE_COMMIT" ]; then
    install -m 0644 "$BACKUP/RELEASE_COMMIT" "$APP/RELEASE_COMMIT"
  fi
  chown -R www:www "$APP/web-vimax/dist"
  for file in "${RUNTIME_FILES[@]}"; do
    if [ -f "$APP/$file" ]; then
      chown www:www "$APP/$file"
    fi
  done
  chown www:www "$APP/RELEASE_COMMIT"
  systemctl start drama-canvas-agent.service
  systemctl start drama-canvas-yj.service
  curl -fsS --max-time 20 http://127.0.0.1:8890/healthz
  echo "database pre-migration backup: $BACKUP/database.dump" >&2
  echo "release report: $BACKUP/release-report.json" >&2
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

systemctl stop drama-canvas-agent.service drama-canvas-yj.service
systemctl is-active --quiet drama-canvas-agent.service && exit 1 || true
systemctl is-active --quiet drama-canvas-yj.service && exit 1 || true

"$PG_DUMP" --format=custom --no-owner --no-acl \
  --file="$BACKUP/database.dump" "$DATABASE_URL"

(
  cd "$STAGE/runtime"
  CANVAS_DB_BACKEND=postgres \
  CANVAS_MULTI_TENANT=1 \
  CANVAS_TENANT_DATA_ROOT="$APP/data" \
  PYTHONPATH="$STAGE/runtime" \
  "$APP/.venv/bin/python" scripts/run-local-migrations.py \
    --url "$DATABASE_URL" \
    --report "$BACKUP/migration-first.json"
)
(
  cd "$STAGE/runtime"
  CANVAS_DB_BACKEND=postgres \
  CANVAS_MULTI_TENANT=1 \
  CANVAS_TENANT_DATA_ROOT="$APP/data" \
  PYTHONPATH="$STAGE/runtime" \
  "$APP/.venv/bin/python" scripts/run-local-migrations.py \
    --url "$DATABASE_URL" \
    --report "$BACKUP/migration-second.json"
)
"$APP/.venv/bin/python" - "$BACKUP/migration-second.json" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
if int(report.get("changedRows") or 0) != 0:
    raise SystemExit(
        f"second migration changed {report.get('changedRows')} rows"
    )
PY

for file in "${RUNTIME_FILES[@]}"; do
  install -m 0644 "$STAGE/runtime/$file" "$APP/$file"
done
rm -rf "$APP/web-vimax/dist"
mv "$STAGE/dist" "$APP/web-vimax/dist"
printf '%s\n' "$COMMIT" > "$APP/RELEASE_COMMIT"
chown -R www:www "$APP/web-vimax/dist"
chown www:www \
  "$APP/server.py" \
  "$APP/canvas_store.py" \
  "$APP/canvas_repository.py" \
  "$APP/generation_outcome.py" \
  "$APP/telemetry.py" \
  "$APP/RELEASE_COMMIT"

systemctl start drama-canvas-agent.service
systemctl start drama-canvas-yj.service

health_file="$BACKUP/health.json"
for attempt in $(seq 1 60); do
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

systemctl is-active --quiet drama-canvas-agent.service
systemctl is-active --quiet drama-canvas-yj.service
test "$(sha256sum "$APP/web-vimax/dist/index.html" | awk '{print $1}')" = "$EXPECTED_INDEX_SHA"
curl -fsS --max-time 20 https://yjapi.pduoduo.vip/api/auth/session >/dev/null

CANVAS_DB_BACKEND=postgres \
CANVAS_MULTI_TENANT=1 \
CANVAS_TENANT_DATA_ROOT="$APP/data" \
"$APP/.venv/bin/python" "$STAGE/runtime/scripts/verify-release-health.py" \
  --base http://127.0.0.1:8890 \
  --database-url "$DATABASE_URL" \
  --expected-commit "$COMMIT" \
  --output "$BACKUP/release-health.json"

"$APP/.venv/bin/python" - \
  "$BACKUP/release-report.json" \
  "$COMMIT" \
  "$STAMP" \
  "$BACKUP" \
  "$STAGE" \
  "$EXPECTED_INDEX_SHA" <<'PY'
import json
import sys

target, commit, stamp, backup, stage, index_sha = sys.argv[1:]
report = {
    "ok": True,
    "commit": commit,
    "stamp": stamp,
    "backup": backup,
    "stage": stage,
    "frontendIndexSha256": index_sha,
}
open(target, "w", encoding="utf-8").write(
    json.dumps(report, ensure_ascii=False, indent=2)
)
PY

SUCCESS=1
echo "release=$COMMIT"
echo "backup=$BACKUP"
echo "stage=$STAGE"
