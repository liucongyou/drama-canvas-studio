from __future__ import annotations

import os
import tempfile
import time
import uuid
from unittest.mock import patch

import psycopg
import pytest
from psycopg import sql

import canvas_store
import generation_queue
import generation_worker
import pg_compat
import schema_migrations
import server
import tenant_context
import tenant_registry


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)


@pytest.fixture()
def worker_env(monkeypatch):
    root = tempfile.TemporaryDirectory()
    suffix = "worker_" + uuid.uuid4().hex[:10]
    tenant_slug = "worker-" + uuid.uuid4().hex[:10]
    tenant_id = tenant_slug
    monkeypatch.setenv("CANVAS_DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_POSTGRES_URL"])
    monkeypatch.setenv("CANVAS_MULTI_TENANT", "1")
    monkeypatch.setenv("CANVAS_TENANT_DATA_ROOT", root.name)
    monkeypatch.setenv("CANVAS_PG_SCHEMA_SUFFIX", suffix)
    control = tenant_registry._connect()
    try:
        schema_migrations.apply_migrations(control, "control")
    finally:
        control.close()
    tenant = tenant_registry.create_tenant(
        tenant_slug,
        tenant_id=tenant_id,
    )
    context = tenant_context.make_context(
        tenant["id"],
        tenant["slug"],
    )
    tenant_context.ensure_context_dirs(context)
    with tenant_context.use_context(context):
        connection = canvas_store._connect()
        try:
            schema_migrations.apply_migrations(connection, "canvas")
        finally:
            connection.close()
    try:
        yield context
    finally:
        stats = pg_compat.pool_stats()
        pg_compat.configure_pool(
            max_connections=stats["max"],
            wait_timeout_ms=stats["waitTimeoutMs"],
            schema_idle_max=stats["schemaIdleMax"],
        )
        schemas = {
            pg_compat.schema_for_path(context.db_path, "canvas"),
            pg_compat.schema_for_path("control.db", "control"),
        }
        with psycopg.connect(os.environ["TEST_POSTGRES_URL"]) as connection:
            for schema_name in schemas:
                connection.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(schema_name)
                    )
                )
        root.cleanup()


def _queued_task(context):
    with tenant_context.use_context(context):
        project_id = canvas_store.create_project("worker-process")
        node_id = "worker-node"
        canvas_store.insert_project_nodes(
            project_id,
            [
                {
                    "id": node_id,
                    "type": "video",
                    "title": node_id,
                    "position": {"x": 0, "y": 0},
                    "metadata": {"episodeId": "EP001", "status": "idle"},
                }
            ],
            episode_id="EP001",
        )
        return canvas_store.create_generation_task(
            project_id,
            node_id,
            kind="video",
            episode_id="EP001",
            provider="mock",
            model="mock-video",
        )


def test_worker_mode_does_not_start_inline_generation_thread():
    with (
        patch.object(server, "GENERATION_EXECUTION_MODE", "worker"),
        patch.object(
            server,
            "start_generation_task_worker",
            side_effect=AssertionError("inline worker must not start"),
        ),
    ):
        started = server.dispatch_generation_task(
            "task-worker-mode",
            "tenant",
            "tenant",
        )
    assert started is False


def test_worker_claims_admits_executes_and_releases(worker_env):
    task = _queued_task(worker_env)
    seen = []

    def execute(task_id, tenant_id, tenant_slug, claim_token, task=None):
        with tenant_context.use_context(worker_env):
            current = canvas_store.get_generation_task(task_id)
            assert current["status"] == "submitting"
            assert current["queue_state"] == "admitted"
            seen.append((task_id, tenant_id, tenant_slug, claim_token))
            canvas_store.finish_generation_task(
                task_id,
                status="succeeded",
                phase="已完成",
                delivery_status="ready",
            )

    worker = generation_worker.GenerationWorker(
        worker_id="worker-process-test",
        concurrency=1,
        poll_seconds=0.1,
        lease_seconds=30,
        admission_seconds=60,
    )
    try:
        with patch.object(
            server,
            "execute_admitted_generation_task",
            side_effect=execute,
        ):
            assert worker.run_once() is True
            deadline = time.monotonic() + 5
            while worker._active_count() and time.monotonic() < deadline:
                time.sleep(0.05)

        with tenant_context.use_context(worker_env):
            current = canvas_store.get_generation_task(task["id"])
            workers = generation_queue.worker_heartbeats()
            admissions = generation_queue.admission_metrics()
    finally:
        worker.shutdown()

    assert current["status"] == "succeeded"
    assert len(seen) == 1
    assert workers[0]["workerId"] == "worker-process-test"
    assert admissions["activeAdmissions"] == 0


def test_worker_releases_admission_when_execution_raises(worker_env):
    task = _queued_task(worker_env)
    worker = generation_worker.GenerationWorker(
        worker_id="worker-process-error",
        concurrency=1,
        poll_seconds=0.1,
        lease_seconds=30,
        admission_seconds=60,
    )
    try:
        with patch.object(
            server,
            "execute_admitted_generation_task",
            side_effect=RuntimeError("worker execution failed"),
        ):
            assert worker.run_once() is True
            deadline = time.monotonic() + 5
            while worker._active_count() and time.monotonic() < deadline:
                time.sleep(0.05)
        with tenant_context.use_context(worker_env):
            metrics = generation_queue.admission_metrics()
            current = canvas_store.get_generation_task(task["id"])
    finally:
        worker.shutdown()

    assert metrics["activeAdmissions"] == 0
    assert current["status"] == "failed"
    assert current["lease_owner"] == ""
