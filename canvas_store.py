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


def _db_path() -> str:
    context = tenant_context.current()
    if context is not None:
        return str(context.db_path)
    return str(DB_PATH)


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
                error TEXT,
                created_at TEXT,
                started_at TEXT,
                updated_at TEXT,
                finished_at TEXT
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
            task_columns = {
                row[1] for row in con.execute("PRAGMA table_info(generation_tasks)")
            }
            if "attempt" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN attempt INTEGER DEFAULT 1"
                )
            con.commit()
            _SCHEMA_READY_PATHS.add(path)
            _SCHEMA_READY = True
    return con


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
    _snapshot(con, pid, title, payload, 1)
    con.commit()
    con.close()
    return pid


def update_project(pid, title=None, payload=None, revision=None):
    con = _connect()
    row = con.execute("SELECT title, payload_json, revision FROM canvas_projects WHERE id=?", (pid,)).fetchone()
    if not row:
        con.close()
        return False
    new_title = title if title is not None else row[0]
    new_payload = json.loads(row[1]) if payload is None else payload
    new_rev = (row[2] + 1) if revision is None else revision
    now = _now_iso()
    con.execute(
        "UPDATE canvas_projects SET title=?, payload_json=?, revision=?, updated_at=? WHERE id=?",
        (new_title, json.dumps(new_payload, ensure_ascii=False), new_rev, now, pid),
    )
    _snapshot(con, pid, new_title, new_payload, new_rev)
    con.commit()
    con.close()
    return True


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
}


def create_generation_task(project_id, node_id, title="", kind="", provider="", model=""):
    con = _connect()
    active = con.execute(
        "SELECT * FROM generation_tasks WHERE project_id=? AND node_id=? "
        "AND status IN ('queued','submitting','running') ORDER BY created_at DESC LIMIT 1",
        (project_id, node_id),
    ).fetchone()
    if active:
        columns = [item[0] for item in con.execute("SELECT * FROM generation_tasks LIMIT 0").description]
        con.close()
        return {**dict(zip(columns, active)), "created_new": False}
    task_id = _rid(22)
    now = _now_iso()
    attempt = int(con.execute(
        "SELECT COUNT(*) FROM generation_tasks WHERE project_id=? AND node_id=?",
        (project_id, node_id),
    ).fetchone()[0] or 0) + 1
    try:
        con.execute(
            "INSERT INTO generation_tasks("
            "id,project_id,node_id,title,kind,status,phase,progress,provider,model,"
            "provider_task_id,attempt,error,created_at,started_at,updated_at,finished_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                task_id, project_id, node_id, title, kind, "queued", "等待调度", 0,
                provider, model, "", attempt, "", now, "", now, "",
            ),
        )
    except sqlite3.IntegrityError:
        con.rollback()
        row = con.execute(
            "SELECT * FROM generation_tasks WHERE project_id=? AND node_id=? "
            "AND status IN ('queued','submitting','running') ORDER BY created_at DESC LIMIT 1",
            (project_id, node_id),
        ).fetchone()
        columns = [item[0] for item in con.execute("SELECT * FROM generation_tasks LIMIT 0").description]
        con.close()
        return ({**dict(zip(columns, row)), "created_new": False} if row else None)
    con.commit()
    con.close()
    return {**(get_generation_task(task_id) or {}), "created_new": True}


def get_generation_task(task_id):
    con = _connect()
    row = con.execute("SELECT * FROM generation_tasks WHERE id=?", (task_id,)).fetchone()
    columns = [item[0] for item in con.execute("SELECT * FROM generation_tasks LIMIT 0").description]
    con.close()
    return dict(zip(columns, row)) if row else None


def update_generation_task(task_id, **fields):
    updates = {key: value for key, value in fields.items() if key in _TASK_FIELDS}
    if not updates:
        return get_generation_task(task_id)
    updates["updated_at"] = _now_iso()
    con = _connect()
    con.execute(
        "UPDATE generation_tasks SET " + ",".join(f"{key}=?" for key in updates) + " WHERE id=?",
        (*updates.values(), task_id),
    )
    con.commit()
    con.close()
    return get_generation_task(task_id)


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


def interrupt_orphan_generation_tasks():
    now = _now_iso()
    con = _connect()
    con.execute(
        "UPDATE generation_tasks SET status='interrupted',phase='服务中断',"
        "error=CASE WHEN error IS NULL OR error='' THEN '后端重启，任务未取得供应商任务 ID' ELSE error END,"
        "finished_at=?,updated_at=? "
        "WHERE status IN ('queued','submitting','running') "
        "AND (provider_task_id IS NULL OR provider_task_id='')",
        (now, now),
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
SNAPSHOT_KEEP = 30

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


def _snapshot(con, pid, title, payload, revision):
    sid = secrets.token_hex(16)
    con.execute(
        "INSERT OR REPLACE INTO canvas_snapshots(id,canvas_id,title,node_count,connection_count,payload_json,revision,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (sid, pid, title, len(payload.get("nodes", [])), len(payload.get("connections", [])),
         json.dumps(payload, ensure_ascii=False), revision, _now_iso()),
    )
    # 裁剪：只保留该画布最近 N 条，其余删除。
    # 用 NOT IN (子查询取最新 N 条) 而不是 `revision <= max-N`，
    # 因为 revision 可能不连续（批量导入/快照还原会跳号），按条数裁更可靠。
    con.execute(
        "DELETE FROM canvas_snapshots WHERE canvas_id=? AND id NOT IN ("
        "  SELECT id FROM canvas_snapshots WHERE canvas_id=? ORDER BY revision DESC LIMIT ?"
        ")",
        (pid, pid, SNAPSHOT_KEEP),
    )
