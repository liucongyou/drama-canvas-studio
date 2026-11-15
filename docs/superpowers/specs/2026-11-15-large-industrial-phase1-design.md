# Large Industrial Platform Phase 1 Design

Date: 2026-11-15
Status: Proposed
Scope: API/worker separation, durable PostgreSQL queue, bounded database
connections, versioned migrations, metrics, multi-instance readiness and
industrial acceptance tests

## 1. Objective

Move the current production system from a single-process industrial baseline to
a first-stage large-platform architecture while preserving all existing product
contracts.

The phase-one acceptance target is:

- 100 tenants;
- 30 concurrent generation tasks;
- API and generation workers in separate processes;
- two API instances supported by shared PostgreSQL state;
- two to four worker instances supported by database leases;
- no in-process queue state required for correctness;
- no duplicate provider submission during worker or API failover;
- one 24-hour mock-provider soak test;
- rolling restart of API or Worker without losing queued work.

The architecture must not be hard-coded to 100 tenants. The same contracts must
allow later growth to 500 or more tenants by adding API and Worker instances.

## 2. Scope and Non-Goals

### 2.1 In scope

- Durable task queue and claim protocol in PostgreSQL.
- API/Worker process separation.
- Distributed generation concurrency admission.
- Bounded PostgreSQL connection pools.
- Versioned database migrations.
- Worker readiness, heartbeat and operational metrics.
- Structured logs with tenant, request, operation and task correlation.
- Rolling restart and graceful drain.
- Multi-instance regression, failure-injection and soak tests.
- Updated production deployment and operations documentation.

### 2.2 Out of scope

- Redis, Kafka, Celery, RabbitMQ or another external queue.
- Kubernetes, Helm or cloud-managed orchestration.
- A full rewrite of the canvas frontend.
- A big-bang split of every existing backend module.
- Changes to the feed JSON, project, task, share, Agent or provider contracts.
- Paid provider generation during load or failover tests.
- Claiming support for 500 or more tenants before its dedicated capacity test.

## 3. Current Baseline

The design addresses these verified conditions:

- Production runs on 2 vCPU and 3.5 GiB RAM with swap exhausted.
- PostgreSQL permits 100 connections; current idle usage is approximately 15.
- `pg_compat.py` caps only the total number of idle connections. It does not
  hard-limit connections in active use.
- Generation workers, provider polling, recovery and maintenance loops currently
  run inside the web process.
- Queue concurrency and provider cooldown counters are process-local.
- `server.py` is approximately 13,600 lines.
- `canvas_store.py` is approximately 9,000 lines and contains 81 schema-related
  statements without a versioned migration registry.
- `CanvasView.tsx` is approximately 20,000 lines.
- Existing PostgreSQL tasks already have useful lease fields, but the execution
  plane does not yet use them as a cross-instance queue.

## 4. Architecture

```text
Browser / Agent
       |
       v
  API instances
  - auth and tenancy
  - project reads/writes
  - task submit/query/cancel
  - no provider generation
       |
       v
 PostgreSQL 16
 - project and task state
 - durable queue
 - worker leases
 - concurrency admission
 - audit and event outbox
       ^
       |
  Worker instances
  - claim queued tasks
  - provider submission/polling
  - result materialization
  - task and node terminal commits
  - heartbeat and recovery
```

API and Worker are separate systemd services. They may run on the same host for
development, but production supports running them on different hosts without a
code or contract change.

## 5. Execution Modes

Add:

```text
CANVAS_GENERATION_EXECUTION_MODE=inline|worker
```

### 5.1 Inline

Preserves the current single-process behavior for local development and
backward-compatible tests. Inline mode must not be enabled in production.

### 5.2 Worker

Production mode. The API creates a durable task and returns `202` without
starting a provider worker thread. Worker instances poll and claim tasks.

When Worker execution mode is configured, API liveness remains independent of
the Worker fleet. API readiness becomes false when no Worker heartbeat has
appeared within the configured readiness window. This permits a controlled
startup order where the API starts before the first Worker without entering a
restart loop.

