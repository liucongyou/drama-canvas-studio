# Canvas Agent Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the current one-shot Agent sidecar into a tenant-scoped, project-aware, tool-using Agent with durable conversations, a reliable AgentDock UI, and confirmed Feed v2 import.

**Architecture:** Keep the Agent sidecar for process isolation, place it behind a same-origin main-service gateway, authenticate internal canvas calls with HMAC-signed context, add typed canvas tools and a bounded model/tool loop, and keep the existing Feed preview/import service as the only canvas writer.

**Tech Stack:** Python 3.12, FastAPI, SQLite/WAL, HTTPX, `unittest`/pytest, React 18, TypeScript, Vite, react-markdown, Vitest, Puppeteer.

**Spec:** `docs/superpowers/specs/2026-11-10-canvas-agent-overhaul-design.md`

## Global Constraints

- Use this repository as the only source of truth.
- Never trigger paid image, video, or audio generation during tests.
- Keep `drama-canvas-feed/v2` as the only canvas artifact contract.
- Use `assetId` as the stable asset identity and `seriesId + variantKey` for variants.
- Only explicit `@` references become Agent generation references.
- Agent never writes canvas nodes directly.
- Import requires preview, current revision, user confirmation, and idempotency.
- Every Agent record is scoped by tenant, account, and project where applicable.
- Browser Agent API is same-origin under `/api/agent/*`.
- Raw HTML in Markdown is disabled.
- Existing unrelated dirty-worktree files must not be reverted or staged.

---

### Task 1: Signed Agent Context and Configuration

**Files:**
- Create: `agent/app/context.py`
- Modify: `agent/app/config.py`
- Test: `agent/tests/test_context_security.py`

**Interfaces:**
- Produces: `sign_request(secret, method, path, timestamp, tenant_id, account_id, body) -> str`
- Produces: `verify_request(secret, method, path, timestamp, tenant_id, account_id, body, signature, now=None) -> None`
- Produces: `RequestContext(tenant_id, account_id, request_id)`

- [ ] **Step 1: Write failing signature tests**

```python
def test_signature_round_trip() -> None:
    signature = sign_request("secret", "POST", "/api/agent/runs", "1700000000", "t1", "a1", b"{}")
    verify_request("secret", "POST", "/api/agent/runs", "1700000000", "t1", "a1", b"{}", signature, now=1700000000)


def test_signature_rejects_tampering() -> None:
    with pytest.raises(ValueError):
        verify_request("secret", "POST", "/api/agent/runs", "1700000000", "t1", "a2", b"{}", "bad", now=1700000000)
```

- [ ] **Step 2: Run and confirm failure**

Run: `agent\.venv\Scripts\python.exe -m pytest agent/tests/test_context_security.py -q`

Expected: import failure because `app.context` does not exist.

- [ ] **Step 3: Implement HMAC context**

```python
def _digest(secret: str, method: str, path: str, timestamp: str, tenant_id: str, account_id: str, body: bytes) -> str:
    body_hash = hashlib.sha256(body or b"").hexdigest()
    message = "\n".join((method.upper(), path, timestamp, tenant_id, account_id, body_hash))
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
```

- [ ] **Step 4: Add settings**

```python
agent_internal_secret: str = ""
agent_sidecar_url: str = "http://127.0.0.1:8895"
agent_gateway_enabled: bool = True
agent_max_tool_steps: int = 12
agent_tool_result_bytes: int = 32 * 1024
agent_signature_window_seconds: int = 60
```

- [ ] **Step 5: Run tests**

Run: `agent\.venv\Scripts\python.exe -m pytest agent/tests/test_context_security.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/app/context.py agent/app/config.py agent/tests/test_context_security.py
git commit -m "feat(agent): add signed request context"
```

### Task 2: Scoped Conversation Store and Migrations

**Files:**
- Modify: `agent/app/db.py`
- Create: `agent/app/conversations.py`
- Test: `agent/tests/test_conversations.py`

**Interfaces:**
- Produces: `ensure_conversation(tenant_id, account_id, project_id, conversation_id="") -> dict`
- Produces: `list_conversations(tenant_id, account_id, project_id) -> list[dict]`
- Produces: `append_user_message(conversation_id, tenant_id, account_id, content_blocks, content_text) -> dict`
- Produces: `save_draft(scope, payload) -> dict`
- Produces: `load_draft(scope) -> dict`

