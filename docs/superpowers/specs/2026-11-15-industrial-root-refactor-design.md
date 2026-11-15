# Drama Canvas Industrial Root Refactor

Date: 2026-11-15
Status: Proposed
Scope: backend structure, generation consistency, frontend canvas decomposition,
test gates, data repair, stress testing, deployment and rollback

## 1. Objective

Deliver one production release that fixes the known structural and behavioral
defects as root causes, while preserving the existing user-facing contracts:

- `drama-canvas-feed/v2`;
- current project, episode, node, task and share APIs;
- current Agent gateway contracts;
- current canvas interaction model.

The release does not promise the absence of unknown future defects. It does
require:

- every known P0/P1 and critical P2 issue to be fixed;
- known failure paths to have regression coverage;
- the full test, stress, browser and production-readback gates to pass;
- no partial production rollout before the gates pass.

## 2. Current Audit Baseline

The current codebase has these confirmed risks:

1. The root backend suite is not reproducible: `622 passed / 194 failed /
   6 skipped`. Most failures are caused by tests inheriting production
   PostgreSQL fail-closed rules without an explicit test harness.
2. Generation terminal writes can compete on the same project revision.
   Concurrent paid results can be downloaded successfully but fail to project
   onto their nodes.
3. Recovery only covers a subset of failed-but-materialized local results.
4. Historical task debt remains:
   - 18 interrupted/uncertain tasks;
   - 9 succeeded tasks with pending delivery receipts;
   - no bounded TTL closure for tasks that have no provider task ID.
5. Ten media nodes still have duplicate version IDs; three active versions do
   not match their node content URL.
6. Project mutations are split across repository locks, direct store writes and
   background workers. Process-local locks are not a sufficient project-wide
   write boundary.
7. SSE connections use one thread per connection and have no tenant/project
   connection budget.
8. Several operations still rewrite large full-project payloads.
9. The frontend canvas is a very large component with tightly coupled media,
   generation, event, version and editor state.
10. Failure, missing-media and uncertain-task states do not consistently expose
    the same recovery actions across card and task center.
11. Frontend runtime errors are not centrally traceable by tenant, project,
    node and task.

## 3. Architecture

The refactor uses a strangler pattern behind stable interfaces. It is not a
big-bang file rewrite.

### 3.1 Backend layers

```text
api/          HTTP parsing, authentication, tenancy, response mapping
services/     project, generation, media, task, audit business rules
repositories/ PostgreSQL transactions, projections, migrations, row locks
workers/      leases, polling, retry, recovery, TTL and outbox delivery
domain/       immutable DTOs, enums, error codes and invariants
```

Compatibility rules:

- Existing HTTP paths and response fields remain valid.
- Existing JSON feed contracts remain unchanged.
- New internal modules may be introduced incrementally.
- HTTP handlers must not contain business-state transitions after migration.
- Worker and HTTP paths must call the same service/repository methods.

### 3.2 Frontend canvas modules

The existing `CanvasView` becomes an orchestration shell. State and rendering
move into bounded modules:

```text
canvas/core/        project and episode scope, revision coordination
canvas/nodes/       media, text and segment cards
canvas/editor/      prompt/editor state and persistence
canvas/generation/  submission, task projection and recovery UI
canvas/versions/    media version selection and preview
canvas/tasks/       task center and operator resolution
canvas/events/      SSE, polling and fallback reconciliation
```

The split must not change:

- click, double-click, hover and drag semantics;
- card dimensions and layout rules;
- readonly/share behavior;
- current visual theme contracts.

## 4. Data and Transaction Invariants

### 4.1 Project single-writer

Every mutation of one project uses one PostgreSQL transaction boundary with a
project-scoped row lock or advisory lock.

This includes:

- project create/update/delete;
- node and edge writes;
- notes, viewport and layout;
- import and restore;
- generation terminal projection;
- delivery recovery and background repair.

Different projects remain concurrent.

### 4.2 Generation terminal

The following must commit atomically:

- task terminal state;
- node metadata;
- active media version;
- projection rows;
- event/outbox row;
- delivery receipt.

A terminal write must be idempotent. Retrying it may not create a second media
version or a second paid provider request.

### 4.3 Delivery recovery

Recovery order:

1. reuse durable local bytes;
2. reuse a persisted provider result URL;
3. query the provider using the persisted task ID;
4. mark unconfirmed when no durable evidence exists.

Recovery never submits a new provider generation task.

### 4.4 Task TTL

Tasks with no provider task ID and no durable result may not remain uncertain
forever.

After the configured provider retention/TTL:

- mark them `expired_unconfirmed`;
- remove them from the active submission blocker;
- allow regeneration only after explicit user or operator confirmation;
- retain their audit trail.

Tasks with a provider task ID remain queryable until provider retention
expires.

### 4.5 Media version identity

New versions use globally unique stable IDs.

An idempotent migration repairs historical duplicates:

- keeps the active/current version ID unchanged;
- assigns deterministic unique IDs to non-current mirrors;
- preserves URLs, thumbnail URLs, timestamps and storage receipts;
- records a migration audit entry;
- may run repeatedly without further mutation.

An active version must point to a URL present in its version list. Mismatches
must be repaired or surfaced as an explicit missing state, never silently
resolved by timestamps alone.

## 5. Backend Services

### 5.1 ProjectService

Owns:

- project revision and metadata;
- episode membership;
- node/edge/note/viewport mutations;
- import and snapshot restore;
- project deletion guard.

All handlers and workers call this service.

### 5.2 GenerationService

Owns:

- idempotency operation keys;
- one active task per node;
- provider capability validation;
- task creation and queue admission;
- terminal state projection;
- manual retry/resolve policy.

### 5.3 DeliveryService

Owns:

- local-result recovery;
- remote-result re-download with backoff;
- delivery claims;
- missing-file classification;
- provider receipt verification.

### 5.4 TaskMaintenance

Owns:

- stale lease expiry;
- provider polling;
- TTL closure;
- interrupted/uncertain reconciliation;
- task metrics and health.

### 5.5 EventService

Owns:

- project event outbox;
- event retention per project;
- SSE subscriber budgets;
- heartbeat and disconnect cleanup;
- compact event payloads.

## 6. API and Error Contract

All mutation errors use stable machine codes and Chinese user messages.

Required codes include:

- `PROJECT_REVISION_CONFLICT`;
- `NODE_UPDATE_CONFLICT`;
- `GENERATION_OPERATION_CONFLICT`;
- `GENERATION_TASK_ACTIVE`;
- `GENERATION_OUTCOME_UNCERTAIN`;
- `GENERATION_OUTCOME_EXPIRED`;
- `MEDIA_SOURCE_MISSING`;
- `MEDIA_VERSION_MISSING`;
- `TENANT_ACCESS_DENIED`;
- `SHARE_READONLY`;
- `SSE_CONNECTION_LIMIT`.

The frontend may display backend status but must not invent task terminal state.

## 7. UX State Model

Every media node follows one state machine:

```text
idle -> submitting -> queued -> generating -> finalizing -> done
                                      \-> failed
                                      \-> uncertain -> expired_unconfirmed
```

Card and task center must agree on:

- state label;
- current action;
- whether submit is locked;
- whether playback/download is available;
- whether history is available;
- whether escalation requires provider billing/task verification.

Missing local media is displayed as `媒体源缺失`, not indefinite loading.

## 8. Performance and Capacity

Targets for the release gate:

- current-episode polling payload below 350 KB;
- 100-node pan/zoom test sustains at least 50 FPS on the reference browser;
- prompt typing produces no full-canvas state rebuild;
- 20-node batch submission remains responsive and cancellable before dispatch;
- SSE connections are bounded per tenant and per project;
- no database pool starvation under concurrent generation and canvas writes;
- no unbounded snapshot, event, audit or task growth.

## 9. Migration and Repair

### 9.1 Test environment

Add explicit test configuration that:

- enables isolated SQLite only for tests;
- keeps production fail-closed behavior;
- prevents tests from sharing the repository production database;
- restores all environment variables after each test.

### 9.2 Production migrations

Before production:

1. take a PostgreSQL tenant snapshot;
2. restore it into a local PostgreSQL test database;
3. run migration and repair scripts there;
4. verify source/output counts and hashes;
5. run the full gate against the migrated local copy;
6. only then execute the same scripts online.

Production repairs are idempotent and audited.

## 10. Test and Stress Gates

### 10.1 Automated

- full backend suite: 0 failures;
- Agent suite: 0 failures;
- frontend Vitest: 0 failures;
- TypeScript: 0 errors;
- production build: pass;
- Python compile/static checks: pass;
- migration dry-run and idempotency: pass.

### 10.2 Stress

- 10 tenants concurrently reading and writing isolated projects;
- 20 simultaneous generation-task state transitions without cross-project
  blocking or node/task mismatch;
- same-node duplicate submission returns one active task;
- concurrent terminal commits preserve all versions and events;
- SSE reconnect, disconnect and connection-limit behavior;
- 100-node canvas interaction and media history switching;
- weak-network save retry and page reload recovery;
- local media first-byte/range playback and missing-file handling.

No paid provider generation is allowed in stress testing. Provider boundaries
use deterministic mocks and recorded fixtures.

### 10.3 Browser

Run real browser tests at desktop and mobile widths:

- project/episode load;
- prompt edit and reload persistence;
- single and multi-node generation projection using mocks;
- task completion and recovery;
- image/video history switching;
- readonly share;
- no page errors, console errors or overlapping controls.

## 11. Release and Rollback

Release is one atomic application switch after all gates pass.

Rollback assets:

- code backup;
- previous frontend dist;
- database snapshot;
- migration audit and reverse/repair notes;
- service health and release commit.

Post-deploy checks:

- main and Agent services active;
- login and tenant-scoped reads succeed;
- project/episode/node counts match baseline;
- no active stale tasks introduced;
- no duplicate version increase;
- media Range requests work;
- frontend entry and security headers correct.

## 12. Non-Goals

- changing the business workflow or canvas interaction model;
- changing provider billing contracts;
- introducing paid generation during testing;
- rewriting every legacy file for style-only reasons;
- claiming mathematical certainty that no future unknown bug can occur.

## 13. Acceptance

The release is accepted only when:

- all known audit findings are fixed or explicitly marked unrecoverable with a
  user-safe recovery path;
- full automated and stress suites pass locally;
- migration on the production snapshot passes with `badEntries=0`;
- a single atomic production deployment succeeds;
- live readback confirms task, node, media, tenant and audit consistency;
- local Git commit and release record contain the deployed commit.