### 5.3 Compatibility

Existing HTTP endpoints and response codes remain unchanged. The only internal
difference is that a queued task is accepted by the API and claimed by a Worker
instead of being executed inside the API process.

## 6. API Responsibilities

The API may:

- validate tenant, account, project, node and provider capabilities;
- enforce request idempotency;
- create one queued task per node;
- read task state and return the backend outcome projection;
- cancel queued work before provider acceptance;
- expose authenticated task, audit and project APIs;
- emit task-created notification or wake-up hints.

The API may not:

- call a paid provider generation endpoint;
- poll a provider generation task;
- materialize a provider result;
- hold an in-memory generation concurrency slot as a correctness condition;
- declare task success or failure from frontend state.

## 7. Worker Responsibilities

Each Worker may:

- claim one eligible queued task using a database lease;
- acquire distributed concurrency admission;
- submit or resume the provider task;
- renew the task lease while working;
- persist provider task identity before any uncertain network boundary;
- poll, download, validate and materialize results;
- commit terminal task, node and media version state atomically;
- release the lease and concurrency admission in `finally`.

A Worker that loses its lease must stop writing task or project state. A late
provider result may be recovered only through the persisted provider task ID and
the normal terminal-commit protocol.

## 8. Durable Queue

The existing `generation_tasks` table remains the source of truth. Add or
normalize these fields as needed:

- `queue_state`;
- `available_at`;
- `priority`;
- `worker_id`;
- `lease_owner`;
- `lease_expires_at`;
- `heartbeat_at`;
- `attempt_count`;
- `last_worker_error`.

Existing `status`, `provider_task_id`, `delivery_status` and result fields remain
authoritative for user-visible state.

### 8.1 Claim query

Workers claim with a transaction equivalent to:

