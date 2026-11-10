from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class ProjectSummary(ApiModel):
    project_id: str
    title: str
    revision: int
    node_count: int
    connection_count: int
    episode_count: int
    updated_at: str | None = None


class NodeView(ApiModel):
    node_id: str
    episode_id: str
    node_type: str
    title: str
    position_x: float
    position_y: float
    metadata_json: dict[str, Any]
    updated_at: str | None = None


class EpisodeWorkspace(ApiModel):
    project_id: str
    episode_id: str
    revision: int
    nodes: list[NodeView]
    edges: list[dict[str, Any]]
    viewport: dict[str, Any]
    layout: dict[str, Any]
    event_cursor: int = 0


class GenerationTaskView(ApiModel):
    task_id: str
    project_id: str
    node_id: str
    episode_id: str
    status: str
    phase: str
    provider_task_id: str | None = None
    delivery_status: str
    progress: int
    error: str
    client_operation_id: str | None = None


class HealthResponse(ApiModel):
    ok: bool
    database: str
    shadow_read_enabled: bool
