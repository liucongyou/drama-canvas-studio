# Large Industrial Phase 2: Multi-Instance Cost Governance

Date: 2026-11-17
Status: Implemented locally, pending release acceptance

## Objective

Close the multi-API budget race in production. A tenant generation budget must
remain correct when several API processes receive submissions at the same time.
The implementation must never rely on a process-local lock for correctness.

## Invariants

- Budget check and task reservation are serialized per tenant across processes.
- A provider submission cannot exceed the configured daily, monthly or
  per-request budget because another API process checked concurrently.
- Local validation failures and explicit queue cancellations do not consume
  billable budget.
- Unknown post-submit outcomes remain conservative reservations because the
  provider may have accepted billable work.
- Every blocked submission records operation ID, entry, batch, session, IP, UA,
  node and an estimate without storing prompt text or secrets.
- The front end displays the backend-provided committed and reserved totals; it
  does not recalculate budget state.

## Design

PostgreSQL session advisory locks are keyed by tenant identity and held only
across the budget check plus durable task insert. The lock connection is always
closed in `finally`, and a connection failure releases all session locks.
SQLite remains available only for isolated tests and uses a process-local guard.

The cost snapshot classifies persisted tasks as:

- `committed`: succeeded, or any task with a provider task ID;
- `reserved`: queued/running, or an uncertain post-submit outcome without a
  provider ID;
- `released`: known pre-submit validation failures and queue cancellation.

## Acceptance

- 24 concurrent processes compete for an 8-unit synthetic budget.
- Exactly 8 tasks are created and the persisted amount is 8.
- No process error occurs.
- Targeted unit, PostgreSQL, backend, frontend and build gates pass.
- Production has no active generation tasks during cutover.
- Production deployment and rollback are verified without a paid provider call.

## Non-Goals

- Estimating provider invoice reconciliation beyond persisted task evidence.
- Rewriting the provider price catalog.
- Introducing Redis, Celery or another queue.
- Replacing the current frontend framework.
