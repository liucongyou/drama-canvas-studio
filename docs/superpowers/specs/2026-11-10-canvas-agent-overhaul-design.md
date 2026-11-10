# Canvas Agent Overhaul Design

- Date: 2026-11-10
- Status: Approved
- Scope: Agent runtime, same-origin access, canvas tools, project-aware conversation, AgentDock UI, Feed validation and confirmed import
- Source of truth: this repository only

## 1. Context

The current Agent implementation is a sidecar service with a working model
configuration path, skill packaging, Feed normalization, import preview, and
SSE event stream. It is not yet a true project Agent:

- A user message starts one isolated run that calls the model once and asks for
  a complete Feed in one response.
- There is no message append API or continuous tool loop.
- Canvas access is limited to reading a complete project, previewing an import,
  and committing an import.
- The browser connects directly to port 8895 with `credentials: 'omit'`, so the
  production login session, tenant boundary, and project permissions are not
  reliably carried into the Agent.
- `CanvasClient` uses a fixed cookie or token from `agent/.env`, not the current
  user session.
- Message rendering uses one opaque string and filters long text or JSON out of
  the visible conversation.
- The input composer is local-only. A refresh or failed request can lose the
  draft, attachments, references, and context.
- The current runtime, messages, stages, and artifacts are not tenant- or
  account-scoped.

There is no separate DeepSeek Harness maintenance line. Existing Agent and
Harness work in this repository is product code and is included in this design.

## 2. Goals

1. Turn the Agent into a continuous, project-aware creative assistant.
2. Let it inspect project metadata, episodes, nodes, dependencies, assets, and
   skill sections through typed tools.
3. Preserve user input, attachments, references, locks, and useful messages
   across reloads, disconnects, and failures.
4. Produce only `drama-canvas-feed/v2` and use the existing preview, revision,
   snapshot, and import path.
5. Use `assetId` as the authoritative asset identity, with
   `seriesId + variantKey` for cross-episode variants.
6. isolate runs by tenant, account, and project.
7. Provide a professional AgentDock UI with readable messages, traceable tools,
   clear human gates, and reliable recovery.
8. Prove correctness with contract, security, concurrency, performance, browser,
   build, deployment, and online smoke tests.

## 3. Non-Goals

- The Agent will not directly mutate canvas nodes, connections, viewport, or
  media history.
- The Agent will not create a second canvas JSON format.
- The Agent will not auto-import without user confirmation.
- The Agent will not trigger paid image, video, or audio generation.
- The Agent will not infer asset identity from display names, thumbnails, or
  media URLs.
- The Agent will not remove user-confirmed segments or characters to make a
  validation error disappear.
- This design does not refactor unrelated canvas editing, media playback,
  sharing, provider, or generation code.

## 4. Architecture

### 4.1 Process topology

The Agent remains a sidecar process for fault isolation, but it is not a
separate product or deployment line.

```text
Browser
  -> same-origin /api/agent/*
  -> main canvas service: authentication, tenant, project permission, proxy
  -> Agent sidecar on loopback/internal network
  -> signed internal canvas APIs
  -> existing canvas store, Feed contract, preview and import services
```

The Agent sidecar is not exposed directly to public browsers. Its public health
endpoint may remain available only on the private network.

### 4.2 Browser channel

- Browser calls the main service origin, not `<hostname>:8895`.
- Existing session cookies are validated by the main service.
- The main service checks that the account can access the requested project.
- The proxy forwards request bodies, cancellation, and SSE without changing
  application semantics.
- User-supplied internal headers are stripped.

### 4.3 Internal service channel

Agent-to-canvas calls use dedicated internal endpoints:

- `GET /api/internal/agent/projects/{project_id}/index`
- `GET /api/internal/agent/projects/{project_id}/episodes`
- `GET /api/internal/agent/projects/{project_id}/nodes`
- `GET /api/internal/agent/projects/{project_id}/nodes/{node_id}`
- `GET /api/internal/agent/projects/{project_id}/assets`
- `GET /api/internal/agent/projects/{project_id}/assets/{asset_id}`
- `POST /api/internal/agent/projects/{project_id}/feed/preview`
- `POST /api/internal/agent/projects/{project_id}/feed/import`

Requests carry:

