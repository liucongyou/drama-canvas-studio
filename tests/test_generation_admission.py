from __future__ import annotations

import os
import tempfile
import threading
import time
import uuid

import psycopg
import pytest
from psycopg import sql

import canvas_store
import generation_queue
import pg_compat
import schema_migrations
import tenant_context
import tenant_registry


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)


@pytest.fixture()
def admission_env(monkeypatch):
    root = tempfile.TemporaryDirectory()
    suffix = "admission_" + uuid.uuid4().hex[:10]
    monkeypatch.setenv("CANVAS_DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_POSTGRES_URL"])
    monkeypatch.setenv("CANVAS_MULTI_TENANT", "1")
    monkeypatch.setenv("CANVAS_TENANT_DATA_ROOT", root.name)
    monkeypatch.setenv("CANVAS_PG_SCHEMA_SUFFIX", suffix)
    contexts = []

    control = tenant_registry._connect()
    try:
        schema_migrations.apply_migrations(control, "control")
    finally:
        control.close()

    def make_tenant():
        tenant_id = "admission-" + uuid.uuid4().hex[:12]
        context = tenant_context.make_context(tenant_id, tenant_id)
        tenant_context.ensure_context_dirs(context)
        contexts.append(context)
        with tenant_context.use_context(context):
            connection = canvas_store._connect()
            try:
                schema_migrations.apply_migrations(connection, "canvas")
            finally:
                connection.close()
        return context

    try:
        yield make_tenant
    finally:
        stats = pg_compat.pool_stats()
        pg_compat.configure_pool(
            max_connections=stats["max"],
            wait_timeout_ms=stats["waitTimeoutMs"],
            schema_idle_max=stats["schemaIdleMax"],
        )
        schemas = {
            pg_compat.schema_for_path(context.db_path, "canvas")
            for context in contexts
        }
        schemas.add(pg_compat.schema_for_path("control.db", "control"))
        with psycopg.connect(os.environ["TEST_POSTGRES_URL"]) as connection:
            for schema_name in schemas:
                connection.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(schema_name)
                    )
                )
        root.cleanup()


def _task(context, index):
    with tenant_context.use_context(context):
        project_id = canvas_store.create_project(
            f"admission-project-{index}"
        )
        node_id = f"admission-node-{index}"
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


def _claim(context, worker_id):
    with tenant_context.use_context(context):
        return generation_queue.claim_next_generation_task(
            worker_id,
            lease_seconds=30,
        )


def _claim_until_available(context, worker_id, timeout=4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        claim = _claim(context, worker_id)
        if claim:
            return claim
        time.sleep(0.1)
    return None


def test_global_limit_is_shared_across_tenant_schemas(admission_env):
    first_tenant = admission_env()
    second_tenant = admission_env()
    first_task = _task(first_tenant, 1)
    second_task = _task(second_tenant, 2)
    first_claim = _claim(first_tenant, "worker-a")
    second_claim = _claim(second_tenant, "worker-b")

    with tenant_context.use_context(first_tenant):
        first = generation_queue.acquire_generation_admission(
            first_task["id"],
            first_claim["claimToken"],
            provider="mock",
            model="mock-video",
            kind="video",
            lease_seconds=30,
            limits={"global": 1, "tenant": 4, "provider": 4, "model": 4},
        )
    with tenant_context.use_context(second_tenant):
        second = generation_queue.acquire_generation_admission(
            second_task["id"],
            second_claim["claimToken"],
            provider="mock",
            model="mock-video",
            kind="video",
            lease_seconds=30,
            limits={"global": 1, "tenant": 4, "provider": 4, "model": 4},
        )

    assert first["acquired"] is True
    assert second["acquired"] is False
    assert second["blockedBy"] == "global"

    with tenant_context.use_context(first_tenant):
        generation_queue.release_generation_admission(
            first_task["id"],
            first_claim["claimToken"],
        )
    retry_claim = _claim_until_available(second_tenant, "worker-c")
    assert retry_claim is not None
    with tenant_context.use_context(second_tenant):
        retry = generation_queue.acquire_generation_admission(
            second_task["id"],
            retry_claim["claimToken"],
            provider="mock",
            model="mock-video",
            kind="video",
            lease_seconds=30,
            limits={"global": 1, "tenant": 4, "provider": 4, "model": 4},
        )
    assert retry["acquired"] is True


def test_concurrent_workers_cannot_exceed_shared_limit(admission_env):
    context = admission_env()
    tasks = [_task(context, index) for index in range(4)]
    claims = [
        _claim(context, f"worker-{index}")
        for index in range(4)
    ]
    barrier = threading.Barrier(5)
    reports = []
    errors = []

    def acquire(index):
        try:
            with tenant_context.use_context(context):
                barrier.wait(timeout=5)
                reports.append(
                    generation_queue.acquire_generation_admission(
                        tasks[index]["id"],
                        claims[index]["claimToken"],
                        provider="mock",
                        model="mock-video",
                        kind="video",
                        lease_seconds=30,
                        limits={
                            "global": 2,
                            "tenant": 2,
                            "provider": 2,
                            "model": 2,
                        },
                    )
                )
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=acquire, args=(index,))
        for index in range(4)
    ]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=20)

    assert not errors
    assert sum(item["acquired"] for item in reports) == 2
    assert sum(item.get("reason") == "capacity" for item in reports) == 2


def test_provider_cooldown_is_shared_and_requeues_task(admission_env):
    context = admission_env()
    task = _task(context, 1)
    claim = _claim(context, "worker-a")
    with tenant_context.use_context(context):
        cooled = generation_queue.mark_provider_cooldown("mock", 30)
        result = generation_queue.acquire_generation_admission(
            task["id"],
            claim["claimToken"],
            provider="mock",
            model="mock-video",
            kind="video",
            lease_seconds=30,
        )
        current = canvas_store.get_generation_task(task["id"])

    assert result["acquired"] is False
    assert result["reason"] == "provider_cooldown"
    assert current["status"] == "queued"
    assert current["queue_state"] == "cooldown"
    assert cooled["untilAt"]