- [ ] **Step 1: Write failing conversation tests**

```python
def test_conversation_is_scoped(tmp_db) -> None:
    conversation = ensure_conversation("tenant-a", "account-a", "project-1")
    assert list_conversations("tenant-b", "account-a", "project-1") == []
    assert list_conversations("tenant-a", "account-a", "project-1")[0]["id"] == conversation["id"]


def test_draft_round_trip(tmp_db) -> None:
    save_draft({"tenantId": "t", "accountId": "a", "conversationId": "c"}, {"text": "中文草稿", "locks": {"track": "国内剧"}})
    assert load_draft({"tenantId": "t", "accountId": "a", "conversationId": "c"})["text"] == "中文草稿"
```

- [ ] **Step 2: Run and confirm failure**

Run: `agent\.venv\Scripts\python.exe -m pytest agent/tests/test_conversations.py -q`

Expected: import failure or missing table.

- [ ] **Step 3: Add additive tables and indexes**

```sql
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  account_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  active_run_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_conversations_scope
  ON conversations(tenant_id, account_id, project_id, updated_at DESC);
```

Add equivalent `conversation_messages`, `agent_drafts`, and `tool_calls` tables.

- [ ] **Step 4: Implement scoped store functions**

Every query must include `tenant_id=? AND account_id=?`; project-aware queries also include `project_id=?`.

- [ ] **Step 5: Run tests**

Run: `agent\.venv\Scripts\python.exe -m pytest agent/tests/test_conversations.py agent/tests/test_agent_core.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/app/db.py agent/app/conversations.py agent/tests/test_conversations.py
git commit -m "feat(agent): add scoped conversation storage"
```

### Task 3: Canvas Tools and Asset Ledger

**Files:**
- Create: `agent/app/canvas_tools.py`
- Modify: `agent/app/canvas_client.py`
- Test: `agent/tests/test_canvas_tools.py`

**Interfaces:**
- Produces: `CanvasTools(project, canvas_client)`
- Produces: `call(name: str, arguments: dict, context: dict) -> dict`
- Produces: `TOOL_DEFINITIONS: list[dict]`

- [ ] **Step 1: Write failing asset identity test**

```python
def test_list_assets_uses_asset_id_not_title() -> None:
    project = {"payload": {"nodes": [
        {"id": "n1", "type": "image", "title": "角色｜陈大强（图2）", "metadata": {"assetId": "character:陈大强:default", "seriesId": "character:陈大强", "variantKey": "default"}},
        {"id": "n2", "type": "image", "title": "角色｜陈大强（图9）", "metadata": {"assetId": "character:陈大强:old", "seriesId": "character:陈大强", "variantKey": "old"}},
    ]}}
    tools = CanvasTools(project, None)
    assets = tools.call("list_assets", {}, {"projectId": "p"})["assets"]
    assert {item["assetId"] for item in assets} == {"character:陈大强:default", "character:陈大强:old"}
```

- [ ] **Step 2: Run and confirm failure**

Run: `agent\.venv\Scripts\python.exe -m pytest agent/tests/test_canvas_tools.py -q`

Expected: import failure.

- [ ] **Step 3: Implement bounded projections**

```python
def project_index(project: dict) -> dict:
    payload = project.get("payload") if isinstance(project.get("payload"), dict) else {}
    nodes = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
    connections = payload.get("connections") if isinstance(payload.get("connections"), list) else []
    return {
        "id": project.get("id", ""),
        "title": project.get("title", ""),
        "revision": project.get("revision", 0),
        "nodeCount": len(nodes),
        "connectionCount": len(connections),
        "episodes": episode_summaries(payload),
    }
```

- [ ] **Step 4: Implement all spec tools**

Implement `get_project_index`, `get_episode_context`, `search_nodes`,
`get_node_detail`, `list_assets`, `get_asset_detail`,
`get_node_dependencies`, `resolve_prompt_references`, `validate_feed`, and
`finalize_feed`. `read_skill_section` remains delegated to the skill reader.

- [ ] **Step 5: Run tests**

Run: `agent\.venv\Scripts\python.exe -m pytest agent/tests/test_canvas_tools.py -q`

Expected: PASS, including duplicate title and missing asset identity cases.

