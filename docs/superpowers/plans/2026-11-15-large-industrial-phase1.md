# Large Industrial Platform Phase 1 Implementation Plan

> Execute in order. Every task ends with focused tests and a local commit.
> Production deployment occurs once, after the full release gate passes.

**Goal:** Separate API and generation execution, add a durable PostgreSQL queue,
enforce a hard database-connection budget, add versioned migrations and expose
operational readiness/metrics for a 100-tenant, 30-concurrent-task platform.

**Spec:**
`docs/superpowers/specs/2026-11-15-large-industrial-phase1-design.md`

## Global Constraints

- Preserve all existing HTTP, feed, Agent, share and project contracts.
- Production remains PostgreSQL-only.
- No paid provider call in any test.
- API and Worker must be separable without changing business APIs.
- Correctness may not depend on process-local queue counters.
- A Worker crash or API restart may not create a duplicate provider request.
- Existing tests and industrial release gates must remain green.
- Each migration is immutable, versioned and idempotently applied once.
- Do not use `git add .`; the worktree contains unrelated user-generated files.

---

## Task 1: Enforce a Bounded PostgreSQL Connection Budget

**Status:** Complete (`bb8e715`)

**Files**

- Modify: `pg_compat.py`
- Create: `tests/test_pg_connection_pool.py`
- Modify: `tests/test_pg_compat.py`
- Modify: `server.py`

**Interfaces**

- `pg_compat.pool_stats()` returns:

```json
{
  "max": 40,
  "active": 3,
  "idle": 12,
  "waiting": 0,
  "acquiredTotal": 100,
  "timeoutTotal": 1
}
```

- `pg_compat.connect_path()` blocks up to
  `CANVAS_PG_POOL_WAIT_TIMEOUT_MS` when the process-wide budget is exhausted.
- Exhaustion raises `pg_compat.OperationalError` with
  `DATABASE_POOL_EXHAUSTED`.

**Steps**

1. Add failing tests proving that 50 concurrent acquisitions never exceed
   the configured maximum and timed-out callers return the stable code.
2. Replace the idle-only queue accounting with:
   - one process-wide semaphore;
   - per-schema idle queues;
   - active/idle/waiting counters;
   - acquisition and release statistics.
3. Preserve transaction cleanliness and the existing SQL compatibility layer.
4. Add pool statistics to the authenticated runtime metrics response.
5. Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_pg_connection_pool.py tests\test_pg_compat.py -q
```

6. Commit:

```text
perf: enforce a hard postgres connection budget
```

**Acceptance**

- No test observes more than the configured active budget.
- Waiting callers either acquire a released connection or fail with the stable
  timeout code.
- Returning a connection never leaks the permit.

---

## Task 2: Add a Versioned Migration Registry

**Status:** Complete

**Files**

- Create: `schema_migrations.py`
- Create: `migrations/0001_generation_worker_queue.sql`
- Create: `migrations/0002_worker_heartbeats.sql`
- Create: `tests/test_schema_migrations.py`
- Modify: `canvas_store.py`
- Modify: `server.py`

**Interfaces**

- `schema_migrations.apply_migrations(connection, schema_kind)` applies missing
  migrations under a PostgreSQL advisory lock.
- `schema_migrations.verify_schema(connection, schema_kind)` returns the current
  version and rejects checksum mismatches.
- Every tenant canvas schema and the control schema have independent migration
  rows.

**Steps**

1. Write a failing test for:
   - first application;
   - second no-op application;
   - checksum mismatch;
   - two concurrent migration callers.
2. Add `schema_migrations` with version, checksum, applied time and duration.
3. Add an advisory-lock-protected migration runner.
4. Move the queue and Worker heartbeat schema additions into immutable SQL files.
5. Add a migration CLI:

```text
python scripts/run-schema-migrations.py --url <database-url> --check|--apply
```

6. Make API and Worker readiness fail when required migrations are missing.
7. Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_schema_migrations.py -q
```