```sql
SELECT id
FROM generation_tasks
WHERE status = 'queued'
  AND available_at <= now()
  AND (lease_expires_at IS NULL OR lease_expires_at <= now())
ORDER BY priority DESC, created_at ASC, rowid ASC
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

The selected row is transitioned to a leased claim in the same transaction.

### 8.2 Lease

- Lease owner contains `worker_id` plus a random claim token.
- Workers renew the lease at one third of the lease TTL or faster.
- Expired leases are eligible for recovery.
- A task with an existing provider task ID is resumed, never resubmitted.
- A task without a provider task ID and with an uncertain submission is not
  automatically resubmitted; it follows the existing confirmation policy.

### 8.3 Wake-up

Workers poll at a configurable short interval. `LISTEN/NOTIFY` or a wake-up table
may be used as an optimization, but correctness may not depend on notification
delivery.

## 9. Distributed Concurrency Admission

Correctness must be shared across all Worker instances. Local thread-pool limits
are an optimization only.

The admission dimensions are:

- global;
- tenant;
- provider;
- tenant and provider;
- model or task kind.

Admission uses a short PostgreSQL transaction with advisory transaction locks
on the relevant dimensions. Within that transaction, the Worker counts active
`submitting` and `running` tasks for the dimension and transitions the claimed
task to `submitting` only if every configured limit has capacity.

Lock acquisition order is deterministic:

```text
global -> tenant -> provider -> tenant/provider -> model/kind
```

If capacity is unavailable:

- the task remains `queued`;
- it receives a backend-generated queue phase and next eligibility time;
- the lease is released;
- the Worker waits and tries another eligible task.

Provider rate-limit cooldowns are persisted with an expiry time so they apply
across Worker instances.

## 10. Bounded PostgreSQL Connection Pool

Replace the current idle-only pool with one process-wide hard connection budget,
plus idle pools scoped by database schema.

Required properties:

- hard maximum of checked-out plus checked-in connections;
- acquisition wait timeout;
- no unbounded fallback connection creation;
- statement and lock timeouts;
- transaction-clean return to pool;
- health statistics for total, idle, active and waiting callers;
- pool exhaustion produces a stable backend error rather than opening more
  connections;
- pool close on service shutdown.

Configuration:

```text
CANVAS_PG_POOL_MAX
CANVAS_PG_POOL_WAIT_TIMEOUT_MS
CANVAS_PG_POOL_IDLE_MAX
CANVAS_PG_CONNECT_TIMEOUT_SECONDS
CANVAS_PG_STATEMENT_TIMEOUT_MS
CANVAS_PG_LOCK_TIMEOUT_MS
```

The production total pool budget must remain below PostgreSQL `max_connections`
after reserving capacity for migrations, administration and emergency access.
Per-schema idle pools may not bypass the process-wide hard budget.

## 11. Versioned Migrations

Introduce one migration registry in each required database schema:

```sql
schema_migrations(
  version TEXT PRIMARY KEY,
  checksum TEXT NOT NULL,
  applied_at TEXT NOT NULL,
  duration_ms INTEGER NOT NULL
)
```

Migrations are immutable, ordered files executed once inside a transaction.

Startup behavior:

- API and Worker verify that all required migrations are applied;
- production services fail closed when the database is behind;
- a migration CLI performs upgrades before the service switch;
- concurrent migration execution is prevented with a PostgreSQL advisory lock;
- a checksum mismatch fails closed.

The initial phase moves queue, worker, metrics and new index changes into
versioned migrations. Existing idempotent bootstrap DDL remains for backward
compatibility until later consolidation.

## 12. Observability

### 12.1 Structured logs

Backend logs include, where applicable:

- timestamp;
- severity;
- service;
- release commit;
- request ID;
- tenant ID;
- project ID;
- episode ID;
- node ID;
- operation ID;
- task ID;
- provider task ID;
- duration.

Secrets, cookies, Authorization headers, API keys and signed URL query strings
are always redacted.

### 12.2 Metrics

Expose Prometheus text metrics on a loopback-only operational listener:

- task queue depth by status and kind;
- oldest queued task age;
- active task count by provider and kind;
- worker heartbeat count and age;
- lease expiry and recovery counts;
- provider submission, polling and delivery failures;
- provider rate-limit cooldowns;
- task duration histogram;
- database pool active, idle, waiting and exhausted counts;
- API request latency and status classes;
- SSE current and rejected connection counts;
- snapshot, audit event and event-outbox storage bytes.

### 12.3 Health

Add:

- `/healthz`: process liveness;
- `/readyz`: database, required schema, queue and Worker-readiness checks;
- Worker heartbeat record in PostgreSQL;
- authenticated admin operational summary.

Liveness must not depend on providers. Readiness fails when the process cannot
serve its assigned role safely.

### 12.4 Alert conditions

The deployment documentation defines thresholds for:

- no Worker heartbeat;
- oldest queued task age;
- pool saturation;
- PostgreSQL connection usage;
- lease expiry spike;
- repeated provider submission uncertainty;
- audit or frontend-error storage growth;
- disk and swap pressure.

## 13. Deployment and Capacity

### 13.1 Services

```text
drama-canvas-yj.service       API
drama-canvas-worker.service   generation queue workers
drama-canvas-agent.service    Agent sidecar
```

All services have:

- explicit memory and task limits;
- graceful shutdown timeout;
- restart backoff;
- release commit environment;
- structured logging configuration;
- access to the same PostgreSQL database and tenant storage.

### 13.2 Capacity policy

The existing 2 vCPU / 3.5 GiB host is sufficient for development and limited
production validation, not for the full first-stage target.

Production capacity must be validated on either:

- an upgraded single host with separately measured API and Worker limits; or
- one API host and one Worker host.

The code must not depend on both services sharing a host.

### 13.3 Rolling restart

- API instances are removed from serving new requests before shutdown.
- The API waits for in-flight HTTP work up to the configured drain timeout.
- Workers stop claiming new tasks immediately.
- Workers renew and finish current tasks up to the drain timeout.
- Unfinished work remains leased and recoverable by another Worker.
- Readiness remains false until startup checks and migrations pass.

### 13.4 Shared media storage

Worker result materialization writes media bytes before the terminal node commit.
When API and Worker run on different hosts, tenant upload storage must be one of:

- shared persistent storage mounted at the configured tenant data root;
- object storage with durable local staging;
- the same Worker-host volume exposed through the authenticated media API.

The terminal node may not be marked ready until the returned media URL is
durably readable from every API instance. A path that exists only on one Worker
host is not a valid multi-host deployment.

## 14. Failure Handling

### 14.1 Worker crash

Another Worker may recover the task after lease expiry. Provider task IDs prevent
duplicate submission.

### 14.2 API crash

Queued and active work continues. A new API instance reads task state from
PostgreSQL.

### 14.3 Database unavailable

API and Worker readiness fail closed. No provider request is submitted unless
the task and provider identity can be persisted safely.

### 14.4 Pool exhaustion

Requests wait up to the configured timeout, then return a stable
`DATABASE_POOL_EXHAUSTED` error. The system does not open unlimited connections.

### 14.5 Queue starvation

Priority and availability rules must prevent low-priority tasks from waiting
forever. Queue ordering combines explicit priority with bounded age promotion,
so old work eventually becomes eligible ahead of newer lower-urgency work. The
metrics expose the oldest queued task by tenant and kind.

### 14.6 Duplicate submission

The node-level active-task guard, operation ID idempotency, database claim lease
and provider task ID persistence jointly prevent duplicate paid submissions.

## 15. Testing

### 15.1 Unit and integration

- bounded pool never exceeds its maximum;
- pool wait timeout returns the stable error;
- migration checksum and concurrency behavior;
- queue claim with multiple Workers;
- expired lease recovery;
- no duplicate active task per node;
- distributed global, tenant, provider and model limits;
- provider cooldown persistence;
- API submission with Worker mode starts no provider call;
- Worker crash after provider acceptance resumes by provider task ID;
- graceful shutdown leaves unfinished tasks recoverable;
- metrics and readiness transitions.

### 15.2 Multi-process tests

Run two API and four Worker processes against an isolated PostgreSQL database.
Use deterministic provider mocks only.

Required results:

- no duplicate provider submission;
- no duplicate active task;
- no task lost during process kill;
- no cross-tenant access;
- no pool over-allocation;
- API remains responsive while provider waits are held open.

### 15.3 Soak test

Run for 24 hours with:

- 100 simulated tenants;
- 30 concurrent tasks;
- image, audio and video task kinds;
- repeated Worker termination and restart;
- provider latency, rate-limit and ambiguous-response scenarios;
- queue backlog and recovery cycles;
- periodic canvas reads and writes;
- audit and telemetry generation.

Acceptance:

- zero duplicate paid requests;
- zero duplicate active tasks;
- zero unrecovered lease leaks;
- zero pool limit violations;
- zero unbounded table or log growth;
- task success or explicit terminal uncertainty for every accepted task;
- no unexpected process restart from unhandled exceptions.

## 16. Rollout

The rollout is incremental:

1. bounded pool, migration registry, structured logging and metrics;
2. durable queue and external Worker support behind the execution-mode flag;
3. API Worker-mode integration and worker readiness;
4. multi-process local integration and failure injection;
5. 24-hour soak test;
6. production deployment with legacy inline mode retained as rollback;
7. switch production to Worker mode in one controlled cutover;
8. verify same release commit, API and Worker heartbeats, queue recovery and no
   paid test generation.

## 17. Acceptance

Phase one is accepted only when:

- API and Worker execute as independent processes;
- two API and four Worker instances pass shared-state tests;
- PostgreSQL connections cannot exceed the configured budget;
- required schema migrations are versioned and verified;
- readiness, metrics, structured logs and admin operational summary are live;
- the 24-hour mocked-provider soak test passes;
- no existing canvas, Agent, feed, share or task contract is broken;
- all existing backend, Agent, frontend, browser and industrial stress gates
  remain green;
- production switch and rollback have both been verified;
- the deployed release commit is recorded in Git and production readback.