- [ ] **Step 6: Commit**

```bash
git add agent/app/canvas_tools.py agent/app/canvas_client.py agent/tests/test_canvas_tools.py
git commit -m "feat(agent): add typed canvas context tools"
```

### Task 4: Model Tool Loop and Runtime State Machine

**Files:**
- Modify: `agent/app/llm.py`
- Modify: `agent/app/runtime.py`
- Modify: `agent/app/db.py`
- Test: `agent/tests/test_runtime_tools.py`

**Interfaces:**
- Produces: `chat_tool_round(system, messages, tools, model_id) -> ToolRound`
- Produces: `ToolRound(content: str, tool_calls: list[dict], native: bool)`
- Produces: runtime states `queued`, `running`, `waiting_locks`, `waiting_review`, `importing`, `completed`, `failed`, `cancelled`, `interrupted`.

- [ ] **Step 1: Write failing bounded-loop tests**

```python
def test_runtime_stops_after_twelve_tool_steps(fake_llm) -> None:
    fake_llm.always_tool("get_project_index", {})
    run = execute_test_run(max_steps=12)
    assert run["status"] == "failed"
    assert run["errorCode"] == "AGENT_TOOL_LIMIT"
    assert len(run["toolCalls"]) == 12
```

- [ ] **Step 2: Run and confirm failure**

Run: `agent\.venv\Scripts\python.exe -m pytest agent/tests/test_runtime_tools.py -q`

Expected: FAIL because tool loop and `chat_tool_round` are missing.

- [ ] **Step 3: Add native tool-call request and fallback**

```python
payload["tools"] = tools
payload["tool_choice"] = "auto"
tool_calls = message.get("tool_calls") or []
if not tool_calls:
    return ToolRound(content=str(message.get("content") or ""), tool_calls=[], native=True)
```

If the provider rejects tools, call `_structured_action(system, messages, model_id)`
and parse one `{"action": "tool"|"final", ...}` object.

- [ ] **Step 4: Implement runtime checkpoints**

Before each model call and tool call, write `current_stage`; check
`cancel_requested`; persist tool call arguments, result, bytes, and idempotency
key. On startup, convert stale `running` runs to `interrupted`.

- [ ] **Step 5: Run tests**

Run: `agent\.venv\Scripts\python.exe -m pytest agent/tests/test_runtime_tools.py agent/tests/test_agent_core.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/app/llm.py agent/app/runtime.py agent/app/db.py agent/tests/test_runtime_tools.py
git commit -m "feat(agent): add bounded tool runtime"
```

### Task 5: Same-Origin Gateway and Internal Canvas APIs

**Files:**
- Create: `agent_gateway.py`
- Modify: `server.py`
- Modify: `agent/app/canvas_client.py`
- Modify: `agent/app/api.py`
- Test: `tests/test_agent_gateway.py`

**Interfaces:**
- Produces: `proxy_agent(handler, path, body=None) -> bool`
- Produces: `internal_project_index(project_id) -> dict`
- Produces: `internal_project_nodes(project_id, filters) -> dict`
- Produces: `internal_project_assets(project_id) -> dict`
- Produces: `internal_feed_preview(project_id, feed) -> dict`
- Produces: `internal_feed_import(project_id, feed, revision, operation_id) -> dict`

- [ ] **Step 1: Write failing gateway tests**

```python
def test_gateway_rejects_missing_session(handler_fixture) -> None:
    response = handler_fixture.post("/api/agent/runs", {})
    assert response.status == 401


def test_internal_request_signature_required(handler_fixture) -> None:
    response = handler_fixture.get("/api/internal/agent/projects/p1/index")
    assert response.status == 401
```

- [ ] **Step 2: Run and confirm failure**

Run: `python -m pytest tests/test_agent_gateway.py -q`

Expected: 404 because routes do not exist.

- [ ] **Step 3: Add same-origin proxy**

Proxy `/api/agent/*` from the authenticated main handler to
`AGENT_SIDECAR_URL`, signing the context headers. For SSE, stream upstream
chunks directly and stop on completion or client disconnect.

- [ ] **Step 4: Add signed internal endpoints**

Use `agent_gateway.verify_internal_request()` before entering tenant context.
Return bounded project index/detail/asset data. Preview and import call the
existing `canvas_feed_contract.prepare_import` and
`prepare_import_locked` paths.

