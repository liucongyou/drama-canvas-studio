# Large Industrial Phase 1 Release

Date: 2026-11-16
Status: Deployed and production-verified; restarted 24-hour soak in progress

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

The full 24-hour mock-provider soak has been restarted and has not yet finished. A
100-tenant smoke and failure-injection test are not equivalent to the 24-hour
acceptance run.

Soak run:

- Started: `2026-11-16 03:35:14 +08:00`
- First-run launcher PID: `24508` (interrupted, no report)
- Restart launcher PID: `33008`
- Isolated database: `drama_canvas_soak_20261116`
- Isolated server: `127.0.0.1:55432`
- Active output directory: `artifacts/soak-100t-24h-v2`
- Provider mode: deterministic mock only
- Worker rotation: one hard kill and replacement every hour
- Expected duration: 24 hours

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

The soak wrapper must produce `artifacts/soak-100t-24h-v2/report.json` with
`ok=true`. Until that file exists and validates, the full phase-one acceptance
remains pending.

## Soak Harness Correction

The first short soak preflights exposed three test-harness defects rather than
generation defects:

- the control `owner` tenant was incorrectly included in batch accounting;
- 100 per-tenant status queries every 250 ms saturated the 2-vCPU test host;
- the validator required every Worker-kill-adjacent task to succeed, even
  though the production contract correctly requires tasks without a provider
  identity to become explicit `interrupted` uncertainty.

The harness now:

- excludes `owner` from synthetic load accounting;
- samples all tenant schemas through one connection every two seconds;
- permits only bounded, explicit interrupted uncertainty after hard kills;
- rejects any active leftover task, failed task or duplicate submission;
- rotates Worker kills hourly during the 24-hour run.

The corrected six-minute preflight passed with 200/200 succeeded, 0 active
leftovers and at most one submit per task:

```text
artifacts/soak-preflight-6m-v4/report.json
```

## Soak Connection-Budget Correction

The first 24-hour run did not complete. At approximately `2026-11-16 11:25`
the isolated local PostgreSQL test cluster rejected a project insert with:

```text
FATAL: sorry, too many clients already
```

The cause was the test topology, not the production service:

- two API child processes;
- four Worker child processes;
- prior short-run child processes and parent sampling;
- each child configured with a process pool maximum of eight;
- a local test cluster limit of 100 connections;
- full release gates running concurrently against the same local server.

The soak child budget is now:

```text
CANVAS_PG_POOL_MAX=4
CANVAS_PG_POOL_IDLE_MAX=2
CANVAS_PG_POOL_WAIT_TIMEOUT_MS=15000
```

The corrected preflight completed with 100/100 tasks succeeded and each API
reporting:

```text
pool max=4
physical=2
waiting=0
timeoutTotal=0
```

Artifact:

```text
artifacts/soak-preflight-6m-v5/report.json
```

The durable recovery journal is also bounded by recent-entry count and age:

```text
CANVAS_GENERATION_JOURNAL_MAX_ENTRIES=10000
CANVAS_GENERATION_JOURNAL_RETENTION_SECONDS=604800
```

Old entries are pruned only after the age window; recent paid-task receipts are
never removed. This prevents the 24-hour run from creating an unbounded number
of tiny journal files.

The `2026-11-16` run was interrupted by the local PostgreSQL maintenance
shutdown and did not produce a passing report.

The current acceptance run was restarted after the Phase 2 release:

- Started: `2026-11-17 02:08:13 +08:00`
- Launcher PID: `21132`
- Isolated database: `drama_canvas_soak_20261117`
- Output directory: `artifacts/soak-100t-24h-v3`
- Mock provider only; no paid provider call
- Status: running, not yet accepted

The run may only be marked passed after
`artifacts/soak-100t-24h-v3/report.json` exists with `ok=true` and validates
the completion criteria above.

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
