# Canvas Projection Production Verification

Date: 2026-10-31
Release: `20b0e93`
Backup: `/www/wwwroot/yj.pduoduo.vip/app/backups/20b0e93-20261031-132752`

## Deployment

- backend files:
  - `canvas_repository.py`
  - `canvas_store.py`
  - `server.py`
- frontend entry: `assets/index-C0Wt5Pdj.js`
- service: `drama-canvas-yj.service`
- final state: `active`
- web bundle: HTTP `200`
- API session: HTTP `200`
- owner database quick check: `ok`

The deployment script backed up the previous backend files, frontend bundle,
and all tenant databases before switching. Failed readiness checks
automatically restored the prior release.

## Projection Verification

All projects in tenant databases with project tables were compared against the
retained payload copy:

- node ids: equal
- edge ids: equal
- episode viewports: equal
- episode layouts: equal

Verified projects:

- owner: 4 projects, 49 / 22 / 23 / 22 nodes
- `u-a365879766`: 14 nodes
- `u-liuchenyou`: 1 node
- `u-aa520520`: empty project
- `u-zyh2018114`: empty project

The `alpha` tenant has no project table and is not a project data divergence.

## Runtime Evidence

Production logs show the frontend calling:

- `/api/projects/{id}/workspace-index`
- `/api/projects/{id}/workspace-nodes?episodeId=...`
- `/api/generation-tasks?...&episodeId=...`

All returned successfully during the verification window. The login screen was
shown in the browser used for deployment because that browser did not have a
valid session; the production API still returned authenticated session `200`.

## Remaining Risk

The write-layer performance gate is still not met. Repeated 12-concurrency,
60-write runs produced P95 values from about 0.87s to 2.5s. All requests
succeeded and no nodes were lost, but SQLite single-writer contention remains.
Further work must be a durable write queue or a split between interactive row
writes and project-wide revision/stat aggregation. Do not describe the write
performance work as complete.