- `X-Agent-Service`
- `X-Agent-Timestamp`
- `X-Agent-Signature`
- `X-Agent-Tenant-Id`
- `X-Agent-Account-Id`

The signature is HMAC-SHA256 over method, path, timestamp, tenant, account, and
body hash. Requests outside a 60-second clock window are rejected. The internal
channel is loopback-only unless a trusted private gateway is configured.

### 4.4 Components

1. `AgentGateway`
   Same-origin proxy, session verification, project authorization, request
   limits, cancellation propagation, and SSE pass-through.

2. `AgentRuntime`
   Conversation state, run state machine, tool loop, checkpoints, retry,
   cancellation, and recovery.

3. `ToolRegistry`
   Typed tool definitions, permission checks, argument validation, timeouts,
   result limits, and audit records.

4. `CanvasContextService`
   Project index and on-demand canvas reads. It never sends a complete large
   project payload to the model by default.

5. `FeedService`
   Deterministic normalization, existing contract checks, preview, import
   confirmation, revision conflict handling, and idempotency.

6. `AgentStore`
   Conversations, messages, drafts, runs, tool calls, artifacts, checkpoints,
   and events. Every row is tenant- and account-scoped.

7. `AgentDock`
   Conversation UI, structured message renderer, composer, tool trace, human
   gates, artifact review, and import review.

## 5. Security and Tenancy

### 5.1 Identity

Every Agent record includes:

```text
tenant_id
account_id
project_id where applicable
```

No run, conversation, message, draft, artifact, event, or tool result may be
queried without all required scope values.

### 5.2 Authorization

- The main service authenticates the browser session before proxying.
- The Agent cannot choose a tenant or account from client input.
- Internal canvas calls use the signed context established by the gateway.
- Project reads and imports are checked against the current account's project
  access on every request.
- Read-only share mode receives `403` for Agent APIs and Agent UI is hidden.
- Admin access is not silently inherited by an ordinary Agent run.

### 5.3 Data protection

- Provider secrets remain in the existing tenant configuration and are never
  returned to the browser or stored in Agent messages.
- Tool results redact authorization headers, cookies, API keys, signed media
  URLs, and local filesystem paths.
- Message Markdown disables raw HTML.
- Links are restricted to safe protocols.
- Skill archives are checked for path traversal, absolute paths, symlinks,
  executable entries, duplicate normalized paths, total uncompressed size, and
  file count.
- Uploaded reference files are scoped to the account and tenant.

### 5.4 Rate and resource limits

- One active run per conversation.
- One active Agent run per account/project by default.
- Maximum 12 tool loop steps per run.
- Per-tool timeout and result-size limits.
- Conversation and attachment quotas are configurable.
- SSE connections per account are limited.
- Rate-limit and quota failures use stable error codes.

## 6. Data Model

The existing SQLite store remains the MVP storage engine, but the schema must be
portable to PostgreSQL. Existing tables are migrated forward; no destructive
table replacement is allowed.

### 6.1 conversations

```text
id
tenant_id
account_id
project_id
title
status
active_run_id
created_at
updated_at
```

### 6.2 messages

```text
id
conversation_id
run_id
role: user | assistant | tool | system | status
content_json
content_text
sequence_no
created_at
```

`content_text` remains for backward compatibility and search.

### 6.3 message content blocks

Allowed block types:

```json
{"type": "text", "text": "..."}
{"type": "markdown", "text": "..."}
{"type": "json", "data": {}}
{"type": "tool", "name": "search_assets", "summary": "...", "status": "done"}
{"type": "artifact", "artifactId": "..."}
{"type": "notice", "level": "info|warning|error", "code": "...", "message": "..."}
```

Unknown block types are displayed as a safe generic notice rather than injected
as HTML.

### 6.4 drafts

```text
tenant_id
account_id
conversation_id
text
attachment_ids_json
reference_json
skill_id
route_id
model_id
locks_json
updated_at
```

The browser writes local storage immediately and syncs the server draft after a
500ms debounce.

### 6.5 runs

```text
id
conversation_id
tenant_id
account_id
project_id
trigger_message_id
skill_id
skill_version
model_id
route_id
locks_json
status
current_stage
cancel_requested
error_code
error_message
idempotency_key
started_at
created_at
updated_at
finished_at
```

