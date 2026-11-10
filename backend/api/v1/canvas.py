from __future__ import annotations

import re
import secrets

from fastapi import APIRouter, Header, HTTPException, Query
from sqlalchemy import text

from backend.config import get_settings
from backend.db.session import SessionLocal
from backend.schemas.api import (
    EpisodeWorkspace,
    GenerationTaskView,
    HealthResponse,
    ProjectSummary,
)
from backend.services.canvas_repository import CanvasReadRepository


router = APIRouter(prefix="/internal/v1", tags=["canvas-shadow"])
repository = CanvasReadRepository()


def require_internal_access(x_internal_token: str | None) -> None:
    settings = get_settings()
    configured = str(settings.internal_api_token or "")
    if settings.environment.strip().lower() in {"prod", "production"} and not configured:
        raise HTTPException(status_code=503, detail="internal API token is not configured")
    if configured and not secrets.compare_digest(
        str(x_internal_token or ""),
        configured,
    ):
        raise HTTPException(status_code=401, detail="invalid internal API token")


def tenant_from_header(x_tenant_id: str | None) -> str:
    tenant = str(x_tenant_id or "").strip()
    if not tenant:
        raise HTTPException(status_code=400, detail="X-Tenant-Id required")
    if not re.fullmatch(r"[a-z0-9_-]{1,64}", tenant):
        raise HTTPException(status_code=400, detail="X-Tenant-Id is invalid")
    return tenant


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    database = "down"
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        database = "up"
    except Exception:
        database = "down"
    return HealthResponse(
        ok=database == "up",
        database=database,
        shadow_read_enabled=get_settings().shadow_read_enabled,
    )


@router.get("/projects", response_model=list[ProjectSummary])
def projects(
    x_internal_token: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
) -> list[ProjectSummary]:
    require_internal_access(x_internal_token)
    tenant = tenant_from_header(x_tenant_id)
    return repository.list_projects(tenant)


@router.get(
    "/projects/{project_id}/episodes/{episode_id}/workspace",
    response_model=EpisodeWorkspace,
)
def workspace(
    project_id: str,
    episode_id: str,
    x_internal_token: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
) -> EpisodeWorkspace:
    require_internal_access(x_internal_token)
    tenant = tenant_from_header(x_tenant_id)
    result = repository.get_workspace(tenant, project_id, episode_id.upper())
    if result is None:
        raise HTTPException(status_code=404, detail="project not found")
    return result


@router.get(
    "/projects/{project_id}/tasks",
    response_model=list[GenerationTaskView],
)
def tasks(
    project_id: str,
    limit: int = Query(default=100, ge=1, le=300),
    x_internal_token: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
) -> list[GenerationTaskView]:
    require_internal_access(x_internal_token)
    tenant = tenant_from_header(x_tenant_id)
    return repository.list_tasks(tenant, project_id, limit=limit)
