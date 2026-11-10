# Canvas Agent Overhaul Verification

- Date: 2026-11-10
- Branch: `codex/agent-overhaul`
- Spec: `docs/superpowers/specs/2026-11-10-canvas-agent-overhaul-design.md`
- Plan: `docs/superpowers/plans/2026-11-10-canvas-agent-overhaul.md`
- Version: `2026.11.10.02`

## Result

The Agent is now a same-origin, tenant/account-scoped, project-aware tool runtime
with durable conversations and drafts, structured message rendering, reliable
Chinese input behavior, and confirmed Feed v2 import only.

No test or smoke step triggered paid image, video, or audio generation.

## Automated Verification

| Check | Result |
|---|---|
| Frontend full Vitest suite | 706 passed |
| Agent pytest suite | 22 passed |
| Main backend pytest suite | 750 passed, 208 subtests passed |
| Agent gateway focused tests | 3 passed |
| Frontend typecheck | 0 errors |
| Canvas-only typecheck | 0 errors |
| Production build | passed |

The frontend production build generated:

```text
AgentDock             42.59 kB / gzip 12.97 kB
ChangelogDialog       88.72 kB / gzip 33.82 kB
react-vendor         145.35 kB / gzip 46.74 kB
markdown-vendor      167.48 kB / gzip 50.85 kB
flow-vendor          185.01 kB / gzip 60.46 kB
main index           582.49 kB / gzip 173.42 kB
```

## Browser Smoke

Environment:

- Main service: isolated local process on `8890`
- Agent sidecar: isolated local process on `8895`
- Frontend: Vite on `8891`
- Model: mock only
- Database: isolated `agent/data/smoke-agent.db`

Verified:

- Same-origin login and Agent gateway.
- Signed main-service to Agent context.
- Signed Agent to internal canvas context.
- Agent skill selection and lock gate.
- 10,000-character Chinese input.
- Draft recovery after full page reload.
- Real append-message request and unique run creation.
- Structured message content.
- Project context tools ran twice: `get_project_index`, `list_assets`.
- Feed validation errors shown as structured, actionable content.
- No console or page errors.
- No import confirmation was clicked.

Latest measured values:

```text
Warm Agent panel open:      14.7 ms
10,000-char input:          629.4 ms end-to-end
Max single long task:       50 ms
Structured content:         yes
Tool trace:                 yes
Browser errors:             0
```

The end-to-end input timing includes server draft debounce and validation work.
The maximum long task is at the 50ms acceptance boundary, not above it.

## Canvas Baseline

`bench-canvas.mjs` was run against the current local large project:

```text
Project nodes:       26
Connections:         52
DOM nodes:           3029
JS heap:             24.3 MB
Transferred media:   44.96 MB
First node:          11,761 ms
Idle average frame:  36.52 ms
```

This local dev run is not a clean cache/GPU baseline. Several original media
assets were multi-megabyte local/TOS files and the headless browser was running
with GPU disabled. The Agent panel itself was separately measured at 14.7ms
warm open with no long task above 50ms from the 10,000-character input test.

## Security and Isolation

- Browser no longer calls port 8895 directly.
- `/api/agent/*` is proxied from the authenticated main service.
- Agent requests to internal canvas APIs use HMAC-SHA256 signatures with a
  60-second replay window.
- Conversation, draft, run, tool-call and event reads are scoped by tenant and
  account.
- Read-only share pages hide AgentDock and reject Agent API access.
- Markdown raw HTML is disabled.
- `assetId` and `seriesId + variantKey` remain the authoritative asset identity.
- Connected card assets are not automatically merged into `params.refs`.

## Remaining Environmental Notes

- The existing local canvas project contains missing/slow media resources; the
  agent smoke filters those unrelated resource console messages but does not
  treat them as Agent failures.
- The main index chunk still exceeds Vite's 500KB advisory threshold. This
  change does not add Agent code to the main bundle; AgentDock and Markdown are
  independently loaded.
