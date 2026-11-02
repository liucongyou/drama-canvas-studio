# -*- coding: utf-8 -*-
"""canvas_store.py — 画布项目存取（SQLite · payload 整包模式，对齐 BeefTV schema）"""
import json
import os
import secrets
import sqlite3
import string
import time
from datetime import datetime, timedelta, timezone

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
# readonly 属于瞬态竞态，重试 4 次（约 1.15s）足够；超过就当真的没有写权限。
_MAX_READONLY_RETRIES = 4
# WAL 自动 checkpoint 默认每约 1000 页触发一次，并且会撞在业务提交线程里。
# 实测同一 INSERT 平时约 17ms，触发 checkpoint 时会突然到 0.2~2s。改为关闭
# 自动触发，由后台维护线程在受控窗口做 PASSIVE checkpoint；保留硬上限，
# 即使维护线程停摆也不会让 WAL 无限增长。
_WAL_AUTOCHECKPOINT_PAGES = 0
_WAL_SOFT_LIMIT_BYTES = 16 * 1024 * 1024
_WAL_LAST_MAINTENANCE: dict[str, float] = {}
_WAL_MAINTENANCE_INTERVAL_SECONDS = 30.0
_UNASSIGNED_EPISODE_ID = "__UNASSIGNED__"


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
    con.execute(
        f"PRAGMA wal_autocheckpoint={_WAL_AUTOCHECKPOINT_PAGES}"
    )
    if path not in _WAL_READY_PATHS:
        with _SCHEMA_LOCK:
            if path not in _WAL_READY_PATHS:
                con.execute("PRAGMA journal_mode=WAL")
                con.execute("PRAGMA synchronous=NORMAL")
                _WAL_READY_PATHS.add(path)
    if path not in _SCHEMA_READY_PATHS:
        with _SCHEMA_LOCK:
            # Double-check under the lock: concurrent first connections must
            # run the DDL/migrations once, not once per thread.
            if path in _SCHEMA_READY_PATHS:
                return con
            con.execute(
            """CREATE TABLE IF NOT EXISTS canvas_projects(
                id TEXT PRIMARY KEY,
                user_id TEXT,
                title TEXT,
                payload_json TEXT,
                revision INTEGER DEFAULT 1,
                created_at TEXT,
                updated_at TEXT,
                node_count INTEGER DEFAULT 0,
                connection_count INTEGER DEFAULT 0,
                episode_count INTEGER DEFAULT 1,
                failed_count INTEGER DEFAULT 0,
                local_blocked_count INTEGER DEFAULT 0
            )"""
            )
            con.execute(
            """CREATE TABLE IF NOT EXISTS canvas_meta(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )"""
            )
            con.execute(
            """CREATE TABLE IF NOT EXISTS canvas_project_meta(
                project_id TEXT PRIMARY KEY,
                meta_json TEXT DEFAULT '{}',
                updated_at TEXT DEFAULT ''
            )"""
            )
            con.execute(
            """CREATE TABLE IF NOT EXISTS canvas_projection_nodes(
                project_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                episode_id TEXT NOT NULL,
                node_type TEXT NOT NULL,
                title TEXT DEFAULT '',
                position_x REAL DEFAULT 0,
                position_y REAL DEFAULT 0,
                position_updated_at TEXT DEFAULT '',
                metadata_json TEXT DEFAULT '{}',
                node_json TEXT DEFAULT '{}',
                updated_at TEXT DEFAULT '',
                PRIMARY KEY(project_id, node_id)
            )"""
            )
            node_columns = {
                row[1]
                for row in con.execute("PRAGMA table_info(canvas_projection_nodes)")
            }
            if "episode_id" not in node_columns:
                con.execute(
                    "ALTER TABLE canvas_projection_nodes "
                    "ADD COLUMN episode_id TEXT NOT NULL DEFAULT '__UNASSIGNED__'"
                )
            con.execute(
                "UPDATE canvas_projection_nodes SET episode_id='__UNASSIGNED__' "
                "WHERE COALESCE(episode_id,'')=''"
            )
            con.execute(
                "CREATE TRIGGER IF NOT EXISTS trg_canvas_projection_nodes_episode_insert "
                "BEFORE INSERT ON canvas_projection_nodes "
                "WHEN NEW.episode_id IS NULL OR TRIM(NEW.episode_id)='' "
                "BEGIN SELECT RAISE(ABORT, 'episode_id required'); END"
            )
            con.execute(
                "CREATE TRIGGER IF NOT EXISTS trg_canvas_projection_nodes_episode_update "
                "BEFORE UPDATE OF episode_id ON canvas_projection_nodes "
                "WHEN NEW.episode_id IS NULL OR TRIM(NEW.episode_id)='' "
                "BEGIN SELECT RAISE(ABORT, 'episode_id required'); END"
            )
            con.execute("DROP INDEX IF EXISTS idx_canvas_nodes_episode")
            con.execute(
            "CREATE INDEX IF NOT EXISTS idx_canvas_projection_nodes_episode "
            "ON canvas_projection_nodes(project_id, episode_id, node_id)"
            )
            con.execute(
            """CREATE TABLE IF NOT EXISTS canvas_projection_edges(
                project_id TEXT NOT NULL,
                edge_id TEXT NOT NULL,
                from_node_id TEXT NOT NULL,
                to_node_id TEXT NOT NULL,
                source TEXT DEFAULT '',
                edge_json TEXT DEFAULT '{}',
                updated_at TEXT DEFAULT '',
                PRIMARY KEY(project_id, edge_id)
            )"""
            )
            con.execute(
            "CREATE INDEX IF NOT EXISTS idx_canvas_projection_edges_project "
            "ON canvas_projection_edges(project_id, edge_id)"
            )
            con.execute(
            """CREATE TABLE IF NOT EXISTS canvas_projection_episodes(
                project_id TEXT NOT NULL,
                episode_id TEXT NOT NULL,
                title TEXT DEFAULT '',
                group_json TEXT DEFAULT '{}',
                node_ids_json TEXT DEFAULT '[]',
                video_node_ids_json TEXT DEFAULT '[]',
                connection_ids_json TEXT DEFAULT '[]',
                viewport_json TEXT DEFAULT '',
                layout_json TEXT DEFAULT '{}',
                updated_at TEXT DEFAULT '',
                PRIMARY KEY(project_id, episode_id)
            )"""
            )
            con.execute(
            "CREATE INDEX IF NOT EXISTS idx_canvas_projection_episodes_project "
            "ON canvas_projection_episodes(project_id, episode_id)"
            )
            con.execute(
            """CREATE TABLE IF NOT EXISTS canvas_projection_notes(
                project_id TEXT NOT NULL,
                episode_id TEXT NOT NULL,
                note_id TEXT NOT NULL,
                text TEXT DEFAULT '',
                position_x REAL DEFAULT 0,
                position_y REAL DEFAULT 0,
                note_json TEXT DEFAULT '{}',
                updated_at TEXT DEFAULT '',
                PRIMARY KEY(project_id, episode_id, note_id)
            )"""
            )
            con.execute(
            "CREATE TRIGGER IF NOT EXISTS trg_canvas_projection_notes_episode_insert "
            "BEFORE INSERT ON canvas_projection_notes "
            "WHEN NEW.episode_id IS NULL OR TRIM(NEW.episode_id)='' "
            "BEGIN SELECT RAISE(ABORT, 'episode_id required'); END"
            )
            con.execute(
            "CREATE TRIGGER IF NOT EXISTS trg_canvas_projection_notes_episode_update "
            "BEFORE UPDATE OF episode_id ON canvas_projection_notes "
            "WHEN NEW.episode_id IS NULL OR TRIM(NEW.episode_id)='' "
            "BEGIN SELECT RAISE(ABORT, 'episode_id required'); END"
            )
            con.execute(
            "CREATE INDEX IF NOT EXISTS idx_canvas_projection_notes_episode "
            "ON canvas_projection_notes(project_id, episode_id, note_id)"
            )
            episode_state_columns = {
                row[1]
                for row in con.execute("PRAGMA table_info(canvas_projection_episodes)")
            }
            if "group_json" not in episode_state_columns:
                con.execute(
                    "ALTER TABLE canvas_projection_episodes "
                    "ADD COLUMN group_json TEXT DEFAULT '{}'"
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
                episode_id TEXT DEFAULT '',
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
                provider_result_url TEXT,
                request_json TEXT,
                client_operation_id TEXT,
                lease_owner TEXT,
                lease_expires_at TEXT,
                heartbeat_at TEXT,
                delivery_status TEXT DEFAULT 'pending',
                delivery_attempts INTEGER DEFAULT 0,
                result_ready_at TEXT,
                progress_updated_at TEXT DEFAULT '',
                next_poll_at TEXT DEFAULT ''
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
            "CREATE INDEX IF NOT EXISTS idx_generation_tasks_receipt "
            "ON generation_tasks(project_id, node_id, status, created_at DESC)"
            )
            con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_generation_tasks_active_node "
            "ON generation_tasks(project_id, node_id) "
            "WHERE status IN ('queued','submitting','running')"
            )
            task_columns = {
                row[1] for row in con.execute("PRAGMA table_info(generation_tasks)")
            }
            if "episode_id" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN episode_id TEXT DEFAULT ''"
                )
                task_columns.add("episode_id")
            if "provider_task_id" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN provider_task_id TEXT"
                )
                task_columns.add("provider_task_id")
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
            if "attempt" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN attempt INTEGER DEFAULT 1"
                )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_generation_tasks_episode "
                "ON generation_tasks(project_id, episode_id, created_at DESC)"
            )
            con.execute(
                "UPDATE generation_tasks SET episode_id='__UNSCOPED__' "
                "WHERE COALESCE(episode_id,'')=''"
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
            if "request_json" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN request_json TEXT"
                )
            if "client_operation_id" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN client_operation_id TEXT"
                )
            if "lease_owner" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN lease_owner TEXT"
                )
            if "lease_expires_at" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN lease_expires_at TEXT"
                )
            if "heartbeat_at" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN heartbeat_at TEXT"
                )
            if "delivery_status" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN delivery_status TEXT DEFAULT 'pending'"
                )
            if "delivery_attempts" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN delivery_attempts INTEGER DEFAULT 0"
                )
            if "result_ready_at" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN result_ready_at TEXT"
                )
            if "progress_updated_at" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN progress_updated_at TEXT DEFAULT ''"
                )
            if "updated_at" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN updated_at TEXT"
                )
                task_columns.add("updated_at")
            if "created_at" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN created_at TEXT"
                )
                task_columns.add("created_at")
            if "next_poll_at" not in task_columns:
                con.execute(
                    "ALTER TABLE generation_tasks ADD COLUMN next_poll_at TEXT DEFAULT ''"
                )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_generation_tasks_active_lease "
                "ON generation_tasks(status, lease_expires_at) "
                "WHERE status IN ('queued','submitting','running')"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_generation_tasks_next_poll "
                "ON generation_tasks(next_poll_at) "
                "WHERE provider_task_id IS NOT NULL "
                "AND provider_task_id != '' "
                "AND status IN ('queued','submitting','running','interrupted')"
            )
            con.execute(
                "UPDATE generation_tasks SET next_poll_at=COALESCE("
                "NULLIF(next_poll_at,''), updated_at, created_at, '') "
                "WHERE COALESCE(next_poll_at,'')='' "
                "AND provider_task_id IS NOT NULL AND provider_task_id!='' "
                "AND status IN ('queued','submitting','running','interrupted')"
            )
            con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_generation_tasks_operation "
                "ON generation_tasks(client_operation_id) "
                "WHERE client_operation_id IS NOT NULL AND client_operation_id != ''"
            )
            snapshot_columns = {
                row[1] for row in con.execute("PRAGMA table_info(canvas_snapshots)")
            }
            if "kind" not in snapshot_columns:
                con.execute(
                    "ALTER TABLE canvas_snapshots ADD COLUMN kind TEXT DEFAULT 'auto'"
                )
            project_columns = {
                row[1] for row in con.execute("PRAGMA table_info(canvas_projects)")
            }
            for column, default in (
                ("node_count", 0),
                ("connection_count", 0),
                ("episode_count", 1),
                ("failed_count", 0),
                ("local_blocked_count", 0),
            ):
                if column not in project_columns:
                    con.execute(
                        f"ALTER TABLE canvas_projects ADD COLUMN {column} "
                        f"INTEGER DEFAULT {default}"
                    )
            stats_version = "project_stats_v4"
            stats_migrated = con.execute(
                "SELECT 1 FROM canvas_meta WHERE key=?",
                (stats_version,),
            ).fetchone()
            # SQLite materializes defaults for existing rows, so checking NULL
            # cannot distinguish an old project from a genuinely empty one.
            # Use an explicit migration marker and backfill the payload-derived
            # counters exactly once.
            if not stats_migrated:
                rows = con.execute(
                    "SELECT id,payload_json FROM canvas_projects"
                ).fetchall()
                for project_id, raw_payload in rows:
                    try:
                        payload = json.loads(raw_payload or "{}")
                    except (TypeError, ValueError):
                        payload = {}
                    con.execute(
                        "UPDATE canvas_projects SET node_count=?,"
                        "connection_count=?,episode_count=?,failed_count=?,"
                        "local_blocked_count=? WHERE id=?",
                        (*payload_stats(payload), project_id),
                    )
                con.execute(
                    "INSERT INTO canvas_meta(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (stats_version, "1"),
                )
            projection_version = "canvas_projection_v1"
            projection_migrated = con.execute(
                "SELECT 1 FROM canvas_meta WHERE key=?",
                (projection_version,),
            ).fetchone()
            if not projection_migrated:
                rows = con.execute(
                    "SELECT id,payload_json FROM canvas_projects"
                ).fetchall()
                for project_id, raw_payload in rows:
                    try:
                        payload = json.loads(raw_payload or "{}")
                    except (TypeError, ValueError):
                        payload = {}
                    sync_payload_projection(
                        con,
                        project_id,
                        payload,
                        updated_at=_now_iso(),
                    )
                con.execute(
                    "INSERT INTO canvas_meta(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (projection_version, "1"),
                )
            notes_migration = "canvas_projection_notes_v1"
            notes_migrated = con.execute(
                "SELECT 1 FROM canvas_meta WHERE key=?",
                (notes_migration,),
            ).fetchone()
            if not notes_migrated:
                rows = con.execute(
                    "SELECT id,payload_json FROM canvas_projects"
                ).fetchall()
                for project_id, raw_payload in rows:
                    try:
                        payload = json.loads(raw_payload or "{}")
                    except (TypeError, ValueError):
                        payload = {}
                    sync_legacy_project_notes(
                        con,
                        project_id,
                        payload,
                    )
                con.execute(
                    "INSERT INTO canvas_meta(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (notes_migration, "1"),
                )
            con.commit()
            _SCHEMA_READY_PATHS.add(path)
            _SCHEMA_READY = True
    return con


def _is_readonly_write_error(exc: sqlite3.OperationalError) -> bool:
    return "readonly" in str(exc).lower()


def _is_retryable_write_error(exc: sqlite3.OperationalError) -> bool:
    """写失败是否值得重试。

    `readonly` 看起来完全不像"忙"，但在 Windows + WAL 下它确实是并发打开数据库的
    瞬态结果：20 线程同时写同一个库，300 次写里实测有 2 次抛
    "attempt to write a readonly database"，紧接着重试就成功（文件存在、目录可写）。
    旧实现只认 locked/busy，这两次会直接冒到接口变成 500，用户看到"保存失败"，
    但再点一次又能存上——正好是这种"偶发失败"的手感。
    """
    message = str(exc).lower()
    if "database is locked" in message or "database is busy" in message:
        return True
    return _is_readonly_write_error(exc)


def payload_stats(payload):
    if not isinstance(payload, dict):
        return 0, 0, 1, 0, 0
    nodes = payload.get("nodes")
    connections = payload.get("connections")
    episodes = payload.get("episodeImports")
    failed_count = 0
    local_blocked_count = 0
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            metadata = node.get("metadata")
            if not isinstance(metadata, dict):
                continue
            if str(metadata.get("status") or "") != "failed":
                continue
            if is_local_validation_error(metadata.get("lastError")):
                local_blocked_count += 1
            else:
                failed_count += 1
    return (
        len(nodes) if isinstance(nodes, list) else 0,
        len(connections) if isinstance(connections, list) else 0,
        max(1, len(episodes)) if isinstance(episodes, list) else 1,
        failed_count,
        local_blocked_count,
    )


def sync_payload_projection(
    con,
    project_id,
    payload,
    *,
    updated_at="",
):
    """Keep relational read models aligned with the legacy payload copy."""
    if not isinstance(payload, dict):
        return
    stamp = str(updated_at or _now_iso())
    nodes = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
    edges = (
        payload.get("connections")
        if isinstance(payload.get("connections"), list)
        else []
    )
    episode_groups = (
        payload.get("episodeImports")
        if isinstance(payload.get("episodeImports"), list)
        else []
    )
    sync_projection_meta(
        con,
        project_id,
        payload,
        updated_at=stamp,
    )
    memberships_by_node = {}
    for group in episode_groups:
        if not isinstance(group, dict):
            continue
        episode_id = str(group.get("id") or "").strip().upper()
        for node_id in group.get("nodeIds") or []:
            node_id = str(node_id or "")
            if node_id and episode_id:
                memberships_by_node.setdefault(node_id, []).append(episode_id)
    for node in nodes:
        if not isinstance(node, dict) or not node.get("id"):
            continue
        node_id = str(node["id"])
        metadata = node.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            node["metadata"] = metadata
        candidates = list(dict.fromkeys(memberships_by_node.get(node_id, [])))
        explicit_owner = str(
            node.get("episodeId") or metadata.get("episodeId") or ""
        ).strip().upper()
        owner = (
            explicit_owner
            if explicit_owner
            else (candidates[0] if len(candidates) == 1 else "")
        )
        sync_projection_node(
            con,
            project_id,
            node,
            episode_id=owner,
            updated_at=stamp,
        )
    node_ids = {
        str(node.get("id"))
        for node in nodes
        if isinstance(node, dict) and node.get("id")
    }
    edge_ids = {
        str(edge.get("id"))
        for edge in edges
        if isinstance(edge, dict) and edge.get("id")
    }
    if node_ids:
        placeholders = ",".join("?" for _ in node_ids)
        con.execute(
            f"DELETE FROM canvas_projection_nodes WHERE project_id=? "
            f"AND node_id NOT IN ({placeholders})",
            (project_id, *sorted(node_ids)),
        )
    else:
        con.execute("DELETE FROM canvas_projection_nodes WHERE project_id=?", (project_id,))
    existing_edges = {
        str(row[0])
        for row in con.execute(
            "SELECT edge_id FROM canvas_projection_edges WHERE project_id=?",
            (project_id,),
        )
    }
    current_edges = {str(edge.get("id")) for edge in edges if isinstance(edge, dict) and edge.get("id")}
    if existing_edges != current_edges:
        for edge in edges:
            sync_projection_edge(con, project_id, edge, updated_at=stamp)
    for edge in edges:
        sync_projection_edge(con, project_id, edge, updated_at=stamp)
    if edge_ids:
        placeholders = ",".join("?" for _ in edge_ids)
        con.execute(
            f"DELETE FROM canvas_projection_edges WHERE project_id=? "
            f"AND edge_id NOT IN ({placeholders})",
            (project_id, *sorted(edge_ids)),
        )
    else:
        con.execute("DELETE FROM canvas_projection_edges WHERE project_id=?", (project_id,))
    viewports = (
        payload.get("episodeViewports")
        if isinstance(payload.get("episodeViewports"), dict)
        else {}
    )
    layouts = (
        payload.get("episodeLayouts")
        if isinstance(payload.get("episodeLayouts"), dict)
        else {}
    )
    episode_ids = set()
    for group in episode_groups:
        if not isinstance(group, dict):
            continue
        episode_id = str(group.get("id") or "").strip().upper()
        if not episode_id:
            continue
        episode_ids.add(episode_id)
        sync_projection_episode(
            con,
            project_id,
            episode_id,
            group,
            viewport_json=(
                json.dumps(viewports.get(episode_id), ensure_ascii=False)
                if episode_id in viewports
                else ""
            ),
            layout_json=json.dumps(
                layouts.get(episode_id) or {},
                ensure_ascii=False,
            ),
            updated_at=stamp,
        )
    if episode_ids:
        placeholders = ",".join("?" for _ in episode_ids)
        con.execute(
            f"DELETE FROM canvas_projection_episodes WHERE project_id=? "
            f"AND episode_id NOT IN ({placeholders})",
            (project_id, *sorted(episode_ids)),
        )
    else:
        con.execute(
            "DELETE FROM canvas_projection_episodes WHERE project_id=?",
            (project_id,),
        )


