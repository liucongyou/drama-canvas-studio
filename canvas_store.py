# -*- coding: utf-8 -*-
"""canvas_store.py — 画布项目存取（SQLite · payload 整包模式，对齐 BeefTV schema）"""
import json
import os
import secrets
import sqlite3
import string
import time
from datetime import datetime, timezone

import tenant_context

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "canvas.db")

_B62 = string.digits + string.ascii_letters


def _rid(n=22):
    return "".join(secrets.choice(_B62) for _ in range(n))


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


_SCHEMA_READY = False
_SCHEMA_READY_PATHS: set[str] = set()
_SCHEMA_LOCK = __import__("threading").RLock()
_WAL_READY_PATHS: set[str] = set()
_WRITE_RETRY_SECONDS = 35.0
_WRITE_RETRY_MAX_DELAY = 0.5


class RevisionConflictError(RuntimeError):
    """Raised when a project write no longer matches its revision precondition."""


def _db_path() -> str:
    context = tenant_context.current()
    if context is not None:
        return str(context.db_path)
    return str(DB_PATH)


def _tenant_data_dir() -> str:
    # Use the same tenant root resolver as DB and upload paths. The current
    # ContextVar is also present in worker threads, so prefer its root.
    context = tenant_context.current()
    if context is not None:
        return str(context.data_root)
    return os.path.dirname(DB_PATH)


def _connect():
    """打开连接 + 确保 schema。

    ★ 两个必须的健壮性设置（此前都没有，是 "database is locked" 全线 500 的根因）：
      1) timeout / busy_timeout：并发写时不要立刻抛错，等锁释放（默认 5s 且无 busy 重试）；
      2) CREATE TABLE 只跑一次：建表是**写操作**，会在每个请求上都抢一次写锁，
         几十个并发时互相排队 → 直接 OperationalError。缓存成模块级一次即可。
    """
    global _SCHEMA_READY
    path = _db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA foreign_keys=ON")
    if path not in _WAL_READY_PATHS:
        with _SCHEMA_LOCK:
            if path not in _WAL_READY_PATHS:
                con.execute("PRAGMA journal_mode=WAL")
                con.execute("PRAGMA synchronous=NORMAL")
                _WAL_READY_PATHS.add(path)
    if not _SCHEMA_READY or path not in _SCHEMA_READY_PATHS:
        with _SCHEMA_LOCK:
            con.execute(
            """CREATE TABLE IF NOT EXISTS canvas_projects(
                id TEXT PRIMARY KEY,
                user_id TEXT,
                title TEXT,
                payload_json TEXT,
                revision INTEGER DEFAULT 1,
                created_at TEXT,
                updated_at TEXT
            )"""
            )
            con.execute(
            """CREATE TABLE IF NOT EXISTS canvas_snapshots(
                id TEXT PRIMARY KEY,
                canvas_id TEXT,
                title TEXT,
                node_count INTEGER,
                connection_count INTEGER,
                payload_json TEXT,
                revision INTEGER,
                kind TEXT DEFAULT 'auto',
                created_at TEXT
            )"""
            )
            con.execute(
            """CREATE TABLE IF NOT EXISTS generation_tasks(
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                title TEXT,
                kind TEXT,
                status TEXT NOT NULL,
                phase TEXT,
                progress INTEGER,
                provider TEXT,
                model TEXT,
                provider_task_id TEXT,
                attempt INTEGER DEFAULT 1,
                estimated_cost REAL DEFAULT 0,
                cost_currency TEXT DEFAULT '',
                error TEXT,
                created_at TEXT,
                started_at TEXT,
                updated_at TEXT,
                finished_at TEXT,
                provider_finished_at TEXT,
                node_written_at TEXT,
                local_result_url TEXT,
                provider_result_url TEXT
            )"""
            )
            con.execute(
            "CREATE INDEX IF NOT EXISTS idx_generation_tasks_project "
            "ON generation_tasks(project_id, created_at DESC)"
            )
            con.execute(
            "CREATE INDEX IF NOT EXISTS idx_generation_tasks_node "
            "ON generation_tasks(project_id, node_id, status)"
            )
            con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_generation_tasks_active_node "
            "ON generation_tasks(project_id, node_id) "
            "WHERE status IN ('queued','submitting','running')"
            )
            con.execute(
            """CREATE TABLE IF NOT EXISTS audit_events(
                id TEXT PRIMARY KEY,
                tenant_id TEXT,
                actor TEXT,
                action TEXT NOT NULL,
                target_type TEXT,
                target_id TEXT,
                status TEXT,
                details_json TEXT,
                created_at TEXT
            )"""
            )
            con.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_events_created "
            "ON audit_events(created_at DESC)"
            )
            # 画布事件的持久化日志：SSE 断线重连靠它补发，轮询降级后靠它对账。
            # 只保留 id 自增，客户端用 Last-Event-ID 续传，不需要按时间排序。
            con.execute(
            """CREATE TABLE IF NOT EXISTS canvas_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                node_id TEXT,
                payload_json TEXT,
                created_at TEXT
            )"""
            )
            con.execute(
            "CREATE INDEX IF NOT EXISTS idx_canvas_events_project "
            "ON canvas_events(project_id, id)"
            )
            task_columns = {
                row[1] for row in con.execute("PRAGMA table_info(generation_tasks)")
            }
            if "attempt" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN attempt INTEGER DEFAULT 1"
                )
            if "estimated_cost" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN estimated_cost REAL DEFAULT 0"
                )
            if "cost_currency" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN cost_currency TEXT DEFAULT ''"
                )
            # 端到端延迟要靠时间戳切段定位：供应商出结果 → 落盘/转存 → 节点写库。
            # 缺了这三段，只能靠轮询间隔反推，查不出"到底慢在哪"。
            if "provider_finished_at" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN provider_finished_at TEXT"
                )
            if "node_written_at" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN node_written_at TEXT"
                )
            if "local_result_url" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN local_result_url TEXT"
                )
            if "provider_result_url" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN provider_result_url TEXT"
                )
            snapshot_columns = {
                row[1] for row in con.execute("PRAGMA table_info(canvas_snapshots)")
            }
            if "kind" not in snapshot_columns:
                con.execute(
                    "ALTER TABLE canvas_snapshots ADD COLUMN kind TEXT DEFAULT 'auto'"
                )
            con.commit()
            _SCHEMA_READY_PATHS.add(path)
            _SCHEMA_READY = True
    return con


