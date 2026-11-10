from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CanvasProject(Base):
    __tablename__ = "canvas_projects"
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), default="")
    title: Mapped[str] = mapped_column(String(500), default="")
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    revision: Mapped[int] = mapped_column(BigInteger, default=1)
    node_count: Mapped[int] = mapped_column(Integer, default=0)
    connection_count: Mapped[int] = mapped_column(Integer, default=0)
    episode_count: Mapped[int] = mapped_column(Integer, default=1)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    local_blocked_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_canvas_projects_revision_positive"),
        Index("idx_canvas_projects_tenant_updated", "tenant_id", "updated_at"),
    )


class CanvasProjectMeta(Base):
    __tablename__ = "canvas_project_meta"
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    meta_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CanvasProjectionNode(Base):
    __tablename__ = "canvas_projection_nodes"
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    episode_id: Mapped[str] = mapped_column(String(64))
    node_type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(500), default="")
    position_x: Mapped[float] = mapped_column(Double, default=0)
    position_y: Mapped[float] = mapped_column(Double, default=0)
    position_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    node_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("btrim(episode_id) <> ''", name="ck_projection_nodes_episode"),
        Index(
            "idx_canvas_projection_nodes_episode",
            "tenant_id",
            "project_id",
            "episode_id",
            "node_id",
        ),
    )


class CanvasProjectionEdge(Base):
    __tablename__ = "canvas_projection_edges"
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    edge_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    from_node_id: Mapped[str] = mapped_column(String(64))
    to_node_id: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(64), default="")
    edge_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("from_node_id <> to_node_id", name="ck_projection_edges_distinct"),
        Index(
            "idx_canvas_projection_edges_from",
            "tenant_id",
            "project_id",
            "from_node_id",
        ),
        Index(
            "idx_canvas_projection_edges_to",
            "tenant_id",
            "project_id",
            "to_node_id",
        ),
    )


class CanvasProjectionEpisode(Base):
    __tablename__ = "canvas_projection_episodes"
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    episode_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(500), default="")
    group_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    node_ids_json: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    video_node_ids_json: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    connection_ids_json: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    viewport_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    layout_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CanvasProjectionNote(Base):
    __tablename__ = "canvas_projection_notes"
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    episode_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    note_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    text: Mapped[str] = mapped_column(Text, default="")
    position_x: Mapped[float] = mapped_column(Double, default=0)
    position_y: Mapped[float] = mapped_column(Double, default=0)
    note_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CanvasSnapshot(Base):
    __tablename__ = "canvas_snapshots"
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(500), default="")
    node_count: Mapped[int] = mapped_column(Integer, default=0)
    connection_count: Mapped[int] = mapped_column(Integer, default=0)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    kind: Mapped[str] = mapped_column(String(32), default="auto")
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index(
            "idx_canvas_snapshots_project",
            "tenant_id",
            "project_id",
            revision.desc(),
            created_at.desc(),
        ),
    )


class GenerationTask(Base):
    __tablename__ = "generation_tasks"
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64))
    node_id: Mapped[str] = mapped_column(String(64))
    episode_id: Mapped[str] = mapped_column(String(64), default="")
    title: Mapped[str] = mapped_column(String(500), default="")
    kind: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(32))
    phase: Mapped[str] = mapped_column(String(100), default="")
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider: Mapped[str] = mapped_column(String(100), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    provider_task_id: Mapped[str] = mapped_column(String(200), default="")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    estimated_cost: Mapped[float] = mapped_column(Numeric(18, 6), default=0)
    cost_currency: Mapped[str] = mapped_column(String(16), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    request_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    client_operation_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_status: Mapped[str] = mapped_column(String(32), default="pending")
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0)
    local_result_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_result_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    node_written_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index(
            "uq_generation_tasks_operation",
            "tenant_id",
            "project_id",
            "node_id",
            "client_operation_id",
            unique=True,
            postgresql_where=text("client_operation_id <> ''"),
        ),
        Index(
            "uq_generation_tasks_active_node",
            "tenant_id",
            "project_id",
            "node_id",
            unique=True,
            postgresql_where=text(
                "status IN ('queued','submitting','running')"
            ),
        ),
        Index(
            "idx_generation_tasks_project",
            "tenant_id",
            "project_id",
            created_at.desc(),
        ),
        Index(
            "idx_generation_tasks_node",
            "tenant_id",
            "project_id",
            "node_id",
            "status",
        ),
        Index("idx_generation_tasks_poll", "tenant_id", "next_poll_at"),
    )


class CanvasEvent(Base):
    __tablename__ = "canvas_events"
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(64))
    node_id: Mapped[str] = mapped_column(String(64), default="")
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("idx_canvas_events_project", "tenant_id", "project_id", "event_id"),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor: Mapped[str] = mapped_column(String(200), default="")
    action: Mapped[str] = mapped_column(String(200))
    target_type: Mapped[str] = mapped_column(String(100), default="")
    target_id: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(32), default="success")
    details_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("idx_audit_events_tenant_created", "tenant_id", created_at.desc(), "event_id"),
    )


class ProjectShare(Base):
    __tablename__ = "project_shares"
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    share_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64))
    token_hash: Mapped[str] = mapped_column(String(128))
    created_by: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    role: Mapped[str] = mapped_column(String(32), default="viewer")
    label: Mapped[str] = mapped_column(String(200), default="")
    __table_args__ = (
        UniqueConstraint("tenant_id", "token_hash", name="uq_project_shares_token"),
    )