def _projection_node_values(project_id, node, *, episode_id="", stamp=""):
    metadata = node.get("metadata")
    position = node.get("position")
    if not isinstance(metadata, dict):
        metadata = {}
    if not isinstance(position, dict):
        position = {}
    return (
        project_id,
        str(node.get("id") or ""),
        str(
            episode_id
            or node.get("episodeId")
            or metadata.get("episodeId")
            or _UNASSIGNED_EPISODE_ID
        ).upper(),
        str(node.get("type") or "text"),
        str(node.get("title") or ""),
        float(position.get("x") or 0),
        float(position.get("y") or 0),
        str(node.get("positionUpdatedAt") or ""),
        json.dumps(metadata, ensure_ascii=False),
        json.dumps(node, ensure_ascii=False),
        str(node.get("updatedAt") or stamp),
    )


def _normalize_note(note, episode_id=""):
    if not isinstance(note, dict) or not note.get("id"):
        return None
    normalized_episode = str(
        episode_id or note.get("episodeId") or ""
    ).strip().upper()
    if not normalized_episode:
        raise ValueError("保存备注必须指定所属集")
    note_id = str(note.get("id") or "").strip()
    if not note_id:
        return None
    try:
        x = float(note.get("x") or 0)
        y = float(note.get("y") or 0)
    except (TypeError, ValueError):
        raise ValueError("备注坐标必须是数字")
    value = {
        "id": note_id,
        "episodeId": normalized_episode,
        "text": str(note.get("text") or ""),
        "x": x,
        "y": y,
        "updatedAt": str(
            note.get("updatedAt")
            or _now_iso()
        ),
    }
    return value


def sync_legacy_project_notes(con, project_id, payload):
    """Migrate project-level notes once, without broadcasting them by episode."""
    existing = con.execute(
        "SELECT 1 FROM canvas_projection_notes WHERE project_id=? LIMIT 1",
        (project_id,),
    ).fetchone()
    if existing:
        return 0
    extras = payload.get("extras") if isinstance(payload, dict) else None
    notes = extras.get("notes") if isinstance(extras, dict) else None
    if not isinstance(notes, list) or not notes:
        return 0
    episode_ids = {
        str(group.get("id") or "").strip().upper()
        for group in payload.get("episodeImports") or []
        if isinstance(group, dict)
    }
    target_episode = "EP001"
    if episode_ids and target_episode not in episode_ids:
        target_episode = sorted(episode_ids)[0]
    con.execute(
        "INSERT INTO canvas_projection_episodes("
        "project_id,episode_id,title,group_json,node_ids_json,"
        "video_node_ids_json,connection_ids_json,viewport_json,layout_json,"
        "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(project_id,episode_id) DO NOTHING",
        (
            project_id,
            target_episode,
            target_episode,
            json.dumps({
                "id": target_episode,
                "title": target_episode,
                "nodeIds": [],
                "videoNodeIds": [],
                "connectionIds": [],
            }, ensure_ascii=False),
            "[]",
            "[]",
            "[]",
            "",
            "{}",
            _now_iso(),
        ),
    )
    stamp = _now_iso()
    migrated = 0
    for raw_note in notes:
        try:
            note = _normalize_note(raw_note, target_episode)
        except ValueError:
            continue
        if not note:
            continue
        con.execute(
            "INSERT INTO canvas_projection_notes("
            "project_id,episode_id,note_id,text,position_x,position_y,"
            "note_json,updated_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(project_id,episode_id,note_id) DO NOTHING",
            (
                project_id,
                target_episode,
                note["id"],
                note["text"],
                note["x"],
                note["y"],
                json.dumps(note, ensure_ascii=False),
                note["updatedAt"] or stamp,
            ),
        )
        migrated += 1
    return migrated


def get_episode_notes(project_id, episode_id):
    normalized = str(episode_id or "").strip().upper()
    if not normalized:
        raise ValueError("读取备注必须指定所属集")
    con = _connect()
    rows = con.execute(
        "SELECT note_json FROM canvas_projection_notes "
        "WHERE project_id=? AND episode_id=? ORDER BY rowid",
        (project_id, normalized),
    ).fetchall()
    con.close()
    notes = []
    for (raw,) in rows:
        try:
            note = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(note, dict) and note.get("id"):
            notes.append({**note, "episodeId": normalized})
    return notes


def replace_episode_notes(
    project_id,
    episode_id,
    notes,
    *,
    if_revision=None,
):
    normalized = str(episode_id or "").strip().upper()
    if not normalized:
        raise ValueError("保存备注必须指定所属集")
    if not isinstance(notes, list):
        raise ValueError("备注必须是数组")
    con = _connect()
    try:
        project_exists = con.execute(
            "SELECT 1 FROM canvas_projects WHERE id=?",
            (project_id,),
        ).fetchone()
        episode_exists = con.execute(
            "SELECT 1 FROM canvas_projection_episodes "
            "WHERE project_id=? AND episode_id=?",
            (project_id, normalized),
        ).fetchone()
    finally:
        con.close()
    if not project_exists:
        return None
    if not episode_exists:
        raise ValueError(f"集 {normalized} 不在当前项目中，请刷新项目后重试")
    normalized_notes = []
    seen = set()
    for raw_note in notes:
        note = _normalize_note(raw_note, normalized)
        if not note or note["id"] in seen:
            continue
        seen.add(note["id"])
        normalized_notes.append(note)

    def write(con):
        row = con.execute(
            "SELECT 1 FROM canvas_projects WHERE id=?",
            (project_id,),
        ).fetchone()
        if not row:
            return None
        current_revision = None
        if if_revision is not None:
            revision_row = con.execute(
                "SELECT revision FROM canvas_projects WHERE id=?",
                (project_id,),
            ).fetchone()
            current_revision = int(revision_row[0] or 0) if revision_row else 0
            if current_revision != int(if_revision):
                raise RevisionConflictError(
                    f"项目已更新：期望 revision={if_revision}，"
                    f"实际 revision={current_revision}"
                )
        stamp = _now_iso()
        existing_note_ids = {
            str(row[0])
            for row in con.execute(
                "SELECT note_id FROM canvas_projection_notes "
                "WHERE project_id=? AND episode_id=?",
                (project_id, normalized),
            )
        }
        next_note_ids = {note["id"] for note in normalized_notes}
        removed_note_ids = existing_note_ids - next_note_ids
        if removed_note_ids:
            placeholders = ",".join("?" for _ in removed_note_ids)
            con.execute(
                f"DELETE FROM canvas_projection_notes "
                f"WHERE project_id=? AND episode_id=? "
                f"AND note_id IN ({placeholders})",
                (project_id, normalized, *sorted(removed_note_ids)),
            )
        for note in normalized_notes:
            con.execute(
                "INSERT INTO canvas_projection_notes("
                "project_id,episode_id,note_id,text,position_x,position_y,"
                "note_json,updated_at) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(project_id,episode_id,note_id) DO UPDATE SET "
                "text=excluded.text,position_x=excluded.position_x,"
                "position_y=excluded.position_y,note_json=excluded.note_json,"
                "updated_at=excluded.updated_at",
                (
                    project_id,
                    normalized,
                    note["id"],
                    note["text"],
                    note["x"],
                    note["y"],
                    json.dumps(note, ensure_ascii=False),
                    note["updatedAt"] or stamp,
                ),
            )
        # Notes are an independent fact table. Bumping the canvas project
        # revision here made concurrent saves from different episodes collide
        # and delayed project edits for a change that never touched the graph.
        revision = current_revision
        return {
            "episodeId": normalized,
            "notes": normalized_notes,
            "revision": revision,
        }

    return _run_write_transaction(write)


def refresh_projection_episode_membership(
    con,
    project_id,
    episode_id,
    *,
    updated_at="",
):
    """Project node ownership into the compatibility episode snapshot."""
    normalized = str(episode_id or "").strip().upper()
    if not normalized or normalized == _UNASSIGNED_EPISODE_ID:
        return
    rows = con.execute(
        "SELECT node_id,node_type FROM canvas_projection_nodes "
        "WHERE project_id=? AND episode_id=? ORDER BY rowid",
        (project_id, normalized),
    ).fetchall()
    node_ids = [str(row[0]) for row in rows]
    video_ids = [str(row[0]) for row in rows if str(row[1]) == "video"]
    con.execute(
        "UPDATE canvas_projection_episodes SET node_ids_json=?,"
        "video_node_ids_json=?,updated_at=COALESCE(NULLIF(?,''),updated_at) "
        "WHERE project_id=? AND episode_id=?",
        (
            json.dumps(node_ids, ensure_ascii=False),
            json.dumps(video_ids, ensure_ascii=False),
            str(updated_at or ""),
            project_id,
            normalized,
        ),
    )
    group_row = con.execute(
        "SELECT group_json FROM canvas_projection_episodes "
        "WHERE project_id=? AND episode_id=?",
        (project_id, normalized),
    ).fetchone()
    if group_row:
        try:
            group = json.loads(group_row[0] or "{}")
        except (TypeError, ValueError):
            group = {}
        if isinstance(group, dict):
            group["id"] = normalized
            group["nodeIds"] = node_ids
            group["videoNodeIds"] = video_ids
            con.execute(
                "UPDATE canvas_projection_episodes SET group_json=? "
                "WHERE project_id=? AND episode_id=?",
                (
                    json.dumps(group, ensure_ascii=False),
                    project_id,
                    normalized,
                ),
            )


def sync_projection_node(
    con,
    project_id,
    node,
    *,
    episode_id="",
    updated_at="",
):
    """Update one node projection row without scanning the graph."""
    if not isinstance(node, dict) or not node.get("id"):
        return
    stamp = str(updated_at or _now_iso())
    previous_row = con.execute(
        "SELECT episode_id FROM canvas_projection_nodes "
        "WHERE project_id=? AND node_id=?",
        (project_id, str(node["id"])),
    ).fetchone()
    previous_owner = str(previous_row[0] or "").strip().upper() if previous_row else ""
    con.execute(
        "INSERT INTO canvas_projection_nodes("
        "project_id,node_id,episode_id,node_type,title,position_x,position_y,"
        "position_updated_at,metadata_json,node_json,updated_at"
        ") VALUES(?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(project_id,node_id) DO UPDATE SET "
        "episode_id=excluded.episode_id,node_type=excluded.node_type,"
        "title=excluded.title,position_x=excluded.position_x,"
        "position_y=excluded.position_y,"
        "position_updated_at=excluded.position_updated_at,"
        "metadata_json=excluded.metadata_json,node_json=excluded.node_json,"
        "updated_at=excluded.updated_at",
        _projection_node_values(
            project_id,
            node,
            episode_id=episode_id,
            stamp=stamp,
        ),
    )
    ownership = str(
        episode_id
        or node.get("episodeId")
        or (node.get("metadata") or {}).get("episodeId")
        or ""
    ).strip().upper()
    if not ownership:
        ownership = _UNASSIGNED_EPISODE_ID
    if ownership != _UNASSIGNED_EPISODE_ID:
        node["episodeId"] = ownership
        metadata = node.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            node["metadata"] = metadata
        metadata["episodeId"] = ownership
    if ownership != _UNASSIGNED_EPISODE_ID:
        claim_nodes_for_episode(
            con,
            project_id,
            ownership,
            [node.get("id")],
            updated_at=stamp,
        )
    if previous_owner and previous_owner != ownership:
        refresh_projection_episode_membership(
            con,
            project_id,
            previous_owner,
            updated_at=stamp,
        )
    refresh_projection_episode_membership(
        con,
        project_id,
        ownership,
        updated_at=stamp,
    )


def refresh_project_projection_stats(con, project_id, *, updated_at=""):
    node_count = int(con.execute(
        "SELECT COUNT(*) FROM canvas_projection_nodes WHERE project_id=?",
        (project_id,),
    ).fetchone()[0] or 0)
    connection_count = int(con.execute(
        "SELECT COUNT(*) FROM canvas_projection_edges WHERE project_id=?",
        (project_id,),
    ).fetchone()[0] or 0)
    episode_count = int(con.execute(
        "SELECT COUNT(*) FROM canvas_projection_episodes WHERE project_id=?",
        (project_id,),
    ).fetchone()[0] or 1)
    failed_count = int(con.execute(
        "SELECT COUNT(*) FROM canvas_projection_nodes "
        "WHERE project_id=? AND metadata_json LIKE '%\"status\"%failed%' "
        "AND metadata_json NOT LIKE '%参考素材缺失%' "
        "AND metadata_json NOT LIKE '%引用节点不存在%' "
        "AND metadata_json NOT LIKE '%提示词为空%' "
        "AND metadata_json NOT LIKE '%音色节点尚未生成%' "
        "AND metadata_json NOT LIKE '%源节点尚未生成%' "
        "AND metadata_json NOT LIKE '%原始产物转存到 TOS%' "
        "AND metadata_json NOT LIKE '%信任窗口只有%'",
        (project_id,),
    ).fetchone()[0] or 0)
    local_blocked_count = int(con.execute(
        "SELECT COUNT(*) FROM canvas_projection_nodes "
        "WHERE project_id=? AND metadata_json LIKE '%\"status\"%failed%' "
        "AND (metadata_json LIKE '%参考素材缺失%' "
        "OR metadata_json LIKE '%引用节点不存在%' "
        "OR metadata_json LIKE '%提示词为空%' "
        "OR metadata_json LIKE '%音色节点尚未生成%' "
        "OR metadata_json LIKE '%源节点尚未生成%' "
        "OR metadata_json LIKE '%原始产物转存到 TOS%' "
        "OR metadata_json LIKE '%信任窗口只有%')",
        (project_id,),
    ).fetchone()[0] or 0)
    con.execute(
        "UPDATE canvas_projects SET node_count=?,connection_count=?,"
        "episode_count=?,failed_count=?,local_blocked_count=? WHERE id=?",
        (
            node_count,
            connection_count,
            max(1, episode_count),
            failed_count,
            local_blocked_count,
            project_id,
        ),
    )
    return {
        "node_count": node_count,
        "connection_count": connection_count,
        "episode_count": max(1, episode_count),
        "failed_count": failed_count,
        "local_blocked_count": local_blocked_count,
    }


