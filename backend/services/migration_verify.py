from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.services.sqlite_migration import TARGET_COLUMNS


JSON_COLUMNS = {
    "payload_json", "meta_json", "metadata_json", "node_json", "edge_json",
    "group_json", "node_ids_json", "video_node_ids_json",
    "connection_ids_json", "viewport_json", "layout_json", "note_json",
    "request_json", "details_json",
}
LIST_JSON_COLUMNS = {
    "node_ids_json", "video_node_ids_json", "connection_ids_json",
}
TIME_COLUMNS = {
    "created_at", "updated_at", "position_updated_at", "started_at",
    "finished_at", "provider_finished_at", "node_written_at",
    "lease_expires_at", "heartbeat_at", "result_ready_at",
    "progress_updated_at", "next_poll_at", "delivery_next_attempt_at",
    "expires_at", "revoked_at",
}
SQL_COLUMN_ALIASES = {
    "canvas_projects": {"project_id": "id"},
    "canvas_snapshots": {"snapshot_id": "id"},
    "generation_tasks": {"task_id": "id"},
    "canvas_events": {"event_id": "id"},
    "audit_events": {"event_id": "id"},
    "project_shares": {"share_id": "id"},
}
SQL_TABLE_COLUMNS = {
    "canvas_projects": (
        "id", "user_id", "title", "payload_json", "revision", "node_count",
        "connection_count", "episode_count", "failed_count",
        "local_blocked_count", "created_at", "updated_at",
    ),
    "canvas_project_meta": (
        "project_id", "meta_json", "updated_at",
    ),
    "canvas_projection_nodes": (
        "project_id", "node_id", "episode_id", "node_type", "title",
        "position_x", "position_y", "position_updated_at", "metadata_json",
        "node_json", "updated_at",
    ),
    "canvas_projection_edges": (
        "project_id", "edge_id", "from_node_id", "to_node_id", "source",
        "edge_json", "updated_at",
    ),
    "canvas_projection_episodes": (
        "project_id", "episode_id", "title", "group_json", "node_ids_json",
        "video_node_ids_json", "connection_ids_json", "viewport_json",
        "layout_json", "updated_at",
    ),
    "canvas_projection_notes": (
        "project_id", "episode_id", "note_id", "text", "position_x",
        "position_y", "note_json", "updated_at",
    ),
    "canvas_snapshots": (
        "id", "canvas_id", "title", "node_count", "connection_count",
        "payload_json", "revision", "kind", "created_at",
    ),
    "generation_tasks": (
        "id", "project_id", "node_id", "episode_id", "title", "kind",
        "status", "phase", "progress", "provider", "model",
        "provider_task_id", "attempt", "estimated_cost", "cost_currency",
        "error", "created_at", "started_at", "updated_at", "finished_at",
        "provider_finished_at", "node_written_at", "local_result_url",
        "provider_result_url", "request_json", "client_operation_id",
        "lease_owner", "lease_expires_at", "heartbeat_at",
        "delivery_status", "delivery_attempts", "result_ready_at",
        "progress_updated_at", "next_poll_at", "delivery_next_attempt_at",
    ),
    "canvas_events": ("id", "project_id", "kind", "node_id", "payload_json", "created_at"),
    "audit_events": (
        "id", "tenant_id", "actor", "action", "target_type", "target_id",
        "status", "details_json", "created_at",
    ),
    "project_shares": (
        "id", "project_id", "token_hash", "created_by", "created_at",
        "expires_at", "revoked_at", "role", "label",
    ),
}


def _sqlite_json(value: Any, expected: type) -> Any:
    if value in (None, ""):
        return expected()
    if isinstance(value, expected):
        return value
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return expected()
    return parsed if isinstance(parsed, expected) else expected()


def _time(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        current = value
    else:
        try:
            current = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return str(value)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _normalize(value: Any, column: str) -> Any:
    if column in LIST_JSON_COLUMNS:
        return _sqlite_json(value, list)
    if column in JSON_COLUMNS:
        if column in LIST_JSON_COLUMNS:
            return _sqlite_json(value, list)
        return _sqlite_json(value, dict)
    if column in TIME_COLUMNS:
        return _time(value)
    if isinstance(value, Decimal):
        return round(float(value), 9)
    if isinstance(value, float):
        return round(value, 9)
    return value


def _digest(rows: list[list[Any]], columns: tuple[str, ...]) -> tuple[int, str]:
    encoded = []
    for row in rows:
        item = {
            column: _normalize(value, column)
            for column, value in zip(columns, row)
        }
        encoded.append(
            json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        )
    return len(rows), hashlib.sha256(
        "\n".join(sorted(encoded)).encode("utf-8")
    ).hexdigest()


def compare_contents(
    session: Session,
    *,
    sqlite_path: str,
    tenant_id: str,
    core_only: bool = True,
    included_tables: set[str] | None = None,
) -> dict[str, Any]:
    selected = included_tables or set(SQL_TABLE_COLUMNS)
    if core_only:
        selected &= {
            "canvas_projects",
            "canvas_project_meta",
            "canvas_projection_nodes",
            "canvas_projection_edges",
            "canvas_projection_episodes",
            "canvas_projection_notes",
            "canvas_snapshots",
            "generation_tasks",
            "canvas_events",
            "audit_events",
            "project_shares",
        }
    connection = sqlite3.connect(
        f"file:{sqlite_path}?mode=ro",
        uri=True,
    )
    summaries: list[dict[str, Any]] = []
    try:
        for table in sorted(selected):
            sql_columns = SQL_TABLE_COLUMNS[table]
            pg_columns = TARGET_COLUMNS[table]
            sql_rows = connection.execute(
                "SELECT " + ",".join(f'"{column}"' for column in sql_columns)
                + f' FROM "{table}"'
            ).fetchall()
            pg_rows = session.execute(
                text(
                    "SELECT " + ",".join(f'"{column}"' for column in pg_columns)
                    + f' FROM "{table}" WHERE tenant_id=:tenant'
                ),
                {"tenant": tenant_id},
            ).fetchall()
            # PostgreSQL omits the leading tenant_id column. Align the source
            # column names to target names before canonicalizing, otherwise the
            # same values hash differently solely because of a renamed PK.
            renamed_source_columns = tuple(
                pg_name for pg_name in pg_columns
            )
            sql_count, sql_digest = _digest(sql_rows, renamed_source_columns)
            pg_count, pg_digest = _digest(pg_rows, pg_columns)
            summaries.append({
                "table": table,
                "sourceRows": sql_count,
                "targetRows": pg_count,
                "match": sql_count == pg_count and sql_digest == pg_digest,
                "sourceSha256": sql_digest,
                "targetSha256": pg_digest,
            })
    finally:
        connection.close()
    mismatches = [item for item in summaries if not item["match"]]
    return {
        "ok": not mismatches,
        "tables": summaries,
        "mismatches": mismatches,
    }
