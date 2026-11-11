from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import pytest

import canvas_store
import pg_compat
import tenant_context


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)


@pytest.fixture()
def pg_canvas(monkeypatch):
    monkeypatch.setenv("CANVAS_DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_POSTGRES_URL"])
    path = Path(tempfile.gettempdir()) / f"canvas-pg-{uuid.uuid4().hex}" / "canvas.db"
    monkeypatch.setattr(canvas_store, "DB_PATH", str(path))
    canvas_store._SCHEMA_READY_PATHS.clear()
    canvas_store._WAL_READY_PATHS.clear()
    context = tenant_context.TenantContext(
        tenant_id="owner",
        slug="owner",
        data_root=path.parent,
        db_path=path,
        upload_dir=path.parent / "uploads",
        config_path=path.parent / "config.local.json",
        log_dir=path.parent / "logs",
    )
    token = tenant_context.set_tenant(context)
    try:
        yield
    finally:
        tenant_context.reset_tenant(token)
        schema = pg_compat.schema_for_path(path, "canvas")
        connection = pg_compat.connect_path(path, "canvas")
        connection.raw.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        connection.raw.commit()
        connection.close()


def test_postgres_canvas_crud_projection_events_and_snapshot(pg_canvas) -> None:
    project_id = canvas_store.create_project("PG 项目")
    project = canvas_store.get_project(project_id)
    payload = project["payload"]
    payload["nodes"] = [
        {
            "id": "n1",
            "type": "text",
            "title": "脚本",
            "position": {"x": 12, "y": 24},
            "metadata": {"prompt": "第一集"},
        }
    ]
    payload["episodeImports"] = [
        {
            "id": "EP001",
            "title": "第01集",
            "nodeIds": ["n1"],
            "videoNodeIds": [],
            "connectionIds": [],
        }
    ]
    assert canvas_store.update_project(
        project_id,
        title="PG 项目",
        payload=payload,
        if_revision=project["revision"],
    )

    loaded = canvas_store.get_project(project_id)
    assert loaded["payload"]["nodes"][0]["metadata"]["prompt"] == "第一集"
    workspace = canvas_store.get_project_workspace_index(project_id)
    assert workspace["episodes"][0]["nodeIds"] == ["n1"]

    event_id = canvas_store.append_event(
        project_id,
        "node.updated",
        "n1",
        {"field": "prompt"},
    )
    assert event_id > 0
    assert canvas_store.list_events(project_id, since_id=0)[-1]["id"] == event_id

    snapshot = canvas_store.create_snapshot(
        project_id,
        title="test snapshot",
        kind="manual",
    )
    assert snapshot["revision"] >= project["revision"]
    assert canvas_store.list_snapshots(project_id)[0]["id"] == snapshot["id"]
    assert canvas_store.storage_stats()["file_bytes"] > 0
    with pg_compat.connect_path(
        os.environ.get("PG_TEST_PATH", "") or canvas_store._db_path(),
        "canvas",
    ) as connection:
        indexes = {
            row[0]
            for row in connection.raw.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname=current_schema()
                """
            ).fetchall()
        }
    assert {
        "ix_pg_projection_nodes_order",
        "ix_pg_projection_edges_order",
        "ix_pg_snapshots_recent",
        "ix_pg_tasks_recent",
    } <= indexes