### 6.6 tool_calls

```text
id
run_id
tool_name
args_json
status
result_json
result_bytes
error_code
idempotency_key
started_at
finished_at
```

### 6.7 artifacts

Existing artifacts gain:

```text
conversation_id
tenant_id
account_id
version
kind
sha256
payload_json
metadata_json
created_at
```

### 6.8 events

Events remain append-only and gain a monotonic per-run event ID. SSE reconnect
uses the standard `Last-Event-ID` header.

## 7. Runtime State Machine

```text
queued
  -> running
  -> waiting_locks
  -> running
  -> waiting_review
  -> importing
  -> completed

Any active state -> failed
Any non-terminal state -> cancelled
Process restart while running -> interrupted
```

Rules:

- A user message creates a run with a unique idempotency key.
- Double submission reuses the same run while the first request is active.
- `waiting_locks` records the requested lock schema and the user's answers.
- `waiting_review` means a validated candidate Feed exists but has not imported.
- `importing` is an idempotent commit state.
- `interrupted` is recoverable from the last completed checkpoint.
- Cancellation is cooperative and checked before every model call, tool call,
  repair round, and import.
- A run that reaches `completed`, `failed`, or `cancelled` is immutable except
  for audit metadata.

## 8. Agent Loop and Tools

### 8.1 Model call contract

The runtime supports native OpenAI-compatible tool calling. If a selected model
does not support native tools, it uses a structured action JSON fallback:

```json
{
  "action": "tool",
  "tool": "search_assets",
  "arguments": {}
}
```

or:

```json
{
  "action": "final",
  "content": []
}
```

The runtime, not the model, decides whether a tool is allowed and validates all
arguments.

### 8.2 Tool definitions

1. `get_project_index`
   Project title, revision, episode count, node counts, media counts, and last
   update. No complete node payload.

2. `get_episode_context`
   Episode title, node membership, video segment count, total duration, task
   states, and warnings.

3. `search_nodes`
   Filters by episode, node type, status, title text, and asset identity.
   Paginated and hard-capped.

4. `get_node_detail`
   One node's prompt, parameters, asset identity, references, dependency
   summary, task state, and version summary.

5. `list_assets`
   Asset ledger with `assetId`, `seriesId`, `variantKey`, kind, display name,
   episode membership, availability, and current node.

6. `get_asset_detail`
   Prompt, current and historical versions, availability, references, and
   cross-episode reuse metadata.

7. `get_node_dependencies`
   Upstream and downstream node summaries. This is observational; it does not
   automatically add generation references.

8. `read_skill_section`
   Reads a bounded section from the fixed skill version selected for the run.
   Arbitrary paths are rejected.

9. `resolve_prompt_references`
   Resolves explicit `@` references to structured asset identities and reports
   missing or ambiguous references.

10. `validate_feed`
    Calls the existing Feed preview service and returns normalized issues.

11. `finalize_feed`
    Records the final candidate artifact and its SHA-256. It does not import.

### 8.3 Tool output limits

- One tool result defaults to 32KB and may be reduced by the tool definition.
- Lists are paginated.
- Large prompts are summarized with offsets and a second detail call.
- Large project payloads are never accepted as one tool result.
- Every truncation is explicit and includes a continuation cursor.

## 9. Canvas Asset and Reference Contract

### 9.1 Identity

`assetId` is the authoritative stable asset identity.

`seriesId + variantKey` identifies a cross-episode asset family and variant.

`nodeId` identifies one node in the current project and is not a stable asset
identity.

Display names, thumbnail URLs, and media URLs are never used as identity keys.

### 9.2 Explicit `@` references

An explicit `@` selection produces:

```json
{
  "nodeId": "current node position",
  "assetId": "character:陈大强:default",
  "seriesId": "character:陈大强",
  "variantKey": "default",
  "displayName": "角色｜陈大强（图2）"
}
```

The final provider-facing prompt may still use
`@角色｜陈大强（图2）`, because that is the canvas prompt syntax. The identity
behind the syntax remains `assetId`.

Opening a media card or reading its connected edges must not automatically add
all connected assets to the Agent's current reference set. Only explicit user
mentions or user-confirmed suggestions enter `params.refs`.

