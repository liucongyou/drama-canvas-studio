# Canvas Write Layer Migration

## Problem

Project payloads are stored as one JSON document in
`canvas_projects.payload_json`. Any node, edge, viewport, or generation result
write serializes the whole document while holding the SQLite single-writer
transaction. Read-side summary queries are now fast, but write P95 still grows
with project size.

Do not hide this with more loading overlays. The storage boundary must remove
full-document writes from interactive and task hot paths.

## Invariants

1. A generation request is submitted at most once. Changing storage must not
   create a retry path for an uncertain paid submission.
2. A provider task id is persisted before the local task is considered live.
3. Node result, terminal task state, and canvas event remain atomic. Splitting
   tables must not reintroduce "node updated but task still running".
4. Historical media versions and old project payloads remain readable. The
   migration is forward-compatible and reversible by keeping the legacy copy.
5. Missing rows are not evidence of absence. During migration, readers merge
   relational rows over the legacy payload and use it as a fallback.

## Target Storage

### `canvas_projects`

Keep low-frequency project metadata:

- identity, owner, title, revision
- created/updated timestamps
- appearance and other small project preferences
- a compact migration/version marker

Do not store the mutable node list as the transaction boundary for interactive
edits.

### `canvas_nodes`

One row per node:

- `project_id`, `node_id`, `episode_id`
- `node_type`, `title`
- `position_json`, `position_updated_at`, `position_version`
- `metadata_json`
- `updated_at`

Use separate statements for position and metadata so a drag cannot overwrite a
newer generation result.

### `canvas_edges`

One row per edge:

- `project_id`, `edge_id`, `from_node_id`, `to_node_id`
- `source`, `updated_at`

Unique by project and normalized endpoint pair. Reject stale endpoints in the
same transaction that deletes a node.

### `canvas_node_versions`

Append-only media history:

- `project_id`, `node_id`, `version_id`
- URL, task id, storage receipt, generated time, validation fields

Ordinary project loads can keep the existing inline versions until the front-end
contract is migrated, but new versions must not require rewriting every node.

### `canvas_episode_state`

One row per project and episode:

- title and node membership
- viewport JSON
- layout JSON, or a dedicated layout table if measurements show it is still hot
- updated time

Viewport writes must update one small row, never a project document.

## Migration Phases

### Phase 1: Shadow tables and dual read

- Create relational tables.
- Backfill from `payload_json` in a bounded startup migration.
- New readers load relational rows first and merge legacy payload data.
- Keep the legacy payload write for this phase, but do not route hot writes
  through it yet.
- Compare row counts and payload hashes in tests and logs.

Exit criteria:

- Fresh and legacy projects produce identical API views.
- Backfill is idempotent.
- Rollback leaves the legacy payload intact.

### Phase 2: Hot writes move

Move these endpoints to row-level transactions:

- node position save
- node metadata save
- connection save
- episode viewport save

Each endpoint updates only its owned rows, returns a row version, and never
serializes all project nodes. Project-level save remains available for import
and explicit version creation.

Exit criteria:

- Project with 2,000 nodes and 1,000 edges shows no full `payload_json` update
  in the hot-write log.
- Drag metadata races are rejected by row version or merged without replacing
  newer fields.
- Client refresh reconstructs the same graph from relational rows.

### Phase 3: Generation terminal commit

Atomically update:

- `canvas_nodes` result fields
- `generation_tasks` terminal state and delivery receipt
- `canvas_events` outbox row

The transaction must remain single-writer, but it should touch a few indexed
rows rather than rewrite the entire canvas. Media history is appended, not
rewritten.

Exit criteria:

- Provider success always produces task success, node result, and event in the
  same commit.
- Restart recovery queries the same provider task and never resubmits.
- Missing local download does not erase a successful provider result.

### Phase 4: Legacy payload retirement

Only after all readers and rollback checks pass:

- Stop updating `payload_json` for hot paths.
- Keep a read-only legacy snapshot for one release.
- Add an export/restore tool before dropping the column.

This phase is intentionally separate. Do not delete the legacy payload in the
same deployment as the first table migration.

## Required Tests

- Backfill idempotency: run twice, compare project views.
- Dual read: relational row changed, legacy payload stale, API returns row.
- Dual read fallback: missing relational node, API returns legacy node.
- Concurrent position and metadata writes: no field overwrite.
- Delete node and edge transaction: no dangling edge.
- Concurrent generation terminals across multiple projects.
- Restart after provider accepted, before local terminal commit.
- Fault injection for download failure and lost lease.

## Performance Gates

Use the same realistic payload distribution as the current benchmark, not a
toy project.

- 12 concurrent writes, 60 requests:
  - current baseline P95 around 5.0s
  - Phase 2 target P95 below 500ms
- 50 projects with large payloads:
  - project list P95 no regression from current read optimization
- 2,000-node project:
  - position save writes at most one node row
  - metadata save writes at most one node row
  - no full payload serialization in the hot path
- Generation terminal:
  - provider result commit touches only node/task/event rows
  - no more than one canonical provider submission per client operation id

Any target that only improves perceived loading while leaving full-document hot
writes is not an acceptable result.

## 2026-10-31 Implementation Status

Correctness work landed:

- isolated projection tables (`canvas_projection_*`) with legacy payload retained
  as a rollback copy
- two-stage reads (`workspace-index`, then episode-scoped `workspace-nodes`)
- progressive canvas shell while the current episode loads
- row-level paths for project viewport, node insert, node edit, and node layout
- provider terminal commits still use a single transaction; no automatic
  resubmission was added
- real owner DB copy comparison passed for all three projects:
  node ids, edge ids, episode viewports, and episode layouts are identical

Measured performance is not yet accepted:

- 12 concurrent writes, 60 node insert requests
- all requests returned `200`, with zero missing nodes
- observed P95 varied from roughly `0.87s` to `2.5s` across repeated runs
- the `500ms` Phase 2 gate is still unmet

The remaining bottleneck is SQLite's single writer plus revision/stat updates
inside the same hot-write transaction. Do not describe the write-layer migration
as complete until repeated runs consistently meet the gate. Moving to an
independent write queue, or splitting revision/stat updates out of the interactive
transaction with a durable outbox, is the next engineering step.
