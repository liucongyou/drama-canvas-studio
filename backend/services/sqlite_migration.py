from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import delete, select, tuple_
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.models.entities import (
    AuditEvent,
    CanvasEvent,
    CanvasProject,
    CanvasProjectMeta,
    CanvasProjectionEdge,
    CanvasProjectionEpisode,
    CanvasProjectionNode,
    CanvasProjectionNote,
    CanvasSnapshot,
    GenerationTask,
    ProjectShare,
)


DATETIME_COLUMNS = {
    "canvas_projects": {"created_at", "updated_at"},
    "canvas_project_meta": {"updated_at"},
    "canvas_projection_nodes": {"position_updated_at", "updated_at"},
    "canvas_projection_edges": {"updated_at"},
    "canvas_projection_episodes": {"updated_at"},
    "canvas_projection_notes": {"updated_at"},
    "canvas_snapshots": {"created_at"},
    "generation_tasks": {
        "lease_expires_at",
        "heartbeat_at",
        "result_ready_at",
        "next_poll_at",
        "delivery_next_attempt_at",
        "provider_finished_at",
        "node_written_at",
        "progress_updated_at",
        "created_at",
        "started_at",
        "updated_at",
        "finished_at",
    },
    "canvas_events": {"created_at"},
    "audit_events": {"created_at"},
    "project_shares": {"created_at", "expires_at", "revoked_at"},
}


@dataclass(frozen=True)
class MigrationResult:
    tenant_id: str
    source: str
    tables: dict[str, int]
    deleted: dict[str, int] = field(default_factory=dict)


