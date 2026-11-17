# Large Industrial Phase 3 Release

Date: 2026-11-17
Commit: `b49f74b8942c5636c0c8169fa03fb27a33558ccb`
Frontend version: `2026.11.17.03`

## Released Scope

- Added the backend-owned media delivery contract:
  `empty`, `object`, `local`, `temporary`, and `missing`.
- Added `deliveryState`, `durable`, and `durabilityRequired` to owner and
  read-only share projections.
- Added media delivery readiness and a no-charge object-mode submission gate
  for image, audio, and video tasks.
- Added a tenant-wide, read-only media durability audit with project grouping,
  `--summary-only`, `--ignore-empty`, and `--require-durable`.
- Hardened the multi-process soak harness with bounded child pools,
  connection sampling, peak thresholds, and HTTP readiness error capture.

## Local Verification

- Backend: 83 modules, 864 passed, 0 failed.
- Agent: 42 passed, 1 skipped because `TEST_POSTGRES_URL` was not configured.
- Frontend: 775 passed; complete and canvas typechecks passed.
- Production build passed.
- Industrial stress passed: 32 concurrent writers, tenant isolation, duplicate
  submission protection, version repair, SSE limits, and large-canvas reads.
- Six-minute preflight passed: 100 tenants, 200/200 tasks succeeded, one submit
  per task, PostgreSQL peak 46/100 connections, 0 monitor errors.

The earlier 24-hour `v3` soak remains rejected because it hit PostgreSQL
connection exhaustion and produced no passing report. A new 24-hour `v4` run
must start only after the accepted six-minute preflight.

## Production Verification

- `drama-canvas-yj.service`, `drama-canvas-worker.service`, and
  `drama-canvas-agent.service` are active.
- `/healthz` and `/readyz` return the released commit.
- `mediaDelivery` is `ok=true` in `auto` mode; local delivery is available and
  object storage is not required.
- Worker fleet readiness reports two active workers and zero active tasks.
- PostgreSQL pool: waiting 0, timeout total 0.
- Public frontend entry: `/assets/index-CSrp2MH8.js`; bundle contains
  `2026.11.17.03` and the delivery-state contract.
- Owner project `CC7nAazDAMK31EW9eJGw5b` exposes 125 media nodes:
  107 `object`, 7 `local`, and 11 `empty`.
- A temporary viewer share exposed 24 `object` media nodes, was read-only, and
  was revoked after verification.
- Real online browser gate passed: 25 media nodes, one history menu, 13.11s
  canvas-pan window, no desktop/mobile overflow or overlap, and zero page or
  console errors.

No paid generation task was submitted during release verification.

## Follow-up Soak

The isolated 24-hour `v4` mock soak was started after the Phase 3 release:

- launcher PID: `7160`
- output: `artifacts/soak-100t-24h-v4`
- database: `drama_canvas_soak_v4_20261117`
- provider mode: deterministic mock only

It is not accepted until its own `report.json` exists with `ok=true`. The
connection monitor and bounded child pool apply to this run.

## Deployment Notes

The first release attempt stopped at bundle checksum validation because the
Windows-generated checksum manifest used CRLF line endings. No runtime or
database mutation occurred, and the old release remained active. The bundle was
regenerated with LF checksums and without `__pycache__`; checksum, schema,
health, readiness, service, commit, active-task, and release-health gates then
passed.

Pre-cutover backup:

```text
/www/wwwroot/yj.pduoduo.vip/app/backups/
  worker-plane-b49f74b8942c5636c0c8169fa03fb27a33558ccb-20261117-092326
```