8. Commit:

```text
feat: add versioned schema migrations
```

**Acceptance**

- Concurrent migration attempts apply each version once.
- A changed applied migration checksum fails closed.
- Service startup never performs an unversioned production table alteration.

---

## Task 3: Implement the Durable PostgreSQL Task Queue

**Status:** Complete

**Files**

- Create: `generation_queue.py`
- Create: `tests/test_generation_queue_claims.py`
- Modify: `canvas_store.py`
- Modify: `tests/test_pg_concurrency.py`

**Interfaces**

- `claim_next_generation_task(worker_id, claim_token)` returns one leased task or
  `None`.
- `renew_generation_task_claim(task_id, claim_token, ttl_seconds)` returns
  `bool`.
- `release_generation_task_claim(task_id, claim_token)` returns `bool`.
- Claims use `FOR UPDATE SKIP LOCKED`.
- Expired tasks with provider task IDs resume; tasks without provider identity
  remain uncertain and are not resubmitted automatically.

**Steps**

1. Add tests for:
   - two Workers claiming different tasks;
   - only one Worker claiming the same task;
   - expired lease recovery;
   - provider-identity resume;
   - non-accepted uncertain task remaining blocked.
2. Add queue indexes:
   - `(status, available_at, priority, created_at)`;
   - `(worker_id, heartbeat_at)`;
   - `(lease_expires_at)`.
3. Implement claim, heartbeat, release and expired-lease queries.
4. Add bounded age promotion to effective priority.
5. Expose queue-depth and oldest-task metrics.
6. Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_generation_queue_claims.py tests\test_pg_concurrency.py -q
```

7. Commit:

```text
feat: add durable postgres generation queue
```

**Acceptance**

- Concurrent claims never return the same task.
- A crashed claim becomes recoverable after lease expiry.
- Queue order cannot starve an old task indefinitely.

---

## Task 4: Add Distributed Generation Concurrency Admission

**Status:** Complete

**Files**

- Modify: `generation_queue.py`
- Modify: `server.py`
- Create: `tests/test_generation_admission.py`
- Modify: `tests/test_generation_queue.py`

**Interfaces**

- `acquire_generation_admission(...)` returns a persisted admission token or a
  next-attempt delay.
- `release_generation_admission(token)` releases capacity.
- Limits are shared across all Worker processes.

**Steps**

1. Add tests for global, tenant, provider, tenant-provider and model limits.
2. Acquire advisory transaction locks in the specified deterministic order.
3. Count only unexpired `submitting` and `running` tasks within the locked
   transaction.
4. Move provider cooldown expiry to PostgreSQL.
5. Keep local thread-pool limits as an optimization only.
6. Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_generation_admission.py tests\test_generation_queue.py -q
```

7. Commit:

```text
feat: coordinate generation admission in postgres
```

**Acceptance**

- Two Workers cannot exceed a shared limit.
- Cooldown set by one Worker applies to another.
- A Worker crash releases capacity after lease expiry.

---

## Task 5: Separate API and Worker Execution

**Status:** Complete

**Files**

- Create: `generation_worker.py`
- Create: `tests/test_generation_worker_process.py`
- Modify: `server.py`
- Modify: `tests/test_generation_task_persistence.py`
- Create: `deploy/drama-canvas-worker.service`

**Interfaces**

- Environment:

```text
CANVAS_GENERATION_EXECUTION_MODE=inline|worker
CANVAS_WORKER_ID
CANVAS_WORKER_CONCURRENCY
CANVAS_WORKER_POLL_SECONDS
```

- In `worker` mode, API submission writes the task and returns `202` without
  starting a generation thread.
- `generation_worker.py` claims queued tasks and invokes the existing generation
  service behind a stable adapter.

**Steps**

1. Add a test proving API submission in Worker mode makes no provider call.
2. Add a Worker loop with bounded local executor and graceful drain.
3. Reuse the current task worker implementation behind a worker adapter; do not
   duplicate provider logic.
