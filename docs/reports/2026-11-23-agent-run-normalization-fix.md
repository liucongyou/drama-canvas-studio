# Agent Run Normalization White-Screen Fix

Date: 2026-11-23
Status: Deployed and verified online

## Reported Failure

Opening `?ep=EP015#CC7nAazDAMK31EW9eJGw5b` could replace the whole page with
the root error boundary:

```text
Cannot read properties of undefined (reading 'id')
```

## Root Cause

The frontend telemetry record contained the full stack and identified the
minified `AgentDock` chunk.

An Agent history response could transiently contain an incomplete run record.
That record was stored in `threadRuns`, and a later update executed:

```text
current.map((item) => item.id === ...)
```

Because one item was `undefined`, the entire React root crashed. The same
unsafe assumption existed in task creation, history switching, pending import
and conversation restoration paths.

## Fix

- Added one response normalization boundary for all Agent run records.
- A run is accepted only when it has a non-empty unique `id`.
- Missing message, stage, workflow-step, artifact and tool-call arrays are
  normalized to empty arrays.
- Duplicate, null and malformed records are discarded.
- All `setRun`, `setRuns`, `setThreadRuns` and `setProjectPendingRuns` paths now
  use the normalized value.
- Existing malformed frontend state is cleaned automatically on the next
  upsert.

## Verification

- Full frontend gate: `92` files, `789` tests passed.
- Production build: passed.
- New unit tests cover:
  - null and undefined runs;
  - missing IDs;
  - duplicate IDs;
  - malformed existing state;
  - default empty run collections.
- Online browser regression on the exact EP015 URL intercepted
  `/api/agent/runs` and returned null, missing-ID and valid run records:
  - no root error boundary;
  - React Flow rendered;
  - Agent panel rendered;
  - no `reading 'id'` page or console error.

## Production

- Release commit: `767b67d`
- Backup:
  `/www/wwwroot/yj.pduoduo.vip/app/backups/recipe-guidance-frontend-767b67d-20261123-204442`
- Online JS asset: `assets/index-CTfzgCPu.js`
- Frontend-only deployment; API, Agent and Worker services were not restarted.
- `/readyz`: healthy, `activeTasks: 0`.
- No paid image, audio or video generation was submitted.