def _is_retryable_write_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "database is locked" in message or "database is busy" in message


def _run_write_transaction(callback):
    """Retry SQLite writer contention beyond a single busy_timeout window."""
    deadline = time.monotonic() + _WRITE_RETRY_SECONDS
    delay = 0.05
    while True:
        try:
            con = _connect()
        except sqlite3.OperationalError as exc:
            if not _is_retryable_write_error(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(_WRITE_RETRY_MAX_DELAY, delay * 2)
            continue
        try:
            con.execute("BEGIN IMMEDIATE")
            result = callback(con)
            con.commit()
            return result
        except sqlite3.OperationalError as exc:
            if con.in_transaction:
                con.rollback()
            if not _is_retryable_write_error(exc) or time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(_WRITE_RETRY_MAX_DELAY, delay * 2)
        except Exception:
            if con.in_transaction:
                con.rollback()
            raise
        finally:
            con.close()


def list_projects():
    con = _connect()
    rows = con.execute(
        "SELECT id, title, revision, updated_at FROM canvas_projects ORDER BY updated_at DESC"
    ).fetchall()
    con.close()
    return [{"id": r[0], "title": r[1], "revision": r[2], "updated_at": r[3]} for r in rows]


def get_project(pid):
    con = _connect()
    row = con.execute(
        "SELECT id, title, payload_json, revision FROM canvas_projects WHERE id=?", (pid,)
    ).fetchone()
    con.close()
    if not row:
        return None
    return {"id": row[0], "title": row[1], "payload": json.loads(row[2]), "revision": row[3]}


def create_project(title, payload=None):
    con = _connect()
    pid = _rid(22)
    now = _now_iso()
    if payload is None:
        payload = {
            "activeChatId": None,
            "appearance": {"mode": "dark"},
            "backgroundMode": "dots",
            "chatSessions": [],
            "connections": [],
            "createdAt": now,
            "directorScenes": [],
            "folderId": None,
            "id": pid,
            "nodes": [],
            "projectId": pid,
            "revision": 1,
            "showImageInfo": False,
            "title": title,
            "updatedAt": now,
            "viewport": {"x": 0, "y": 0, "zoom": 1},
            "workspaceProjectId": pid,
        }
    else:
        payload.setdefault("id", pid)
        payload.setdefault("projectId", pid)
        payload.setdefault("workspaceProjectId", pid)
        payload.setdefault("title", title)
        payload.setdefault("revision", 1)
    con.execute(
        "INSERT INTO canvas_projects(id,user_id,title,payload_json,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (
            pid,
            tenant_context.current_tenant_id(),
            title,
            json.dumps(payload, ensure_ascii=False),
            1,
            now,
            now,
        ),
    )
    _snapshot(con, pid, title, payload, 1, kind="auto")
    con.commit()
    con.close()
    return pid


