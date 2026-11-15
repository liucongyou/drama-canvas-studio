# Backend Consistency Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make backend tests deterministic and close the known project, generation, delivery, task and SSE consistency gaps without changing public API contracts.

**Architecture:** Add one project-scoped PostgreSQL write boundary, one task-maintenance policy, one delivery-recovery policy and one media-version repair path. Existing HTTP and JSON contracts remain stable.

**Tech Stack:** Python 3.11/3.12, `unittest`/pytest, SQLite test mode, PostgreSQL 16, psycopg, `server.py`, `canvas_store.py`

**Spec:** `docs/superpowers/specs/2026-11-15-industrial-root-refactor-design.md`

## Global Constraints

- Production must use PostgreSQL and remain fail-closed.
- Tests may use SQLite only with `CANVAS_DB_BACKEND=sqlite` and `CANVAS_ALLOW_SQLITE=1`.
- Never submit a paid provider generation during tests.
- Different projects must remain concurrent.
- Every migration and repair must be idempotent.
- Every failure response must use a stable machine code and Chinese message.

---

### Task 1: Deterministic Test Harness

**Files:**
- Create: `conftest.py`
- Create: `pytest.ini`
- Create: `scripts/run_backend_tests.py`
- Create: `tests/test_test_environment_contract.py`
- Modify: `tests/test_postgres_fail_closed.py`

**Interfaces:**
- Consumes: existing test files and `pg_compat.postgres_enabled()`
- Produces: a root test process whose default backend is isolated SQLite; production fail-closed tests can still remove the opt-in

- [ ] **Step 1: Write the failing test**

```python
import os

import pg_compat


def test_root_pytest_uses_explicit_sqlite_test_mode():
    assert os.environ["CANVAS_DB_BACKEND"] == "sqlite"
    assert os.environ["CANVAS_ALLOW_SQLITE"] == "1"
    assert pg_compat.postgres_enabled() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_test_environment_contract.py -q`
Expected: FAIL because the variables are not set.

- [ ] **Step 3: Add deterministic test bootstrap**

```python
import os
import tempfile
from pathlib import Path

TEST_ROOT = Path(tempfile.mkdtemp(prefix="drama-canvas-pytest-"))
os.environ["CANVAS_DB_BACKEND"] = "sqlite"
os.environ["CANVAS_ALLOW_SQLITE"] = "1"
os.environ["CANVAS_MULTI_TENANT"] = "0"
os.environ.setdefault("CANVAS_TENANT_DATA_ROOT", str(TEST_ROOT))
os.environ.setdefault("CANVAS_UPLOAD_DIR", str(TEST_ROOT / "uploads"))
```

Add `pytest.ini` with bounded test discovery:

```ini
[pytest]
testpaths = .
python_files = test_*.py tests/test_*.py
addopts = -ra
```

- [ ] **Step 4: Run the contract and fail-closed tests**

Run:

```powershell
python -m pytest tests/test_test_environment_contract.py tests/test_postgres_fail_closed.py -q
```

Expected: all passed.

- [ ] **Step 5: Run the full isolated module suite**

Run: `python -X utf8 scripts/run_backend_tests.py`
Expected: every module subprocess passes. Legacy modules that share process globals
must not contaminate later modules.

- [ ] **Step 6: Commit**

```powershell
git add conftest.py pytest.ini scripts/run_backend_tests.py tests/test_test_environment_contract.py tests/test_postgres_fail_closed.py
git commit -m "test: make backend gate deterministic"
```

### Task 2: Project-Scoped Database Write Boundary

**Files:**
- Modify: `canvas_store.py`
- Modify: `canvas_repository.py`
- Create: `tests/test_project_write_boundary.py`

**Interfaces:**
- Consumes: `pg_compat.PGConnection`, `_run_write_transaction()`
- Produces: `lock_project_row(con, project_id) -> row`; every project mutation calls it before revision validation

- [ ] **Step 1: Write the failing concurrency test**