- [ ] **Step 5: Switch Agent CanvasClient**

Use signed internal requests, never `agent/.env` cookies or tokens.

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_agent_gateway.py -q`

Run: `agent\.venv\Scripts\python.exe -m pytest agent/tests -q`

Expected: PASS, and no public 8895 URL remains in `agentApi.ts`.

- [ ] **Step 7: Commit**

```bash
git add agent_gateway.py server.py agent/app/canvas_client.py agent/app/api.py tests/test_agent_gateway.py
git commit -m "feat(agent): add same-origin authenticated gateway"
```

### Task 6: Append-Message, Draft, and Recovery APIs

**Files:**
- Modify: `agent/app/api.py`
- Modify: `agent/app/runtime.py`
- Modify: `agent/app/conversations.py`
- Test: `agent/tests/test_agent_api.py`

**Interfaces:**
- `GET /api/agent/bootstrap?projectId=...`
- `POST /api/agent/conversations`
- `GET /api/agent/conversations/{id}`
- `PUT /api/agent/conversations/{id}/draft`
- `POST /api/agent/conversations/{id}/messages`
- `POST /api/agent/runs/{id}/resume`
- `POST /api/agent/runs/{id}/cancel`
- `POST /api/agent/runs/{id}/confirm`
- `GET /api/agent/runs/{id}/events`

- [ ] **Step 1: Write failing API tests**

```python
def test_append_message_is_idempotent(client) -> None:
    body = {"content": [{"type": "text", "text": "继续"}]}
    first = client.post("/api/agent/conversations/c1/messages", json=body, headers={"Idempotency-Key": "same"})
    second = client.post("/api/agent/conversations/c1/messages", json=body, headers={"Idempotency-Key": "same"})
    assert first.json()["run"]["id"] == second.json()["run"]["id"]
```

- [ ] **Step 2: Run and confirm failure**

Run: `agent\.venv\Scripts\python.exe -m pytest agent/tests/test_agent_api.py -q`

Expected: 404.

- [ ] **Step 3: Implement routes**

Read the signed `RequestContext` on every route, enforce tenant/account scope,
and return stable `AGENT_*` error codes.

- [ ] **Step 4: Emit SSE**

Use `id:` for every event, support `Last-Event-ID`, and emit `interrupted`,
`review_required`, `imported`, `failed`, and `cancelled`.

- [ ] **Step 5: Run tests**

Run: `agent\.venv\Scripts\python.exe -m pytest agent/tests/test_agent_api.py agent/tests/test_conversations.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/app/api.py agent/app/runtime.py agent/app/conversations.py agent/tests/test_agent_api.py
git commit -m "feat(agent): add durable message and draft APIs"
```

### Task 7: AgentDock UI, Structured Messages, and Composer Reliability

**Files:**
- Modify: `web-vimax/src/agent/agentApi.ts`
- Modify: `web-vimax/src/agent/AgentDock.tsx`
- Create: `web-vimax/src/agent/AgentMessageContent.tsx`
- Create: `web-vimax/src/agent/AgentComposer.tsx`
- Modify: `web-vimax/src/agent-dock.css`
- Modify: `web-vimax/src/main.tsx`
- Test: `web-vimax/src/agent/agentApi.test.ts`
- Test: `web-vimax/src/agent/AgentMessageContent.test.tsx`
- Test: `web-vimax/src/agent/AgentComposer.test.tsx`

**Interfaces:**
- `AgentMessage.blocks: AgentContentBlock[]`
- `AgentDraft` with text, attachments, references, skill, route, model, locks.
- `AgentComposer` emits `onSubmit({text, attachments, references})`.

- [ ] **Step 1: Write failing frontend tests**

```tsx
it('does not submit while composing Chinese input', () => {
  const onSubmit = vi.fn();
  render(<AgentComposer value="陈大强" onChange={() => {}} onSubmit={onSubmit} />);
  fireEvent.compositionStart(screen.getByRole('textbox'));
  fireEvent.keyDown(screen.getByRole('textbox'), {key: 'Enter', ctrlKey: true});
  expect(onSubmit).not.toHaveBeenCalled();
});