def update_project(
    pid,
    title=None,
    payload=None,
    revision=None,
    if_revision=None,
    snapshot_mode="auto",
):
    con = _connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT title, payload_json, revision FROM canvas_projects WHERE id=?",
            (pid,),
        ).fetchone()
        if not row:
            con.rollback()
            return False
        current_revision = int(row[2])
        if if_revision is not None and current_revision != int(if_revision):
            con.rollback()
            raise RevisionConflictError(
                f"项目已更新：期望 revision={if_revision}，实际 revision={current_revision}"
            )
        new_title = title if title is not None else row[0]
        new_payload = json.loads(row[1]) if payload is None else payload
        if not isinstance(new_payload, dict):
            con.rollback()
            raise ValueError("payload must be an object")
        new_rev = (current_revision + 1) if revision is None else int(revision)
        now = _now_iso()
        new_payload = json.loads(json.dumps(new_payload, ensure_ascii=False))
        new_payload["revision"] = new_rev
        new_payload["title"] = new_title
        new_payload["updatedAt"] = now
        cursor = con.execute(
            "UPDATE canvas_projects SET title=?, payload_json=?, revision=?, updated_at=? "
            "WHERE id=? AND revision=?",
            (
                new_title,
                json.dumps(new_payload, ensure_ascii=False),
                new_rev,
                now,
                pid,
                current_revision,
            ),
        )
        if cursor.rowcount != 1:
            con.rollback()
            raise RevisionConflictError("项目写入冲突，请重新读取后再试")
        _maybe_snapshot(
            con,
            pid,
            new_title,
            new_payload,
            new_rev,
            mode=snapshot_mode,
            now=now,
        )
        con.commit()
        return True
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise
    finally:
        con.close()


def delete_project(pid):
    con = _connect()
    con.execute("DELETE FROM generation_tasks WHERE project_id=?", (pid,))
    con.execute("DELETE FROM canvas_snapshots WHERE canvas_id=?", (pid,))
    con.execute("DELETE FROM canvas_projects WHERE id=?", (pid,))
    con.commit()
    con.close()


_TASK_FIELDS = {
    "status", "phase", "progress", "provider", "model", "provider_task_id",
    "error", "started_at", "finished_at", "title", "kind",
    "estimated_cost", "cost_currency", "provider_finished_at", "node_written_at",
    "local_result_url", "provider_result_url",
}

_ACTIVE_TASK_STATUSES = {"queued", "submitting", "running"}


def _merge_generation_task_update(task: dict | None, updates: dict) -> dict:
    """Keep persisted task state monotonic at the storage boundary.

    Provider callbacks, restart reconciliation and the UI poller can race. A
    stale writer must not turn a succeeded task into failed/running, and only a
    successful result may replace a persisted failure.
    """
    if not task:
        return dict(updates)
    current = str(task.get("status") or "")
    requested = str(updates.get("status") or current)
    merged = dict(updates)
    allowed = True
    if current == "succeeded":
        allowed = requested == "succeeded"
    elif current in {"failed", "interrupted", "cancelled"}:
        allowed = requested == "succeeded" or requested == current
    elif current in _ACTIVE_TASK_STATUSES and requested == "queued":
        allowed = False
    if allowed:
        return merged
    # Keep non-status evidence (for example a provider task id from a late
    # callback), but never regress the task lifecycle itself.
    merged.pop("status", None)
    for key in ("phase", "progress", "finished_at", "error"):
        merged.pop(key, None)
    return merged