def _connect_readonly(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    raw = str(value).strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    # SQLite's historical writers mix naive UTC and explicit UTC strings.
    # PostgreSQL TIMESTAMPTZ would otherwise interpret naive values in the
    # server session timezone (Asia/Shanghai) and shift them by 8 hours.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _json_value(value: Any, *, expected: type) -> Any:
    if value in (None, ""):
        return expected()
    if isinstance(value, expected):
        return value
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return expected()
    return parsed if isinstance(parsed, expected) else expected()


def _rows(connection: sqlite3.Connection, table: str) -> Iterable[dict[str, Any]]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not exists:
        return []
    cursor = connection.execute(f'SELECT * FROM "{table}"')
    columns = [item[0] for item in cursor.description]
    return [
        {
            **dict(zip(columns, row)),
            **{
                column: _parse_datetime(value)
                for column, value in zip(columns, row)
                if column in DATETIME_COLUMNS.get(table, set())
            },
        }
        for row in cursor.fetchall()
    ]


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _nullable_text(value: Any) -> str | None:
    """Preserve SQLite NULL instead of collapsing it into an empty string."""
    return None if value is None else str(value)


def _upsert(session: Session, model, values: dict[str, Any], keys: tuple[str, ...]) -> None:
    statement = pg_insert(model).values(**values)
    update_values = {
        column: statement.excluded[column]
        for column in values
        if column not in keys
    }
    session.execute(
        statement.on_conflict_do_update(
            index_elements=list(keys),
            set_=update_values,
        )
    )


TARGET_KEY_COLUMNS: dict[str, tuple[str, ...]] = {
    "canvas_projects": ("project_id",),
    "canvas_project_meta": ("project_id",),
    "canvas_projection_nodes": ("project_id", "node_id"),
    "canvas_projection_edges": ("project_id", "edge_id"),
    "canvas_projection_episodes": ("project_id", "episode_id"),
    "canvas_projection_notes": ("project_id", "episode_id", "note_id"),
    "canvas_snapshots": ("snapshot_id",),
    "generation_tasks": ("task_id",),
    "canvas_events": ("event_id",),
    "audit_events": ("event_id",),
    "project_shares": ("share_id",),
}

SOURCE_KEY_COLUMNS: dict[str, tuple[str, ...]] = {
    "canvas_projects": ("id",),
    "canvas_project_meta": ("project_id",),
    "canvas_projection_nodes": ("project_id", "node_id"),
    "canvas_projection_edges": ("project_id", "edge_id"),
    "canvas_projection_episodes": ("project_id", "episode_id"),
    "canvas_projection_notes": ("project_id", "episode_id", "note_id"),
    "canvas_snapshots": ("id",),
    "generation_tasks": ("id",),
    "canvas_events": ("id",),
    "audit_events": ("id",),
    "project_shares": ("id",),
}


def _source_keys(
    table: str,
    rows: Iterable[dict[str, Any]],
) -> set[tuple[Any, ...]]:
    columns = SOURCE_KEY_COLUMNS[table]
    return {
        tuple(row.get(column) for column in columns)
        for row in rows
        if all(row.get(column) not in (None, "") for column in columns)
    }


def _prune_missing_keys(
    session: Session,
    *,
    tenant_id: str,
    table: str,
    model,
    source_rows: Iterable[dict[str, Any]],
) -> int:
    key_columns = TARGET_KEY_COLUMNS[table]
    target_columns = [getattr(model, column) for column in key_columns]
    target_keys = {
        tuple(row)
        for row in session.execute(
            select(*target_columns).where(model.tenant_id == tenant_id)
        ).all()
    }
    stale_keys = target_keys - _source_keys(table, source_rows)
    if not stale_keys:
        return 0
    ordered = sorted(
        stale_keys,
        key=lambda item: tuple(str(part) for part in item),
    )
    for offset in range(0, len(ordered), 500):
        chunk = ordered[offset:offset + 500]
        session.execute(
            delete(model).where(
                model.tenant_id == tenant_id,
                tuple_(*target_columns).in_(chunk),
            )
        )
    return len(stale_keys)


def migrate_tenant_database(
    session: Session,
    *,
    sqlite_path: str,
    tenant_id: str,
    dry_run: bool = True,
) -> MigrationResult:
    connection = _connect_readonly(sqlite_path)
    try:
        counts: dict[str, int] = {}
        deleted: dict[str, int] = {}
        present_tables = {
            table
            for table in TARGET_KEY_COLUMNS
            if _table_exists(connection, table)
        }

        def prune(table: str, model, rows: list[dict[str, Any]]) -> None:
            if dry_run or table not in present_tables:
                return
            deleted[table] = _prune_missing_keys(
                session,
                tenant_id=tenant_id,
                table=table,
                model=model,
                source_rows=rows,
            )

        projects = _rows(connection, "canvas_projects")
        for row in projects:
            values = {
                "tenant_id": tenant_id,
                "project_id": row.get("id"),
                "user_id": row.get("user_id") or "",
                "title": row.get("title") or "",
                "payload_json": _json_value(row.get("payload_json"), expected=dict),
                "revision": int(row.get("revision") or 1),
                "node_count": int(row.get("node_count") or 0),
                "connection_count": int(row.get("connection_count") or 0),
                "episode_count": int(row.get("episode_count") or 1),
                "failed_count": int(row.get("failed_count") or 0),
                "local_blocked_count": int(row.get("local_blocked_count") or 0),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            }
            if not dry_run:
                _upsert(session, CanvasProject, values, ("tenant_id", "project_id"))
        prune("canvas_projects", CanvasProject, projects)
        counts["canvas_projects"] = len(projects)

        meta = _rows(connection, "canvas_project_meta")
        for row in meta:
            values = {
                "tenant_id": tenant_id,
                "project_id": row.get("project_id"),
                "meta_json": _json_value(row.get("meta_json"), expected=dict),
                "updated_at": row.get("updated_at"),
            }
            if not dry_run:
                _upsert(session, CanvasProjectMeta, values, ("tenant_id", "project_id"))
        prune("canvas_project_meta", CanvasProjectMeta, meta)
        counts["canvas_project_meta"] = len(meta)

        nodes = _rows(connection, "canvas_projection_nodes")
        for row in nodes:
            episode_id = str(row.get("episode_id") or "").strip() or "__UNASSIGNED__"
            metadata_json = _json_value(row.get("metadata_json"), expected=dict)
            position_x = float(row.get("position_x") or 0)
            position_y = float(row.get("position_y") or 0)
            values = {
                "tenant_id": tenant_id,
                "project_id": row.get("project_id"),
                "node_id": row.get("node_id"),
                "episode_id": episode_id,
                "node_type": row.get("node_type") or "",
                "title": row.get("title") or "",
                "position_x": position_x,
                "position_y": position_y,
                "position_updated_at": row.get("position_updated_at"),
                "metadata_json": metadata_json,
                "node_json": _json_value(row.get("node_json"), expected=dict),
                "updated_at": row.get("updated_at"),
            }
            if not dry_run:
                _upsert(
                    session,
                    CanvasProjectionNode,
                    values,
                    ("tenant_id", "project_id", "node_id"),
                )
        prune("canvas_projection_nodes", CanvasProjectionNode, nodes)
        counts["canvas_projection_nodes"] = len(nodes)

        edges = _rows(connection, "canvas_projection_edges")
        for row in edges:
            values = {
                "tenant_id": tenant_id,
                "project_id": row.get("project_id"),
                "edge_id": row.get("edge_id"),
                "from_node_id": row.get("from_node_id"),
                "to_node_id": row.get("to_node_id"),
                "source": row.get("source") or "",
                "edge_json": _json_value(row.get("edge_json"), expected=dict),
                "updated_at": row.get("updated_at"),
            }
            if not dry_run:
                _upsert(session, CanvasProjectionEdge, values, ("tenant_id", "project_id", "edge_id"))
        prune("canvas_projection_edges", CanvasProjectionEdge, edges)
        counts["canvas_projection_edges"] = len(edges)

        episodes = _rows(connection, "canvas_projection_episodes")
        for row in episodes:
            values = {
                "tenant_id": tenant_id,
                "project_id": row.get("project_id"),
                "episode_id": row.get("episode_id"),
                "title": row.get("title") or "",
                "group_json": _json_value(row.get("group_json"), expected=dict),
                "node_ids_json": _json_value(row.get("node_ids_json"), expected=list),
                "video_node_ids_json": _json_value(row.get("video_node_ids_json"), expected=list),
                "connection_ids_json": _json_value(row.get("connection_ids_json"), expected=list),
                "viewport_json": _json_value(row.get("viewport_json"), expected=dict),
                "layout_json": _json_value(row.get("layout_json"), expected=dict),
                "updated_at": row.get("updated_at"),
            }
            if not dry_run:
                _upsert(
                    session,
                    CanvasProjectionEpisode,
                    values,
                    ("tenant_id", "project_id", "episode_id"),
                )
        prune("canvas_projection_episodes", CanvasProjectionEpisode, episodes)
        counts["canvas_projection_episodes"] = len(episodes)

        notes = _rows(connection, "canvas_projection_notes")
        for row in notes:
            values = {
                "tenant_id": tenant_id,
                "project_id": row.get("project_id"),
                "episode_id": row.get("episode_id"),
                "note_id": row.get("note_id"),
                "text": row.get("text") or "",
                "position_x": float(row.get("position_x") or 0),
                "position_y": float(row.get("position_y") or 0),
                "note_json": _json_value(row.get("note_json"), expected=dict),
                "updated_at": row.get("updated_at"),
            }
            if not dry_run:
                _upsert(
                    session,
                    CanvasProjectionNote,
                    values,
                    ("tenant_id", "project_id", "episode_id", "note_id"),
                )
        prune("canvas_projection_notes", CanvasProjectionNote, notes)
        counts["canvas_projection_notes"] = len(notes)

        snapshots = _rows(connection, "canvas_snapshots")
        for row in snapshots:
            values = {
                "tenant_id": tenant_id,
                "snapshot_id": row.get("id"),
                "project_id": row.get("canvas_id"),
                "title": row.get("title") or "",
                "node_count": int(row.get("node_count") or 0),
                "connection_count": int(row.get("connection_count") or 0),
                "payload_json": _json_value(row.get("payload_json"), expected=dict),
                "revision": int(row.get("revision") or 0),
                "kind": row.get("kind") or "auto",
                "created_at": row.get("created_at"),
            }
            if not dry_run:
                _upsert(session, CanvasSnapshot, values, ("tenant_id", "snapshot_id"))
        prune("canvas_snapshots", CanvasSnapshot, snapshots)
        counts["canvas_snapshots"] = len(snapshots)

        tasks = _rows(connection, "generation_tasks")
        for row in tasks:
            values = {
                "tenant_id": tenant_id,
                "task_id": row.get("id"),
                "project_id": row.get("project_id") or "",
                "node_id": row.get("node_id") or "",
                "episode_id": row.get("episode_id") or "",
                "title": row.get("title") or "",
                "kind": row.get("kind") or "",
                "status": row.get("status") or "queued",
                "phase": row.get("phase") or "",
                "progress": (
                    None
                    if row.get("progress") is None
                    else int(row.get("progress"))
                ),
                "provider": row.get("provider") or "",
                "model": row.get("model") or "",
                "provider_task_id": row.get("provider_task_id") or "",
                "attempt": int(row.get("attempt") or 1),
                "estimated_cost": float(row.get("estimated_cost") or 0),
                "cost_currency": row.get("cost_currency") or "",
                "error": row.get("error") or "",
                "request_json": _json_value(row.get("request_json"), expected=dict),
                "client_operation_id": _nullable_text(row.get("client_operation_id")),
                "lease_owner": _nullable_text(row.get("lease_owner")),
                "lease_expires_at": row.get("lease_expires_at"),
                "heartbeat_at": row.get("heartbeat_at"),
                "delivery_status": row.get("delivery_status") or "pending",
                "delivery_attempts": int(row.get("delivery_attempts") or 0),
                "local_result_url": _nullable_text(row.get("local_result_url")),
                "provider_result_url": _nullable_text(row.get("provider_result_url")),
                "result_ready_at": row.get("result_ready_at"),
                "next_poll_at": row.get("next_poll_at"),
                "delivery_next_attempt_at": row.get("delivery_next_attempt_at"),
                "provider_finished_at": row.get("provider_finished_at"),
                "node_written_at": row.get("node_written_at"),
                "progress_updated_at": row.get("progress_updated_at"),
                "created_at": row.get("created_at"),
                "started_at": row.get("started_at"),
                "updated_at": row.get("updated_at"),
                "finished_at": row.get("finished_at"),
            }
            if not dry_run:
                _upsert(session, GenerationTask, values, ("tenant_id", "task_id"))
        prune("generation_tasks", GenerationTask, tasks)
        counts["generation_tasks"] = len(tasks)

        events = _rows(connection, "canvas_events")
        for row in events:
            values = {
                "tenant_id": tenant_id,
                "event_id": int(row.get("id")),
                "project_id": row.get("project_id"),
                "kind": row.get("kind") or "",
                "node_id": row.get("node_id") or "",
                "payload_json": _json_value(row.get("payload_json"), expected=dict),
                "created_at": row.get("created_at") or datetime.now(timezone.utc),
            }
            if not dry_run:
                _upsert(session, CanvasEvent, values, ("tenant_id", "event_id"))
        prune("canvas_events", CanvasEvent, events)
        counts["canvas_events"] = len(events)

        audits = _rows(connection, "audit_events")
        for row in audits:
            values = {
                "tenant_id": tenant_id,
                "event_id": row.get("id"),
                "actor": row.get("actor") or "",
                "action": row.get("action") or "",
                "target_type": row.get("target_type") or "",
                "target_id": row.get("target_id") or "",
                "status": row.get("status") or "success",
                "details_json": _json_value(row.get("details_json"), expected=dict),
                "created_at": row.get("created_at") or datetime.now(timezone.utc),
            }
            if not dry_run:
                _upsert(session, AuditEvent, values, ("tenant_id", "event_id"))
        prune("audit_events", AuditEvent, audits)
        counts["audit_events"] = len(audits)

        shares = _rows(connection, "project_shares")
        for row in shares:
            values = {
                "tenant_id": tenant_id,
                "share_id": row.get("id"),
                "project_id": row.get("project_id") or "",
                "token_hash": row.get("token_hash") or "",
                "created_by": row.get("created_by") or "",
                "created_at": row.get("created_at") or datetime.now(timezone.utc),
                "expires_at": row.get("expires_at") or datetime.now(timezone.utc),
                "revoked_at": row.get("revoked_at"),
                "role": row.get("role") or "viewer",
                "label": row.get("label") or "",
            }
            if not dry_run:
                _upsert(session, ProjectShare, values, ("tenant_id", "share_id"))
        prune("project_shares", ProjectShare, shares)
        counts["project_shares"] = len(shares)

        if dry_run:
            # Dry runs execute no writes, so there is nothing to keep.
            session.rollback()
        return MigrationResult(
            tenant_id=tenant_id,
            source=str(sqlite_path),
            tables=counts,
            deleted=deleted,
        )
    except Exception:
        session.rollback()
        raise
    finally:
        connection.close()


TARGET_COLUMNS: dict[str, tuple[str, ...]] = {
    "canvas_projects": (
        "project_id", "user_id", "title", "payload_json", "revision",
        "node_count", "connection_count", "episode_count", "failed_count",
        "local_blocked_count", "created_at", "updated_at",
    ),
    "canvas_project_meta": ("project_id", "meta_json", "updated_at"),
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
        "snapshot_id", "project_id", "title", "node_count",
        "connection_count", "payload_json", "revision", "kind", "created_at",
    ),
    "generation_tasks": (
        "task_id", "project_id", "node_id", "episode_id", "title", "kind",
        "status", "phase", "progress", "provider", "model",
        "provider_task_id", "attempt", "estimated_cost", "cost_currency",
        "error", "created_at", "started_at", "updated_at", "finished_at",
        "provider_finished_at", "node_written_at", "local_result_url",
        "provider_result_url", "request_json", "client_operation_id",
        "lease_owner", "lease_expires_at", "heartbeat_at", "delivery_status",
        "delivery_attempts", "result_ready_at", "progress_updated_at",
        "next_poll_at", "delivery_next_attempt_at",
    ),
    "canvas_events": (
        "event_id", "project_id", "kind", "node_id", "payload_json",
        "created_at",
    ),
    "audit_events": (
        "event_id", "tenant_id", "actor", "action", "target_type",
        "target_id", "status", "details_json", "created_at",
    ),
    "project_shares": (
        "share_id", "project_id", "token_hash", "created_by", "created_at",
        "expires_at", "revoked_at", "role", "label",
    ),
}


def tenant_table_counts(
    session: Session,
    *,
    tenant_id: str,
) -> dict[str, int]:
    """Count rows after tenancy scoping, without exposing row content."""
    from sqlalchemy import text

    counts: dict[str, int] = {}
    for table in TARGET_COLUMNS:
        counts[table] = int(
            session.execute(
                text(f'SELECT COUNT(*) FROM "{table}" WHERE tenant_id=:tenant'),
                {"tenant": tenant_id},
            ).scalar_one()
        )
    return counts
