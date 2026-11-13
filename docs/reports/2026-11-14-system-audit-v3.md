# System Audit v3 · Stability, Media and Data Hygiene

- Date: 2026-11-14
- Scope: production PostgreSQL, generation task ledger, canvas projections,
  media references, image assets, snapshots, frontend media loading and release gates
- Mode: read-only production audit, isolated regression fixtures, no paid generation

## Executive Summary

This pass focused on defects that can silently cost money, hide valid media,
pollute operational queues or make canvas interaction unreliable.

Implemented in release `2026.11.14.01`:

1. Image cards now use persisted 640px thumbnails while previews remain unchanged.
2. Video story-order links can no longer become the next segment's reference video.
3. Removed reference slots are explicitly tracked and cannot be resurrected by links.
4. Duplicate-submit records no longer masquerade as uncertain paid tasks.
5. Automatic snapshots are capped at 12 per project; manual history is preserved.

## Production Baseline

- PostgreSQL tenant schemas audited: 5
- `owner` projects: 13
- `owner` nodes: 593
- media nodes: 309 video, 178 image, 76 audio, 30 text
- tasks before remediation: 468 succeeded, 116 failed, 39 interrupted
- active generation tasks: 0
- stale active tasks over 2 hours: 0
- maximum project payload: 3,213,955 bytes
- owner uploads: 1.4 GB
- PostgreSQL logs after log-policy remediation: 176 KB
- systemd journal: 992.1 MB (1 GB configured cap)
- `/www`: 30 GB available

## Fixed

### 1. Image thumbnail pipeline

Before this release, 146 active image nodes had no durable thumbnail. Cards and
history could decode multi-megabyte originals.

Changes:

- New image generation records a 640px JPEG thumbnail per local image version.
- Local image uploads create the same receipt.
- Startup background backfill repairs older image versions without blocking API startup.
- Image cards use the thumbnail; lightbox, download and provider references keep the original.
- A failed thumbnail request falls back to the original image through the existing retry path.

Production result:

- 143 historical image thumbnails generated.
- All active images with readable local source bytes now have thumbnails.
- 41 image nodes remain without a thumbnail by definition:
  - 32 have no media version and are empty/placeholder nodes;
  - 9 reference local source files that no longer exist and cannot be reconstructed
    without regeneration or re-import.

### 2. Video-link reference boundary

The root cause of the segment 6 audio appearing in segment 7 was an adjacent
video link being sent as a provider reference.

The provider request contract now:

- still carries linked image/text background assets;
- never treats an adjacent video link as a video reference unless explicitly selected;
- honors explicit `removedRefNodeIds` so a removed reference cannot be restored by a stale link;
- keeps `@` mentions and stable asset identities as the action-reference authority.

### 3. False uncertain tasks

Before remediation, 40 tasks were counted as uncertain.

23 were duplicate-submit rejections that never obtained a provider task id and
never created a billable provider request. They are now terminal ignored records.

Production result:

- uncertain queue: 40 -> 17;
- 16 remain `interrupted`;
- 1 additional failed task still carries an uncertain phase;
- no automatic retry or resubmission was performed.

The remaining 17 require provider-side verification or an explicit operator
decision. They were not guessed failed because a provider may have accepted them.

### 4. Snapshot retention

Automatic snapshots had reached 360 rows and about 239 MB while retaining up to
30 copies per project.

Changes:

- automatic retention reduced to 12 per project;
- existing excess auto snapshots are pruned at startup;
- manually pinned snapshots are excluded from pruning.

Production result:

- snapshots: 360 -> 146 rows;
- snapshot payload: about 239 MB -> 96.5 MB;
- maximum automatic snapshots per project: 12.

## Remaining Risks

These are real findings but were not changed without stronger evidence or a
safe migration path.

### P1

- 15 succeeded historical tasks still have `delivery_status=pending`. Some lack
  both `local_result_url` and `node_written_at`; automatically marking them ready
  could hide an undelivered provider result.
- 17 genuinely uncertain task records remain. They are old and no provider task
  id is stored, but automatic resolution could be wrong if a provider accepted the request.
- 9 nodes contain duplicate `versionId` values. Current frontend resolution uses
  `(versionId,url)` plus content URL disambiguation, so valid local copies remain
  addressable; no destructive ID rewrite was performed.

### P2

- 9 image nodes point to deleted local files. They remain explicit missing-media
  cases and require regeneration or re-import.
- `CanvasView.tsx` is still a large monolith. This pass avoided a risky structural
  split and limited changes to existing media/reference boundaries.
- The largest project payload remains approximately 3.2 MB; compact workspace
  reads already avoid loading it during routine generation polling.

## Verification

- Backend full suite: 810 passed, 6 skipped, 211 subtests passed.
- Frontend full suite: 751 passed across 81 test files.
- TypeScript: 0 errors.
- Production frontend build: passed.
- Python compile checks: passed.
- `git diff --check`: passed.
- Production services: `drama-canvas-yj.service` and
  `drama-canvas-agent.service` both active.
- Online browser check confirmed an image card requested a 640x360 `-thumb.jpg`
  asset, not the original image, with 0 page or console errors.
- No paid image, audio or video generation was submitted.
