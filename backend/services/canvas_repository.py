from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import set_tenant_scope
from backend.models.entities import (
    CanvasProject,
    GenerationTask,
)
from backend.schemas.api import (
    EpisodeWorkspace,
    GenerationTaskView,
    NodeView,
    ProjectSummary,
)


def _iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


class CanvasReadRepository:
    """Read-only PostgreSQL repository for the migration/control plane.

    The production legacy server still owns writes until the shadow/canary gate
    passes. Keeping reads here allows dual-read parity checks without making a
    second state machine authoritative.
    """

    def __init__(self, session_factory: sessionmaker[Session] = None):
        from backend.db.session import SessionLocal

        self._session_factory = session_factory or SessionLocal

    def _session(self, tenant_id: str) -> Session:
        session = self._session_factory()
        set_tenant_scope(session, tenant_id)
        return session

    def list_projects(self, tenant_id: str) -> list[ProjectSummary]:
        session = self._session(tenant_id)
        try:
            rows = session.execute(
                select(
                    CanvasProject.project_id,
                    CanvasProject.title,
                    CanvasProject.revision,
                    CanvasProject.node_count,
                    CanvasProject.connection_count,
                    CanvasProject.episode_count,
                    CanvasProject.updated_at,
                )
                .where(CanvasProject.tenant_id == tenant_id)
                .order_by(CanvasProject.updated_at.desc())
            ).all()
            return [
                ProjectSummary(
                    project_id=row.project_id,
                    title=row.title,
                    revision=int(row.revision or 0),
                    node_count=int(row.node_count or 0),
                    connection_count=int(row.connection_count or 0),
                    episode_count=int(row.episode_count or 0),
                    updated_at=_iso(row.updated_at),
                )
                for row in rows
            ]
        finally:
            session.close()

    def get_workspace(
        self,
        tenant_id: str,
        project_id: str,
        episode_id: str,
    ) -> EpisodeWorkspace | None:
        session = self._session(tenant_id)
        try:
            row = session.execute(
                text(
                    """
                    SELECT
                        p.revision,
                        COALESCE(
                            (
                                SELECT jsonb_agg(
                                    jsonb_build_object(
                                        'node_id', n.node_id,
                                        'episode_id', n.episode_id,
                                        'node_type', n.node_type,
                                        'title', n.title,
                                        'position_x', n.position_x,
                                        'position_y', n.position_y,
                                        'metadata_json', n.metadata_json,
                                        'updated_at', n.updated_at
                                    )
                                    ORDER BY n.node_id
                                )
                                FROM canvas_projection_nodes n
                                WHERE n.tenant_id = :tenant_id
                                  AND n.project_id = :project_id
                                  AND n.episode_id = :episode_id
                            ),
                            '[]'::jsonb
                        ) AS nodes,
                        (
                            SELECT jsonb_build_object(
                                'viewport_json', ep.viewport_json,
                                'layout_json', ep.layout_json
                            )
                            FROM canvas_projection_episodes ep
                            WHERE ep.tenant_id = :tenant_id
                              AND ep.project_id = :project_id
                              AND ep.episode_id = :episode_id
                        ) AS episode,
                        COALESCE(
                            (
                                SELECT jsonb_agg(ed.edge_json ORDER BY ed.edge_id)
                                FROM canvas_projection_edges ed
                                WHERE ed.tenant_id = :tenant_id
                                  AND ed.project_id = :project_id
                            ),
                            '[]'::jsonb
                        ) AS edges,
                        COALESCE(
                            (
                                SELECT MAX(ev.event_id)
                                FROM canvas_events ev
                                WHERE ev.tenant_id = :tenant_id
                                  AND ev.project_id = :project_id
                            ),
                            0
                        ) AS event_cursor
                    FROM canvas_projects p
                    WHERE p.tenant_id = :tenant_id
                      AND p.project_id = :project_id
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "episode_id": episode_id,
                },
            ).mappings().one_or_none()
            if row is None:
                return None
            nodes = list(row["nodes"] or [])
            episode = row["episode"] or {}
            edges = list(row["edges"] or [])
            return EpisodeWorkspace(
                project_id=project_id,
                episode_id=episode_id,
                revision=int(row["revision"] or 0),
                nodes=[
                    NodeView(
                        node_id=node["node_id"],
                        episode_id=node["episode_id"],
                        node_type=node["node_type"],
                        title=node.get("title") or "",
                        position_x=float(node.get("position_x") or 0),
                        position_y=float(node.get("position_y") or 0),
                        metadata_json=dict(node.get("metadata_json") or {}),
                        updated_at=_iso(node.get("updated_at")),
                    )
                    for node in nodes
                ],
                edges=[dict(edge or {}) for edge in edges],
                viewport=dict(episode.get("viewport_json") or {}),
                layout=dict(episode.get("layout_json") or {}),
                event_cursor=int(row["event_cursor"] or 0),
            )
        finally:
            session.close()

    def list_tasks(
        self,
        tenant_id: str,
        project_id: str,
        *,
        limit: int = 100,
    ) -> list[GenerationTaskView]:
        session = self._session(tenant_id)
        try:
            rows = session.execute(
                select(
                    GenerationTask.task_id,
                    GenerationTask.project_id,
                    GenerationTask.node_id,
                    GenerationTask.episode_id,
                    GenerationTask.status,
                    GenerationTask.phase,
                    GenerationTask.provider_task_id,
                    GenerationTask.delivery_status,
                    GenerationTask.progress,
                    GenerationTask.error,
                    GenerationTask.client_operation_id,
                )
                .where(
                    GenerationTask.tenant_id == tenant_id,
                    GenerationTask.project_id == project_id,
                )
                .order_by(GenerationTask.created_at.desc())
                .limit(max(1, min(int(limit), 300)))
            ).all()
            return [
                GenerationTaskView(
                    task_id=row.task_id,
                    project_id=row.project_id,
                    node_id=row.node_id,
                    episode_id=row.episode_id,
                    status=row.status,
                    phase=row.phase,
                    provider_task_id=row.provider_task_id,
                    delivery_status=row.delivery_status,
                    progress=int(row.progress or 0),
                    error=row.error,
                    client_operation_id=row.client_operation_id,
                )
                for row in rows
            ]
        finally:
            session.close()