def sync_project_state(
    con,
    project_id,
    current_revision,
    *,
    nodes=None,
    edges=None,
    episode_groups=None,
    viewports=None,
    layouts=None,
    snapshot_mode="none",
):
    """Atomically update relational graph rows and revision metadata."""
    row = con.execute(
        "SELECT title,payload_json,revision FROM canvas_projects WHERE id=?",
        (project_id,),
    ).fetchone()
    if not row:
        return None
    revision = int(current_revision)
    current_revision = revision - 1
    stamp = _now_iso()
    layout_patch = None
    selected_episode = None
    if isinstance(layouts, str):
        try:
            layout_patch = json.loads(layouts)
        except (TypeError, ValueError):
            layout_patch = None
    if nodes is not None:
        replace_projection_nodes(
            con,
            project_id,
            nodes,
            updated_at=stamp,
        )
        if isinstance(layout_patch, dict):
            for node_id, position in layout_patch.items():
                if not isinstance(position, dict):
                    continue
                row = con.execute(
                    "SELECT node_json FROM canvas_projection_nodes "
                    "WHERE project_id=? AND node_id=?",
                    (project_id, str(node_id)),
                ).fetchone()
                if not row:
                    continue
                try:
                    node = json.loads(row[0] or "{}")
                except (TypeError, ValueError):
                    continue
                if not isinstance(node, dict):
                    continue
                node["position"] = position
                sync_projection_node(
                    con,
                    project_id,
                    node,
                    updated_at=stamp,
                )
    if edges is not None:
        replace_projection_edges(
            con,
            project_id,
            edges,
            updated_at=stamp,
        )
    if isinstance(layouts, str):
        layout_written = False
        if isinstance(episode_groups, list):
            selected_episode = None
            for group in episode_groups:
                if not isinstance(group, dict):
                    continue
                episode_id = str(group.get("id") or "").strip().upper()
                if episode_id:
                    cursor = con.execute(
                        "UPDATE canvas_projection_episodes SET layout_json=? "
                        "WHERE project_id=? AND episode_id=?",
                        (layouts, project_id, episode_id),
                    )
                    if cursor.rowcount:
                        selected_episode = episode_id
                        layout_written = True
                        break
        else:
            con.execute(
                "UPDATE canvas_projection_episodes SET layout_json=? "
                "WHERE project_id=? AND episode_id=("
                "SELECT episode_id FROM canvas_projection_episodes "
                "WHERE project_id=? ORDER BY episode_id LIMIT 1)",
                (layouts, project_id, project_id),
            )
            layout_written = True
        if selected_episode is None:
            selected_episode = con.execute(
                "SELECT episode_id FROM canvas_projection_episodes "
                "WHERE project_id=? ORDER BY episode_id LIMIT 1",
                (project_id,),
            ).fetchone()
            selected_episode = str(selected_episode[0]) if selected_episode else None
        if isinstance(layout_patch, dict):
            for node_id, position in layout_patch.items():
                if not isinstance(position, dict):
                    continue
                node_row = con.execute(
                    "SELECT node_json FROM canvas_projection_nodes "
                    "WHERE project_id=? AND node_id=?",
                    (project_id, str(node_id)),
                ).fetchone()
                if not node_row:
                    continue
                try:
                    node = json.loads(node_row[0] or "{}")
                except (TypeError, ValueError):
                    continue
                if isinstance(node, dict):
                    node["position"] = position
                    sync_projection_node(
                        con,
                        project_id,
                        node,
                        updated_at=stamp,
                        update_stats=False,
                    )
        if not layout_written:
            con.execute(
                "UPDATE canvas_projection_episodes SET layout_json=? "
                "WHERE project_id=? AND episode_id=("
                "SELECT episode_id FROM canvas_projection_episodes "
                "WHERE project_id=? ORDER BY episode_id LIMIT 1)",
                (layouts, project_id, project_id),
            )
    if episode_groups is not None:
        groups = episode_groups if isinstance(episode_groups, list) else []
        if selected_episode is None and groups:
            selected_episode = str(groups[0].get("id") or "").strip().upper() or None
        current_rows = {
            str(row[0]): row
            for row in con.execute(
                "SELECT episode_id,viewport_json,layout_json "
                "FROM canvas_projection_episodes WHERE project_id=?",
                (project_id,),
            )
        }
        for group in groups:
            if not isinstance(group, dict):
                continue
            episode_id = str(group.get("id") or "").strip().upper()
            if not episode_id:
                continue
            existing = current_rows.get(episode_id)
            sync_projection_episode(
                con,
                project_id,
                episode_id,
                group,
                viewport_json=(
                    existing[1]
                    if existing and existing[1]
                    else (
                        json.dumps(viewports.get(episode_id), ensure_ascii=False)
                        if isinstance(viewports, dict)
                        and episode_id in viewports
                        else ""
                    )
                ),
                layout_json=(
                    json.dumps(layouts.get(episode_id) or {}, ensure_ascii=False)
                    if isinstance(layouts, dict)
                    else (
                        layouts
                        if isinstance(layouts, str)
                        and selected_episode == episode_id
                        else (existing[2] if existing and existing[2] else "{}")
                    )
                ),
                updated_at=stamp,
            )
        keep = {
            str(group.get("id") or "").strip().upper()
            for group in groups
            if isinstance(group, dict) and group.get("id")
        }
        for episode_id in set(current_rows) - keep:
            con.execute(
                "DELETE FROM canvas_projection_episodes "
                "WHERE project_id=? AND episode_id=?",
                (project_id, episode_id),
            )
    elif viewports is not None or layouts is not None:
        current_rows = {
            str(row[0]): row
            for row in con.execute(
                "SELECT episode_id,viewport_json,layout_json "
                "FROM canvas_projection_episodes WHERE project_id=?",
                (project_id,),
            )
        }
        for episode_id, (_, current_viewport, current_layout) in current_rows.items():
            sync_projection_episode(
                con,
                project_id,
                episode_id,
                viewport_json=(
                    json.dumps(viewports.get(episode_id), ensure_ascii=False)
                    if isinstance(viewports, dict)
                    and episode_id in viewports
                    else current_viewport
                ),
                layout_json=(
                    json.dumps(layouts.get(episode_id) or {}, ensure_ascii=False)
                    if isinstance(layouts, dict)
                    and episode_id in layouts
                    else current_layout
                ),
                updated_at=stamp,
            )
    stats = None
    cursor = con.execute(
        "UPDATE canvas_projects SET revision=?,updated_at=? "
        "WHERE id=? AND revision=?",
        (revision, stamp, project_id, int(current_revision)),
    )
    if cursor.rowcount != 1:
        raise RevisionConflictError(
            f"项目写入冲突：期望 revision={int(current_revision)}，"
            f"实际 revision={con.execute('SELECT revision FROM canvas_projects WHERE id=?', (project_id,)).fetchone()[0]}"
        )
    payload = None
    if snapshot_mode != "none":
        payload = get_projection_payload(con, project_id)
        if payload:
            _maybe_snapshot(
                con,
                project_id,
                row[0],
                payload,
                revision,
                mode=snapshot_mode,
                now=stamp,
            )
    return {
        "revision": revision,
        "updated_at": stamp,
        "stats": stats,
    }


def get_projection_payload(con, project_id):
    """Build a payload from projection rows on an existing connection."""
    meta_row = con.execute(
        "SELECT meta_json FROM canvas_project_meta WHERE project_id=?",
        (project_id,),
    ).fetchone()
    project_row = con.execute(
        "SELECT id,title,revision,updated_at FROM canvas_projects WHERE id=?",
        (project_id,),
    ).fetchone()
    if not project_row:
        return None
    try:
        payload = json.loads(meta_row[0]) if meta_row else {}
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.update({
        "id": project_row[0],
        "title": project_row[1],
        "revision": project_row[2],
        "updatedAt": project_row[3],
    })
    payload["nodes"] = []
    payload["connections"] = []
    for (raw,) in con.execute(
        "SELECT node_json FROM canvas_projection_nodes "
        "WHERE project_id=? ORDER BY rowid",
        (project_id,),
    ):
        try:
            node = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(node, dict):
            payload["nodes"].append(node)
    for (raw,) in con.execute(
        "SELECT edge_json FROM canvas_projection_edges "
        "WHERE project_id=? ORDER BY rowid",
        (project_id,),
    ):
        try:
            edge = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(edge, dict):
            payload["connections"].append(edge)
    payload["episodeImports"] = []
    payload["episodeViewports"] = {}
    payload["episodeLayouts"] = {}
    membership_by_episode = {}
    for episode_id, node_id, node_type in con.execute(
        "SELECT episode_id,node_id,node_type FROM canvas_projection_nodes "
        "WHERE project_id=? AND episode_id<>? ORDER BY rowid",
        (project_id, _UNASSIGNED_EPISODE_ID),
    ):
        values = membership_by_episode.setdefault(str(episode_id), {
            "nodeIds": [],
            "videoNodeIds": [],
        })
        values["nodeIds"].append(str(node_id))
        if str(node_type) == "video":
            values["videoNodeIds"].append(str(node_id))
    for (
        episode_id,
        title,
        group_json,
        connection_ids_json,
        viewport_json,
        layout_json,
    ) in con.execute(
        "SELECT episode_id,title,group_json,"
        "connection_ids_json,viewport_json,layout_json "
        "FROM canvas_projection_episodes WHERE project_id=? "
        "ORDER BY episode_id",
        (project_id,),
    ):
        try:
            group = json.loads(group_json or "{}")
            connection_ids = json.loads(connection_ids_json or "[]")
            layout = json.loads(layout_json or "{}")
        except (TypeError, ValueError):
            group, connection_ids, layout = {}, [], {}
        if not isinstance(group, dict):
            group = {}
        membership = membership_by_episode.get(
            str(episode_id),
            {"nodeIds": [], "videoNodeIds": []},
        )
        group.update({
            "id": episode_id,
            "title": title,
            "nodeIds": membership["nodeIds"],
            "videoNodeIds": membership["videoNodeIds"],
            "connectionIds": (
                connection_ids if isinstance(connection_ids, list) else []
            ),
        })
        payload["episodeImports"].append(group)
        if viewport_json:
            try:
                payload["episodeViewports"][episode_id] = json.loads(viewport_json)
            except (TypeError, ValueError):
                pass
        if isinstance(layout, dict) and layout:
            payload["episodeLayouts"][episode_id] = layout
    if not payload["episodeImports"]:
        payload["episodeImports"] = [{
            "id": "EP001",
            "title": payload.get("title") or "画布工作流",
            "nodeIds": [
                node.get("id")
                for node in payload["nodes"]
                if node.get("id")
            ],
            "videoNodeIds": [
                node.get("id")
                for node in payload["nodes"]
                if node.get("type") == "video"
            ],
        }]
        payload.setdefault("episodeViewports", {})
        payload.setdefault("episodeLayouts", {})
    return payload


def sync_projection_edge(con, project_id, edge, *, updated_at=""):
    if not isinstance(edge, dict) or not edge.get("id"):
        return
    stamp = str(updated_at or _now_iso())
    con.execute(
        "INSERT INTO canvas_projection_edges("
        "project_id,edge_id,from_node_id,to_node_id,source,edge_json,updated_at"
        ") VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(project_id,edge_id) DO UPDATE SET "
        "from_node_id=excluded.from_node_id,to_node_id=excluded.to_node_id,"
        "source=excluded.source,edge_json=excluded.edge_json,"
        "updated_at=excluded.updated_at",
        (
            project_id,
            str(edge["id"]),
            str(edge.get("fromNodeId") or ""),
            str(edge.get("toNodeId") or ""),
            str(edge.get("source") or ""),
            json.dumps(edge, ensure_ascii=False),
            stamp,
        ),
    )


def sync_projection_episode(
    con,
    project_id,
    episode_id,
    group=None,
    *,
    viewport_json=None,
    layout_json=None,
    updated_at="",
):
    normalized = str(episode_id or "").strip().upper()
    if not normalized:
        return
    stamp = str(updated_at or _now_iso())
    if isinstance(group, dict):
        values = (
            normalized,
            str(group.get("title") or ""),
            json.dumps(group, ensure_ascii=False),
            json.dumps(group.get("nodeIds") or [], ensure_ascii=False),
            json.dumps(group.get("videoNodeIds") or [], ensure_ascii=False),
            json.dumps(group.get("connectionIds") or [], ensure_ascii=False),
        )
        con.execute(
            "INSERT INTO canvas_projection_episodes("
            "project_id,episode_id,title,group_json,node_ids_json,"
            "video_node_ids_json,connection_ids_json,viewport_json,layout_json,"
            "updated_at) VALUES(?,?,?,?,?,?,?,COALESCE(?, ''),"
            "COALESCE(?, '{}'),?) "
            "ON CONFLICT(project_id,episode_id) DO UPDATE SET "
            "title=excluded.title,group_json=excluded.group_json,"
            "node_ids_json=excluded.node_ids_json,"
            "video_node_ids_json=excluded.video_node_ids_json,"
            "connection_ids_json=excluded.connection_ids_json,"
            "viewport_json=CASE WHEN ? IS NULL THEN viewport_json "
            "ELSE excluded.viewport_json END,"
            "layout_json=CASE WHEN ? IS NULL THEN layout_json "
            "ELSE excluded.layout_json END,"
            "updated_at=excluded.updated_at",
            (
                project_id,
                *values,
                viewport_json,
                layout_json,
                stamp,
                viewport_json,
                layout_json,
            ),
        )
        return
    existing = con.execute(
        "SELECT 1 FROM canvas_projection_episodes "
        "WHERE project_id=? AND episode_id=?",
        (project_id, normalized),
    ).fetchone()
    if existing:
        con.execute(
            "UPDATE canvas_projection_episodes SET "
            "viewport_json=COALESCE(?,viewport_json),"
            "layout_json=COALESCE(?,layout_json),updated_at=? "
            "WHERE project_id=? AND episode_id=?",
            (
                viewport_json,
                layout_json,
                stamp,
                project_id,
                normalized,
            ),
        )


def claim_nodes_for_episode(
    con,
    project_id,
    episode_id,
    node_ids,
    *,
    updated_at="",
):
    """Make episode ownership exclusive for the submitted node ids."""
    normalized = str(episode_id or "").strip().upper()
    ids = {
        str(node_id)
        for node_id in (node_ids or [])
        if str(node_id or "").strip()
    }
    if not normalized or not ids:
        return
    stamp = str(updated_at or _now_iso())
    placeholders = ",".join("?" for _ in ids)
    previous_episodes = {
        str(row[0] or "").strip().upper()
        for row in con.execute(
            f"SELECT DISTINCT episode_id FROM canvas_projection_nodes "
            f"WHERE project_id=? AND node_id IN ({placeholders})",
            (project_id, *sorted(ids)),
        )
        if str(row[0] or "").strip()
    }
    con.execute(
        f"UPDATE canvas_projection_nodes SET episode_id=?,updated_at=? "
        f"WHERE project_id=? AND node_id IN ({placeholders})",
        (normalized, stamp, project_id, *sorted(ids)),
    )
    for episode in previous_episodes | {normalized}:
        refresh_projection_episode_membership(
            con,
            project_id,
            episode,
            updated_at=stamp,
        )


def sync_projection_nodes_batch(
    con,
    project_id,
    nodes,
    *,
    episode_id="",
    updated_at="",
):
    """Write node rows and their episode roster in one bounded pass."""
    items = [
        item
        for item in (nodes or [])
        if isinstance(item, dict) and item.get("id")
    ]
    if not items:
        return 0
    stamp = str(updated_at or _now_iso())
    normalized = str(episode_id or "").strip().upper()
    node_ids = sorted({str(item["id"]) for item in items})
    previous_owners = set()
    if node_ids:
        placeholders = ",".join("?" for _ in node_ids)
        previous_owners = {
            str(row[0] or "").strip().upper()
            for row in con.execute(
                f"SELECT DISTINCT episode_id FROM canvas_projection_nodes "
                f"WHERE project_id=? AND node_id IN ({placeholders})",
                (project_id, *node_ids),
            )
            if str(row[0] or "").strip()
        }
    written = 0
    for item in items:
        metadata = item.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        ownership = normalized or str(
            item.get("episodeId") or metadata.get("episodeId") or ""
        ).strip().upper() or _UNASSIGNED_EPISODE_ID
        if normalized:
            metadata["episodeId"] = normalized
            item["metadata"] = metadata
        item.setdefault("updatedAt", stamp)
        con.execute(
            "INSERT INTO canvas_projection_nodes("
            "project_id,node_id,episode_id,node_type,title,position_x,position_y,"
            "position_updated_at,metadata_json,node_json,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(project_id,node_id) DO UPDATE SET "
            "episode_id=excluded.episode_id,node_type=excluded.node_type,"
            "title=excluded.title,position_x=excluded.position_x,"
            "position_y=excluded.position_y,"
            "position_updated_at=excluded.position_updated_at,"
            "metadata_json=excluded.metadata_json,node_json=excluded.node_json,"
            "updated_at=excluded.updated_at",
            _projection_node_values(
                project_id,
                item,
                episode_id=ownership,
                stamp=stamp,
            ),
        )
        written += 1
    touched = {
        owner for owner in previous_owners | {normalized}
        if owner and owner != _UNASSIGNED_EPISODE_ID
    }
    for owner in sorted(touched):
        refresh_projection_episode_membership(
            con,
            project_id,
            owner,
            updated_at=stamp,
        )
    return written


def replace_projection_nodes(con, project_id, nodes, *, updated_at=""):
    """Replace only changed node rows; delete rows absent from the payload."""
    if not isinstance(nodes, list):
        nodes = []
    stamp = str(updated_at or _now_iso())
    keep = set()
    owners = {}
    for item in nodes:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        node_id = str(item["id"])
        keep.add(node_id)
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        owners[node_id] = str(
            item.get("episodeId") or metadata.get("episodeId") or ""
        ).upper()
        encoded = json.dumps(item, ensure_ascii=False, sort_keys=True)
        current = con.execute(
            "SELECT node_json FROM canvas_projection_nodes "
            "WHERE project_id=? AND node_id=?",
            (project_id, node_id),
        ).fetchone()
        if current:
            try:
                if json.loads(current[0] or "{}") == json.loads(encoded):
                    continue
            except (TypeError, ValueError):
                pass
        sync_projection_node(
            con,
            project_id,
            item,
            episode_id=owners[node_id],
            updated_at=stamp,
        )
    existing = {
        str(row[0])
        for row in con.execute(
            "SELECT node_id FROM canvas_projection_nodes WHERE project_id=?",
            (project_id,),
        )
    }
    stale = existing - keep
    for node_id in stale:
        con.execute(
            "DELETE FROM canvas_projection_nodes "
            "WHERE project_id=? AND node_id=?",
            (project_id, node_id),
        )


def replace_projection_edges(con, project_id, edges, *, updated_at=""):
    if not isinstance(edges, list):
        edges = []
    stamp = str(updated_at or _now_iso())
    keep = set()
    for item in edges:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        edge_id = str(item["id"])
        keep.add(edge_id)
        encoded = json.dumps(item, ensure_ascii=False, sort_keys=True)
        current = con.execute(
            "SELECT edge_json FROM canvas_projection_edges "
            "WHERE project_id=? AND edge_id=?",
            (project_id, edge_id),
        ).fetchone()
        if current:
            try:
                if json.loads(current[0] or "{}") == json.loads(encoded):
                    continue
            except (TypeError, ValueError):
                pass
        sync_projection_edge(con, project_id, item, updated_at=stamp)
    existing = {
        str(row[0])
        for row in con.execute(
            "SELECT edge_id FROM canvas_projection_edges WHERE project_id=?",
            (project_id,),
        )
    }
    for edge_id in existing - keep:
        con.execute(
            "DELETE FROM canvas_projection_edges "
            "WHERE project_id=? AND edge_id=?",
            (project_id, edge_id),
        )


def replace_projection_episodes(
    con,
    project_id,
    episode_groups,
    viewports,
    layouts,
    *,
    updated_at="",
):
    if not isinstance(episode_groups, list):
        episode_groups = []
    viewports = viewports if isinstance(viewports, dict) else {}
    layouts = layouts if isinstance(layouts, dict) else {}
    stamp = str(updated_at or _now_iso())
    keep = set()
    for group in episode_groups:
        if not isinstance(group, dict):
            continue
        episode_id = str(group.get("id") or "").strip().upper()
        if not episode_id:
            continue
        keep.add(episode_id)
        sync_projection_episode(
            con,
            project_id,
            episode_id,
            group,
            viewport_json=(
                json.dumps(viewports.get(episode_id), ensure_ascii=False)
                if episode_id in viewports
                else None
            ),
            layout_json=json.dumps(
                layouts.get(episode_id) or {},
                ensure_ascii=False,
            ),
            updated_at=stamp,
        )
    existing = {
        str(row[0])
        for row in con.execute(
            "SELECT episode_id FROM canvas_projection_episodes "
            "WHERE project_id=?",
            (project_id,),
        )
    }
    for episode_id in existing - keep:
        con.execute(
            "DELETE FROM canvas_projection_episodes "
            "WHERE project_id=? AND episode_id=?",
            (project_id, episode_id),
        )