def append_event(project_id, kind, node_id="", payload=None):
    """写一条画布事件。落库不是给"在线的人"看的，是为了断线重连能补齐。"""
    con = _connect()
    cur = con.execute(
        "INSERT INTO canvas_events(project_id,kind,node_id,payload_json,created_at) "
        "VALUES(?,?,?,?,?)",
        (
            project_id,
            str(kind),
            str(node_id or ""),
            json.dumps(payload or {}, ensure_ascii=False),
            _now_iso(),
        ),
    )
    event_id = int(cur.lastrowid or 0)
    if event_id > 0:
        # 只保留最近 2000 条：够补发一次断线，也不会让表无限增长
        con.execute(
            "DELETE FROM canvas_events WHERE project_id=? AND id<=?",
            (project_id, event_id - 2000),
        )
    con.commit()
    con.close()
    return event_id


def list_events(project_id, since_id=0, limit=200):
    con = _connect()
    rows = con.execute(
        "SELECT id,kind,node_id,payload_json,created_at FROM canvas_events "
        "WHERE project_id=? AND id>? ORDER BY id LIMIT ?",
        (project_id, int(since_id or 0), max(1, min(int(limit or 200), 500))),
    ).fetchall()
    con.close()
    events = []
    for row in rows:
        try:
            payload = json.loads(row[3] or "{}")
        except (TypeError, ValueError):
            payload = {}
        events.append({
            "id": int(row[0]),
            "kind": row[1],
            "nodeId": row[2] or "",
            "payload": payload if isinstance(payload, dict) else {},
            "at": row[4],
        })
    return events


def create_generation_task(
    project_id,
    node_id,
    title="",
    kind="",
    provider="",
    model="",
    estimated_cost=0,
    cost_currency="",
):
    con = _connect()
    active = con.execute(
        "SELECT * FROM generation_tasks WHERE project_id=? AND node_id=? "
        "AND status IN ('queued','submitting','running') ORDER BY created_at DESC LIMIT 1",
        (project_id, node_id),
    ).fetchone()
    if active:
        columns = [item[0] for item in con.execute("SELECT * FROM generation_tasks LIMIT 0").description]
        active_task = dict(zip(columns, active))
        con.close()
        latest = get_generation_task(active_task["id"]) or active_task
        return {**latest, "created_new": False}
    task_id = _rid(22)
    now = _now_iso()
    attempt = int(con.execute(
        "SELECT COUNT(*) FROM generation_tasks WHERE project_id=? AND node_id=?",
        (project_id, node_id),
    ).fetchone()[0] or 0) + 1
    con.close()

    def insert(connection):
        try:
            connection.execute(
                "INSERT INTO generation_tasks("
                "id,project_id,node_id,title,kind,status,phase,progress,provider,model,"
                "provider_task_id,attempt,estimated_cost,cost_currency,error,"
                "created_at,started_at,updated_at,finished_at,local_result_url,"
                "provider_result_url"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id, project_id, node_id, title, kind, "queued", "等待调度", 0,
                    provider, model, "", attempt, float(estimated_cost or 0),
                    str(cost_currency or ""), "", now, "", now, "", "", "",
                ),
            )
        except sqlite3.IntegrityError:
            row = connection.execute(
                "SELECT * FROM generation_tasks WHERE project_id=? AND node_id=? "
                "AND status IN ('queued','submitting','running') ORDER BY created_at DESC LIMIT 1",
                (project_id, node_id),
            ).fetchone()
            columns = [
                item[0] for item in connection.execute(
                    "SELECT * FROM generation_tasks LIMIT 0"
                ).description
            ]
            return {**dict(zip(columns, row)), "created_new": False} if row else None
        return {"created": True}

    result = _run_write_transaction(insert)
    if result and not result.get("created"):
        latest = get_generation_task(result.get("id")) or result
        return {**latest, "created_new": False}
    return {**(get_generation_task(task_id) or {}), "created_new": True}


def get_generation_task(task_id):
    con = _connect()
    row = con.execute("SELECT * FROM generation_tasks WHERE id=?", (task_id,)).fetchone()
    columns = [item[0] for item in con.execute("SELECT * FROM generation_tasks LIMIT 0").description]
    con.close()
    return dict(zip(columns, row)) if row else None


