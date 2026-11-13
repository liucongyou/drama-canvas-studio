# System Audit v4 · Batch Closure and Data Integrity

- Date: 2026-11-14
- Scope: batch generation, delivery receipts, duplicate media versions,
  missing-media recoverability, static security headers and production readback
- Mode: production read-only audit, isolated regression fixtures, no paid generation

## Fixed

### 1. Batch generation no longer fails as an opaque loop

The previous dialog awaited every node in one loop. A rejected or failed node
stopped the loop without showing which items had already reached the provider,
and the button could be pressed again while work was in flight.

Changes:

- shows per-item progress and accepted count;
- blocks duplicate submission while the batch is running;
- provides `停止剩余` rather than pretending already-submitted work was cancelled;
- records accepted node IDs so retry processes only the remaining items;
- surfaces the actual submission exception and keeps the dialog open for retry;
- uses `newCanvasEntityId('batch')` instead of an unguarded `crypto.randomUUID()`.

No provider cancellation endpoint is assumed. `停止剩余` stops only items that
have not yet been submitted.

### 2. Succeeded delivery evidence

Before this pass, 15 succeeded legacy tasks still had `delivery_status=pending`.
The migration now closes a task only when:

- the referenced node still exists;
- a real local media file exists and is non-empty;
- if several succeeded tasks target the same node, the task has a provider task
  ID that identifies the exact version.

Production result:

- evidenced pending receipts closed: 6;
- succeeded pending receipts: 15 -> 9;
- no task was guessed ready from status alone.

The remaining 9 point to deleted local files and have no provider-result URL in
the node or task ledger, so they cannot be reconstructed safely.

### 3. Duplicate version ID selection

Nine production nodes still contain duplicate version IDs. Removing or rewriting
those IDs would break historical selection links, so no destructive migration
was performed.

The selection path now:

- honors an explicit URL hint when one is supplied;
- otherwise prefers a version with a real source type over an incidental mirror;
- then prefers local bytes or a storage receipt over unknown provider URLs;
- guarantees only the chosen duplicate carries `active=true`.

Frontend duplicate handling remains `(versionId,url)` plus current-content URL
disambiguation.

### 4. Static security headers

API responses already had the intended security policy, but the static site
header omitted the same protection.

`yj.pduoduo.vip` now returns:

- `Strict-Transport-Security`
- `Content-Security-Policy`
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer`
- `Permissions-Policy`

Nginx config test and reload succeeded. A rollback copy was retained under
`/root/yj-nginx-security-20261114-074412.conf`.

## Missing Media Audit

Production has 9 media nodes whose `content` points to a deleted local upload.
For every one:

- no provider-result URL exists on the node;
- no provider-result URL exists in the recent task ledger;
- no object-store receipt exists.

These are unrecoverable without regeneration or re-import. The application keeps
them in an explicit missing-media state rather than requesting dead URLs.

## Remaining Risks

- 17 genuinely uncertain provider submissions remain. They have no provider task
  ID and require provider billing/task verification or an operator decision.
- 9 succeeded tasks remain receipt-pending because their local files are gone.
- 9 duplicate version ID nodes remain as data debt, but selection is deterministic.
- 32 image nodes have no media version and are placeholders, not failed thumbnails.
- `CanvasView.tsx` remains a large component; no risky structural split was mixed
  into this integrity release.

## Verification

- Backend full suite: 814 passed, 6 skipped, 211 subtests passed.
- Frontend full suite: 754 passed, 82 test files.
- TypeScript: 0 errors.
- Production build: passed.
- Static site security headers: verified by live HEAD response.
- Online browser: 640x360 image thumbnail loaded, 0 page errors, 0 console errors.
- Main and Agent services: active.
- No paid generation was submitted.
