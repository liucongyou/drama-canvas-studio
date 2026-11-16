# Video Submitting Lease Recovery

Date: 2026-11-16
Status: Fix verified locally; production deployment after the release gate

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

## Deployment Rule

This change follows the required order:

```text
full repair -> local all-green -> stress -> Git commit -> single production
release -> live verification
```

No production generation is used to test the fix.
