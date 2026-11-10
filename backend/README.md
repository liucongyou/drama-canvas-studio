# PostgreSQL Shadow Backend

This package is a read-only shadow/control-plane service. The legacy
`server.py` process remains the write authority until migration gates pass.

## Roles

- `canvas_admin` is the migration/DDL role. It is a PostgreSQL superuser in the
  local development cluster, so it must not be used by the application.
- `canvas_runtime` is the application role. It has `NOSUPERUSER`,
  `NOBYPASSRLS`, and `NOINHERIT`; it receives DML privileges only.

Provision the runtime role with:

```powershell
$env:CANVAS_MIGRATION_DATABASE_URL = "postgresql://canvas_admin@127.0.0.1:55432/drama_canvas_shadow"
python -m backend.provision_runtime_role
```

Use a separate runtime URL for the API:

```powershell
$env:CANVAS_DATABASE_URL = "postgresql+psycopg://canvas_runtime@127.0.0.1:55432/drama_canvas_shadow"
$env:CANVAS_INTERNAL_API_TOKEN = "<a long random token>"
$env:CANVAS_ENVIRONMENT = "development"
python -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8897
```

Verify tenant isolation before enabling reads:

```powershell
python -m backend.verify_runtime_role `
  --admin-url "postgresql://canvas_admin@127.0.0.1:55432/drama_canvas_shadow" `
  --runtime-url "postgresql://canvas_runtime@127.0.0.1:55432/drama_canvas_shadow" `
  --tenant owner
```

## Migration Gate

`python -m backend.migrate_tenant ...` performs a real SQLite-to-PostgreSQL
backfill and compares content hashes, not only row counts. It does not switch
traffic.

The following remain intentionally blocked until separately verified:

- online incremental synchronization
- PostgreSQL write authority
- dual-read canary
- production traffic switch and rollback drill

The shadow API exposes only internal read endpoints under `/internal/v1` and
requires both `X-Internal-Token` and `X-Tenant-Id`. In production, a missing
`CANVAS_INTERNAL_API_TOKEN` makes the internal endpoints return 503 rather than
silently exposing the shadow data. PostgreSQL RLS is defense in depth; the
application still sets the tenant scope explicitly for every request.

## Scheduled Shadow Sync

Run one idempotent sync from a scheduler:

```powershell
python -m backend.sync_shadow `
  --sqlite "D:\tenant-data\owner\canvas.db" `
  --tenant owner `
  --json
```

The command holds a tenant-specific PostgreSQL advisory lock, upserts changed
rows, prunes rows deleted from SQLite, verifies content hashes, and commits only
when verification passes. It does not switch traffic or touch SQLite.