def get_project_projection(project_id):
    """Load a project through the relational projection, not payload_json."""
    con = _connect()
    project_row = con.execute(
        "SELECT id,title,revision,updated_at FROM canvas_projects WHERE id=?",
        (project_id,),
    ).fetchone()
    if not project_row:
        con.close()
        return None
    meta_row = con.execute(
        "SELECT meta_json FROM canvas_project_meta WHERE project_id=?",
        (project_id,),
    ).fetchone()
    node_rows = con.execute(
        "SELECT node_json FROM canvas_projection_nodes WHERE project_id=? "
        "ORDER BY rowid",
        (project_id,),
    ).fetchall()
    edge_rows = con.execute(
        "SELECT edge_json FROM canvas_projection_edges WHERE project_id=? "
        "ORDER BY rowid",
        (project_id,),
    ).fetchall()
    episode_rows = con.execute(
        "SELECT episode_id,title,group_json,"
        "connection_ids_json,viewport_json,layout_json "
        "FROM canvas_projection_episodes WHERE project_id=? "
        "ORDER BY episode_id",
        (project_id,),
    ).fetchall()
    membership_rows = con.execute(
        "SELECT episode_id,node_id,node_type FROM canvas_projection_nodes "
        "WHERE project_id=? AND episode_id<>? ORDER BY rowid",
        (project_id, _UNASSIGNED_EPISODE_ID),
    ).fetchall()
    node_summary_rows = con.execute(
        "SELECT node_id,node_type FROM canvas_projection_nodes "
        "WHERE project_id=? ORDER BY rowid",
        (project_id,),
    ).fetchall()
    con.close()
    try:
        payload = json.loads(meta_row[0]) if meta_row else {}
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.update({
        "id": project_row[0],
        "title": project_row[1],
        "revision": project_row[2],
        "updatedAt": project_row[3],
    })
    payload["nodes"] = []
    payload["connections"] = []
    for row in node_rows:
        try:
            node = json.loads(row[0])
        except (TypeError, ValueError):
            continue
        if isinstance(node, dict):
            payload["nodes"].append(node)
    for row in edge_rows:
        try:
            edge = json.loads(row[0])
        except (TypeError, ValueError):
            continue
        if isinstance(edge, dict):
            payload["connections"].append(edge)
    payload["episodeImports"] = []
    payload["episodeViewports"] = {}
    payload["episodeLayouts"] = {}
    membership_by_episode = {}
    for episode_id, node_id, node_type in membership_rows:
        values = membership_by_episode.setdefault(str(episode_id), {
            "nodeIds": [],
            "videoNodeIds": [],
        })
        values["nodeIds"].append(str(node_id))
        if str(node_type) == "video":
            values["videoNodeIds"].append(str(node_id))
    for (
        episode_id,
        title,
        group_json,
        connection_ids_json,
        viewport_json,
        layout_json,
    ) in episode_rows:
        try:
            connection_ids = json.loads(connection_ids_json or "[]")
            layout = json.loads(layout_json or "{}")
        except (TypeError, ValueError):
            connection_ids, layout = [], {}
        try:
            group = json.loads(group_json or "{}")
        except (TypeError, ValueError):
            group = {}
        if not isinstance(group, dict):
            group = {}
        membership = membership_by_episode.get(
            str(episode_id),
            {"nodeIds": [], "videoNodeIds": []},
        )
        group.update({
            "id": episode_id,
            "title": title,
            "nodeIds": membership["nodeIds"],
            "videoNodeIds": membership["videoNodeIds"],
            "connectionIds": (
                connection_ids if isinstance(connection_ids, list) else []
            ),
        })
        payload["episodeImports"].append(group)
        if viewport_json:
            try:
                payload["episodeViewports"][episode_id] = json.loads(viewport_json)
            except (TypeError, ValueError):
                pass
        if isinstance(layout, dict) and layout:
            payload["episodeLayouts"][episode_id] = layout
    if not payload["episodeImports"]:
        payload["episodeImports"] = [{
            "id": "EP001",
            "title": payload.get("title") or "画布工作流",
            "nodeIds": [
                node.get("id") for node in payload["nodes"] if node.get("id")
            ],
            "videoNodeIds": [
                node.get("id")
                for node in payload["nodes"]
                if node.get("type") == "video"
            ],
        }]
    return {
        "id": project_row[0],
        "title": project_row[1],
        "payload": payload,
        "revision": project_row[2],
        "projection": True,
    }


def project_projection_is_ready(project_id):
    con = _connect()
    row = con.execute(
        "SELECT 1 FROM canvas_project_meta WHERE project_id=? LIMIT 1",
        (project_id,),
    ).fetchone()
    con.close()
    # Empty projects legitimately have no episode row. The projection reader
    # synthesizes EP001, so the project metadata marker is the readiness fact.
    return bool(row)


def _projection_reconcile_marker(con, project_id):
    marker = f"canvas_projection_reconciled_v2:{project_id}"
    existing = con.execute(
        "SELECT 1 FROM canvas_meta WHERE key=?",
        (marker,),
    ).fetchone()
    return marker, bool(existing)


def ensure_default_episode_on_connection(con, project_id):
    existing = con.execute(
        "SELECT 1 FROM canvas_projection_episodes WHERE project_id=? LIMIT 1",
        (project_id,),
    ).fetchone()
    if existing:
        return False
    project = con.execute(
        "SELECT title FROM canvas_projects WHERE id=?",
        (project_id,),
    ).fetchone()
    if not project:
        return False
    node_rows = con.execute(
        "SELECT node_id,node_type FROM canvas_projection_nodes "
        "WHERE project_id=? ORDER BY rowid",
        (project_id,),
    ).fetchall()
    group = {
        "id": "EP001",
        "title": str(project[0] or "画布工作流"),
        "nodeIds": [str(row[0]) for row in node_rows],
        "videoNodeIds": [
            str(row[0]) for row in node_rows if str(row[1]) == "video"
        ],
        "connectionIds": [],
    }
    sync_projection_episode(
        con,
        project_id,
        "EP001",
        group,
        updated_at=_now_iso(),
    )
    con.execute(
        "UPDATE canvas_projects SET episode_count=MAX(1,episode_count) "
        "WHERE id=?",
        (project_id,),
    )
    return True


def reconcile_project_projection_on_connection(con, project_id):
    """Repair projection rows from the rollback payload when safe."""
    marker, migrated = _projection_reconcile_marker(con, project_id)
    if migrated:
        ensure_default_episode_on_connection(con, project_id)
        return False
    if not con.in_transaction:
        con.execute("BEGIN IMMEDIATE")
    marker, migrated = _projection_reconcile_marker(con, project_id)
    if migrated:
        ensure_default_episode_on_connection(con, project_id)
        return False
    revision_row = con.execute(
        "SELECT revision FROM canvas_projects WHERE id=?",
        (project_id,),
    ).fetchone()
    if not revision_row:
        return False
    project_revision = int(revision_row[0] or 0)
    rollback_versions = {
        int(value)
        for (value,) in con.execute(
            "SELECT revision FROM canvas_snapshots "
            "WHERE canvas_id=? AND kind='auto'",
            (project_id,),
        )
        if value is not None
    }
    latest_snapshot = con.execute(
        "SELECT revision FROM canvas_snapshots "
        "WHERE canvas_id=? AND kind='auto' "
        "ORDER BY revision DESC, created_at DESC, rowid DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    if (
        project_revision > 1
        and project_revision not in rollback_versions
        and not (
            latest_snapshot
            and project_revision - int(latest_snapshot[0] or 0) < 20
        )
    ):
        con.execute(
            "INSERT INTO canvas_meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (marker, "skipped_stale_rollback"),
        )
        ensure_default_episode_on_connection(con, project_id)
        return False
    row = con.execute(
        "SELECT payload_json FROM canvas_projects WHERE id=?",
        (project_id,),
    ).fetchone()
    if not row:
        return False
    try:
        payload = json.loads(row[0] or "{}")
    except (TypeError, ValueError):
        payload = {}
    if isinstance(payload, dict) and payload:
        sync_payload_projection(
            con,
            project_id,
            payload,
            updated_at=_now_iso(),
        )
    ensure_default_episode_on_connection(con, project_id)
    con.execute(
        "INSERT INTO canvas_meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (marker, "1"),
    )
    return True


def reconcile_project_projection(project_id):
    """Repair stale shadow rows from the rollback payload exactly once."""
    con = _connect()
    try:
        return reconcile_project_projection_on_connection(con, project_id)
    finally:
        con.close()


def ensure_project_default_episode(project_id):
    """Materialize EP001 for legacy projects that have nodes but no episode row."""
    # 打开老项目时会走这里做一次补写。它在读路径上，写失败会直接让画布打不开，
    # 所以要跟其它写路径一样带重试。
    return _run_write_transaction(
        lambda con: ensure_default_episode_on_connection(con, project_id)
    )


def get_project_workspace_index(project_id):
    """Return project identity and episode metadata without loading nodes."""
    con = _connect()
    project_row = con.execute(
        "SELECT id,title,revision,updated_at FROM canvas_projects WHERE id=?",
        (project_id,),
    ).fetchone()
    if not project_row:
        con.close()
        return None
    meta_row = con.execute(
        "SELECT meta_json FROM canvas_project_meta WHERE project_id=?",
        (project_id,),
    ).fetchone()
    episode_rows = con.execute(
        "SELECT episode_id,title,group_json,"
        "connection_ids_json,viewport_json,layout_json "
        "FROM canvas_projection_episodes WHERE project_id=? "
        "ORDER BY episode_id",
        (project_id,),
    ).fetchall()
    membership_rows = con.execute(
        "SELECT episode_id,node_id,node_type FROM canvas_projection_nodes "
        "WHERE project_id=? AND episode_id<>? ORDER BY rowid",
        (project_id, _UNASSIGNED_EPISODE_ID),
    ).fetchall()
    con.close()
    try:
        project_meta = json.loads(meta_row[0]) if meta_row else {}
    except (TypeError, ValueError):
        project_meta = {}
    if not isinstance(project_meta, dict):
        project_meta = {}
    episodes = []
    viewports = {}
    membership_by_episode = {}
    for episode_id, node_id, node_type in membership_rows:
        values = membership_by_episode.setdefault(str(episode_id), {
            "nodeIds": [],
            "videoNodeIds": [],
        })
        values["nodeIds"].append(str(node_id))
        if str(node_type) == "video":
            values["videoNodeIds"].append(str(node_id))
    for row in episode_rows:
        episode_id, title, group_json, connection_ids_json, viewport_json, layout_json = row
        try:
            group = json.loads(group_json or "{}")
            connection_ids = json.loads(connection_ids_json or "[]")
            layout = json.loads(layout_json or "{}")
            viewport = json.loads(viewport_json) if viewport_json else None
        except (TypeError, ValueError):
            group, connection_ids = {}, []
            layout, viewport = {}, None
        if not isinstance(group, dict):
            group = {}
        membership = membership_by_episode.get(
            str(episode_id),
            {"nodeIds": [], "videoNodeIds": []},
        )
        group.update({
            "id": episode_id,
            "title": title,
            "nodeIds": membership["nodeIds"],
            "videoNodeIds": membership["videoNodeIds"],
            "connectionIds": connection_ids if isinstance(connection_ids, list) else [],
            "shot_count": len(membership["videoNodeIds"]),
            "described_count": len(membership["videoNodeIds"]),
            "storyboard_ready": True,
        })
        episodes.append(group)
        if viewport:
            viewports[episode_id] = viewport
    if not episodes:
        episodes.append({
            "id": "EP001",
            "title": project_row[1] or "画布工作流",
            "nodeIds": [],
            "videoNodeIds": [],
            "connectionIds": [],
            "shot_count": 0,
            "described_count": 0,
            "storyboard_ready": True,
        })
    return {
        "id": project_row[0],
        "title": project_row[1],
        "revision": project_row[2],
        "updatedAt": project_row[3],
        "projectMeta": project_meta,
        "episodes": episodes,
        "episodeViewports": viewports,
    }


def get_project_workspace_nodes(project_id, episode_id=None):
    """Return current episode nodes and edges from relational projection."""
    normalized_episode = str(episode_id or "").strip().upper()
    con = _connect()
    if normalized_episode:
        episode_row = con.execute(
            "SELECT group_json,layout_json FROM canvas_projection_episodes "
            "WHERE project_id=? AND episode_id=?",
            (project_id, normalized_episode),
        ).fetchone()
    else:
        episode_row = None
    if episode_row:
        try:
            group = json.loads(episode_row[0] or "{}")
            layout = json.loads(episode_row[1] or "{}")
        except (TypeError, ValueError):
            group, layout = {}, {}
    else:
        group = {}
        layout = {}
    if normalized_episode:
        node_rows = con.execute(
            "SELECT node_json FROM canvas_projection_nodes WHERE project_id=? "
            "AND episode_id=? ORDER BY rowid",
            (project_id, normalized_episode),
        ).fetchall()
    else:
        node_rows = con.execute(
            "SELECT node_json FROM canvas_projection_nodes WHERE project_id=? ORDER BY rowid",
            (project_id,),
        ).fetchall()
    nodes = []
    for row in node_rows:
        try:
            node = json.loads(row[0])
        except (TypeError, ValueError):
            continue
        if isinstance(node, dict):
            nodes.append(node)
    if normalized_episode:
        nodes.extend(
            _workspace_referenced_shared_nodes(
                con,
                project_id,
                normalized_episode,
                nodes,
            )
        )
    node_ids = {str(node.get("id")) for node in nodes if node.get("id")}
    edge_rows = con.execute(
        "SELECT edge_json FROM canvas_projection_edges WHERE project_id=? ORDER BY rowid",
        (project_id,),
    ).fetchall()
    con.close()
    edges = []
    for row in edge_rows:
        try:
            edge = json.loads(row[0])
        except (TypeError, ValueError):
            continue
        if (
            isinstance(edge, dict)
            and str(edge.get("fromNodeId") or "") in node_ids
            and str(edge.get("toNodeId") or "") in node_ids
        ):
            edges.append(edge)
    return {
        "episodeId": normalized_episode or group.get("id") or "EP001",
        "episode": group,
        "nodes": nodes,
        "connections": edges,
        "layouts": layout if isinstance(layout, dict) else {},
        "notes": (
            get_episode_notes(project_id, normalized_episode)
            if normalized_episode
            else []
        ),
    }


def _workspace_referenced_shared_nodes(con, project_id, episode_id, nodes):
    """Resolve shared assets used by the current episode's generation contract.

    Node ownership stays exclusive in the projection table. Images and audio
    can still be referenced by videos in several episodes, so workspace reads
    must include those stable assets independently of their ownership row.
    """
    referenced = set()
    for node in nodes:
        if not isinstance(node, dict) or str(node.get("type") or "") != "video":
            continue
        metadata = node.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        params = metadata.get("params")
        params = params if isinstance(params, dict) else {}
        for key in ("refs", "voiceRefs"):
            for collection in (params.get(key), metadata.get(key)):
                for item in collection or []:
                    if isinstance(item, dict) and item.get("nodeId"):
                        referenced.add(str(item["nodeId"]))
        for key in ("voiceRef",):
            item = params.get(key) or metadata.get(key)
            if isinstance(item, dict) and item.get("nodeId"):
                referenced.add(str(item["nodeId"]))

    visible_ids = {
        str(node.get("id"))
        for node in nodes
        if isinstance(node, dict) and node.get("id")
    }
    wanted = sorted(referenced - visible_ids)
    if not wanted:
        return []
    placeholders = ",".join("?" for _ in wanted)
    rows = con.execute(
        f"SELECT node_json FROM canvas_projection_nodes "
        f"WHERE project_id=? AND node_id IN ({placeholders}) "
        f"AND node_type IN ('image','audio') ORDER BY rowid",
        (project_id, *wanted),
    ).fetchall()
    shared = []
    for row in rows:
        try:
            node = json.loads(row[0])
        except (TypeError, ValueError):
            continue
        if not isinstance(node, dict):
            continue
        metadata = node.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            node["metadata"] = metadata
        metadata["sharedAcrossEpisodes"] = True
        shared.append(node)
    return shared


def repair_episode_node_ownership(project_id):
    """Repair explicit ownership and quarantine ambiguous cross-episode nodes."""
    repaired = 0
    ambiguous = 0

    def write(con):
        nonlocal repaired, ambiguous
        row = con.execute(
            "SELECT payload_json FROM canvas_projects WHERE id=?",
            (project_id,),
        ).fetchone()
        if not row:
            return 0
        try:
            payload = json.loads(row[0] or "{}")
        except (TypeError, ValueError):
            payload = {}
        nodes = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
        groups = (
            payload.get("episodeImports")
            if isinstance(payload.get("episodeImports"), list)
            else []
        )
        memberships: dict[str, list[str]] = {}
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_episode = str(group.get("id") or "").strip().upper()
            if not group_episode:
                continue
            for node_id in group.get("nodeIds") or []:
                memberships.setdefault(str(node_id), []).append(group_episode)
        stamp = _now_iso()
        for node in nodes:
            if not isinstance(node, dict) or not node.get("id"):
                continue
            node_id = str(node["id"])
            meta = node.get("metadata")
            if not isinstance(meta, dict):
                meta = {}
                node["metadata"] = meta
            explicit = str(
                node.get("episodeId") or meta.get("episodeId") or ""
            ).strip().upper()
            candidates = list(dict.fromkeys(memberships.get(node_id, [])))
            owner = explicit or (candidates[0] if len(candidates) == 1 else "")
            if not owner:
                continue
            for group in groups:
                if not isinstance(group, dict):
                    continue
                group_episode = str(group.get("id") or "").strip().upper()
                if not group_episode or group_episode == owner:
                    continue
                before_node_ids = list(group.get("nodeIds") or [])
                before_video_ids = list(group.get("videoNodeIds") or [])
                group["nodeIds"] = [
                    value for value in group.get("nodeIds") or []
                    if str(value) != node_id
                ]
                group["videoNodeIds"] = [
                    value for value in group.get("videoNodeIds") or []
                    if str(value) != node_id
                ]
                if (
                    group["nodeIds"] != before_node_ids
                    or group["videoNodeIds"] != before_video_ids
                ):
                    repaired += 1
            if meta.get("episodeId") != owner:
                meta["episodeId"] = owner
                node["updatedAt"] = stamp
                repaired += 1
            sync_projection_node(
                con,
                project_id,
                node,
                episode_id=owner,
                updated_at=stamp,
            )
            claim_nodes_for_episode(
                con,
                project_id,
                owner,
                [node_id],
                updated_at=stamp,
            )
        ambiguous_ids = set()
        if groups:
            for group in groups:
                if not isinstance(group, dict):
                    continue
                group_episode = str(group.get("id") or "").strip().upper()
                if not group_episode:
                    continue
                sync_projection_episode(
                    con,
                    project_id,
                    group_episode,
                    group,
                    updated_at=stamp,
                )
            # A node without explicit ownership that appears in multiple
            # episodes is not safe to assign arbitrarily. Keep its row and
            # content, but remove the ambiguous shared membership so it cannot
            # leak into every episode. The node remains recoverable from the
            # project payload and will be claimed when edited in one episode.
            for node in nodes:
                if not isinstance(node, dict) or not node.get("id"):
                    continue
                node_id = str(node["id"])
                meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
                explicit = str(
                    node.get("episodeId") or meta.get("episodeId") or ""
                ).strip().upper()
                candidates = list(dict.fromkeys(memberships.get(node_id, [])))
                if not explicit and len(candidates) > 1:
                    ambiguous_ids.add(node_id)
            if ambiguous_ids:
                for group in groups:
                    if not isinstance(group, dict):
                        continue
                    group["nodeIds"] = [
                        node_id for node_id in group.get("nodeIds") or []
                        if str(node_id) not in ambiguous_ids
                    ]
                    group["videoNodeIds"] = [
                        node_id for node_id in group.get("videoNodeIds") or []
                        if str(node_id) not in ambiguous_ids
                    ]
                for node_id in ambiguous_ids:
                    con.execute(
                        "UPDATE canvas_projection_nodes SET episode_id=? "
                        "WHERE project_id=? AND node_id=?",
                        (_UNASSIGNED_EPISODE_ID, project_id, node_id),
                    )
                ambiguous = len(ambiguous_ids)
            for group in groups:
                if not isinstance(group, dict):
                    continue
                group_episode = str(group.get("id") or "").strip().upper()
                if not group_episode:
                    continue
                sync_projection_episode(
                    con,
                    project_id,
                    group_episode,
                    group,
                    updated_at=stamp,
                )
        if repaired or ambiguous:
            con.execute(
                "UPDATE canvas_projects SET payload_json=?,updated_at=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), stamp, project_id),
            )
            for group in groups:
                if not isinstance(group, dict):
                    continue
                group_episode = str(group.get("id") or "").strip().upper()
                if not group_episode:
                    continue
                sync_projection_episode(
                    con,
                    project_id,
                    group_episode,
                    group,
                    updated_at=stamp,
                )
        return repaired + ambiguous

    return int(_run_write_transaction(write) or 0)


