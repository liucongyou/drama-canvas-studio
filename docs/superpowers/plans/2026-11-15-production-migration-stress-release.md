# Production Snapshot Migration, Stress and Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the complete system against a local PostgreSQL copy of production, prove migration and concurrency safety, then perform one atomic production deployment.

**Architecture:** Capture production schema/data, restore into a local PostgreSQL 16 cluster, run all migrations and stress suites, then deploy the exact tested commit with a reversible service/dist/database procedure.

**Tech Stack:** PostgreSQL 16, `pg_dump`, `pg_restore`, PowerShell, pytest, Vitest, Vite, systemd, Nginx

**Spec:** `docs/superpowers/specs/2026-11-15-industrial-root-refactor-design.md`

## Global Constraints

- No paid provider generation during migration, stress or release checks.
- Production stays on the existing release until every local gate passes.
- The deployed commit must equal the locally tested commit.
- The release uses one atomic switch and retains a rollback copy.
- Database IDs, tenant schemas, audit records and media URLs may not be guessed or discarded.

---

### Task 1: Local PostgreSQL 16 Runtime

**Files:**
- Create: `scripts/local-pg.ps1`
- Create: `docs/reports/2026-11-15-local-pg-baseline.md`

**Interfaces:**
- Consumes: Windows `winget`, local filesystem
- Produces: local PG16 on `127.0.0.1:55432`, database `drama_canvas_test`

- [ ] **Step 1: Install PostgreSQL 16 if absent**

Run:

```powershell
winget install --id PostgreSQL.PostgreSQL.16 --exact --silent `
  --accept-package-agreements --accept-source-agreements
```

Expected: `initdb`, `pg_ctl`, `createdb`, `psql`, `pg_dump`, `pg_restore`
exist under `C:\Program Files\PostgreSQL\16\bin`.

- [ ] **Step 2: Implement local cluster script**

```powershell
param(
  [ValidateSet('init','start','stop','status','reset')]
  [string]$Action = 'status'
)

$PgBin = 'C:\Program Files\PostgreSQL\16\bin'
$Data = Join-Path $PSScriptRoot '..\.pg-test\data'
$Log = Join-Path $PSScriptRoot '..\.pg-test\postgres.log'
$Port = 55432