def update_generation_task(task_id, **fields):
    def write(con):
        row = con.execute(
            "SELECT * FROM generation_tasks WHERE id=?", (task_id,)
        ).fetchone()
        columns = [
            item[0] for item in con.execute(
                "SELECT * FROM generation_tasks LIMIT 0"
            ).description
        ]
        current = dict(zip(columns, row)) if row else None
        updates = _merge_generation_task_update(
            current,
            {key: value for key, value in fields.items() if key in _TASK_FIELDS},
        )
        if not updates:
            return
        updates["updated_at"] = _now_iso()
        con.execute(
            "UPDATE generation_tasks SET " + ",".join(f"{key}=?" for key in updates) + " WHERE id=?",
            (*updates.values(), task_id),
        )
    _run_write_transaction(write)
    return get_generation_task(task_id)


def remember_provider_task(
    task_id,
    provider_task_id,
    *,
    project_id="",
    node_id="",
    provider="",
    model="",
):
    """Persist a recovery journal entry independently from SQLite."""
    provider_task_id = str(provider_task_id or "").strip()
    if not provider_task_id:
        raise ValueError("供应商任务号为空")
    journal_dir = os.path.join(_tenant_data_dir(), "generation-journal")
    os.makedirs(journal_dir, exist_ok=True)
    payload = {
        "taskId": str(task_id or ""),
        "providerTaskId": provider_task_id,
        "projectId": str(project_id or ""),
        "nodeId": str(node_id or ""),
        "provider": str(provider or ""),
        "model": str(model or ""),
        "savedAt": _now_iso(),
    }
    safe_name = "".join(
        ch for ch in provider_task_id
        if ch.isalnum() or ch in {"-", "_", "."}
    )[:180]
    if not safe_name:
        safe_name = _rid(24)
    path = os.path.join(journal_dir, safe_name + ".json")
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)
    return payload


def list_provider_task_journal():
    """Return durable provider-task journal entries for startup reconciliation."""
    journal_dir = os.path.join(_tenant_data_dir(), "generation-journal")
    if not os.path.isdir(journal_dir):
        return []
    entries = []
    for name in sorted(os.listdir(journal_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(journal_dir, name)
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if not str(payload.get("providerTaskId") or "").strip():
            continue
        entries.append(payload)
    return entries


def list_generation_tasks(project_id=None, limit=80):
    con = _connect()
    columns = [item[0] for item in con.execute("SELECT * FROM generation_tasks LIMIT 0").description]
    if project_id:
        rows = con.execute(
            "SELECT * FROM generation_tasks WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
            (project_id, max(1, min(int(limit or 80), 300))),
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM generation_tasks ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit or 80), 300)),),
        ).fetchall()
    con.close()
    return [dict(zip(columns, row)) for row in rows]


def generation_cost_total(since_iso):
    con = _connect()
    row = con.execute(
        "SELECT COALESCE(SUM(estimated_cost),0), "
        "COALESCE(MAX(cost_currency),''), "
        "COALESCE(GROUP_CONCAT(DISTINCT NULLIF(cost_currency,'')),'') "
        "FROM generation_tasks "
        "WHERE created_at>=? "
        "AND NOT (status='interrupted' AND "
        "(provider_task_id IS NULL OR provider_task_id=''))",
        (since_iso,),
    ).fetchone()
    con.close()
    return {
        "amount": float(row[0] or 0),
        "currency": str(row[1] or ""),
        "currencies": [
            item for item in str(row[2] or "").split(",") if item
        ],
    }


_AUDIT_SECRET_KEYS = {
    "api_key",
    "apikey",
    "secret",
    "secret_key",
    "access_key",
    "token",
    "password",
    "authorization",
    "cookie",
}


def _redact_audit_value(value):
    if isinstance(value, dict):
        return {
            key: "***" if str(key).lower() in _AUDIT_SECRET_KEYS else _redact_audit_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_audit_value(item) for item in value]
    return value


def record_audit_event(
    action,
    *,
    actor="",
    target_type="",
    target_id="",
    status="success",
    details=None,
):
    event_id = _rid(22)
    con = None
    try:
        con = _connect()
        con.execute(
            "INSERT INTO audit_events("
            "id,tenant_id,actor,action,target_type,target_id,status,details_json,created_at"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                tenant_context.current_tenant_id(),
                str(actor or ""),
                str(action or "")[:120],
                str(target_type or "")[:80],
                str(target_id or "")[:180],
                str(status or "")[:32],
                json.dumps(_redact_audit_value(details or {}), ensure_ascii=False)[:12000],
                _now_iso(),
            ),
        )
        con.commit()
        return event_id
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return ""
    finally:
        if con is not None:
            con.close()


