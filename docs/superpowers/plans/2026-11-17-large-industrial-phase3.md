# Large Industrial Phase 3 Implementation Plan

**Goal:** Establish an explicit durable media delivery contract and safe
multi-host storage gate without changing existing media playback semantics.

## Task 1: Delivery State Contract

**Status:** Complete locally

- Add `media_delivery.py`.
- Classify node/version media as `empty`, `object`, `local`, `temporary` or
  `missing`.
- Project backend-owned state onto owner and share API responses.

## Task 2: Readiness Gate

**Status:** Complete locally

- Add media delivery readiness to `/readyz`.
- Probe local upload writes.
- In `object` mode require enabled, ready and verified object storage.

## Task 3: Paid-Submission Gate

**Status:** Complete locally

- Reject image/audio/video submission before task creation when object storage
  is required but unavailable.
- Return `MEDIA_DELIVERY_UNAVAILABLE` with a no-charge explanation.

## Task 4: Durable Media Audit

**Status:** Complete locally

- Add `scripts/audit_media_delivery.py`.
- Scan nodes and versions without network calls.
- Ignore never-generated empty placeholders as failures.
- Group non-durable findings by project.
- Support `--summary-only`, `--ignore-empty`, and `--require-durable`.

## Task 5: Verification and Release

**Status:** Local verification complete; release pending

- Run media delivery, storage, readiness, backend, frontend and build gates.
- The corrected 100-tenant, 200-task preflight passed with 200/200 succeeded,
  one submit per task, readiness green, and a peak of 46/100 PostgreSQL
  connections.
- Commit and deploy in one release window.
- Verify production remains in `auto` mode unless storage is explicitly
  validated for object-only operation.

## Remaining Phase 1 Acceptance

The 24-hour `v3` soak was stopped after the isolated local PostgreSQL cluster
reached `too many clients already`. It did not produce a passing report and is
not accepted. The harness now applies a bounded child pool budget; a six-minute
preflight must pass before starting `v4`.