## 10. Feed Validation and Import

### 10.1 Feed pipeline

```text
model draft
  -> deterministic normalization
  -> schema validation
  -> semantic validation
  -> existing build_payload
  -> final node/reference/edge validation
  -> preview artifact
  -> user confirmation
  -> existing revision-guarded import
```

The only accepted canvas format is `drama-canvas-feed/v2`.

### 10.2 Validation issues

Every issue includes:

```text
code
message
path
segment_no when applicable
role when applicable
asset_id when applicable
node_code when applicable
repairable
```

The Agent may use at most two repair rounds. A repair may not:

- remove a confirmed segment
- remove a confirmed speaking role
- change the selected skill version
- alter locked creative parameters
- bypass a schema or revision error

If repair still fails, the Feed remains a draft and cannot be imported.

### 10.3 Import

- Preview returns the current `revision`, diff summary, asset reuse summary,
  warnings, and candidate artifact SHA-256.
- Import requires `if_revision`.
- Revision conflict triggers a new preview and explicit user confirmation.
- Repeated import of the same `runId + artifactVersion` returns the original
  idempotent result.
- The existing import service remains the only writer.
- Import creates the normal project snapshot.
- A successful import reports added, updated, reused node and connection counts.
- Re-import does not delete shared assets that are still used by the project.
- The Agent never triggers image, video, or audio generation.

## 11. AgentDock UI

### 11.1 Visual direction

AgentDock is a dense professional creation workspace. It uses canvas theme
tokens and does not create a second visual system.

- Default width: 520px.
- Resizable range: 380px to 760px.
- Mobile: full-screen drawer.
- Header, conversation, composer.
- Maximum corner radius for content regions: 10px.
- Active run uses the theme accent and a sky-blue running signal.
- Status always uses text plus color.
- Icon-only controls use Lucide icons, tooltips, and `aria-label`.

### 11.2 Header

- Project title.
- Conversation title.
- Skill name and version.
- Model name.
- Run state.
- New conversation, history, skill library, refresh, collapse, close.

### 11.3 Conversation

- Markdown, tables, code, and JSON blocks.
- Tool trace collapsed by default, expandable per message.
- Human gates inline.
- Artifact cards inline.
- Import preview inline.
- Errors inline with actionable controls.
- Auto-scroll only when the user is at the bottom.
- Message windows are capped for long conversations.
- Streaming updates are isolated to the current message.

### 11.4 Composer

- Multi-line auto-grow from 64px to 160px.
- Chinese IME composition events block Enter submission.
- `Ctrl/Cmd + Enter` submits.
- Ordinary Enter inserts a newline.
- Local draft is immediate; server draft sync is debounced by 500ms.
- Draft survives refresh, project switch, conversation switch, and failed run.
- Send uses a synchronous submission guard.
- One active run per conversation; the user can edit the next draft while it
  runs.
- Attachments, skill, route, model, references, and locks are visible as chips.
- `@` search uses the asset ledger and shows type, episode, current version,
  availability, and stable identity.
- File paste and drag/drop use the same attachment path.

### 11.5 Read-only mode

- Agent launcher and panel are absent in share read-only mode.
- Direct API access returns `403`.
- Read-only viewers retain canvas pan, zoom, media playback, and prompt viewing.

## 12. Error Handling

Stable user-facing error codes include:

```text
AGENT_AUTH_REQUIRED
AGENT_PROJECT_FORBIDDEN
AGENT_READ_ONLY
AGENT_RUN_ACTIVE
AGENT_RUN_NOT_FOUND
AGENT_MODEL_UNAVAILABLE
AGENT_MODEL_TIMEOUT
AGENT_TOOL_TIMEOUT
AGENT_TOOL_ARGUMENT_INVALID
AGENT_CONTEXT_TOO_LARGE
AGENT_FEED_VALIDATION_FAILED
AGENT_FEED_REVISION_CONFLICT
AGENT_IMPORT_CONFLICT
AGENT_BUDGET_EXCEEDED
AGENT_INTERRUPTED
AGENT_CANCELLED
```

The browser receives a safe message and recovery actions. Raw provider bodies,
stack traces, cookies, API keys, and local paths remain in internal logs.

## 13. Performance Requirements

