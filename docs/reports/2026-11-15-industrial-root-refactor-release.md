# Industrial Root Refactor Release

## Release identity

- Runtime commit: `51c9af6a16ec3733aaddbfd6dd7ff40d7257e2b7`
- Previous production commit: `c2e8c18`
- Branch: `codex/agent-overhaul`
- Release method: all local gates, one service cutover, online readback
- Paid generation during validation: none

## Local gate

- Backend: 71 modules, 833 tests passed, 0 failed, 0 skipped
- Agent: 43 tests passed against PostgreSQL, no skipped PostgreSQL test
- Frontend: 87 files, 774 tests passed
- TypeScript typecheck: passed
- Production build: passed
- Migration convergence: 0 changed rows on the second local run
- Stress: 32-way concurrency, no duplicate active task, no lost media URL
  - large canvas read: 105.57 ms
  - same-project patch median: 712.27 ms
  - same-project patch p95: 4,684.76 ms
- Browser: 60 media nodes, two history menus, desktop/mobile no overflow,
  no page or console errors

## Production cutover

- PostgreSQL backup was taken before migration:
  `backups/industrial-51c9af6a16ec3733aaddbfd6dd7ff40d7257e2b7-20261115-193639/database.dump`
- Stage:
  `releases/industrial-51c9af6a16ec3733aaddbfd6dd7ff40d7257e2b7-20261115-193639`
- First migration changed 25 rows:
  - 9 unrecoverable deliveries classified
  - 6 projects' video history backfilled
  - 10 media version identities repaired
- Second migration changed 0 rows
- Main and Agent services are active
- Internal `/healthz` readback reports runtime commit `51c9af6`
- Frontend entry is `/assets/index-C2mwS-c1.js`
- Frontend index SHA-256:
  `94018b4a2e1d684859313f84a964defda168c0443ea7676e0cc557565cc07d4d`
- Main JavaScript SHA-256:
  `eb879f96c14109d14329999e3f1de0df0c50c498ebf21822344e7739954e8aed`

## Online verification

- Owner projects: 14
- Projection nodes: 776
- Generation tasks: 791
- Snapshot rows: 159
- Active generation tasks: 0
- Duplicate active tasks: 0
- Admin audit health now reports filesystem capacity instead of a false
  PostgreSQL `freeBytes=0`
- Media Range smoke: HTTP 206, `bytes 0-1023/2073249`
- Read-only browser smoke on a real owner project:
  - 29 media nodes loaded
  - prompt modal opened with full content
  - 390 px mobile viewport had no horizontal overflow
  - no page errors

## Rollback record

The first cutover attempt stopped before code switch because the staging
directory omitted `provider_protocols.py`. The rollback restored
`c2e8c18`, restarted both services, and returned production to healthy
state. The release bundle was corrected and the second cutover completed.

## Operational retention

- PostgreSQL logs are rotated by the existing daily prune timer.
- Current PostgreSQL log directory size: 356 KiB.
- systemd journal is bounded to 1 GiB, 64 MiB per file, and 14 days.