```python
def test_concurrent_project_mutations_do_not_lose_updates(tmp_path, monkeypatch):
    configure_sqlite_store(monkeypatch, tmp_path)
    project = canvas_store.create_project("并发项目")
    barrier = threading.Barrier(3)

    def write(prompt):
        barrier.wait()
        canvas_repository.repository.patch_node(
            project["id"],
            "node-1",
            {"metadata": {"prompt": prompt}},
            episode_id="EP001",
        )

    seed_node(project["id"], "node-1", "EP001")
    threads = [threading.Thread(target=write, args=(f"p{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    node = canvas_store.get_projection_node(project["id"], "node-1")
    assert node["metadata"]["prompt"] in {"p0", "p1"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_project_write_boundary.py -q`
Expected: at least one write raises revision conflict or update is lost.

- [ ] **Step 3: Add the lock helper**

```python
def lock_project_row(con, project_id):
    sql = (
        "SELECT title,payload_json,revision "
        "FROM canvas_projects WHERE id=?"
        + (" FOR UPDATE" if isinstance(con, pg_compat.PGConnection) else "")
    )
    row = con.execute(sql, (project_id,)).fetchone()
    if not row:
        raise KeyError("项目不存在")
    return row
```

- [ ] **Step 4: Replace direct project-row reads**

In these project mutation paths, replace the initial project SELECT with
`lock_project_row(con, project_id)`:

- `update_project()`
- `update_project_node()`
- `patch_project_node_atomic()`
- `update_project_viewport()`
- `update_project_layout()`
- `update_project_rows()`
- `delete_project()`
- import/restore repository mutations

Keep the existing revision preconditions after the lock.

- [ ] **Step 5: Run concurrency tests**

Run:

```powershell
python -m pytest tests/test_project_write_boundary.py test_canvas_store_concurrency.py test_pg_concurrency.py -q
```

Expected: all passed.

- [ ] **Step 6: Commit**

```powershell
git add canvas_store.py canvas_repository.py tests/test_project_write_boundary.py
git commit -m "fix: serialize project writes at database boundary"
```

### Task 3: Delivery Recovery and Terminal Receipts

**Files:**
- Modify: `canvas_store.py`
- Modify: `server.py`
- Modify: `tests/test_generation_delivery_recovery.py`

**Interfaces:**
- Consumes: `list_delivery_pending_tasks()`, `claim_local_delivery()`, `_recover_persisted_result_to_canvas()`
- Produces: recovery for failed-but-materialized tasks and explicit classification for unrecoverable succeeded tasks

- [ ] **Step 1: Add failing tests**

```python
def test_failed_materialized_task_is_recovered(monkeypatch, tmp_path):
    task = seed_failed_local_video(monkeypatch, tmp_path)
    assert server.reconcile_pending_generation_deliveries() == 1
    saved = canvas_store.get_generation_task(task["id"])
    assert saved["status"] == "succeeded"
    assert saved["delivery_status"] == "ready"


def test_succeeded_pending_without_evidence_is_marked_unrecoverable(monkeypatch, tmp_path):
    task = seed_succeeded_without_receipt(monkeypatch, tmp_path)
    assert server.reconcile_pending_generation_deliveries() == 0
    saved = canvas_store.get_generation_task(task["id"])
    assert saved["delivery_status"] == "unrecoverable"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_generation_delivery_recovery.py -q`
Expected: new tests fail.

- [ ] **Step 3: Implement explicit retrieval**

Extend `reconcile_pending_generation_deliveries()`:

```python
if not local_url and not provider_url:
    store.update_generation_task(
        task["id"],
        delivery_status="unrecoverable",
        error="本地文件和供应商结果地址均不存在，需重新生成或重新导入",
    )
    continue
```

Do not change a paid task back to `pending` when neither receipt exists.

- [ ] **Step 4: Run recovery and task tests**

Run:

```powershell
python -m pytest tests/test_generation_delivery_recovery.py test_media_recovery.py test_generation_task_persistence.py -q
```