Local acceptance targets:

- Agent panel first open: under 150ms.
- Conversation switch with 50 history items: under 200ms.
- 10,000 characters of continuous input: no main-thread task over 50ms.
- 500-message conversation: usable scrolling with windowing.
- SSE reconnect: under 2 seconds.
- Project index request for 400 nodes and 1,000 connections: no full payload.
- Agent state changes do not rerender the canvas graph.
- AgentDock is code-split and loaded after the canvas shell.

Backend targets:

- Tool call P95 under 1 second for local index and detail reads.
- Feed preview P95 under 3 seconds for a normal episode.
- No database lock failures under 20 concurrent simulated Agent sessions.
- No cross-tenant reads or run leakage under concurrency.

## 14. Test Plan

### 14.1 Backend

- Gateway authentication and project permission.
- Internal signature, timestamp, body hash, and replay rejection.
- Tenant/account scoping for every table and endpoint.
- Conversation, message, draft, run, tool, artifact, and event lifecycle.
- Native tool-call and structured fallback paths.
- State-machine, cancellation, interruption, and resume.
- Idempotent message submission, tool calls, and import.
- Feed validation, two repair rounds, and forbidden destructive repair.
- Asset identity resolution by `assetId` and `seriesId + variantKey`.
- Explicit `@` reference enforcement.

### 14.2 Frontend

- Composer IME behavior.
- Draft persistence and recovery.
- Duplicate submission prevention.
- Markdown, code, JSON, and unknown block rendering.
- Markdown XSS prevention.
- Long message and long conversation rendering.
- SSE reconnect and run restoration.
- Human gates and validation issue navigation.
- Import preview and revision conflict UI.
- Read-only Agent absence.

### 14.3 End-to-end

1. Select project and skill.
2. Send a script or creative request.
3. Agent reads project index and selected asset details.
4. Agent asks for lock answers.
5. Agent produces a Feed draft.
6. Feed validation fails once and Agent repairs without deleting content.
7. User reviews asset mapping and preview.
8. User confirms import.
9. Canvas refreshes with the expected nodes and connections.

All E2E tests use mock models and simulated canvas data. They must not call a
paid provider.

### 14.4 Security

- Raw HTML and script injection in messages.
- Unsafe link protocols.
- Skill Zip Slip, absolute path, symlink, executable, duplicate path, and size
  bomb.
- Arbitrary path reads.
- Cross-tenant conversation and artifact access.
- Cross-project node and asset access.
- Read-only share API access.
- Internal signature replay.

### 14.5 Concurrency

- 20 concurrent conversations across multiple tenants.
- Multiple messages submitted to one conversation within the same second.
- SSE reconnect while a run is completing.
- Import idempotency under simultaneous confirmation.
- SQLite WAL concurrency and retry behavior.

## 15. Rollout and Rollback

- Add a tenant-aware feature flag for Agent v2.
- Default the flag off in production until the local and internal acceptance
  suites pass.
- Roll out to the owner tenant first.
- Verify login, project permission, skill selection, tool reads, Feed preview,
  confirmed import, SSE recovery, and zero browser errors.
- Expand to further tenants only after owner acceptance.
- Rollback disables the Agent gateway and UI flag; existing canvas and imported
  data remain unchanged.
- Database changes are additive and backward compatible.

## 16. Definition of Done

- [ ] The same-origin gateway and signed internal canvas APIs are implemented.
- [ ] Existing Agent/Harness code is consolidated into this repository.
- [ ] All Agent data is tenant-, account-, and project-scoped.
- [ ] Continuous conversation and append-message APIs work.
- [ ] Tool loop, checkpoints, cancellation, interruption, and resume work.
- [ ] Asset identity uses `assetId` and `seriesId + variantKey`.
- [ ] Explicit `@` references are the only automatic generation references.
- [ ] The AgentDock UI and input experience meet this specification.
- [ ] Feed v2 validation and confirmed import pass all gates.
- [ ] Security and concurrency suites pass.
- [ ] Performance targets pass.
- [ ] Full frontend and backend tests, type checks, and production build pass.
- [ ] No paid generation is triggered by tests.
- [ ] Changelog is updated.
- [ ] Local Git commit exists.
- [ ] Production deployment and online smoke verification pass.
