# Audit v2 Remediation · 2026-11-10

Source audit: `2026-11-09-system-audit-v2.md`.

## Completed

### NEW-01 Duplicate version IDs

- Frontend history keeps versions by `(versionId, url)`.
- Active-version resolution uses the current content URL to disambiguate
  duplicate IDs.
- The selection request carries `versionUrl`; the server uses it only to choose
  between rows that already share the requested stable version ID.
- If no URL hint exists, the server prefers an available durable local/stored
  copy over an unknown provider URL.

### NEW-03 Unknown versions

- Only `mediaAvailability === "available"` versions can be selected.
- `unknown` versions are disabled and labeled `待确认`.
- Arrow navigation and the history popover use the same availability rule.

### NEW-04 Version-switch payload rewrite

- `/select-version` updates the projection node row and project revision.
- It no longer serializes or rewrites the complete `payload_json`.
- Failure/local-blocked counters are updated by metadata delta instead of
  rescanning every node.

### NEW-05 Version-selection queue

- Selection coordination is per node, not one global FIFO.
- Rapid clicks on one card still coalesce to the newest intent.
- Different cards can issue selections concurrently.

### AUD-01r Cross-episode prompt/parameter edits

- Rechecked current code and tests. Shared assets now save editable content
  without changing their original episode ownership.

## Verification

- Backend full suite: `747 passed, 208 subtests passed`.
- Frontend full suite: `702 passed`.
- Canvas TypeScript check: passed.
- Production build: passed.
- `git diff --check` and Python compile checks: passed.

The repository-wide `npm run typecheck` still reports an existing error in
`web-vimax/src/agent/AgentDock.tsx`; that directory is a separate development
line and was not modified here.

## Performance

Synthetic 180-node project, 12 repeated single-node edits:

- full-payload update: P50 `100.7 ms`, max `548.1 ms`
- projection-only update: P50 `52.8 ms`, max `64.5 ms`

This removes payload-size-dependent serialization from the version-switch hot
path.