Expected: all passed.

- [ ] **Step 5: Commit**

```powershell
git add canvas_store.py server.py tests/test_generation_delivery_recovery.py
git commit -m "fix: complete local result delivery recovery"
```

### Task 4: Uncertain Task TTL and Operator Closure

**Files:**
- Modify: `canvas_store.py`
- Modify: `generation_outcome.py`
- Modify: `server.py`
- Create: `tests/test_generation_task_ttl.py`
- Modify: `web-vimax/src/taskOutcomePresentation.ts`

**Interfaces:**
- Consumes: `generation_tasks.provider_task_id`, `phase`, `created_at`
- Produces: `expire_unconfirmed_generation_tasks(now=None) -> list[dict]`; outcome reason `provider_unconfirmed_expired`

- [ ] **Step 1: Write failing TTL tests**

```python
def test_old_uncertain_task_without_provider_id_expires(monkeypatch, tmp_path):
    task = seed_interrupted_task(monkeypatch, tmp_path, provider_task_id="", age_hours=25)
    expired = canvas_store.expire_unconfirmed_generation_tasks()
    assert [item["id"] for item in expired] == [task["id"]]
    saved = canvas_store.get_generation_task(task["id"])
    assert saved["status"] == "failed"
    assert saved["phase"] == "无法确认且已过期"


def test_task_with_provider_id_is_not_auto_closed(monkeypatch, tmp_path):
    task = seed_interrupted_task(monkeypatch, tmp_path, provider_task_id="provider-1", age_hours=25)
    assert canvas_store.expire_unconfirmed_generation_tasks() == []
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/test_generation_task_ttl.py -q`
Expected: function missing.

- [ ] **Step 3: Implement the TTL function**

```python
def expire_unconfirmed_generation_tasks(now=None):
    current = now or datetime.now(timezone.utc)
    cutoff = (current - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    tasks = [
        item for item in list_runtime_reconcile_tasks(500)
        if item["status"] == "interrupted"
        and not str(item.get("provider_task_id") or "").strip()
        and str(item.get("created_at") or "") < cutoff
    ]
    for task in tasks:
        finish_generation_task(
            task["id"],
            status="failed",
            phase="无法确认且已过期",
            error="供应商结果无法确认，任务已过期；请核对供应商记录后再决定是否重新生成。",
            delivery_status="failed",
        )
    return tasks
```

Call it from `reconcile_generation_runtime_once()` before provider polling.

- [ ] **Step 4: Update outcome presentation**

Add a stable phase mapping:

```ts
if (task.phase === '无法确认且已过期') {
  return label('结果无法确认', '需核对供应商记录');
}
```

- [ ] **Step 5: Run tests**

Run:

```powershell
python -m pytest tests/test_generation_task_ttl.py test_generation_errors.py test_generation_queue.py -q
cd web-vimax
npx vitest run src/taskOutcomePresentation.test.ts
```

Expected: all passed.

- [ ] **Step 6: Commit**

```powershell
git add canvas_store.py generation_outcome.py server.py tests/test_generation_task_ttl.py web-vimax/src/taskOutcomePresentation.ts
git commit -m "fix: bound uncertain generation task lifecycle"
```

### Task 5: Media Version Identity Repair

**Files:**
- Create: `data_repair_media_versions.py`
- Modify: `canvas_store.py`
- Create: `tests/test_media_version_repair.py`

**Interfaces:**
- Consumes: `canvas_projection_nodes.node_json`
- Produces: `repair_media_version_identities(apply=False) -> dict`

- [ ] **Step 1: Write failing repair tests**

```python
def test_duplicate_version_ids_become_unique_without_losing_urls(monkeypatch, tmp_path):
    node = seed_node_with_duplicate_versions(monkeypatch, tmp_path)
    result = repair_media_version_identities(apply=True)
    repaired = canvas_store.get_projection_node("p", node["id"])
    versions = repaired["metadata"]["params"]["mediaVersions"]
    assert result["duplicateIdsBefore"] == 1
    assert len({item["versionId"] for item in versions}) == len(versions)
    assert {item["url"] for item in versions} == node_urls(node)
```

