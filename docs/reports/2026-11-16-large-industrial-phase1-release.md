# Large Industrial Phase 1 Release

Date: 2026-11-16
Status: Deployed and production-verified; 24-hour soak pending

## Release

- Branch: `codex/agent-overhaul`
- API and Worker release: `1367e8e4e97ea8dfbb2c3176c5d34968593b7f56`
- Previous production release: `09431366bb0a8e886da64cace07c2816d442e4f9`
- Production host: `121.196.230.104`
- Application root: `/www/wwwroot/yj.pduoduo.vip/app`
- Release stage:
  `releases/worker-plane-1367e8e4e97ea8dfbb2c3176c5d34968593b7f56-20261116-020310`
- Rollback backup:
  `backups/worker-plane-1367e8e4e97ea8dfbb2c3176c5d34968593b7f56-20261116-020310`

The release contains the phase-one API/Worker split, durable PostgreSQL
generation queue, bounded connection pool, versioned migrations, worker
heartbeat/readiness, metrics, multi-process failure handling, and the final
cross-origin audio waveform fix.

## Production Verification

Post-cutover readback:

- `drama-canvas-yj.service`: active
- `drama-canvas-worker.service`: active
- `drama-canvas-agent.service`: active
- `nginx.service`: active
- `RELEASE_COMMIT`: `1367e8e4e97ea8dfbb2c3176c5d34968593b7f56`
- `/healthz`: OK with matching release commit
- `/readyz`: OK, worker execution mode, database and migrations healthy
- Worker heartbeat: current release, one active Worker
- Active generation tasks: `0`
- Duplicate active tasks: `0`
- PostgreSQL process pool: `15` physical, `0` waiting, `0` timeouts
- PostgreSQL log directory: approximately `784 KiB`

Live browser verification used the current production project
`JhKkYbCMnmTYPmZAGu2TEk`:

- 22 media nodes loaded in the current episode.
- All four task recovery actions rendered: restore local result, check provider,
  verify and close, and regenerate.
- A media node opened its prompt editor.
- Browser console errors: `0`.
- TOS/CORS errors: `0`.
- No generation was submitted and no paid provider was called.

## Local Release Gate

The full gate passed on commit `1367e8e`:

- Backend: 845 passed, 0 failed, 0 skipped.
- Agent: 43 passed.
- Frontend: 87 files, 775 tests passed.
- TypeScript canvas check: passed.
- Production frontend build: passed.
- Migration first/second run: `changedRows=0`.
- 32-way industrial stress: passed.
- Multi-process API/Worker kill and recovery: passed.
- Browser desktop/mobile, task recovery, version history, pan/zoom, input
  composition, control overlap and console-error checks: passed.

The 100-tenant, 30-concurrent-task smoke completed in an earlier release gate:

- 100/100 tasks succeeded.
- 2 API and 4 Worker processes.
- One Worker was killed and recovered by a replacement.
- `maxSubmitsPerTask=1`.
- No duplicate provider submission.
- Pool maximum `8`, final active `0`, physical `2`.

Artifact:

- `artifacts/multiprocess-soak-100t-60s-v4/report.json`

## Verification Limit

The full 24-hour mock-provider soak has not yet run. A 100-tenant smoke and
failure-injection test are not equivalent to the 24-hour acceptance run.

Run only against an isolated PostgreSQL database with deterministic mock
providers:

```powershell
.\.venv\Scripts\python.exe scripts\soak_generation.py `
  --url <isolated-postgresql-url> `
  --hours 24 `
  --output artifacts\soak-100t-24h
```

Phase one acceptance remains open until that report passes with:

- zero duplicate paid requests;
- zero duplicate active tasks;
- zero unrecovered lease leaks;
- zero pool-limit violations;
- bounded log and table growth;
- terminal or explicit uncertain state for every accepted task.

## Rollback

The production rollout script automatically restores the previous API, Worker,
frontend, systemd configuration and release commit if health or readiness
fails.

Manual rollback source:

```text
/www/wwwroot/yj.pduoduo.vip/app/backups/
  worker-plane-1367e8e4e97ea8dfbb2c3176c5d34968593b7f56-20261116-020310
```

The backup includes the pre-migration PostgreSQL dump. Restore the database
snapshot only when a migration rollback is required.
