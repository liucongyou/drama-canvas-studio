# 2026-11-15 Local PostgreSQL Baseline

## Scope

This report records the reproducible local PostgreSQL runtime used before the
industrial-root-refactor release. It is not a production migration report.

## Runtime

- PostgreSQL: 16.2 portable PG16 runtime
- Host: `127.0.0.1`
- Port: `55432`
- Main database: `drama_canvas_test`
- Binaries: `.pg-test/pgserver/pgserver/pginstall` (portable PostgreSQL 16.2)
- Data directory: `.pg-test/data`
- Log: `.pg-test/postgres.log`
- Authentication: local trust, loopback-only listener

## Commands

```powershell
powershell -ExecutionPolicy Bypass -File scripts/local-pg.ps1 init
powershell -ExecutionPolicy Bypass -File scripts/local-pg.ps1 start
& 'C:\Program Files\PostgreSQL\16\bin\createdb.exe' -h 127.0.0.1 -p 55432 -U postgres drama_canvas_test
```

## Safety

- No provider generation is called by cluster startup or verification.
- The cluster listens only on loopback.
- Reset verifies the target path remains inside the workspace before deleting.

## Environment Note

The machine-level PostgreSQL 16.16 installer requires interactive elevation and
did not complete in the non-interactive Codex session. The reproducible gate
therefore uses the self-contained PG16 wheel runtime. PostgreSQL SQL and
schema behavior are exercised on the same major version; the exact deployed
server remains PostgreSQL 16.16 on production.