4. Support task resume after process kill.
5. Add Worker heartbeat and readiness state.
6. Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_generation_worker_process.py tests\test_generation_task_persistence.py -q
```

7. Commit:

```text
feat: separate generation api and worker execution
```

**Acceptance**

- API process performs no provider submit or poll in Worker mode.
- Worker restart resumes the same task.
- No paid request is duplicated after kill between provider acceptance and local
  terminal write.

---

## Task 6: Add Readiness, Metrics and Structured Correlation

**Status:** Complete

**Files**

- Create: `operational_metrics.py`
- Create: `tests/test_operational_readiness.py`
- Modify: `server.py`
- Modify: `generation_worker.py`
- Modify: `telemetry.py`

**Interfaces**

- `/healthz`: liveness only.
- `/readyz`: database, migration, queue and role readiness.
- Loopback Prometheus endpoint.
- Every log record contains available correlation IDs.

**Steps**

1. Add readiness tests for:
   - API without Worker heartbeat;
   - Worker after heartbeat;
   - schema behind;
   - database unavailable;
   - recovered state.
2. Add queue, pool, Worker, provider, task-duration and storage metrics.
3. Add correlation middleware for API and Worker jobs.
4. Redact credentials and signed URL queries in all structured logs.
5. Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_operational_readiness.py tests\test_audit_metrics.py -q
```

6. Commit:

```text
feat: add operational readiness and metrics
```

**Acceptance**

- Liveness does not depend on providers.
- Readiness reflects each process role.
- Metrics contain no tenant secrets or signed URLs.

---

## Task 7: Add Multi-Process Failure and Soak Harness

**Status:** Complete

**Files**

- Create: `scripts/multiprocess_stress.py`
- Create: `scripts/soak_generation.py`
- Create: `tests/test_multiprocess_harness.py`
- Modify: `scripts/industrial_release_gate.ps1`

**Interfaces**

- Harness starts two API and four Worker processes against a temporary
  PostgreSQL schema suffix.
- Provider adapters are deterministic mocks.
- The harness can kill and restart Workers at selected state transitions.

**Steps**

1. Implement process supervision and deterministic mock provider endpoints.
2. Add scenarios for Worker kill, API kill, provider timeout, ambiguous
   acceptance, pool exhaustion and queue backlog.
3. Add 24-hour soak mode with 100 tenants and 30 concurrent tasks.
4. Add a bounded smoke mode for the normal release gate.
5. Assert zero duplicates, zero lease leaks, zero pool violations and no lost
   accepted task.
6. Commit:

```text
test: add multiprocess and soak generation harness
```

**Acceptance**

- Smoke mode finishes inside the release gate time budget.
- Soak mode produces a machine-readable report.
- Every accepted task reaches a terminal or explicit uncertain state.

---

## Task 8: Update Deployment and Release

**Status:** Implementation complete; production verification pending

**Files**

- Modify: `deploy/README.md`
- Create: `deploy/20261116-worker-plane-release.sh`
- Modify: `deploy/20261115-industrial-root-refactor.sh`
- Modify: `docs/reports/2026-11-15-industrial-root-refactor-release.md`
- Create: `docs/reports/2026-11-16-large-industrial-phase1-release.md`

**Steps**

1. Remove obsolete SQLite production instructions.
2. Document same-host and split-host topology, shared media storage, migration
   CLI and pool budgets.
3. Add API and Worker drain/rolling restart procedures.
4. Add production alert thresholds.
5. Run the full release gate, migration idempotency and multiprocess smoke.
6. Deploy API and Worker services in one release window.
7. Verify release commit, readiness, queue recovery, media Range and no paid
   test generation.
8. Commit release records.

**Acceptance**

- API and Worker are independently active.
- Worker stop does not break API reads or task submission.
- Worker restart recovers queued work.
- Rollback restores the previous API/Worker release and release commit.
