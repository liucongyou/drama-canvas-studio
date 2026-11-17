# Large Industrial Phase 3: Durable Media Plane

Date: 2026-11-17
Status: Implemented locally, release pending

## Objective

Make media durability an explicit backend contract for API/Worker deployments.
When API and Worker do not share an upload volume, `object` mode must fail
closed before a paid media generation request when verified object storage is
not available. Every node and media version exposes one authoritative delivery
state.

## Delivery States

- `empty`: an imported or never-generated placeholder with no media
  expectation; excluded from durable-media failure counts.
- `object`: a durable object-store descriptor with provider and key exists.
- `local`: local bytes are the only durable copy. Valid in `auto/local` modes.
- `temporary`: only a provider URL is currently available.
- `missing`: no playable source can be classified.

The backend sets `deliveryState`, `durable`, and `durabilityRequired` on media
nodes and versions. The front end maps these fields but does not infer them.

## Modes

```text
CANVAS_MEDIA_DELIVERY_MODE=auto|local|object
```

- `auto`: object storage or verified local bytes are accepted.
- `local`: intended only when API and Worker share one upload volume.
- `object`: object storage must be enabled, ready and previously verified before
  image/audio/video task submission.

## Readiness

`/readyz` includes a `mediaDelivery` check:

- local upload directory probe;
- active storage configuration readiness;
- storage test verification;
- whether object storage is mandatory for the current topology.

In `object` mode, readiness fails when storage is missing, disabled, incomplete
or unverified. A blocked generation returns `MEDIA_DELIVERY_UNAVAILABLE`
before task creation, so no provider charge is incurred.

## Audit

`scripts/audit_media_delivery.py` scans all tenants without network or paid
provider calls and reports node/version counts for each delivery state. It
groups real non-durable findings by project, never treats empty placeholders as
failures, and supports `--summary-only`, `--ignore-empty`, and
`--require-durable` for release gates.

## Acceptance

- Unit tests cover all five delivery states.
- Object mode rejects unverified storage before submission.
- `/readyz` fails closed in object mode without a verified storage plane.
- Existing media URL, Range, history and recovery contracts remain unchanged.
- Production remains in `auto` until object storage and shared-media topology
  are explicitly validated.