def _run_write_transaction(callback):
    """Retry SQLite writer contention beyond a single busy_timeout window."""
    deadline = time.monotonic() + _WRITE_RETRY_SECONDS
    delay = 0.05
    # readonly 只给几次机会：瞬态竞态重试一两次就好；真·只读（权限、只读介质）
    # 不该白等满 35 秒才报错。
    readonly_budget = _MAX_READONLY_RETRIES

    def retryable(exc: sqlite3.OperationalError) -> bool:
        nonlocal readonly_budget
        if _is_readonly_write_error(exc):
            if readonly_budget <= 0:
                return False
            readonly_budget -= 1
            return True
        return _is_retryable_write_error(exc)

    while True:
        try:
            con = _connect()
        except sqlite3.OperationalError as exc:
            if not retryable(exc) or time.monotonic() >= deadline:
                raise
            print(
                f"[sqlite-write] retry connect error={exc}",
                file=__import__("sys").stderr,
            )
            time.sleep(delay)
            delay = min(_WRITE_RETRY_MAX_DELAY, delay * 2)
            continue
        try:
            con.execute("BEGIN IMMEDIATE")
            result = callback(con)
            con.commit()
            # PASSIVE never blocks writers or readers. A soft hard-limit check
            # only runs after a commit and is rate-limited, so normal canvas
            # saves do not pay a checkpoint on every write.
            path = _db_path()
            now = time.monotonic()
            wal_path = f"{path}-wal"
            if (
                now - _WAL_LAST_MAINTENANCE.get(path, 0.0)
                >= _WAL_MAINTENANCE_INTERVAL_SECONDS
                and os.path.exists(wal_path)
                and os.path.getsize(wal_path) >= _WAL_SOFT_LIMIT_BYTES
            ):
                _WAL_LAST_MAINTENANCE[path] = now
                try:
                    con.execute("PRAGMA wal_checkpoint(PASSIVE)")
                except sqlite3.OperationalError:
                    # The next maintenance window retries; business writes must
                    # never fail because a passive checkpoint was busy.
                    pass
            return result
        except sqlite3.OperationalError as exc:
            if con.in_transaction:
                con.rollback()
            if not retryable(exc) or time.monotonic() >= deadline:
                raise
            print(
                f"[sqlite-write] retry transaction error={exc}",
                file=__import__("sys").stderr,
            )
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


def list_project_summaries():
    """Return project cards without parsing every full canvas payload.

    The old /api/projects path loaded and JSON-decoded every project solely to
    count nodes and failed generations. SQLite can aggregate the common counts
    directly from the versioned snapshot metadata, while task/session state
    still comes from their own indexed tables.
    """
    con = _connect()
    rows = con.execute(
        """
        SELECT
            p.id,
            p.title,
            p.revision,
            p.updated_at,
            COALESCE(p.node_count, 0) AS node_count,
            COALESCE(p.connection_count, 0) AS connection_count,
            COALESCE(p.episode_count, 1) AS episode_count,
            COALESCE(p.failed_count, 0) AS failed_count,
            COALESCE(p.local_blocked_count, 0) AS local_blocked_count
        FROM canvas_projects p
        ORDER BY p.updated_at DESC
        """
    ).fetchall()
    con.close()
    return [
        {
            "id": row[0],
            "title": row[1],
            "revision": row[2],
            "updated_at": row[3],
            "node_count": int(row[4] or 0),
            "connection_count": int(row[5] or 0),
            "episode_count": int(row[6] or 1),
            "failed_count": int(row[7] or 0),
            "local_blocked_count": int(row[8] or 0),
        }
        for row in rows
    ]


def get_project(pid):
    projection = (
        get_project_projection(pid)
        if project_projection_is_ready(pid)
        else None
    )
    if projection is not None:
        return projection
    con = _connect()
    row = con.execute(
        "SELECT id, title, payload_json, revision FROM canvas_projects WHERE id=?", (pid,)
    ).fetchone()
    con.close()
    if not row:
        return None
    return {"id": row[0], "title": row[1], "payload": json.loads(row[2]), "revision": row[3]}


def project_exists(project_id):
    con = _connect()
    row = con.execute(
        "SELECT 1 FROM canvas_projects WHERE id=? LIMIT 1",
        (project_id,),
    ).fetchone()
    con.close()
    return bool(row)


def get_project_revision(project_id):
    con = _connect()
    row = con.execute(
        "SELECT revision FROM canvas_projects WHERE id=? LIMIT 1",
        (project_id,),
    ).fetchone()
    con.close()
    return int(row[0] or 0) if row else None


def project_activity_summaries():
    """Return project activity without scanning full canvas payloads.

    running_count is useful UI truth. Legacy failed/local counts depended on
    stale node metadata and were not reliable project-level facts; current
    failure details live in tasks and the per-project task center.
    """
    con = _connect()
    rows = con.execute(
        """
        SELECT
            t.project_id,t.status,t.phase,t.error,
            (
                SELECT COUNT(*) FROM generation_tasks a
                WHERE a.project_id=t.project_id
                AND a.status IN ('queued','submitting','running')
            ) AS running_count
        FROM generation_tasks t
        WHERE t.rowid IN (
            SELECT t2.rowid
            FROM generation_tasks t2
            WHERE t2.project_id=t.project_id
            ORDER BY t2.created_at DESC, t2.updated_at DESC, t2.rowid DESC
            LIMIT 1
        ) OR t.status IN ('queued','submitting','running')
        ORDER BY t.created_at DESC, t.updated_at DESC, t.rowid DESC
        """
    ).fetchall()
    con.close()
    summaries = {}
    for project_id, status, phase, error, running_count in rows:
        key = str(project_id or "")
        if not key:
            continue
        item = summaries.setdefault(key, {
            "running_count": 0,
            "failed_count": 0,
            "local_blocked_count": 0,
            "latest_task_status": "",
            "latest_task_phase": "",
            "latest_task_error_kind": "",
        })
        item["running_count"] = int(running_count or 0)
        if not item["latest_task_status"]:
            item["latest_task_status"] = str(status or "")
            item["latest_task_phase"] = str(phase or "")
            item["latest_task_error_kind"] = (
                "local_validation"
                if status == "failed" and is_local_validation_error(error)
                else ("provider" if status == "failed" else "")
            )
    return summaries


def get_workspace_artifacts(project_id, episode_id=None):
    """Compatibility slice of project metadata for workspace clients."""
    index = get_project_workspace_index(project_id)
    if not index:
        return None
    episode = str(episode_id or "").strip().upper()
    meta = index.get("projectMeta") or {}
    return {
        "meta": meta,
        "episodeViewports": index.get("episodeViewports") or {},
        "episodes": index.get("episodes") or [],
        "currentEpisode": episode or (
            (index.get("episodes") or [{}])[0].get("id") or "EP001"
        ),
    }


def is_local_validation_error(message):
    text = str(message or "")
    markers = (
        "参考素材未就绪",
        "参考素材缺失",
        "引用节点不存在",
        "提示词为空",
        "当前模型最多",
        "音色节点尚未生成",
        "源节点尚未生成",
        "原始产物转存到 TOS",
        "Seedream 5.0 角色图进入 Seedance",
        "信任窗口只有",
    )
    return any(marker in text for marker in markers)


def create_project(title, payload=None):
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
    # 以前这里是裸连接直写：没开 BEGIN IMMEDIATE、没走重试包装，出错时
    # con.close() 也不会执行（连接泄漏）。并发建项目时实测会抛
    # "attempt to write a readonly database" 直接导致建项目失败。
    def write(con):
        con.execute(
            "INSERT INTO canvas_projects(id,user_id,title,payload_json,revision,"
            "created_at,updated_at,node_count,connection_count,episode_count,"
            "failed_count,local_blocked_count) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                pid,
                tenant_context.current_tenant_id(),
                title,
                json.dumps(payload, ensure_ascii=False),
                1,
                now,
                now,
                *payload_stats(payload),
            ),
        )
        sync_payload_projection(con, pid, payload, updated_at=now)
        _snapshot(con, pid, title, payload, 1, kind="auto")
        return pid

    return _run_write_transaction(write)


def update_project(
    pid,
    title=None,
    payload=None,
    revision=None,
    if_revision=None,
    snapshot_mode="auto",
):
    # 画布每次保存都走这里，是全项目最关键的写路径。以前是裸 BEGIN IMMEDIATE，
    # 并发下偶发的 "attempt to write a readonly database" 会让用户看到"保存失败"，
    # 但再点一次又成功。乐观锁 WHERE revision=? 保证重跑不会重复写。
    def write(con):
        row = con.execute(
            "SELECT title, payload_json, revision FROM canvas_projects WHERE id=?",
            (pid,),
        ).fetchone()
        if not row:
            return False
        current_revision = int(row[2])
        if if_revision is not None and current_revision != int(if_revision):
            raise RevisionConflictError(
                f"项目已更新：期望 revision={if_revision}，实际 revision={current_revision}"
            )
        new_title = title if title is not None else row[0]
        new_payload = json.loads(row[1]) if payload is None else payload
        if not isinstance(new_payload, dict):
            raise ValueError("payload must be an object")
        new_rev = (current_revision + 1) if revision is None else int(revision)
        now = _now_iso()
        new_payload = json.loads(json.dumps(new_payload, ensure_ascii=False))
        new_payload["revision"] = new_rev
        new_payload["title"] = new_title
        new_payload["updatedAt"] = now
        cursor = con.execute(
            "UPDATE canvas_projects SET title=?, payload_json=?, revision=?, updated_at=?, "
            "node_count=?, connection_count=?, episode_count=?,"
            "failed_count=?, local_blocked_count=? "
            "WHERE id=? AND revision=?",
            (
                new_title,
                json.dumps(new_payload, ensure_ascii=False),
                new_rev,
                now,
                *payload_stats(new_payload),
                pid,
                current_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise RevisionConflictError("项目写入冲突，请重新读取后再试")
        sync_payload_projection(
            con,
            pid,
            new_payload,
            updated_at=now,
        )
        _maybe_snapshot(
            con,
            pid,
            new_title,
            new_payload,
            new_rev,
            mode=snapshot_mode,
            now=now,
        )
        return True

    return _run_write_transaction(write)


def delete_project(pid):
    def write(con):
        con.execute("DELETE FROM generation_tasks WHERE project_id=?", (pid,))
        con.execute("DELETE FROM canvas_snapshots WHERE canvas_id=?", (pid,))
        con.execute("DELETE FROM canvas_projection_nodes WHERE project_id=?", (pid,))
        con.execute("DELETE FROM canvas_projection_edges WHERE project_id=?", (pid,))
        con.execute("DELETE FROM canvas_projection_episodes WHERE project_id=?", (pid,))
        con.execute("DELETE FROM canvas_projection_notes WHERE project_id=?", (pid,))
        con.execute("DELETE FROM canvas_project_meta WHERE project_id=?", (pid,))
        con.execute("DELETE FROM canvas_projects WHERE id=?", (pid,))

    return _run_write_transaction(write)


_TASK_FIELDS = {
    "status", "phase", "progress", "provider", "model", "provider_task_id",
    "error", "started_at", "finished_at", "title", "kind",
    "episode_id",
    "estimated_cost", "cost_currency", "provider_finished_at", "node_written_at",
    "local_result_url", "provider_result_url",
    "request_json", "client_operation_id", "lease_owner", "lease_expires_at",
    "heartbeat_at", "delivery_status", "delivery_attempts", "result_ready_at",
    "progress_updated_at", "next_poll_at",
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
    elif (
        current in _ACTIVE_TASK_STATUSES
        and requested == "queued"
        and current != "queued"
    ):
        allowed = False
    if (
        allowed
        and current in _ACTIVE_TASK_STATUSES
        and requested in _ACTIVE_TASK_STATUSES
        and "progress" in updates
    ):
        try:
            requested_progress = int(updates.get("progress") or 0)
            current_progress = int(task.get("progress") or 0)
        except (TypeError, ValueError):
            requested_progress = current_progress = 0
        if requested_progress < current_progress:
            merged.pop("progress", None)
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

    def write(con):
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
        return event_id

    # 每次生成进度/完成都会打一行，是全库最热的写路径之一，必须走统一重试。
    return _run_write_transaction(write)


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
    episode_id="",
    provider="",
    model="",
    estimated_cost=0,
    cost_currency="",
    request_snapshot=None,
    client_operation_id="",
):
    client_operation_id = str(client_operation_id or "").strip()
    normalized_episode = str(episode_id or "").strip().upper()
    request_json = json.dumps(request_snapshot or {}, ensure_ascii=False, sort_keys=True)
    con = _connect()
    if client_operation_id:
        existing_for_operation = con.execute(
            "SELECT * FROM generation_tasks WHERE client_operation_id=? LIMIT 1",
            (client_operation_id,),
        ).fetchone()
        if existing_for_operation:
            columns = [
                item[0] for item in con.execute(
                    "SELECT * FROM generation_tasks LIMIT 0"
                ).description
            ]
            con.close()
            return {
                **dict(zip(columns, existing_for_operation)),
                "created_new": False,
                "replayed": True,
            }
    active = con.execute(
        "SELECT * FROM generation_tasks WHERE project_id=? AND node_id=? "
        "AND status IN ('queued','submitting','running') "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (project_id, node_id),
    ).fetchone()
    if active:
        columns = [item[0] for item in con.execute("SELECT * FROM generation_tasks LIMIT 0").description]
        active_task = dict(zip(columns, active))
        con.close()
        if not str(active_task.get("provider_task_id") or "").strip():
            journal_task_id = ""
            try:
                for entry in list_provider_task_journal():
                    if str(entry.get("taskId") or "") != str(active_task["id"]):
                        continue
                    journal_task_id = str(entry.get("providerTaskId") or "").strip()
                    if journal_task_id:
                        break
            except Exception:
                journal_task_id = ""
            if journal_task_id:
                active_task = update_generation_task(
                    active_task["id"],
                    provider_task_id=journal_task_id,
                ) or {
                    **active_task,
                    "provider_task_id": journal_task_id,
                }
        latest = get_generation_task(active_task["id"]) or active_task
        return {**latest, "created_new": False}
    owner = con.execute(
        "SELECT episode_id FROM canvas_projection_nodes "
        "WHERE project_id=? AND node_id=?",
        (project_id, node_id),
    ).fetchone()
    owner_episode = str(owner[0] or "").strip().upper() if owner else ""
    if owner_episode and owner_episode != _UNASSIGNED_EPISODE_ID:
        if normalized_episode and normalized_episode != owner_episode:
            con.close()
            raise ValueError(
                f"节点 {node_id} 属于 {owner_episode}，不能创建 {normalized_episode} 的任务"
            )
        episode_id = owner_episode
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
                "id,project_id,node_id,episode_id,title,kind,status,phase,progress,provider,model,"
                "provider_task_id,attempt,estimated_cost,cost_currency,error,"
                "created_at,started_at,updated_at,finished_at,local_result_url,"
                "provider_result_url,request_json,client_operation_id,delivery_status,"
                "next_poll_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id, project_id, node_id, str(episode_id or "").upper(),
                    title, kind, "queued", "等待调度", 0,
                    provider, model, "", attempt, float(estimated_cost or 0),
                    str(cost_currency or ""), "", now, "", now, "", "", "",
                    request_json, client_operation_id, "pending", now,
                ),
            )
        except sqlite3.IntegrityError:
            if client_operation_id:
                row = connection.execute(
                    "SELECT * FROM generation_tasks WHERE client_operation_id=? LIMIT 1",
                    (client_operation_id,),
                ).fetchone()
                if row:
                    columns = [
                        item[0] for item in connection.execute(
                            "SELECT * FROM generation_tasks LIMIT 0"
                        ).description
                    ]
                    return {
                        **dict(zip(columns, row)),
                        "created_new": False,
                        "replayed": True,
                    }
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


def insert_project_nodes(
    project_id,
    nodes,
    *,
    episode_id=None,
    if_revision=None,
):
    """Insert new nodes and episode membership without a payload JSON rewrite."""
    items = [
        item
        for item in (nodes or [])
        if isinstance(item, dict) and item.get("id")
    ]
    if not items:
        return {"created": 0, "revision": None}

    def write(con):
        reconcile_project_projection_on_connection(con, project_id)
        row = con.execute(
            "SELECT revision FROM canvas_projects WHERE id=?",
            (project_id,),
        ).fetchone()
        if not row:
            return None
        current_revision = int(row[0] or 0)
        if if_revision is not None and current_revision != int(if_revision):
            raise RevisionConflictError(
                f"项目已更新：期望 revision={if_revision}，实际 revision={current_revision}"
            )
        stamp = _now_iso()
        normalized_episode = str(episode_id or "").strip().upper()
        if not normalized_episode:
            raise ValueError("新建节点必须指定所属集")
        for item in items:
            metadata = item.get("metadata")
            metadata = dict(metadata) if isinstance(metadata, dict) else {}
            metadata_episode = str(metadata.get("episodeId") or "").strip().upper()
            if metadata_episode and metadata_episode != normalized_episode:
                raise ValueError(
                    f"节点 {item.get('id')} 的集号 {metadata_episode} "
                    f"与保存目标 {normalized_episode} 不一致"
                )
            metadata["episodeId"] = normalized_episode
            item["metadata"] = metadata
            item.setdefault("createdAt", stamp)
            item.setdefault("positionUpdatedAt", stamp)
            item.setdefault("updatedAt", stamp)
            sync_projection_node(
                con,
                project_id,
                item,
                episode_id=normalized_episode,
                updated_at=stamp,
            )
        group = {
            "id": normalized_episode,
            "title": normalized_episode,
            "nodeIds": [str(item["id"]) for item in items],
            "videoNodeIds": [
                str(item["id"]) for item in items if item.get("type") == "video"
            ],
            "connectionIds": [],
        }
        group_row = con.execute(
            "SELECT title FROM canvas_projection_episodes "
            "WHERE project_id=? AND episode_id=?",
            (project_id, normalized_episode),
        ).fetchone()
        if group_row and group_row[0]:
            group["title"] = str(group_row[0])
        sync_projection_episode(
            con,
            project_id,
            normalized_episode,
            group,
            updated_at=stamp,
        )
        revision = current_revision + 1
        con.execute(
            "UPDATE canvas_projects SET revision=?,updated_at=?,"
            "node_count=node_count+?,episode_count=MAX(episode_count,?) "
            "WHERE id=? AND revision=?",
            (
                revision,
                stamp,
                len(items),
                1 if normalized_episode else 0,
                project_id,
                current_revision,
            ),
        )
        return {
            "created": len(items),
            "revision": revision,
            "nodes": items,
            "episode": group,
        }

    result = _run_write_transaction(write)
    return result or {"created": 0, "revision": None}