- [ ] **Step 2: Implement deterministic suffix generation**

```python
def stable_duplicate_version_id(version_id, url, ordinal):
    digest = hashlib.sha256(f"{version_id}|{url}".encode("utf-8")).hexdigest()[:12]
    return f"{version_id}:variant:{ordinal}:{digest}"
```

Preserve the active version ID. Rewrite only duplicate non-active IDs.

- [ ] **Step 3: Implement active mismatch repair**

If `activeVersionId` is not present in the list:

1. use `content` URL to find the exact version;
2. otherwise select the newest local `/uploads/` version;
3. otherwise mark the node `mediaIdentityUnresolved=true` and expose a missing state.

- [ ] **Step 4: Dry-run against production snapshot**

Run:

```powershell
python data_repair_media_versions.py --input prod-snapshot.json --dry-run
python data_repair_media_versions.py --input prod-snapshot.json --apply --output repaired-snapshot.json
```

Expected: duplicate count reaches `0`; active mismatch count reaches `0`; no URL count changes.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_media_version_repair.py test_media_versions.py test_canvas_feed_roundtrip.py -q`
Expected: all passed.

- [ ] **Step 6: Commit**

```powershell
git add data_repair_media_versions.py canvas_store.py tests/test_media_version_repair.py
git commit -m "fix: repair media version identity mismatches"
```

### Task 6: SSE and Event Retention Boundaries

**Files:**
- Modify: `server.py`
- Modify: `canvas_store.py`
- Create: `tests/test_sse_connection_budget.py`
- Create: `tests/test_event_retention.py`

**Interfaces:**
- Consumes: `_EVENT_SUBSCRIBERS`, project event table
- Produces: `SSE_TENANT_LIMIT`, `SSE_PROJECT_LIMIT`; events retained per project

- [ ] **Step 1: Write failing budget tests**

```python
def test_project_stream_limit_rejects_extra_connection(monkeypatch):
    monkeypatch.setattr(server, "SSE_PROJECT_LIMIT", 1)
    first = register_test_subscriber("p")
    assert first.acquired is True
    second = register_test_subscriber("p")
    assert second.acquired is False
    assert second.code == "SSE_CONNECTION_LIMIT"
```

- [ ] **Step 2: Add limits and cleanup**

```python
SSE_TENANT_LIMIT = int(os.getenv("CANVAS_SSE_TENANT_LIMIT", "16"))
SSE_PROJECT_LIMIT = int(os.getenv("CANVAS_SSE_PROJECT_LIMIT", "4"))
```

Reject excess subscribers with HTTP 429 and code `SSE_CONNECTION_LIMIT`.
Always remove the subscriber in `finally`.

- [ ] **Step 3: Fix event retention**

Replace the global event-id deletion with a per-project cap:

```sql
DELETE FROM canvas_events
WHERE project_id=?
  AND id NOT IN (
    SELECT id FROM canvas_events
    WHERE project_id=?
    ORDER BY id DESC
    LIMIT 2000
  )
```

- [ ] **Step 4: Run tests**

Run:

```powershell
python -m pytest tests/test_sse_connection_budget.py tests/test_event_retention.py test_canvas_store_concurrency.py -q
```

Expected: all passed.

- [ ] **Step 5: Commit**

```powershell
git add server.py canvas_store.py tests/test_sse_connection_budget.py tests/test_event_retention.py
git commit -m "fix: bound SSE connections and project event retention"
```

## Plan Self-Review

- Spec coverage: deterministic tests, project lock, delivery, TTL, version repair
  and SSE boundaries are each assigned to a task.
- Placeholder scan: no TBD/TODO; each task has concrete tests and commands.
- Interface consistency: `lock_project_row`, `expire_unconfirmed_generation_tasks`,
  `repair_media_version_identities` and SSE constants are defined before use.
