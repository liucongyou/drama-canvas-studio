from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from app import db
from app.config import get_settings


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)


def test_postgres_agent_store_init_and_lastrowid(monkeypatch, tmp_path) -> None:
    suffix = uuid.uuid4().hex[:12]
    monkeypatch.setenv("CANVAS_DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_POSTGRES_URL"])
    monkeypatch.setenv("CANVAS_PG_SCHEMA_SUFFIX", suffix)
    settings = get_settings()
    monkeypatch.setattr(settings, "db_path", tmp_path / "agent.db")
    db.init_db()

    message_id = db.create_message("run-1", "status", "ready")
    assert message_id > 0
    stage_id = db.start_stage("run-1", "generate")
    assert stage_id > 0
    db.finish_stage(stage_id, "done", {"ok": True})
    db.add_event("run-1", "run.created", {"projectId": "p1"})

    with db.db() as connection:
        assert connection.execute(
            "SELECT content FROM messages WHERE id=?",
            (message_id,),
        ).fetchone()[0] == "ready"
        assert connection.execute(
            "SELECT status FROM stages WHERE id=?",
            (stage_id,),
        ).fetchone()[0] == "done"
        assert connection.execute(
            "SELECT COUNT(*) FROM events WHERE run_id=?",
            ("run-1",),
        ).fetchone()[0] == 1

    schema = f"agent_{suffix}"
    with psycopg.connect(os.environ["TEST_POSTGRES_URL"]) as connection:
        connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