def update_project_layout(
    project_id,
    episode_id,
    layout,
    *,
    if_revision=None,
):
    """Persist one episode layout and node coordinates only."""
    normalized = str(episode_id or "").strip().upper()
    if not normalized or not isinstance(layout, dict):
        return None

    def write(con):
        reconcile_project_projection_on_connection(con, project_id)
        row = con.execute(
            "SELECT revision FROM canvas_projects WHERE id=?",
            (project_id,),
        ).fetchone()
        if not row:
            return None
        current = int(row[0] or 0)
        if if_revision is not None and current != int(if_revision):
            raise RevisionConflictError(
                f"项目已更新：期望 revision={if_revision}，实际 revision={current}"
            )
        stamp = _now_iso()
        episode_members = {
            str(row[0])
            for row in con.execute(
                "SELECT node_id FROM canvas_projection_nodes "
                "WHERE project_id=? AND episode_id=?",
                (project_id, normalized),
            )
        }
        filtered_layout = {
            str(node_id): position
            for node_id, position in layout.items()
            if str(node_id) in episode_members
        }
        encoded_layout = json.dumps(filtered_layout, ensure_ascii=False)
        for node_id, position in filtered_layout.items():
            if not isinstance(position, dict):
                continue
            if str(node_id) not in episode_members:
                continue
            node_row = con.execute(
                "SELECT node_json FROM canvas_projection_nodes "
                "WHERE project_id=? AND node_id=? AND episode_id=?",
                (project_id, str(node_id), normalized),
            ).fetchone()
            if not node_row:
                continue
            try:
                node = json.loads(node_row[0] or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(node, dict):
                continue
            node["position"] = position
            sync_projection_node(
                con,
                project_id,
                node,
                episode_id=normalized,
                updated_at=stamp,
            )
        group_row = con.execute(
            "SELECT group_json,connection_ids_json,viewport_json,title "
            "FROM canvas_projection_episodes "
            "WHERE project_id=? AND episode_id=?",
            (project_id, normalized),
        ).fetchone()
        if group_row:
            try:
                group = json.loads(group_row[0] or "{}")
            except (TypeError, ValueError):
                group = {}
            if not isinstance(group, dict):
                group = {}
            group.update({
                "id": normalized,
                "title": group.get("title") or group_row[3] or normalized,
                "nodeIds": sorted(episode_members),
                "videoNodeIds": [
                    str(row[0])
                    for row in con.execute(
                        "SELECT node_id FROM canvas_projection_nodes "
                        "WHERE project_id=? AND episode_id=? "
                        "AND node_type='video' ORDER BY rowid",
                        (project_id, normalized),
                    )
                ],
                "connectionIds": json.loads(group_row[1] or "[]"),
            })
            sync_projection_episode(
                con,
                project_id,
                normalized,
                group,
                viewport_json=group_row[2] or "",
                layout_json=encoded_layout,
                updated_at=stamp,
            )
        else:
            sync_projection_episode(
                con,
                project_id,
                normalized,
                {
                    "id": normalized,
                    "title": normalized,
                    "nodeIds": sorted(filtered_layout),
                    "videoNodeIds": [],
                    "connectionIds": [],
                },
                layout_json=encoded_layout,
                updated_at=stamp,
            )
        # Aggregate stats are maintained incrementally from node deltas.
        revision = current + 1
        con.execute(
            "UPDATE canvas_projects SET revision=?,updated_at=? "
            "WHERE id=? AND revision=?",
            (
                revision,
                stamp,
                project_id,
                current,
            ),
        )
        return {"revision": revision, "updated_at": stamp}

    return _run_write_transaction(write)


def update_project_nodes(
    project_id,
    nodes,
    *,
    episode_id=None,
    if_revision=None,
):
    """Update existing node rows without serializing the complete project."""
    items = [
        item
        for item in (nodes or [])
        if isinstance(item, dict) and item.get("id")
    ]
    if not items:
        return {"updated": 0, "revision": None}

    def write(con):
        reconcile_project_projection_on_connection(con, project_id)
        row = con.execute(
            "SELECT revision FROM canvas_projects WHERE id=?",
            (project_id,),
        ).fetchone()
        if not row:
            return None
        current_revision = int(row[0] or 0)
        if if_revision is not None and current_revision != int(if_revision):
            raise RevisionConflictError(
                f"项目已更新：期望 revision={if_revision}，实际 revision={current_revision}"
            )
        stamp = _now_iso()
        normalized_episode = str(episode_id or "").strip().upper()
        updated = 0
        if normalized_episode:
            group_row = con.execute(
                "SELECT 1 FROM canvas_projection_episodes "
                "WHERE project_id=? AND episode_id=?",
                (project_id, normalized_episode),
            ).fetchone()
            if not group_row:
                sync_projection_episode(
                    con,
                    project_id,
                    normalized_episode,
                    {
                        "id": normalized_episode,
                        "title": normalized_episode,
                        "nodeIds": [],
                        "videoNodeIds": [],
                        "connectionIds": [],
                    },
                    updated_at=stamp,
                )
            claim_nodes_for_episode(
                con,
                project_id,
                normalized_episode,
                [item.get("id") for item in items],
                updated_at=stamp,
            )
        for item in items:
            metadata = item.get("metadata")
            metadata = dict(metadata) if isinstance(metadata, dict) else {}
            if normalized_episode:
                metadata_episode = str(metadata.get("episodeId") or "").strip().upper()
                if metadata_episode and metadata_episode != normalized_episode:
                    raise ValueError(
                        f"节点 {item.get('id')} 的集号 {metadata_episode} "
                        f"与保存目标 {normalized_episode} 不一致"
                    )
                metadata["episodeId"] = normalized_episode
                item["metadata"] = metadata
            item.setdefault("updatedAt", stamp)
        existing_ids = {
            str(row[0])
            for row in con.execute(
                "SELECT node_id FROM canvas_projection_nodes "
                "WHERE project_id=?",
                (project_id,),
            )
        }
        existing_items = [
            item for item in items
            if str(item.get("id")) in existing_ids
        ]
        updated = sync_projection_nodes_batch(
            con,
            project_id,
            existing_items,
            episode_id=normalized_episode,
            updated_at=stamp,
        )
        stats = refresh_project_projection_stats(con, project_id)
        revision = current_revision + 1
        con.execute(
            "UPDATE canvas_projects SET revision=?,updated_at=?,"
            "failed_count=?,local_blocked_count=? "
            "WHERE id=? AND revision=?",
            (
                revision,
                stamp,
                int(stats.get("failed_count") or 0),
                int(stats.get("local_blocked_count") or 0),
                project_id,
                current_revision,
            ),
        )
        return {
            "updated": updated,
            "revision": revision,
            "nodes": items,
        }

    result = _run_write_transaction(write)
    return result or {"updated": 0, "revision": None}


def get_projection_node(project_id, node_id):
    con = _connect()
    row = con.execute(
        "SELECT node_json FROM canvas_projection_nodes "
        "WHERE project_id=? AND node_id=?",
        (project_id, node_id),
    ).fetchone()
    con.close()
    if not row:
        return None
    try:
        value = json.loads(row[0] or "{}")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def get_generation_task(task_id):
    con = _connect()
    row = con.execute("SELECT * FROM generation_tasks WHERE id=?", (task_id,)).fetchone()
    columns = [item[0] for item in con.execute("SELECT * FROM generation_tasks LIMIT 0").description]
    con.close()
    return dict(zip(columns, row)) if row else None


def update_generation_task(task_id, **fields):
    lease_owner = fields.pop("_lease_owner", None)

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
        if (
            current
            and current.get("status") in _ACTIVE_TASK_STATUSES
            and "progress" in updates
        ):
            try:
                if int(updates.get("progress") or 0) < int(current.get("progress") or 0):
                    updates.pop("progress", None)
            except (TypeError, ValueError):
                updates.pop("progress", None)
        if not updates:
            return
        now = _now_iso()
        updates["updated_at"] = now
        if "progress" in updates:
            updates["progress_updated_at"] = now
        if lease_owner is not None:
            set_clause = ",".join(
                (
                    f"{key}=MAX(COALESCE({key},0),?)"
                    if key == "progress"
                    else f"{key}=?"
                )
                for key in updates
            )
            con.execute(
                "UPDATE generation_tasks SET "
                + set_clause
                + " WHERE id=? AND lease_owner=? AND status='running'",
                (*updates.values(), task_id, str(lease_owner or "")),
            )
            return
        set_clause = ",".join(
            (
                f"{key}=MAX(COALESCE({key},0),?)"
                if key == "progress"
                else f"{key}=?"
            )
            for key in updates
        )
        con.execute(
            "UPDATE generation_tasks SET "
            + set_clause
            + " WHERE id=?",
            (*updates.values(), task_id),
        )
    _run_write_transaction(write)
    return get_generation_task(task_id)


def update_project_rows(
    project_id,
    *,
    nodes=None,
    edges=None,
    episode_groups=None,
    viewports=None,
    layouts=None,
    if_revision=None,
    snapshot_mode="none",
):
    """Patch graph rows without serializing the entire project payload."""
    def write(con):
        reconcile_project_projection_on_connection(con, project_id)
        row = con.execute(
            "SELECT revision FROM canvas_projects WHERE id=?",
            (project_id,),
        ).fetchone()
        if not row:
            return None
        current = int(row[0] or 0)
        if if_revision is not None and current != int(if_revision):
            raise RevisionConflictError(
                f"项目已更新：期望 revision={if_revision}，实际 revision={current}"
            )
        return sync_project_state(
            con,
            project_id,
            current + 1,
            nodes=nodes,
            edges=edges,
            episode_groups=episode_groups,
            viewports=viewports,
            layouts=layouts,
            snapshot_mode=snapshot_mode,
        )

    return _run_write_transaction(write)


def sync_projection_meta(con, project_id, payload, *, updated_at=""):
    if not isinstance(payload, dict):
        return
    stamp = str(updated_at or _now_iso())
    project_meta = {
        key: value
        for key, value in payload.items()
        if key not in {
            "nodes",
            "connections",
            "episodeImports",
            "episodeLayouts",
            "episodeViewports",
        }
    }
    con.execute(
        "INSERT INTO canvas_project_meta(project_id,meta_json,updated_at) "
        "VALUES(?,?,?) ON CONFLICT(project_id) DO UPDATE SET "
        "meta_json=excluded.meta_json,updated_at=excluded.updated_at",
        (
            project_id,
            json.dumps(project_meta, ensure_ascii=False),
            stamp,
        ),
    )


def update_project_node(
    project_id,
    node_id,
    *,
    patch_node=None,
    episode_id=None,
    if_revision=None,
    snapshot_mode="none",
):
    """Patch one node and one projection row without rewriting other nodes."""
    normalized_episode = str(episode_id or "").strip().upper()
    if not normalized_episode:
        raise ValueError("保存节点必须指定所属集")

    def write(con):
        row = con.execute(
            "SELECT title,payload_json,revision FROM canvas_projects WHERE id=?",
            (project_id,),
        ).fetchone()
        if not row:
            return None
        current_revision = int(row[2] or 0)
        if if_revision is not None and current_revision != int(if_revision):
            raise RevisionConflictError(
                f"项目已更新：期望 revision={if_revision}，实际 revision={current_revision}"
            )
        try:
            payload = json.loads(row[1] or "{}")
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        nodes = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
        node = next(
            (
                item
                for item in nodes
                if isinstance(item, dict) and item.get("id") == node_id
            ),
            None,
        )
        if node is None:
            return None
        owner = con.execute(
            "SELECT episode_id FROM canvas_projection_nodes "
            "WHERE project_id=? AND node_id=?",
            (project_id, node_id),
        ).fetchone()
        owner_episode = str(owner[0] or "").strip().upper() if owner else ""
        if (
            owner_episode
            and owner_episode != _UNASSIGNED_EPISODE_ID
            and owner_episode != normalized_episode
        ):
            raise ValueError(
                f"节点 {node_id} 属于 {owner_episode}，不能从 {normalized_episode} 保存"
            )
        if not owner_episode or owner_episode == _UNASSIGNED_EPISODE_ID:
            sync_projection_node(
                con,
                project_id,
                node,
                episode_id=normalized_episode,
                updated_at=_now_iso(),
            )
        incoming = patch_node if isinstance(patch_node, dict) else {}
        if isinstance(incoming.get("metadata"), dict):
            node["metadata"] = {
                **(node.get("metadata") or {}),
                **incoming["metadata"],
            }
        if isinstance(incoming.get("position"), dict):
            node["position"] = incoming["position"]
        if "title" in incoming:
            node["title"] = incoming["title"]
        if incoming.get("type") in {"text", "image", "audio", "video"}:
            node["type"] = incoming["type"]
        node["episodeId"] = normalized_episode
        node.setdefault("metadata", {})["episodeId"] = normalized_episode
        stamp = _now_iso()
        node["updatedAt"] = stamp
        payload["title"] = row[0]
        payload["revision"] = current_revision + 1
        payload["updatedAt"] = stamp
        con.execute(
            "UPDATE canvas_projects SET payload_json=?,revision=?,updated_at=?,"
            "node_count=?,connection_count=?,episode_count=?,failed_count=?,"
            "local_blocked_count=? WHERE id=?",
            (
                json.dumps(payload, ensure_ascii=False),
                current_revision + 1,
                stamp,
                *payload_stats(payload),
                project_id,
            ),
        )
        sync_projection_node(
            con,
            project_id,
            node,
            episode_id=normalized_episode,
            updated_at=stamp,
        )
        sync_projection_meta(con, project_id, payload, updated_at=stamp)
        _maybe_snapshot(
            con,
            project_id,
            row[0],
            payload,
            current_revision + 1,
            mode=snapshot_mode,
            now=stamp,
        )
        return {
            "revision": current_revision + 1,
            "updated_at": stamp,
            "payload": payload,
            "node": node,
        }

    return _run_write_transaction(write)


def _editable_node_value(node):
    if not isinstance(node, dict):
        return {}
    metadata = node.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    return {
        "title": str(node.get("title") or ""),
        "prompt": str(metadata.get("prompt") or ""),
        "refs": metadata.get("refs") or [],
        "params": {
            key: value
            for key, value in (metadata.get("params") or {}).items()
            if key not in {
                "mediaVersions",
                "videoVersions",
                "mediaVersionIndex",
                "videoVersionIndex",
                "providerTaskId",
                "providerSubmittedAt",
            }
        } if isinstance(metadata.get("params"), dict) else {},
    }


def patch_project_node_atomic(
    project_id,
    episode_id,
    node_id,
    patch_node,
    *,
    merge_metadata,
):
    normalized_episode = str(episode_id or "").strip().upper()
    if not normalized_episode:
        raise ValueError("保存节点必须指定所属集")

    def write(con):
        project_row = con.execute(
            "SELECT revision FROM canvas_projects WHERE id=?",
            (project_id,),
        ).fetchone()
        if not project_row:
            return None
        current_revision = int(project_row[0] or 0)
        row = con.execute(
            "SELECT node_json FROM canvas_projection_nodes "
            "WHERE project_id=? AND episode_id=? AND node_id=?",
            (project_id, normalized_episode, node_id),
        ).fetchone()
        if not row:
            return None
        source = json.loads(row[0] or "{}")
        if not isinstance(source, dict):
            return None
        incoming = patch_node if isinstance(patch_node, dict) else {}
        base_updated_at = str(incoming.get("_baseUpdatedAt") or "")
        if base_updated_at and str(source.get("updatedAt") or "") > base_updated_at:
            if _editable_node_value(incoming) != _editable_node_value(source):
                raise RevisionConflictError("另一个标签页已更新此节点")
        metadata = source.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        merge_metadata(metadata, incoming.get("metadata") or {})
        source["metadata"] = metadata
        if isinstance(incoming.get("position"), dict):
            source["position"] = incoming["position"]
        if "title" in incoming:
            source["title"] = incoming["title"]
        source["episodeId"] = normalized_episode
        source["metadata"]["episodeId"] = normalized_episode
        stamp = _now_iso()
        source["updatedAt"] = stamp
        changed = con.execute(
            "UPDATE canvas_projection_nodes SET episode_id=?,node_type=?,title=?,"
            "position_x=?,position_y=?,metadata_json=?,node_json=?,updated_at=? "
            "WHERE project_id=? AND episode_id=? AND node_id=?",
            (
                normalized_episode,
                str(source.get("type") or "text"),
                str(source.get("title") or ""),
                float((source.get("position") or {}).get("x") or 0),
                float((source.get("position") or {}).get("y") or 0),
                json.dumps(source["metadata"], ensure_ascii=False),
                json.dumps(source, ensure_ascii=False),
                stamp,
                project_id,
                normalized_episode,
                node_id,
            ),
        )
        if changed.rowcount != 1:
            return None
        next_revision = current_revision + 1
        payload_row = con.execute(
            "SELECT payload_json FROM canvas_projects WHERE id=?",
            (project_id,),
        ).fetchone()
        payload = {}
        if payload_row:
            try:
                payload = json.loads(payload_row[0] or "{}")
            except (TypeError, ValueError):
                payload = {}
        nodes = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
        for index, item in enumerate(nodes):
            if isinstance(item, dict) and item.get("id") == node_id:
                nodes[index] = source
                break
        else:
            nodes.append(source)
        payload["nodes"] = nodes
        payload["revision"] = next_revision
        payload["updatedAt"] = stamp
        stats = payload_stats(payload)
        sync_projection_meta(con, project_id, payload, updated_at=stamp)
        revision_changed = con.execute(
            "UPDATE canvas_projects SET payload_json=?,revision=?,updated_at=?,"
            "node_count=?,connection_count=?,episode_count=?,failed_count=?,"
            "local_blocked_count=? "
            "WHERE id=? AND revision=?",
            (
                json.dumps(payload, ensure_ascii=False),
                next_revision,
                stamp,
                *stats,
                project_id,
                current_revision,
            ),
        )
        if revision_changed.rowcount != 1:
            return None
        refresh_projection_episode_membership(
            con,
            project_id,
            normalized_episode,
            updated_at=stamp,
        )
        return source

    return _run_write_transaction(write)


def update_project_viewport(
    project_id,
    viewport,
    *,
    episode_id=None,
    if_revision=None,
):
    """Persist one viewport and legacy rollback copy without touching graph rows."""
    def write(con):
        row = con.execute(
            "SELECT title,payload_json,revision FROM canvas_projects WHERE id=?",
            (project_id,),
        ).fetchone()
        if not row:
            return None
        current_revision = int(row[2] or 0)
        if if_revision is not None and current_revision != int(if_revision):
            raise RevisionConflictError(
                f"项目已更新：期望 revision={if_revision}，实际 revision={current_revision}"
            )
        try:
            payload = json.loads(row[1] or "{}")
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        normalized = {
            "x": float(viewport.get("x") or 0),
            "y": float(viewport.get("y") or 0),
            "zoom": float(viewport.get("zoom") or 1),
        }
        ep_id = str(episode_id or "").strip().upper()
        if ep_id:
            payload.setdefault("episodeViewports", {})[ep_id] = normalized
        else:
            payload["viewport"] = normalized
        stamp = _now_iso()
        payload["revision"] = current_revision + 1
        payload["updatedAt"] = stamp
        cursor = con.execute(
            "UPDATE canvas_projects SET payload_json=?,revision=?,updated_at=? "
            "WHERE id=? AND revision=?",
            (
                json.dumps(payload, ensure_ascii=False),
                current_revision + 1,
                stamp,
                project_id,
                current_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise RevisionConflictError("项目写入冲突，请重新读取后再试")
        if ep_id:
            sync_projection_episode(
                con,
                project_id,
                ep_id,
                {
                    "id": ep_id,
                    "title": ep_id,
                    "nodeIds": [
                        str(row[0])
                        for row in con.execute(
                            "SELECT node_id FROM canvas_projection_nodes "
                            "WHERE project_id=? AND episode_id=? ORDER BY rowid",
                            (project_id, ep_id),
                        )
                    ],
                    "videoNodeIds": [
                        str(row[0])
                        for row in con.execute(
                            "SELECT node_id FROM canvas_projection_nodes "
                            "WHERE project_id=? AND episode_id=? "
                            "AND node_type='video' ORDER BY rowid",
                            (project_id, ep_id),
                        )
                    ],
                    "connectionIds": [],
                },
                viewport_json=json.dumps(normalized, ensure_ascii=False),
                updated_at=stamp,
            )
        sync_projection_meta(con, project_id, payload, updated_at=stamp)
        return {
            "viewport": normalized,
            "revision": current_revision + 1,
            "updated_at": stamp,
        }

    return _run_write_transaction(write)



def commit_generation_terminal(
    project_id,
    node_id,
    node_updates,
    *,
    task_id="",
    task_updates=None,
    event_kind="media",
    event_payload=None,
    lease_owner=None,
    snapshot_mode="auto",
):
    """Commit node result, task terminal state and outbox event atomically."""
    result = {
        "committed": False,
        "reason": "",
        "eventId": 0,
        "eventPayload": {},
        "nodeWrittenAt": "",
        "task": None,
    }

    def write(con):
        node_written_at = _now_iso()
        effective_task_updates = dict(task_updates or {})
        if "node_written_at" in effective_task_updates:
            effective_task_updates["node_written_at"] = (
                effective_task_updates["node_written_at"] or node_written_at
            )
        if "finished_at" in effective_task_updates:
            effective_task_updates["finished_at"] = (
                effective_task_updates["finished_at"] or node_written_at
            )
        row = con.execute(
            "SELECT title,payload_json,revision FROM canvas_projects WHERE id=?",
            (project_id,),
        ).fetchone()
        if not row:
            result["reason"] = "project_not_found"
            return
        task = None
        task_columns = [
            item[0]
            for item in con.execute("SELECT * FROM generation_tasks LIMIT 0").description
        ]
        if task_id:
            task_row = con.execute(
                "SELECT * FROM generation_tasks WHERE id=?",
                (task_id,),
            ).fetchone()
            task = dict(zip(task_columns, task_row)) if task_row else None
            if not task:
                result["reason"] = "task_not_found"
                return
            if str(effective_task_updates.get("status") or "") in {
                "succeeded", "failed", "interrupted", "cancelled",
            }:
                effective_task_updates["next_poll_at"] = ""
            if lease_owner is not None:
                owner = str(lease_owner or "")
                if (
                    str(task.get("lease_owner") or "") != owner
                    or str(task.get("status") or "") != "running"
                ):
                    result["reason"] = "lease_lost"
                    return
            fields = {
                key: value
                for key, value in effective_task_updates.items()
                if key in _TASK_FIELDS
            }
            merged = _merge_generation_task_update(task, fields)
            if merged:
                merged["updated_at"] = _now_iso()
                cursor = con.execute(
                    "UPDATE generation_tasks SET "
                    + ",".join(f"{key}=?" for key in merged)
                    + (
                        " WHERE id=? AND lease_owner=? AND status='running'"
                        if lease_owner is not None
                        else " WHERE id=?"
                    ),
                    (
                        *merged.values(),
                        task_id,
                        *(
                            [str(lease_owner or "")]
                            if lease_owner is not None
                            else []
                        ),
                    ),
                )
                if cursor.rowcount != 1:
                    result["reason"] = "lease_lost"
                    return
                task = {
                    **task,
                    **merged,
                }

        # Projection rows are the source of truth after the write-layer
        # migration. The legacy payload_json column may be empty or stale for
        # newly inserted nodes, so reading it here makes every terminal write
        # fail with `node_not_found` even though the node is visible on canvas.
        payload = get_projection_payload(con, project_id)
        if not isinstance(payload, dict):
            result["reason"] = "project_not_found"
            return
        nodes = payload.get("nodes") or []
        node = next(
            (item for item in nodes if item.get("id") == node_id),
            None,
        )
        if not node:
            result["reason"] = "node_not_found"
            return
        meta = node.setdefault("metadata", {})
        pending = dict(node_updates or {})
        params_patch = pending.pop("_paramsPatch", None)
        for key, value in pending.items():
            if value is None:
                meta.pop(key, None)
            else:
                meta[key] = value
        if isinstance(params_patch, dict) and params_patch:
            meta["params"] = {
                **(meta.get("params") or {}),
                **params_patch,
            }
        node["updatedAt"] = node_written_at

        current_revision = int(row[2])
        new_revision = current_revision + 1
        new_payload = json.loads(json.dumps(payload, ensure_ascii=False))
        new_payload["revision"] = new_revision
        new_payload["updatedAt"] = node_written_at
        title = row[0]
        cursor = con.execute(
            "UPDATE canvas_projects SET payload_json=?,revision=?,updated_at=?,"
            "node_count=?,connection_count=?,episode_count=?,"
            "failed_count=?,local_blocked_count=? "
            "WHERE id=? AND revision=?",
            (
                json.dumps(new_payload, ensure_ascii=False),
                new_revision,
                node_written_at,
                *payload_stats(new_payload),
                project_id,
                current_revision,
            ),
        )
        if cursor.rowcount != 1:
            result["reason"] = "project_revision_conflict"
            raise RevisionConflictError("项目写入冲突，请重新读取后再试")
        sync_payload_projection(
            con,
            project_id,
            new_payload,
            updated_at=node_written_at,
        )
        _maybe_snapshot(
            con,
            project_id,
            title,
            new_payload,
            new_revision,
            mode=snapshot_mode,
            now=node_written_at,
        )
        event_cursor = con.execute(
            "INSERT INTO canvas_events(project_id,kind,node_id,payload_json,created_at) "
            "VALUES(?,?,?,?,?)",
            (
                project_id,
                str(event_kind or "media"),
                str(node_id or ""),
                json.dumps(event_payload or {}, ensure_ascii=False),
                node_written_at,
            ),
        )
        event_id = int(event_cursor.lastrowid or 0)
        if event_id > 0:
            con.execute(
                "DELETE FROM canvas_events WHERE project_id=? AND id<=?",
                (project_id, event_id - 2000),
            )
        result.update({
            "committed": True,
            "reason": "",
            "eventId": event_id,
            "eventPayload": event_payload or {},
            "nodeWrittenAt": node_written_at,
            "task": task,
        })

    _run_write_transaction(write)
    return result


def finish_generation_task(
    task_id,
    *,
    status,
    phase,
    error="",
    progress=None,
    provider_task_id=None,
    local_result_url=None,
    provider_result_url=None,
    delivery_status=None,
    result_ready_at=None,
    lease_owner=None,
):
    """Write a terminal state and release the active slot in one transaction."""
    updates = {
        "status": str(status),
        "phase": str(phase or ""),
        "error": str(error or ""),
        "finished_at": _now_iso(),
        "lease_owner": "",
    }
    updates["lease_expires_at"] = None
    if progress is not None:
        updates["progress"] = int(progress)
    if provider_task_id is not None:
        updates["provider_task_id"] = str(provider_task_id or "")
    if local_result_url is not None:
        updates["local_result_url"] = str(local_result_url or "")
    if provider_result_url is not None:
        updates["provider_result_url"] = str(provider_result_url or "")
    if delivery_status is not None:
        updates["delivery_status"] = str(delivery_status or "")
    if result_ready_at is not None:
        updates["result_ready_at"] = str(result_ready_at or "")

    wrote = {"value": False}

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
        merged = _merge_generation_task_update(current, updates)
        if not merged:
            return
        if lease_owner is not None:
            owner = str(lease_owner or "")
            merged["lease_owner"] = ""
            merged["lease_expires_at"] = None
            result = con.execute(
                "UPDATE generation_tasks SET "
                + ",".join(f"{key}=?" for key in merged)
                + " WHERE id=? AND lease_owner=?",
                (*merged.values(), task_id, owner),
            )
            wrote["value"] = result.rowcount == 1
            return
        result = con.execute(
            "UPDATE generation_tasks SET "
            + ",".join(f"{key}=?" for key in merged)
            + " WHERE id=?",
            (*merged.values(), task_id),
        )
        wrote["value"] = result.rowcount == 1

    _run_write_transaction(write)
    return get_generation_task(task_id) if wrote["value"] else None


def reopen_generation_task_for_provider(
    task_id,
    provider_task_id,
    *,
    message,
):
    """Re-open only a failed row that proves the same provider task is live."""
    task_id = str(task_id or "").strip()
    provider_task_id = str(provider_task_id or "").strip()
    if not task_id or not provider_task_id:
        return None
    reopened = {"value": False}

    def write(con):
        result = con.execute(
            "UPDATE generation_tasks SET status='interrupted',phase='结果待确认',"
            "error=?,provider_task_id=?,delivery_status='pending',"
            "lease_owner='',lease_expires_at=NULL,finished_at='',updated_at=? "
            "WHERE id=? AND status='failed' "
            "AND COALESCE(phase,'') NOT IN ('查询失败','生成失败') "
            "AND COALESCE(error,'') NOT LIKE '%供应商明确返回任务不存在%' "
            "AND COALESCE(error,'') NOT LIKE '%HTTP 404%' "
            "AND COALESCE(error,'') NOT LIKE '%供应商视频生成失败%' "
            "AND COALESCE(error,'') NOT LIKE '%供应商图片生成失败%' "
            "AND COALESCE(error,'') NOT LIKE '%供应商返回生成失败%' "
            "AND (provider_task_id=? OR provider_task_id IS NULL OR provider_task_id='')",
            (
                str(message or ""),
                provider_task_id,
                _now_iso(),
                task_id,
                provider_task_id,
            ),
        )
        reopened["value"] = result.rowcount == 1

    _run_write_transaction(write)
    return get_generation_task(task_id) if reopened["value"] else None


def claim_generation_task(task_id, owner, lease_seconds=45, allow_recover=False):
    owner = str(owner or "").strip()
    if not owner:
        raise ValueError("任务执行者不能为空")
    now = datetime.now(timezone.utc)
    lease_expires_at = (now + timedelta(seconds=max(5, int(lease_seconds)))).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"
    claimed = {"value": False}

    def write(con):
        row = con.execute(
            "SELECT status,lease_owner,lease_expires_at,provider_task_id "
            "FROM generation_tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        if not row:
            return
        (
            current_status,
            current_owner,
            current_lease_expires_at,
            current_provider_task_id,
        ) = row
        if current_status == "running" and current_owner == owner:
            con.execute(
                "UPDATE generation_tasks SET lease_expires_at=?,heartbeat_at=?,updated_at=? "
                "WHERE id=? AND status='running' AND lease_owner=?",
                (lease_expires_at, now.isoformat(), now.isoformat(), task_id, owner),
            )
            claimed["value"] = True
            return
        if current_status == "running" and current_owner and current_owner != owner:
            existing_lease_expires_at = str(current_lease_expires_at or "")
            if (
                existing_lease_expires_at
                and existing_lease_expires_at > now.isoformat()
            ):
                return
        if (
            not allow_recover
            and current_status == "running"
            and not current_owner
            and not str(current_provider_task_id or "").strip()
        ):
            # A queued task can be pre-marked running before the worker claims
            # its lease. Reclaim that ownerless local row; otherwise the stale
            # scanner can close it a few seconds before the provider callback
            # persists the real provider task id.
            result = con.execute(
                "UPDATE generation_tasks SET status='running', phase='提交供应商',"
                "lease_owner=?,lease_expires_at=?,heartbeat_at=?,updated_at=?,"
                "started_at=CASE WHEN started_at IS NULL OR started_at='' "
                "THEN ? ELSE started_at END,error='',finished_at='' "
                "WHERE id=? AND status='running' "
                "AND (lease_owner IS NULL OR lease_owner='') "
                "AND COALESCE(provider_task_id,'')=''",
                (
                    owner,
                    lease_expires_at,
                    now.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                    task_id,
                ),
            )
            claimed["value"] = result.rowcount == 1
            return
        if allow_recover:
            status_clause = (
                "(status IN ('queued','submitting','interrupted','failed') "
                "OR (status='running' AND "
                "(lease_owner IS NULL OR lease_owner='' OR lease_expires_at IS NULL OR lease_expires_at<=?)))"
            )
            status_params = (now.isoformat(),)
        else:
            status_clause = "status IN ('queued','submitting')"
            status_params = ()
        result = con.execute(
            "UPDATE generation_tasks SET status='running', phase='已领取执行租约',"
            "lease_owner=?,lease_expires_at=?,heartbeat_at=?,updated_at=?,"
            "started_at=CASE WHEN started_at IS NULL OR started_at='' THEN ? ELSE started_at END,"
            "error='',finished_at='' "
            f"WHERE id=? AND {status_clause} ",
            (
                owner,
                lease_expires_at,
                now.isoformat(),
                now.isoformat(),
                now.isoformat(),
                task_id,
                *status_params,
            ),
        )
        claimed["value"] = result.rowcount == 1

    _run_write_transaction(write)
    return claimed["value"]


def claim_generation_task_for_recovery(task_id, provider_task_id, owner, lease_seconds=45):
    """Atomically claim one persisted provider task for a single poller.

    The provider task id is part of the ownership predicate. This prevents a
    startup reconciliation and a foreground request from querying and
    finalizing the same paid task twice.
    """
    task_id = str(task_id or "").strip()
    provider_task_id = str(provider_task_id or "").strip()
    owner = str(owner or "").strip()
    if not task_id or not provider_task_id or not owner:
        return False
    now = datetime.now(timezone.utc)
    now_text = now.isoformat()
    lease_expires_at = (now + timedelta(seconds=max(5, int(lease_seconds)))).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"
    claimed = {"value": False}

    def write(con):
        result = con.execute(
            "UPDATE generation_tasks SET status='running',phase='已领取查询租约',"
            "lease_owner=?,lease_expires_at=?,heartbeat_at=?,updated_at=?,"
            "error='',finished_at='' "
            "WHERE id=? AND provider_task_id=? "
            "AND (status IN ('queued','submitting','interrupted') "
            "OR status='running' AND ("
            "lease_owner IS NULL OR lease_owner='' "
            "OR lease_expires_at IS NULL OR lease_expires_at<=?))",
            (
                owner,
                lease_expires_at,
                now_text,
                now_text,
                task_id,
                provider_task_id,
                now_text,
            ),
        )
        if result.rowcount == 1:
            claimed["value"] = True
            return
        result = con.execute(
            "UPDATE generation_tasks SET status='running',phase='已领取查询租约',"
            "lease_owner=?,lease_expires_at=?,heartbeat_at=?,updated_at=?,"
            "error='',finished_at='' "
            "WHERE id=? AND provider_task_id=? AND status='running' "
            "AND (lease_owner IS NULL OR lease_owner='' OR lease_expires_at IS NULL OR lease_expires_at<=?)",
            (
                owner,
                lease_expires_at,
                now_text,
                now_text,
                task_id,
                provider_task_id,
                now_text,
            ),
        )
        claimed["value"] = result.rowcount == 1

    _run_write_transaction(write)
    return claimed["value"]


def renew_generation_task_lease(task_id, owner, lease_seconds=45):
    owner = str(owner or "").strip()
    now = datetime.now(timezone.utc)
    lease_expires_at = (now + timedelta(seconds=max(5, int(lease_seconds)))).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"
    renewed = {"value": False}

    def write(con):
        result = con.execute(
            "UPDATE generation_tasks SET lease_expires_at=?,heartbeat_at=?,updated_at=? "
            "WHERE id=? AND status='running' AND lease_owner=?",
            (
                lease_expires_at,
                now.isoformat(),
                now.isoformat(),
                task_id,
                owner,
            ),
        )
        renewed["value"] = result.rowcount == 1

    _run_write_transaction(write)
    return renewed["value"]


def release_generation_task_lease(task_id, owner):
    owner = str(owner or "").strip()
    if not owner:
        return

    def write(con):
        # Do not release a terminal row's historical (already-empty) lease.
        con.execute(
            "UPDATE generation_tasks SET lease_owner='',lease_expires_at=NULL,updated_at=? "
            "WHERE id=? AND status='running' AND lease_owner=?",
            (_now_iso(), task_id, owner),
        )

    _run_write_transaction(write)


def expire_stale_generation_tasks(now=None):
    current = now or datetime.now(timezone.utc)
    now_iso = current.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    launch_grace_since = (
        current - timedelta(seconds=120)
    ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    expired = []
    journal_by_task = {}
    try:
        for entry in list_provider_task_journal():
            task_id = str(entry.get("taskId") or "").strip()
            provider_task_id = str(entry.get("providerTaskId") or "").strip()
            if task_id and provider_task_id:
                journal_by_task[task_id] = provider_task_id
    except Exception:
        journal_by_task = {}
    def write(con):
        rows = con.execute(
            "SELECT id,node_id,status,phase,provider_task_id,lease_owner,created_at "
            "FROM generation_tasks "
            "WHERE status IN ('queued','submitting','running') "
            "AND (lease_expires_at IS NULL OR lease_expires_at='' OR lease_expires_at<=?) "
            "ORDER BY created_at ASC",
            (now_iso,),
        ).fetchall()
        for row in rows:
            (
                task_id,
                node_id,
                status,
                phase,
                provider_task_id,
                lease_owner,
                created_at,
            ) = row
            if status in {"queued", "submitting"} and str(created_at or "") > launch_grace_since:
                # Worker thread startup is asynchronous. A fresh queued row is
                # not stale merely because no lease exists yet.
                continue
            if not str(provider_task_id or "").strip():
                provider_task_id = journal_by_task.get(task_id, "")
                if provider_task_id:
                    # The callback/journal can land after a blocked supplier
                    # request but before SQLite receives the provider id. Never
                    # classify that task as "no provider task" while the
                    # durable journal already proves otherwise.
                    con.execute(
                        "UPDATE generation_tasks SET provider_task_id=?,"
                        "updated_at=? WHERE id=? AND COALESCE(provider_task_id,'')=''",
                        (provider_task_id, now_iso, task_id),
                    )
            has_provider_task = bool(str(provider_task_id or "").strip())
            next_status = "interrupted" if has_provider_task else "failed"
            next_phase = "结果待确认" if has_provider_task else "执行失联"
            detail = (
                "执行租约已过期，但已有供应商任务号；系统会继续查询原任务，不会自动重新提交。"
                if has_provider_task
                else "执行线程已失联且没有供应商任务号；本次可安全重新生成。"
            )
            con.execute(
                "UPDATE generation_tasks SET status=?,phase=?,error=?,"
                "lease_owner='',lease_expires_at=NULL,finished_at=?,updated_at=? "
                "WHERE id=? AND status=? AND lease_owner=?",
                (
                    next_status,
                    next_phase,
                    detail,
                    now_iso,
                    now_iso,
                    task_id,
                    status,
                    lease_owner,
                ),
            )
            expired.append(
                {
                    "id": task_id,
                    "projectId": (
                        con.execute(
                            "SELECT project_id FROM generation_tasks WHERE id=?",
                            (task_id,),
                        ).fetchone()[0]
                    ),
                    "nodeId": (
                        con.execute(
                            "SELECT node_id FROM generation_tasks WHERE id=?",
                            (task_id,),
                        ).fetchone()[0]
                    ),
                    "providerTaskId": str(provider_task_id or ""),
                    "tenantId": tenant_context.current_tenant_id(),
                    "tenantSlug": tenant_context.current_slug(),
                    "status": next_status,
                    "phase": next_phase,
                    "error": detail,
                }
            )

    _run_write_transaction(write)
    return expired


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


def list_generation_tasks(project_id=None, limit=80, episode_id=None):
    con = _connect()
    columns = [item[0] for item in con.execute("SELECT * FROM generation_tasks LIMIT 0").description]
    normalized_episode = str(episode_id or "").strip().upper()
    if not project_id:
        rows = con.execute(
            "SELECT * FROM generation_tasks "
            "ORDER BY created_at DESC, updated_at DESC, rowid DESC LIMIT ?",
            (max(1, min(int(limit or 80), 300)),),
        ).fetchall()
    elif normalized_episode:
        rows = con.execute(
            "SELECT * FROM generation_tasks "
            "WHERE project_id=? AND UPPER(COALESCE(episode_id,''))=? "
            "AND COALESCE(episode_id,'')!='__UNSCOPED__' "
            "ORDER BY created_at DESC, updated_at DESC, rowid DESC LIMIT ?",
            (
                project_id,
                normalized_episode,
                max(1, min(int(limit or 80), 300)),
            ),
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM generation_tasks WHERE project_id=? "
            "ORDER BY created_at DESC, updated_at DESC, rowid DESC LIMIT ?",
            (project_id, max(1, min(int(limit or 80), 300))),
        ).fetchall()
    con.close()
    return [dict(zip(columns, row)) for row in rows]


def list_generation_tasks_for_node_ids(
    project_id,
    node_ids,
    limit=300,
):
    """List task history by node without an earlier project-wide truncation."""
    normalized = [
        str(node_id)
        for node_id in (node_ids or [])
        if str(node_id or "").strip()
    ]
    if not project_id or not normalized:
        return []
    capped_limit = max(1, min(int(limit or 300), 1000))
    con = _connect()
    columns = [
        item[0]
        for item in con.execute(
            "SELECT * FROM generation_tasks LIMIT 0"
        ).description
    ]
    tasks_by_id = {}
    for offset in range(0, len(normalized), 200):
        batch = normalized[offset:offset + 200]
        placeholders = ",".join("?" for _ in batch)
        rows = con.execute(
            "SELECT * FROM generation_tasks "
            f"WHERE project_id=? AND node_id IN ({placeholders}) "
            "ORDER BY created_at DESC, updated_at DESC, rowid DESC LIMIT ?",
            (project_id, *batch, capped_limit),
        ).fetchall()
        for row in rows:
            task = dict(zip(columns, row))
            tasks_by_id[str(task.get("id") or "")] = task
    con.close()
    tasks = list(tasks_by_id.values())
    tasks.sort(
        key=lambda task: (
            str(task.get("created_at") or ""),
            str(task.get("updated_at") or ""),
        ),
        reverse=True,
    )
    return tasks[:capped_limit]


def list_latest_success_task_for_node(project_id, node_id):
    """Return the newest successful task that still carries a provider URL."""
    if not project_id or not node_id:
        return None
    con = _connect()
    columns = [
        item[0]
        for item in con.execute(
            "SELECT * FROM generation_tasks LIMIT 0"
        ).description
    ]
    row = con.execute(
        "SELECT * FROM generation_tasks "
        "WHERE project_id=? AND node_id=? AND status='succeeded' "
        "AND COALESCE(provider_result_url,'')!='' "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (project_id, node_id),
    ).fetchone()
    con.close()
    return dict(zip(columns, row)) if row else None


def list_due_provider_tasks(limit=50, now=None):
    """Return only provider tasks whose indexed next poll is due.

    This is intentionally a bounded query. Recovery must never scan every
    project or every historical task on a timer.
    """
    current = str(now or _now_iso())
    capped = max(1, min(int(limit or 50), 200))
    con = _connect()
    columns = [
        item[0]
        for item in con.execute(
            "SELECT * FROM generation_tasks LIMIT 0"
        ).description
    ]
    rows = con.execute(
        "SELECT * FROM generation_tasks "
        "WHERE provider_task_id IS NOT NULL AND provider_task_id != '' "
        "AND status IN ('queued','submitting','running','interrupted') "
        "AND COALESCE(next_poll_at, '') <= ? "
        "ORDER BY next_poll_at ASC, created_at ASC LIMIT ?",
        (current, capped),
    ).fetchall()
    con.close()
    return [dict(zip(columns, row)) for row in rows]


def schedule_provider_task_poll(task_id, next_poll_at):
    if not task_id:
        return None
    return update_generation_task(task_id, next_poll_at=str(next_poll_at or ""))


def get_latest_succeeded_generation_task_for_node(
    project_id,
    node_id,
    *,
    kind="",
):
    """Fetch one successful task receipt without scanning project history."""
    if not project_id or not node_id:
        return None
    normalized_kind = str(kind or "").strip()
    con = _connect()
    columns = [
        item[0]
        for item in con.execute(
            "SELECT * FROM generation_tasks LIMIT 0"
        ).description
    ]
    sql = (
        "SELECT * FROM generation_tasks "
        "WHERE project_id=? AND node_id=? AND status='succeeded' "
        "AND COALESCE(provider_result_url,'')!='' "
    )
    params = [project_id, node_id]
    if normalized_kind:
        sql += "AND kind=? "
        params.append(normalized_kind)
    sql += "ORDER BY created_at DESC, updated_at DESC, rowid DESC LIMIT 1"
    row = con.execute(sql, params).fetchone()
    con.close()
    return dict(zip(columns, row)) if row else None


def list_succeeded_generation_task_receipts(project_id, kind=""):
    """Return one latest successful receipt per node without N+1 queries."""
    if not project_id:
        return []
    normalized_kind = str(kind or "").strip()
    con = _connect()
    columns = [
        item[0]
        for item in con.execute(
            "SELECT * FROM generation_tasks LIMIT 0"
        ).description
    ]
    sql = (
        "SELECT * FROM ("
        "SELECT generation_tasks.*, ROW_NUMBER() OVER ("
        "PARTITION BY node_id ORDER BY created_at DESC, updated_at DESC, rowid DESC"
        ") AS receipt_rank FROM generation_tasks "
        "WHERE project_id=? AND status='succeeded' "
        "AND COALESCE(provider_result_url,'')!='' "
    )
    params = [project_id]
    if normalized_kind:
        sql += "AND kind=? "
        params.append(normalized_kind)
    sql += ") WHERE receipt_rank=1"
    rows = con.execute(sql, params).fetchall()
    con.close()
    receipts = []
    for row in rows:
        task = dict(zip([*columns, "receipt_rank"], row))
        task.pop("receipt_rank", None)
        receipts.append(task)
    return receipts


def get_episode_node_ids(project_id, episode_id):
    """Return node ids from database ownership, the sole runtime truth."""
    normalized_episode = str(episode_id or "").strip().upper()
    if not project_id or not normalized_episode:
        return []
    con = _connect()
    try:
        values = con.execute(
            "SELECT node_id FROM canvas_projection_nodes "
            "WHERE project_id=? AND episode_id=? ORDER BY rowid",
            (project_id, normalized_episode),
        ).fetchall()
    finally:
        con.close()
    return [str(value[0]) for value in values if value[0]]


def list_queued_generation_tasks(limit=100):
    con = _connect()
    columns = [item[0] for item in con.execute("SELECT * FROM generation_tasks LIMIT 0").description]
    rows = con.execute(
        "SELECT * FROM generation_tasks WHERE status='queued' "
        "ORDER BY created_at LIMIT ?",
        (max(1, min(int(limit or 100), 300)),),
    ).fetchall()
    con.close()
    return [dict(zip(columns, row)) for row in rows]


def get_active_generation_task_for_node(project_id, node_id):
    """Return a truly active task for one node, never a terminal duplicate."""
    if not project_id or not node_id:
        return None
    con = _connect()
    columns = [
        item[0]
        for item in con.execute(
            "SELECT * FROM generation_tasks LIMIT 0"
        ).description
    ]
    row = con.execute(
        "SELECT * FROM generation_tasks "
        "WHERE project_id=? AND node_id=? "
        "AND status IN ('queued','submitting','running') "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (project_id, node_id),
    ).fetchone()
    con.close()
    return dict(zip(columns, row)) if row else None


def list_resumable_video_tasks(limit=300):
    """Return unresolved video tasks that need a same-task provider query."""
    con = _connect()
    columns = [
        item[0]
        for item in con.execute(
            "SELECT * FROM generation_tasks LIMIT 0"
        ).description
    ]
    rows = con.execute(
        "SELECT * FROM generation_tasks "
        "WHERE kind='video' "
        "AND status IN ('running','interrupted') "
        "AND COALESCE(provider_task_id,'')!='' "
        "AND COALESCE(local_result_url,'')='' "
        "AND COALESCE(phase,'') NOT IN ('查询失败','生成失败') "
        "ORDER BY updated_at DESC LIMIT ?",
        (max(1, min(int(limit or 300), 500)),),
    ).fetchall()
    con.close()
    return [dict(zip(columns, row)) for row in rows]


def backfill_generation_task_episode_ids():
    """Attach legacy task rows to the episode group that owns their node."""
    updated = 0
    for project_row in list_projects():
        project = get_project(project_row.get("id"))
        if not project:
            continue
        owners = {}
        for group in project.get("payload", {}).get("episodeImports", []):
            episode_id = str(group.get("id") or "").strip().upper()
            if not episode_id:
                continue
            for node_id in group.get("nodeIds", []):
                if node_id and node_id not in owners:
                    owners[str(node_id)] = episode_id
        if not owners:
            continue
        con = _connect()
        rows = con.execute(
            "SELECT id,node_id FROM generation_tasks "
            "WHERE project_id=? AND COALESCE(episode_id,'')=''",
            (project_row.get("id"),),
        ).fetchall()
        con.close()
        for task_id, node_id in rows:
            episode_id = owners.get(str(node_id or ""))
            if not episode_id:
                continue
            update_generation_task(task_id, episode_id=episode_id)
            updated += 1
        con = _connect()
        con.execute(
            "UPDATE generation_tasks SET episode_id='__UNSCOPED__' "
            "WHERE project_id=? AND COALESCE(episode_id,'')=''",
            (project_row.get("id"),),
        )
        con.commit()
        con.close()
    return updated


def list_delivery_pending_tasks(limit=100):
    con = _connect()
    columns = [item[0] for item in con.execute("SELECT * FROM generation_tasks LIMIT 0").description]
    rows = con.execute(
        "SELECT * FROM generation_tasks "
        "WHERE status IN ('running','interrupted','succeeded') "
        "AND delivery_status IN ('pending','materializing') "
        "ORDER BY updated_at DESC LIMIT ?",
        (max(1, min(int(limit or 100), 500)),),
    ).fetchall()
    con.close()
    return [dict(zip(columns, row)) for row in rows]


def list_runtime_reconcile_tasks(limit=200):
    """Return only task rows that a periodic reconciler may need to inspect."""
    con = _connect()
    columns = [
        item[0]
        for item in con.execute(
            "SELECT * FROM generation_tasks LIMIT 0"
        ).description
    ]
    rows = con.execute(
        "SELECT * FROM generation_tasks "
        "WHERE status IN ('queued','submitting','running','failed','interrupted') "
        "AND ("
        "status IN ('queued','submitting','running') "
        "OR COALESCE(delivery_status,'pending') IN ('pending','materializing') "
        "OR COALESCE(provider_task_id,'')!=''"
        ") "
        "ORDER BY updated_at DESC LIMIT ?",
        (max(1, min(int(limit or 200), 500)),),
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


def migrate_active_tenant_schemas():
    """Open every known tenant database so all schemas reach the current version."""
    migrated = []
    import tenant_registry

    for tenant in tenant_registry.list_tenants(include_disabled=True):
        try:
            with tenant_context.use_tenant(tenant["id"], tenant["slug"]):
                _connect()
                migrated.append(tenant["slug"])
        except Exception as exc:
            print(
                f"[schema] tenant migration failed: {tenant.get('slug')} {exc}",
                file=__import__("sys").stderr,
            )
    return migrated


def backfill_generation_delivery_status():
    """Backfill delivery receipts for historical rows without guessing outcomes."""
    changed = 0

    def write(con):
        nonlocal changed
        ready = con.execute(
            "UPDATE generation_tasks SET delivery_status='ready' "
            "WHERE status='succeeded' "
            "AND (delivery_status IS NULL OR delivery_status='' OR delivery_status='pending') "
            "AND ("
            "(local_result_url IS NOT NULL AND local_result_url!='') "
            "OR (node_written_at IS NOT NULL AND node_written_at!='')"
            ")"
        )
        changed += ready.rowcount
        failed = con.execute(
            "UPDATE generation_tasks SET delivery_status='failed' "
            "WHERE status IN ('failed','interrupted') "
            "AND (delivery_status IS NULL OR delivery_status='' OR delivery_status='pending')"
        )
        changed += failed.rowcount

    _run_write_transaction(write)
    return changed


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


def storage_stats() -> dict:
    """当前库的页占用情况（只读，用于判断是否需要回收）。"""
    path = _db_path()
    if not os.path.exists(path):
        return {"path": path, "page_count": 0, "free_pages": 0, "file_bytes": 0,
                "free_bytes": 0, "free_ratio": 0.0}
    con = None
    try:
        con = sqlite3.connect(path, timeout=5)
        page_size = int(con.execute("PRAGMA page_size").fetchone()[0] or 0)
        page_count = int(con.execute("PRAGMA page_count").fetchone()[0] or 0)
        free_pages = int(con.execute("PRAGMA freelist_count").fetchone()[0] or 0)
    except sqlite3.Error:
        # 库被独占（VACUUM / 长事务）时不能把异常抛给后台线程或接口，
        # 统计不到就当"无需回收"，下一轮再看。
        return {"path": path, "page_count": 0, "free_pages": 0, "file_bytes": 0,
                "free_bytes": 0, "free_ratio": 0.0}
    finally:
        if con is not None:
            con.close()
    free_bytes = free_pages * page_size
    return {
        "path": path,
        "page_count": page_count,
        "free_pages": free_pages,
        "page_size": page_size,
        "file_bytes": page_count * page_size,
        "free_bytes": free_bytes,
        "free_ratio": (free_pages / page_count) if page_count else 0.0,
    }


def maintain_storage(min_free_ratio: float = 0.35, min_free_bytes: int = 32 * 1024 * 1024) -> int:
    """空闲页过多时 VACUUM 回收磁盘，返回释放的字节数。

    为什么需要它：项目仍按整包 `payload_json` 覆盖写（每次节点编辑重写整份 JSON），
    SQLite 会把旧页放进 freelist 并且**永不归还操作系统**。实测 175MB 的库里
    有 145MB 是空闲页（83%），文件只涨不缩。VACUUM 会重建库文件把空洞还回去。

    触发条件保守：只有「空闲占比 > min_free_ratio 且空闲 > min_free_bytes」才执行，
    避免为几 MB 碎片去锁库。VACUUM 需要短暂独占写锁，调用方应放在低峰或启动后台线程里。
    """
    path = _db_path()
    if not os.path.exists(path):
        return 0
    stats = storage_stats()
    if stats["free_ratio"] < min_free_ratio or stats["free_bytes"] < min_free_bytes:
        return 0
    before = stats["file_bytes"]
    with _SCHEMA_LOCK:
        con = sqlite3.connect(path, timeout=60, isolation_level=None)
        try:
            con.execute("PRAGMA busy_timeout=60000")
            con.execute("VACUUM")
        except sqlite3.OperationalError:
            # 有长事务在跑（大导入 / 生成落盘），下一轮再收。
            return 0
        finally:
            con.close()
    after = storage_stats()["file_bytes"]
    return max(0, before - after)


def checkpoint_wal(*, mode: str = "PASSIVE") -> dict:
    """Move WAL pages back into the main database outside business commits."""
    path = _db_path()
    if not os.path.exists(path):
        return {"ok": True, "busy": 0, "log": 0, "checkpointed": 0}
    normalized = str(mode or "PASSIVE").strip().upper()
    if normalized not in {"PASSIVE", "TRUNCATE"}:
        raise ValueError("不支持的 WAL checkpoint 模式")
    con = None
    try:
        con = sqlite3.connect(path, timeout=1)
        con.execute("PRAGMA busy_timeout=1000")
        con.execute("PRAGMA wal_autocheckpoint=0")
        busy, log, checkpointed = con.execute(
            f"PRAGMA wal_checkpoint({normalized})"
        ).fetchone()
        return {
            "ok": int(busy or 0) == 0,
            "busy": int(busy or 0),
            "log": int(log or 0),
            "checkpointed": int(checkpointed or 0),
        }
    except (sqlite3.Error, OSError):
        return {"ok": False, "busy": 1, "log": 0, "checkpointed": 0}
    finally:
        if con is not None:
            con.close()