switch ($Action) {
  'init' {
    if (Test-Path $Data) { throw "Data directory already exists: $Data" }
    & "$PgBin\initdb.exe" -D $Data -U postgres -A trust --encoding=UTF8
  }
  'start' {
    & "$PgBin\pg_ctl.exe" -D $Data -l $Log -o "-p $Port -h 127.0.0.1" start
  }
  'stop' {
    & "$PgBin\pg_ctl.exe" -D $Data stop -m fast
  }
  'status' {
    & "$PgBin\pg_ctl.exe" -D $Data status
  }
  'reset' {
    if (Test-Path $Data) {
      $resolved = (Resolve-Path $Data).Path
      $workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
      if (-not $resolved.StartsWith($workspace, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to delete outside workspace: $resolved"
      }
      Remove-Item -LiteralPath $Data -Recurse -Force
    }
    & $PSCommandPath init
  }
}
```

- [ ] **Step 3: Start and create the database**

Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/local-pg.ps1 init
powershell -ExecutionPolicy Bypass -File scripts/local-pg.ps1 start
& 'C:\Program Files\PostgreSQL\16\bin\createdb.exe' -h 127.0.0.1 -p 55432 -U postgres drama_canvas_test
```

Expected: database is available locally.

- [ ] **Step 4: Verify fail-closed runtime**

Run:

```powershell
$env:CANVAS_DB_BACKEND='postgres'
$env:CANVAS_DATABASE_URL='postgresql://postgres@127.0.0.1:55432/drama_canvas_test'
python -m pytest tests/test_postgres_fail_closed.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add scripts/local-pg.ps1 docs/reports/2026-11-15-local-pg-baseline.md
git commit -m "test: add reproducible local postgres runtime"
```

### Task 2: Production Snapshot Capture and Restore

**Files:**
- Create: `scripts/capture-production-snapshot.ps1`
- Create: `scripts/restore-production-snapshot.ps1`
- Create: `scripts/verify-snapshot-counts.py`

**Interfaces:**
- Consumes: production PostgreSQL service environment and SSH
- Produces: `artifacts/prod-snapshot-YYYYMMDD.dump`, tenant/project/node/task count manifest

- [ ] **Step 1: Capture the production dump**

```powershell
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$Dump = "artifacts/prod-snapshot-$Stamp.dump"
New-Item -ItemType Directory -Force artifacts | Out-Null

ssh root@121.196.230.104 @"
set -a
. /etc/drama-canvas-pg.env
set +a
pg_dump --format=custom --no-owner --no-acl --file=/tmp/prod-snapshot.dump `"`$DATABASE_URL`"
"@

scp -q root@121.196.230.104:/tmp/prod-snapshot.dump $Dump
```

- [ ] **Step 2: Restore locally**

```powershell
$PgBin = 'C:\Program Files\PostgreSQL\16\bin'
& "$PgBin\dropdb.exe" -h 127.0.0.1 -p 55432 -U postgres --if-exists drama_canvas_snapshot
& "$PgBin\createdb.exe" -h 127.0.0.1 -p 55432 -U postgres drama_canvas_snapshot
& "$PgBin\pg_restore.exe" -h 127.0.0.1 -p 55432 -U postgres `
  --no-owner --no-acl --exit-on-error `
  --dbname drama_canvas_snapshot $Dump
```

- [ ] **Step 3: Verify schema and record counts**

`verify-snapshot-counts.py` must query and compare:

```sql
SELECT schema_name FROM information_schema.schemata
WHERE schema_name LIKE 'tenant_%' ORDER BY schema_name;

SELECT COUNT(*) FROM tenant_owner.canvas_projects;
SELECT COUNT(*) FROM tenant_owner.canvas_projection_nodes;
SELECT status, COUNT(*) FROM tenant_owner.generation_tasks GROUP BY status;
SELECT COUNT(*), SUM(octet_length(payload_json))
FROM tenant_owner.canvas_snapshots;
```

Run:

```powershell
python scripts/verify-snapshot-counts.py `
  --url postgresql://postgres@127.0.0.1:55432/drama_canvas_snapshot `
  --expected artifacts/prod-counts.json
```

Expected: tenant schemas and all record counts match production readback.

- [ ] **Step 4: Commit**

```powershell
git add scripts/capture-production-snapshot.ps1 scripts/restore-production-snapshot.ps1 scripts/verify-snapshot-counts.py
git commit -m "test: add production snapshot capture and restore"
```

### Task 3: Migration and Repair Runner

**Files:**
- Create: `scripts/run-local-migrations.py`
- Create: `tests/test_local_migration_runner.py`
- Modify: `data_repair_media_versions.py`

**Interfaces:**
- Consumes: local snapshot database URL
- Produces: idempotent schema migration, task receipt repair, duplicate-version repair and `migration-report.json`

- [ ] **Step 1: Write a failing idempotency test**

```python
def test_second_migration_run_changes_zero_rows(snapshot_db):
    first = run_local_migrations(snapshot_db, dry_run=False)
    second = run_local_migrations(snapshot_db, dry_run=False)
    assert first["applied"] is True
    assert second["applied"] is False
    assert second["changedRows"] == 0
    assert second["duplicateVersionIds"] == 0
```

- [ ] **Step 2: Implement ordered migration steps**

```python
STEPS = (
    backfill_generation_delivery_status,
    backfill_succeeded_delivery_evidence,
    reconcile_delivery_receipts,
    expire_unconfirmed_generation_tasks,
    repair_media_version_identities,
    normalize_active_media_versions,
)
```

Each step returns:

```json
{"name": "repair_media_version_identities", "changedRows": 10, "details": {}}
```

- [ ] **Step 3: Run against the local snapshot**

Run:

```powershell
python scripts/run-local-migrations.py `
  --url postgresql://postgres@127.0.0.1:55432/drama_canvas_snapshot `
  --report artifacts/migration-report.json
```

Expected:

- duplicate version IDs `0`;
- active-version mismatches `0`;
- uncertain tasks without provider IDs `0`;
- no media URL deletions;
- no provider calls.

- [ ] **Step 4: Run again**

Expected: `changedRows=0`.

- [ ] **Step 5: Commit**

```powershell
git add scripts/run-local-migrations.py tests/test_local_migration_runner.py data_repair_media_versions.py
git commit -m "test: add idempotent local migration runner"
```

### Task 4: Industrial Stress Harness

**Files:**
- Create: `scripts/industrial_stress.py`
- Create: `tests/fixtures/stress/projects.json`
- Create: `docs/reports/2026-11-15-industrial-stress-results.md`

**Interfaces:**
- Consumes: local API on `127.0.0.1:8890` or in-process handler
- Produces: bounded JSON stress report with latency, error and consistency assertions

- [ ] **Step 1: Define scenarios**

```python
SCENARIOS = (
    "multi_tenant_read_write",
    "same_project_concurrent_nodes",
    "same_node_duplicate_submit",
    "concurrent_terminal_commits",
    "delivery_recovery",
    "sse_reconnect_and_limit",
    "weak_network_save_retry",
    "version_switch_concurrency",
    "large_canvas_read",
    "media_range_read",
)
```

- [ ] **Step 2: Set bounds**

```python
MAX_REQUESTS = 2_000
MAX_CONCURRENCY = 32
REQUEST_TIMEOUT_SECONDS = 20
MAX_DURATION_SECONDS = 600
```

- [ ] **Step 3: Implement consistency assertions**

After each scenario:

```python
assert active_tasks_for_node(node_id) <= 1
assert task_node_terminal_states_match()
assert requested_media_range_count == successful_media_range_count
assert tenant_a_ids.isdisjoint(tenant_b_ids)
assert duplicate_version_ids() == 0
assert active_version_mismatches() == 0
```

- [ ] **Step 4: Run**

Run:

```powershell
$env:CANVAS_DATABASE_URL='postgresql://postgres@127.0.0.1:55432/drama_canvas_snapshot'
python scripts/industrial_stress.py --report artifacts/industrial-stress.json
```

Expected thresholds:

- HTTP 5xx: `0`;
- cross-tenant leakage: `0`;
- duplicate active task: `0`;
- node/task terminal mismatch: `0`;
- p95 API latency for compact reads: `< 1000 ms`;
- SSE rejection returns `SSE_CONNECTION_LIMIT`;
- no process deadlock or pool exhaustion.

- [ ] **Step 5: Commit**

```powershell
git add scripts/industrial_stress.py tests/fixtures/stress/projects.json docs/reports/2026-11-15-industrial-stress-results.md
git commit -m "test: add bounded industrial stress harness"
```

### Task 5: One-Command Release Gate

**Files:**
- Create: `scripts/industrial_release_gate.ps1`
- Create: `scripts/verify-release-health.py`

**Interfaces:**
- Consumes: local PG snapshot, backend/frontend/Agent sources
- Produces: one nonzero-on-failure command that writes `artifacts/release-gate.json`

- [ ] **Step 1: Implement the gate**

The script runs these in order and stops on first failure:

```text
1. python -m pytest -q
2. agent/.venv/Scripts/python.exe -m pytest agent/tests -q
3. npm run typecheck:canvas in web-vimax
4. npm test in web-vimax
5. npm run build in web-vimax
6. run-local-migrations.py against local snapshot
7. run-local-migrations.py second time, expect changedRows=0
8. industrial_stress.py
9. verify-industrial-canvas.mjs
10. git diff --check
```

- [ ] **Step 2: Add artifact hashes**

`release-gate.json` records:

```json
{
  "commit": "<git sha>",
  "backendTests": "pass",
  "agentTests": "pass",
  "frontendTests": "pass",
  "typecheck": "pass",
  "build": "pass",
  "migrationIdempotent": true,
  "stress": "pass",
  "browser": "pass",
  "gatePassed": true
}
```

- [ ] **Step 3: Run until all gates pass**

Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/industrial_release_gate.ps1
```

Expected: all gates pass; no production deployment has occurred yet.

- [ ] **Step 4: Commit**

```powershell
git add scripts/industrial_release_gate.ps1 scripts/verify-release-health.py
git commit -m "test: enforce all-or-nothing industrial release gate"
```

### Task 6: Atomic Production Release

**Files:**
- Create: `deploy/20261115-industrial-root-refactor.sh`
- Create: `docs/reports/2026-11-15-industrial-root-refactor-release.md`

**Interfaces:**
- Consumes: tested commit, backend files, frontend dist, migration report
- Produces: one atomic deployed release with service and data readback

- [ ] **Step 1: Back up current production**

```bash
APP=/www/wwwroot/yj.pduoduo.vip/app
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$APP/backups/industrial-root-$STAMP"
mkdir -p "$BACKUP"
cp -a "$APP/server.py" "$APP/canvas_store.py" "$APP/canvas_repository.py" "$BACKUP/"
cp -a "$APP/web-vimax/dist" "$BACKUP/dist"
cp -a "$APP/RELEASE_COMMIT" "$BACKUP/RELEASE_COMMIT"
```

- [ ] **Step 2: Apply database migration**

Run the exact idempotent migration runner used locally.
Abort if any of these are nonzero after migration:

```sql
tenants with duplicate version IDs
tenants with active-version mismatches
tasks interrupted without provider IDs older than 24 hours
```

- [ ] **Step 3: Install files and switch frontend**

Use temporary `.new` paths, validate entry hashes, then atomically rename.
Do not copy partial files directly onto live paths.

- [ ] **Step 4: Restart and verify**

Run:

```bash
systemctl restart drama-canvas-agent.service
systemctl restart drama-canvas-yj.service
systemctl is-active drama-canvas-agent.service
systemctl is-active drama-canvas-yj.service
curl -fsS https://yjapi.pduoduo.vip/api/auth/session >/dev/null
```

Then run `verify-release-health.py` to assert:

```text
release commit matches tested commit;
project/episode/node counts match baseline;
no stale active tasks introduced;
no duplicate version ID increase;
main and Agent health pass;
frontend entry equals built artifact;
media Range smoke passes.
```

- [ ] **Step 5: Roll back on any failure**

Restore:

- backed-up code;
- previous dist;
- database snapshot if migration changed source data;
- previous `RELEASE_COMMIT`;
- restart both services;
- verify health.

- [ ] **Step 6: Commit release record**

```powershell
git add deploy/20261115-industrial-root-refactor.sh docs/reports/2026-11-15-industrial-root-refactor-release.md
git commit -m "chore: record industrial root refactor release"
```

## Plan Self-Review

- Spec coverage: local PostgreSQL, production snapshot, idempotent migration,
  stress, frontend browser gate and atomic release are all covered.
- Placeholder scan: no TBD/TODO; every task has commands and assertions.
- Interface consistency: migration runner and release health verifier are
  reused unchanged between local and production.
