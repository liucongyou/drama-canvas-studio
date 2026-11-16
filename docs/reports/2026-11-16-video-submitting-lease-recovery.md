# Video Submitting Lease Recovery

Date: 2026-11-16
Status: Fix verified locally; billed result recovered in production

## Symptom

A video generation task was accepted by the API and displayed as submitting,
then ended with:

```text
生成执行结束但未形成终态，本次未更新画布
```

The task had no provider task ID and no provider request was accepted.

## Root Cause

The durable worker performs this transition:

```text
queue claim -> running -> admission -> submitting
```

The generation heartbeat resumed the task lease with a SQL predicate that only
accepted `status='running'`. Once admission changed the task to `submitting`,
the next heartbeat update affected zero rows and the worker logged a lost
lease. The outer executor then observed a non-terminal task and wrote the
fallback error.

This was a state-machine contract defect, not a provider or model failure.

## Fix

Lease renewal now accepts both active submission states:

```sql
status IN ('running', 'submitting')
```

The lease owner remains mandatory. External task identity, provider task ID,
and terminal status protections remain unchanged.

The same status assumption also existed in:

- the task-scoped update path used to persist provider task IDs;
- the terminal transaction pre-check and UPDATE;
- queue-claim renewal and release.

All of those paths now accept `running` and `submitting`. Terminal updates also
clear the lease in the same transaction.

## Regression Coverage

- Added a real admission-path test covering:
  `claim -> acquire_generation_admission -> submitting -> renew lease`.
- The test verifies the row remains `submitting`, retains the same owner and
  receives a future lease expiry.
- Related backend chain:
  `tests/test_generation_queue_claims.py`,
  `tests/test_generation_worker_process.py`,
  `tests/test_generation_delivery_recovery.py`,
  `test_generation_queue.py`, and `test_account_auth.py`.
- Related backend chain result: `100 passed`.
- Focused queue/lease regression after the final fix: `9 passed`.

## Release Gate

- Backend: `845 passed`, 78/78 modules passed.
- Agent: `43 passed`.
- Frontend: 87 files, `775 passed`.
- TypeScript: passed.
- Production build: passed.
- Migration first/second run: `changedRows=0`.
- 32-way industrial stress: passed.
- Multi-process worker-kill smoke: passed.
- Browser desktop/mobile regression: passed.
- Browser page errors: `0`.
- Browser console errors: `0`.
- Production recovered-video Range check: HTTP `206`, `video/mp4`,
  `Content-Range: bytes 0-1023/2741736`.

## Submission-Path Stress

Mock provider only:

- 20 simulated tenants.
- 80 tasks.
- 2 API processes.
- 4 Worker processes, 4 concurrency each.
- 90-second duration window.
- 80/80 tasks succeeded.
- 0 active leftovers.
- 80 submit calls.
- `maxSubmitsPerTask=1`.
- No real image, audio, or video provider call.

Artifact:

```text
artifacts/submission-flow-stress-20t-90s/report.json
```

## Billed Result Recovery

Production task:

```text
6IrCTfcok2AS9su35p0Q7r
```

Provider task:

```text
cgt-20261116113458-hqemg
```

The durable journal retained the provider task ID after the database callback
failed to persist it. A read-only provider query returned `succeeded`, and the
existing paid MP4 was downloaded without another provider submission.

Recovered node result:

```text
/uploads/generated-1794802989-0fd7b05e4b.mp4
/uploads/generated-1794802989-0fd7b05e4b-cover.jpg
```

The task is now `succeeded / ready`; the node is `done`; the original provider
task ID and both result receipts are stored.

## Automatic Journal Reconcile

Journal restore previously ran only once during startup. The runtime reconciler
now also runs the durable journal restore every 30 seconds, so a task that was
incorrectly closed after provider acceptance can be reattached to the same paid
provider task without a service restart.

## Deployment Rule

This change follows the required order:

```text
full repair -> local all-green -> stress -> Git commit -> single production
release -> live verification
```

No production generation is used to test the fix.
