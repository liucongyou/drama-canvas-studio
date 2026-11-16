# Large Industrial Phase 2 Release: Multi-Instance Cost Governance

Date: 2026-11-17
Status: Deployed and production-verified

## Release

- Branch: `codex/agent-overhaul`
- Release commit: `5bd90c07f1d60f907eb7c55a8bf26c1c234ba3f3`
- Previous production release: `757dad2d4c3e3909ef0d6ed41d56d2b8e7546570`
- Production host: `121.196.230.104`
- Application root: `/www/wwwroot/yj.pduoduo.vip/app`
- Rollback backup:
  `/www/wwwroot/yj.pduoduo.vip/app/backups/phase2-budget-5bd90c07f1d60f907eb7c55a8bf26c1c234ba3f3-20261117-020121`

## Changes

- Generation budget check and task reservation now share one tenant-scoped
  PostgreSQL advisory lock across API processes.
- Guard connections use a separate bounded direct-connection budget so waiting
  submitters cannot exhaust the application PostgreSQL pool.
- Cost snapshots split `committed`, `reserved` and released pre-submit tasks.
- Known local validation failures and explicit queue cancellation no longer
  consume budget.
- Budget blocking records operation ID, entry, batch, session, IP, UA, node and
  estimate without prompt text or credentials.
- The budget dialog displays backend-provided committed and reserved amounts.
- Added migration `0003` with a creation-time index for generation cost reads.

## Local Gates

- Backend: 81/81 modules, 881/881 tests passed.
- Agent: 43 passed.
- Frontend: 87 files, 775 tests passed.
- TypeScript canvas check: passed.
- Production build: passed.
- Schema migration first apply: `0003` applied to every test tenant.
- Schema migration second apply: `0003` skipped, zero changes.
- 24-process budget stress:
  - limit 8;
  - accepted 8;
  - persisted tasks 8;
  - amount 8;
  - errors 0.
- Multi-process API/Worker gate:
  - 4 tenants, 12 tasks;
  - 12 succeeded;
  - max submits per task 1;
  - pool waiting 0;
  - readiness OK.

Artifacts:

- `artifacts/budget-guard-stress-final2.json`
- `artifacts/multiprocess-gate-phase2/report.json`
- `artifacts/schema-migrations-phase2.json`
- `artifacts/schema-migrations-phase2-second.json`

## Production Verification

- `drama-canvas-yj.service`: active
- `drama-canvas-worker.service`: active
- `drama-canvas-agent.service`: active
- `RELEASE_COMMIT`: `5bd90c07f1d60f907eb7c55a8bf26c1c234ba3f3`
- `/healthz`: OK with matching commit
- `/readyz`: OK, database, migrations and Worker fleet healthy
- PostgreSQL pool: waiting `0`, timeout total `0`
- Frontend entry: `/assets/index-D3W4GuLG.js`
- Browser: login and shell loaded, page errors `0`, console errors `0`
- Migration `0003`: applied once in every active tenant schema
- Active generation tasks: `0`

Production budget readback:

```json
{
  "daySpend": 126.292,
  "dayCommitted": 118.404,
  "dayReserved": 7.888,
  "monthSpend": 874.098,
  "monthCommitted": 855.872,
  "monthReserved": 18.226
}
```

The legacy `tenant_legacy_root_32d1143b` schema has no migration registry and is
not part of the active tenant readiness set. It was left untouched.

## Cost Safety

No paid image, audio or video generation was submitted during development,
stress, migration, deployment or verification.

## Remaining Phase 1 Acceptance

The 24-hour mock-provider soak still requires a passing report. This Phase 2
release does not replace or imply completion of that soak.