def list_audit_events(limit=200):
    con = _connect()
    rows = con.execute(
        "SELECT id,tenant_id,actor,action,target_type,target_id,status,details_json,created_at "
        "FROM audit_events ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (max(1, min(int(limit or 200), 1000)),),
    ).fetchall()
    con.close()
    events = []
    for row in rows:
        try:
            details = json.loads(row[7] or "{}")
        except (TypeError, json.JSONDecodeError):
            details = {}
        events.append({
            "id": row[0],
            "tenantId": row[1],
            "actor": row[2],
            "action": row[3],
            "targetType": row[4],
            "targetId": row[5],
            "status": row[6],
            "details": details,
            "createdAt": row[8],
        })
    return events


def runtime_metrics():
    con = _connect()
    project_count = int(con.execute("SELECT COUNT(*) FROM canvas_projects").fetchone()[0] or 0)
    snapshot_count = int(con.execute("SELECT COUNT(*) FROM canvas_snapshots").fetchone()[0] or 0)
    snapshot_bytes = int(con.execute(
        "SELECT COALESCE(SUM(LENGTH(payload_json)),0) FROM canvas_snapshots"
    ).fetchone()[0] or 0)
    audit_count = int(con.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] or 0)
    task_rows = con.execute(
        "SELECT status,COUNT(*) FROM generation_tasks GROUP BY status"
    ).fetchall()
    oldest_active = con.execute(
        "SELECT MIN(created_at) FROM generation_tasks "
        "WHERE status IN ('queued','submitting','running')"
    ).fetchone()[0]
    con.close()
    tasks = {str(row[0] or ""): int(row[1] or 0) for row in task_rows}
    return {
        "projects": project_count,
        "snapshots": snapshot_count,
        "snapshotBytes": snapshot_bytes,
        "auditEvents": audit_count,
        "tasks": tasks,
        "activeTasks": sum(tasks.get(status, 0) for status in ("queued", "submitting", "running")),
        "oldestActiveTaskAt": oldest_active or "",
        "databaseBytes": os.path.getsize(_db_path()) if os.path.exists(_db_path()) else 0,
    }


def list_snapshot_payloads(project_id, limit=30):
    con = _connect()
    rows = con.execute(
        "SELECT revision,created_at,payload_json FROM canvas_snapshots "
        "WHERE canvas_id=? ORDER BY revision DESC LIMIT ?",
        (project_id, max(1, min(int(limit or 30), 100))),
    ).fetchall()
    con.close()
    payloads = []
    for revision, created_at, raw in reversed(rows):
        try:
            payloads.append({
                "revision": revision,
                "createdAt": created_at,
                "payload": json.loads(raw),
            })
        except (TypeError, json.JSONDecodeError):
            continue
    return payloads


def list_snapshots(project_id, limit=50):
    con = _connect()
    rows = con.execute(
        "SELECT id,title,node_count,connection_count,revision,kind,created_at "
        "FROM canvas_snapshots WHERE canvas_id=? "
        "ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (project_id, max(1, min(int(limit or 50), 200))),
    ).fetchall()
    con.close()
    return [
        {
            "id": row[0],
            "title": row[1],
            "nodeCount": row[2],
            "connectionCount": row[3],
            "revision": row[4],
            "kind": row[5] or "auto",
            "createdAt": row[6],
        }
        for row in rows
    ]


def get_snapshot(project_id, snapshot_id):
    con = _connect()
    row = con.execute(
        "SELECT id,title,node_count,connection_count,payload_json,revision,kind,created_at "
        "FROM canvas_snapshots WHERE canvas_id=? AND id=?",
        (project_id, snapshot_id),
    ).fetchone()
    con.close()
    if not row:
        return None
    try:
        payload = json.loads(row[4])
    except (TypeError, json.JSONDecodeError):
        return None
    return {
        "id": row[0],
        "title": row[1],
        "nodeCount": row[2],
        "connectionCount": row[3],
        "payload": payload,
        "revision": row[5],
        "kind": row[6] or "auto",
        "createdAt": row[7],
    }