it('renders markdown code and JSON without dropping long content', () => {
  render(<AgentMessageContent blocks={[
    {type: 'markdown', text: '## 结果'},
    {type: 'json', data: {assetId: 'character:陈大强:default'}},
    {type: 'markdown', text: '长'.repeat(5000)},
  ]} />);
  expect(screen.getByText('结果')).toBeVisible();
  expect(screen.getByText(/character:陈大强/)).toBeVisible();
});
```

- [ ] **Step 2: Run and confirm failure**

Run: `npm run test -- --run src/agent/AgentMessageContent.test.tsx src/agent/AgentComposer.test.tsx`

Expected: missing component errors.

- [ ] **Step 3: Implement same-origin API**

Use `/api/agent` as the default base URL, `credentials: 'same-origin'`, request
timeouts, `Idempotency-Key`, abort controllers, and typed error codes.

- [ ] **Step 4: Implement structured message renderer**

Use `react-markdown` with `skipHtml` and a safe link component. Render JSON in
collapsible blocks and tool messages as trace rows.

- [ ] **Step 5: Implement composer**

Add IME guards, draft restore, server draft sync, attachment chips, `@` asset
search, duplicate-submit lock, paste/drop upload, and autosizing.

- [ ] **Step 6: Implement professional panel layout**

Use 520px default width, 380 to 760 range, header/conversation/composer, mobile
drawer, theme tokens, inline human gates and artifacts, and code-split import
from `main.tsx`.

- [ ] **Step 7: Run tests and build**

Run: `npm run typecheck`

Run: `npm run test -- --run src/agent`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add web-vimax/src/agent/agentApi.ts web-vimax/src/agent/AgentDock.tsx web-vimax/src/agent/AgentMessageContent.tsx web-vimax/src/agent/AgentComposer.tsx web-vimax/src/agent-dock.css web-vimax/src/main.tsx web-vimax/src/agent/agentApi.test.ts web-vimax/src/agent/AgentMessageContent.test.tsx web-vimax/src/agent/AgentComposer.test.tsx
git commit -m "feat(agent): rebuild project-aware agent dock"
```

### Task 8: End-to-End, Performance, Release, and Rollback

**Files:**
- Create: `agent/tests/test_agent_concurrency.py`
- Create: `web-vimax/scripts/smoke-agent.mjs`
- Create: `docs/reports/2026-11-10-canvas-agent-overhaul-verification.md`
- Modify: `web-vimax/src/changelog.ts`
- Modify: `web-vimax/src/changelogEntries.ts`

**Interfaces:**
- Produces: repeatable local and online Agent smoke evidence.

- [ ] **Step 1: Add concurrency and tenancy tests**

Create 20 conversations across two tenants and assert no run, message, draft,
tool call, artifact, or SSE event crosses scope.

- [ ] **Step 2: Add browser smoke**

The script logs in, opens AgentDock, sends text with mock model, verifies
structured messages, tool trace, draft restoration, preview, and zero
`pageerror` events. It must not trigger paid generation.

- [ ] **Step 3: Run full verification**

Run: `agent\.venv\Scripts\python.exe -m pytest -q`

Run: `npm run typecheck`

Run: `npm test -- --run`

Run: `npm run build`

Run: `node scripts/smoke-agent.mjs`

Expected: all pass, performance report recorded.

- [ ] **Step 4: Update version and changelog**

Add the next `2026.11.10.*` entry describing continuous conversation, tool
trace, reliable input, same-origin authentication, and confirmed Feed import.

- [ ] **Step 5: Commit release**

```bash
git add agent agent_gateway.py server.py web-vimax/src/agent web-vimax/src/agent-dock.css web-vimax/src/main.tsx web-vimax/src/changelog.ts web-vimax/src/changelogEntries.ts web-vimax/scripts/smoke-agent.mjs docs/reports/2026-11-10-canvas-agent-overhaul-verification.md tests/test_agent_gateway.py
git commit -m "feat: ship project-aware canvas agent"
```

- [ ] **Step 6: Deploy**

Build the frontend, deploy the main service helper changes and Agent sidecar,
restart both services, verify `/healthz`, `/api/agent/health`, login, project
permission, Agent SSE, and zero console errors.

- [ ] **Step 7: Record online evidence**

Append deployed commit, bundle hash, test counts, performance values, and smoke
results to the verification report. Commit the report.
