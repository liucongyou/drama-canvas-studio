from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.config import get_settings

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pg_compat  # noqa: E402


def _sqlite_test_mode_enabled() -> bool:
    return os.getenv("CANVAS_ALLOW_SQLITE", "").strip() == "1"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect():
    settings = get_settings()
    try:
        use_postgres = pg_compat.postgres_enabled()
    except RuntimeError:
        # Tests historically only set DB_PATH. Keep them isolated while the
        # production path remains fail-closed for PostgreSQL configuration.
        if not _sqlite_test_mode_enabled():
            raise
        use_postgres = False
    if use_postgres:
        conn = pg_compat.connect_path(settings.db_path, "agent")
    else:
        if not _sqlite_test_mode_enabled():
            raise RuntimeError("Agent 生产模式拒绝回退 SQLite")
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(settings.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db() -> Iterator[Any]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS skills (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                active_version TEXT NOT NULL,
                root_path TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS skill_versions (
                skill_id TEXT NOT NULL,
                version TEXT NOT NULL,
                root_path TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (skill_id, version)
            );
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                mime TEXT NOT NULL DEFAULT '',
                size INTEGER NOT NULL DEFAULT 0,
                text TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL DEFAULT '',
                account_id TEXT NOT NULL DEFAULT '',
                skill_id TEXT NOT NULL,
                skill_version TEXT NOT NULL,
                model_id TEXT NOT NULL DEFAULT '',
                route_id TEXT NOT NULL DEFAULT '',
                locks_json TEXT NOT NULL DEFAULT '{}',
                file_ids_json TEXT NOT NULL DEFAULT '[]',
                reference_ids_json TEXT NOT NULL DEFAULT '[]',
                conversation_id TEXT NOT NULL DEFAULT '',
                prompt TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '',
                error_code TEXT NOT NULL DEFAULT '',
                current_stage TEXT NOT NULL DEFAULT '',
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                idempotency_key TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL DEFAULT '',
                finished_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS stages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 1,
                detail_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
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
            CREATE TABLE IF NOT EXISTS conversation_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                run_id TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL,
                content_json TEXT NOT NULL DEFAULT '[]',
                content_text TEXT NOT NULL DEFAULT '',
                sequence_no INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_drafts (
                tenant_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                text TEXT NOT NULL DEFAULT '',
                attachment_ids_json TEXT NOT NULL DEFAULT '[]',
                reference_json TEXT NOT NULL DEFAULT '[]',
                skill_id TEXT NOT NULL DEFAULT '',
                route_id TEXT NOT NULL DEFAULT '',
                model_id TEXT NOT NULL DEFAULT '',
                locks_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, account_id, conversation_id)
            );
            CREATE TABLE IF NOT EXISTS tool_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                args_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL,
                result_json TEXT NOT NULL DEFAULT '{}',
                result_bytes INTEGER NOT NULL DEFAULT 0,
                error_code TEXT NOT NULL DEFAULT '',
                idempotency_key TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS ix_messages_run ON messages(run_id, id);
            CREATE INDEX IF NOT EXISTS ix_stages_run ON stages(run_id, id);
            CREATE INDEX IF NOT EXISTS ix_events_run ON events(run_id, id);
            CREATE INDEX IF NOT EXISTS ix_runs_project ON runs(project_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS ix_conversations_scope
                ON conversations(tenant_id, account_id, project_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS ix_conversation_messages_scope
                ON conversation_messages(conversation_id, tenant_id, account_id, sequence_no);
            CREATE INDEX IF NOT EXISTS ix_tool_calls_run ON tool_calls(run_id, id);
            """
        )
        # Lightweight forward migration for existing agent databases.
        run_columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        if "tenant_id" not in run_columns:
            conn.execute("ALTER TABLE runs ADD COLUMN tenant_id TEXT NOT NULL DEFAULT ''")
        if "account_id" not in run_columns:
            conn.execute("ALTER TABLE runs ADD COLUMN account_id TEXT NOT NULL DEFAULT ''")
        if "model_id" not in run_columns:
            conn.execute("ALTER TABLE runs ADD COLUMN model_id TEXT NOT NULL DEFAULT ''")
        if "route_id" not in run_columns:
            conn.execute("ALTER TABLE runs ADD COLUMN route_id TEXT NOT NULL DEFAULT ''")
        if "locks_json" not in run_columns:
            conn.execute("ALTER TABLE runs ADD COLUMN locks_json TEXT NOT NULL DEFAULT '{}'")
        if "file_ids_json" not in run_columns:
            conn.execute("ALTER TABLE runs ADD COLUMN file_ids_json TEXT NOT NULL DEFAULT '[]'")
        if "reference_ids_json" not in run_columns:
            conn.execute("ALTER TABLE runs ADD COLUMN reference_ids_json TEXT NOT NULL DEFAULT '[]'")
        if "conversation_id" not in run_columns:
            conn.execute("ALTER TABLE runs ADD COLUMN conversation_id TEXT NOT NULL DEFAULT ''")
        if "error_code" not in run_columns:
            conn.execute("ALTER TABLE runs ADD COLUMN error_code TEXT NOT NULL DEFAULT ''")
        if "current_stage" not in run_columns:
            conn.execute("ALTER TABLE runs ADD COLUMN current_stage TEXT NOT NULL DEFAULT ''")
        if "cancel_requested" not in run_columns:
            conn.execute("ALTER TABLE runs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0")
        if "idempotency_key" not in run_columns:
            conn.execute("ALTER TABLE runs ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT ''")
        if "started_at" not in run_columns:
            conn.execute("ALTER TABLE runs ADD COLUMN started_at TEXT NOT NULL DEFAULT ''")
        if "finished_at" not in run_columns:
            conn.execute("ALTER TABLE runs ADD COLUMN finished_at TEXT NOT NULL DEFAULT ''")


def json_loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def add_message(run_id: str, role: str, content: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO messages(run_id, role, content, created_at) VALUES(?,?,?,?)",
            (run_id, role, content, utcnow()),
        )


def add_step(run_id: str, name: str, status: str, detail: dict | None = None) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO steps(run_id, name, status, detail_json, created_at) VALUES(?,?,?,?,?)",
            (run_id, name, status, json.dumps(detail or {}, ensure_ascii=False), utcnow()),
        )


def add_event(run_id: str, event_type: str, payload: dict | None = None) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO events(run_id, type, payload_json, created_at) VALUES(?,?,?,?)",
            (run_id, event_type, json.dumps(payload or {}, ensure_ascii=False), utcnow()),
        )


def set_run_status(run_id: str, status: str, error: str = "") -> None:
    now = utcnow()
    finished = now if status in {"done", "completed", "failed", "cancelled", "interrupted"} else ""
    with db() as conn:
        conn.execute(
            "UPDATE runs SET status=?, error=?, updated_at=?, finished_at=CASE WHEN ?='' THEN finished_at ELSE ? END WHERE id=?",
            (status, error, now, finished, finished, run_id),
        )


def set_run_stage(run_id: str, stage: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE runs SET current_stage=?, updated_at=? WHERE id=?",
            (str(stage or ""), utcnow(), run_id),
        )


def request_run_cancel(run_id: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE runs SET cancel_requested=1, updated_at=? WHERE id=?",
            (utcnow(), run_id),
        )


def mark_interrupted_runs() -> int:
    now = utcnow()
    with db() as conn:
        cur = conn.execute(
            "UPDATE runs SET status='interrupted', error='服务重启时运行未完成', "
            "error_code='AGENT_INTERRUPTED', finished_at=?, updated_at=? "
            "WHERE status IN ('queued','running','importing')",
            (now, now),
        )
        return int(cur.rowcount or 0)


def start_tool_call(
    run_id: str,
    tool_name: str,
    args: dict | None,
    idempotency_key: str = "",
) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO tool_calls(run_id, tool_name, args_json, status, idempotency_key, started_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                run_id,
                str(tool_name or ""),
                json.dumps(args or {}, ensure_ascii=False),
                "running",
                str(idempotency_key or ""),
                utcnow(),
            ),
        )
        return int(cur.lastrowid or 0)


def finish_tool_call(
    call_id: int,
    status: str,
    result: dict | None = None,
    error_code: str = "",
) -> None:
    payload = json.dumps(result or {}, ensure_ascii=False)
    with db() as conn:
        conn.execute(
            "UPDATE tool_calls SET status=?, result_json=?, result_bytes=?, error_code=?, finished_at=? WHERE id=?",
            (
                str(status or ""),
                payload,
                len(payload.encode("utf-8")),
                str(error_code or ""),
                utcnow(),
                int(call_id),
            ),
        )


def start_stage(run_id: str, name: str, detail: dict | None = None, attempt: int = 1) -> int:
    now = utcnow()
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO stages(run_id, name, status, attempt, detail_json, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (run_id, name, "running", attempt, json.dumps(detail or {}, ensure_ascii=False), now, now),
        )
        return int(cur.lastrowid or 0)


def finish_stage(stage_id: int, status: str, detail: dict | None = None) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE stages SET status=?, detail_json=?, updated_at=? WHERE id=?",
            (status, json.dumps(detail or {}, ensure_ascii=False), utcnow(), stage_id),
        )


def fail_running_stages(run_id: str, error: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE stages SET status='failed', detail_json=?, updated_at=? "
            "WHERE run_id=? AND status='running'",
            (json.dumps({"error": error}, ensure_ascii=False), utcnow(), run_id),
        )


def create_message(run_id: str, role: str, content: str = "") -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO messages(run_id, role, content, created_at) VALUES(?,?,?,?)",
            (run_id, role, content, utcnow()),
        )
        return int(cur.lastrowid or 0)


def update_message(message_id: int, content: str) -> None:
    with db() as conn:
        conn.execute("UPDATE messages SET content=? WHERE id=?", (content, message_id))