def create_snapshot(project_id, title=None, kind="manual"):
    con = _connect()
    row = con.execute(
        "SELECT title,payload_json,revision FROM canvas_projects WHERE id=?",
        (project_id,),
    ).fetchone()
    if not row:
        con.close()
        return None
    snapshot_title = title or row[0] or "项目版本"
    payload = json.loads(row[1])
    _snapshot(
        con,
        project_id,
        snapshot_title,
        payload,
        int(row[2]),
        kind=kind,
    )
    snapshot = con.execute(
        "SELECT id FROM canvas_snapshots WHERE canvas_id=? ORDER BY rowid DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    con.commit()
    con.close()
    return get_snapshot(project_id, snapshot[0]) if snapshot else None


def interrupt_orphan_generation_tasks():
    """Close persisted active rows after a restart without inventing certainty.

    A row with no provider task id may be an interrupted pre-submit task, or
    it may be a synchronous image request that already returned and is still
    finalizing. Startup cannot distinguish those from the task row alone, so
    list it for the runtime reconciler instead of blanket-failing paid work.
    """
    now = _now_iso()
    con = _connect()
    con.execute(
        "UPDATE generation_tasks SET phase='等待恢复',"
        "error=CASE WHEN error IS NULL OR error='' THEN '后端重启，正在核对供应商结果' ELSE error END,"
        "updated_at=? "
        "WHERE status IN ('queued','submitting','running') "
        "AND (provider_task_id IS NULL OR provider_task_id='')",
        (now,),
    )
    con.commit()
    con.close()


# 每个画布保留的快照条数上限。
#
# ★ 为什么必须限制（2026-10-25 第二次踩同一个坑）：
#   `update_project` 每次保存都整包插一条快照，而本项目一次拖动/一次引用变更就是
#   一次 update_project。实测 accumulate 到 1693 条 / 109MB 的 payload（库涨到 174MB），
#   读接口因为要扫这张表而变慢，最坏直接超时。
#   上一轮只是**手工** DELETE + VACUUM 清到 3.1MB —— 代码没改，所以又涨回去了。
#   这里改成写入后自动裁剪，才是真修。
#
# 为什么是 30：快照只为「误删/误改兜底」，不是版本历史。
#   30 条足够覆盖一次连续误操作的回退窗口，同时把库稳定在几 MB 量级。
AUTO_SNAPSHOT_KEEP = 30
AUTO_SNAPSHOT_MIN_INTERVAL_SECONDS = 30
AUTO_SNAPSHOT_MIN_REVISIONS = 20

MAX_VIDEO_VERSIONS = 20


def backfill_video_version_history():
    """Recover distinct generated media URLs from existing snapshots.

    Older nodes only kept the latest content URL. Snapshots still contain earlier
    completed images, audio and videos, so this idempotent pass restores
    params.mediaVersions (and legacy videoVersions) without changing prompts or
    positions.
    """
    updated_projects = 0
    for project_row in list_projects():
        pid = project_row.get("id")
        current = get_project(pid)
        if not current:
            continue
        payload = current["payload"]
        media_ids = {
            node.get("id")
            for node in payload.get("nodes", [])
            if node.get("type") in {"image", "audio", "video"} and node.get("id")
        }
        if not media_ids:
            continue
        type_by_id = {
            node.get("id"): node.get("type")
            for node in payload.get("nodes", [])
            if node.get("id") in media_ids
        }
        con = _connect()
        rows = con.execute(
            "SELECT created_at,payload_json FROM canvas_snapshots "
            "WHERE canvas_id=? ORDER BY revision ASC",
            (pid,),
        ).fetchall()
        con.close()
        recovered: dict[str, list[dict]] = {nid: [] for nid in media_ids}
        seen: dict[str, set[str]] = {nid: set() for nid in media_ids}
        for created_at, raw_payload in rows:
            try:
                snapshot = json.loads(raw_payload)
            except (TypeError, json.JSONDecodeError):
                continue
            for node in snapshot.get("nodes", []):
                nid = node.get("id")
                if nid not in media_ids:
                    continue
                meta = node.get("metadata") or {}
                url = str(meta.get("content") or "").strip()
                if not url or url in seen[nid]:
                    continue
                production = meta.get("production") or {}
                recovered[nid].append({
                    "url": url,
                    "taskId": str(meta.get("taskId") or production.get("taskId") or ""),
                    "generatedAt": str(production.get("generatedAt") or created_at or ""),
                    "kind": type_by_id.get(nid, ""),
                    "provider": str(meta.get("provider") or production.get("provider") or ""),
                    "model": str(meta.get("model") or production.get("model") or ""),
                })
                seen[nid].add(url)
        changed = False
        for node in payload.get("nodes", []):
            nid = node.get("id")
            if nid not in media_ids:
                continue
            meta = node.setdefault("metadata", {})
            params = meta.setdefault("params", {})
            node_type = node.get("type")
            existing = params.get("mediaVersions")
            if not existing and node_type == "video":
                existing = params.get("videoVersions")
            if isinstance(existing, list) and existing:
                versions = [
                    item for item in existing
                    if isinstance(item, dict) and str(item.get("url") or "").strip()
                ][-MAX_VIDEO_VERSIONS:]
            else:
                versions = recovered.get(nid, [])
            current_url = str(meta.get("content") or "").strip()
            if current_url and not any(str(item.get("url") or "") == current_url for item in versions):
                production = meta.get("production") or {}
                versions.append({
                    "url": current_url,
                    "taskId": str(meta.get("taskId") or production.get("taskId") or ""),
                    "generatedAt": str(production.get("generatedAt") or ""),
                    "kind": node_type or "",
                    "provider": str(meta.get("provider") or production.get("provider") or ""),
                    "model": str(meta.get("model") or production.get("model") or ""),
                })
            versions = versions[-MAX_VIDEO_VERSIONS:]
            if versions and (
                params.get("mediaVersions") != versions
                or params.get("mediaVersionIndex") != len(versions) - 1
                or (node_type == "video" and params.get("videoVersions") != versions)
            ):
                params["mediaVersions"] = versions
                params["mediaVersionIndex"] = len(versions) - 1
                if node_type == "video":
                    params["videoVersions"] = [dict(item) for item in versions]
                    params["videoVersionIndex"] = len(versions) - 1
                changed = True
        if changed:
            update_project(pid, payload=payload)
            updated_projects += 1
    return updated_projects


def _snapshot(con, pid, title, payload, revision, kind="auto"):
    sid = secrets.token_hex(16)
    con.execute(
        "INSERT OR REPLACE INTO canvas_snapshots("
        "id,canvas_id,title,node_count,connection_count,payload_json,revision,kind,created_at"
        ") VALUES(?,?,?,?,?,?,?,?,?)",
        (sid, pid, title, len(payload.get("nodes", [])), len(payload.get("connections", [])),
         json.dumps(payload, ensure_ascii=False), revision, kind, _now_iso()),
    )
    # 自动快照按数量裁剪；用户手动钉住的版本不能进入这条裁剪链。
    con.execute(
        "DELETE FROM canvas_snapshots WHERE canvas_id=? AND kind='auto' AND id NOT IN ("
        "  SELECT id FROM canvas_snapshots WHERE canvas_id=? AND kind='auto' "
        "  ORDER BY revision DESC, created_at DESC LIMIT ?"
        ")",
        (pid, pid, AUTO_SNAPSHOT_KEEP),
    )


def _maybe_snapshot(con, pid, title, payload, revision, mode="auto", now=None):
    if mode == "none":
        return
    if mode == "manual":
        _snapshot(con, pid, title, payload, revision, kind="manual")
        return
    latest = con.execute(
        "SELECT revision,created_at FROM canvas_snapshots "
        "WHERE canvas_id=? AND kind='auto' ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (pid,),
    ).fetchone()
    if not latest:
        _snapshot(con, pid, title, payload, revision, kind="auto")
        return
    revision_gap = int(revision) - int(latest[0] or 0)
    if revision_gap >= AUTO_SNAPSHOT_MIN_REVISIONS:
        _snapshot(con, pid, title, payload, revision, kind="auto")
        return
    try:
        created = datetime.fromisoformat(
            str(latest[1]).replace("Z", "+00:00")
        )
        current = datetime.fromisoformat(
            str(now or _now_iso()).replace("Z", "+00:00")
        )
        age = (current - created).total_seconds()
    except (TypeError, ValueError):
        age = AUTO_SNAPSHOT_MIN_INTERVAL_SECONDS
    if age >= AUTO_SNAPSHOT_MIN_INTERVAL_SECONDS:
        _snapshot(con, pid, title, payload, revision, kind="auto")
