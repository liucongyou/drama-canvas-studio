from __future__ import annotations

import os
import tempfile
import threading
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

import canvas_store
import generation_queue
import pg_compat
import schema_migrations
import server
import tenant_context


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)


@pytest.fixture()
def queue_tenant(monkeypatch):
    root = tempfile.TemporaryDirectory()
    tenant_id = "queue-" + uuid.uuid4().hex[:12]
    monkeypatch.setenv("CANVAS_DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_POSTGRES_URL"])
    monkeypatch.setenv("CANVAS_MULTI_TENANT", "1")
    monkeypatch.setenv("CANVAS_TENANT_DATA_ROOT", root.name)
    context = tenant_context.make_context(tenant_id, tenant_id)
    tenant_context.ensure_context_dirs(context)
    with tenant_context.use_context(context):
        connection = canvas_store._connect()
        try:
            schema_migrations.apply_migrations(connection, "canvas")
        finally:
            connection.close()
    schema = pg_compat.schema_for_path(context.db_path, "canvas")
    try:
        yield context
    finally:
        with psycopg.connect(os.environ["TEST_POSTGRES_URL"]) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema)
                )
            )
        root.cleanup()


def _task(context, index=0):
    with tenant_context.use_context(context):
        project_id = canvas_store.create_project(f"queue-project-{index}")
        node_id = f"queue-node-{index}"
        canvas_store.insert_project_nodes(
            project_id,
            [
                {
                    "id": node_id,
                    "type": "video",
                    "title": f"queue node {index}",
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


def test_two_workers_claim_different_tasks(queue_tenant):
    first = _task(queue_tenant, 1)
    second = _task(queue_tenant, 2)
    barrier = threading.Barrier(3)
    claims = []
    errors = []

    def claim(worker_id):
        try:
            with tenant_context.use_context(queue_tenant):
                barrier.wait(timeout=5)
                claims.append(
                    generation_queue.claim_next_generation_task(
                        worker_id,
                        lease_seconds=30,
                    )
                )
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=claim, args=(f"worker-{index}",))
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=20)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert {item["id"] for item in claims if item} == {
        first["id"],
        second["id"],
    }


def test_same_task_is_claimed_by_only_one_worker(queue_tenant):
    task = _task(queue_tenant)
    barrier = threading.Barrier(3)
    claims = []

    def claim(worker_id):
        with tenant_context.use_context(queue_tenant):
            barrier.wait(timeout=5)
            claims.append(
                generation_queue.claim_next_generation_task(
                    worker_id,
                    lease_seconds=30,
                )
            )

    threads = [
        threading.Thread(target=claim, args=(f"worker-{index}",))
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=20)

    claimed = [item for item in claims if item]
    assert len(claimed) == 1
    assert claimed[0]["id"] == task["id"]


def test_expired_provider_claim_is_resumed_not_resubmitted(queue_tenant):
    task = _task(queue_tenant)
    with tenant_context.use_context(queue_tenant):
        claim = generation_queue.claim_next_generation_task(
            "worker-a",
            lease_seconds=30,
        )
        canvas_store.update_generation_task(
            task["id"],
            status="running",
            provider_task_id="provider-task-1",
            lease_expires_at="2000-01-01T00:00:00.000Z",
        )
        recovered = generation_queue.recover_expired_queue_claims()
        resumed = generation_queue.claim_next_generation_task(
            "worker-b",
            lease_seconds=30,
        )

    assert claim["id"] == task["id"]
    assert recovered[0]["provider_task_id"] == "provider-task-1"
    assert recovered[0]["status"] == "queued"
    assert resumed["id"] == task["id"]
    assert resumed["provider_task_id"] == "provider-task-1"


def test_expired_uncertain_claim_is_blocked(queue_tenant):
    task = _task(queue_tenant)
    with tenant_context.use_context(queue_tenant):
        generation_queue.claim_next_generation_task(
            "worker-a",
            lease_seconds=30,
        )
        canvas_store.update_generation_task(
            task["id"],
            lease_expires_at="2000-01-01T00:00:00.000Z",
        )
        generation_queue.recover_expired_queue_claims()
        current = canvas_store.get_generation_task(task["id"])
        next_claim = generation_queue.claim_next_generation_task(
            "worker-b",
            lease_seconds=30,
        )

    assert current["status"] == "interrupted"
    assert current["queue_state"] == "blocked"
    assert next_claim is None


def test_claim_lease_can_be_renewed_while_admitted_for_submission(
    queue_tenant,
):
    task = _task(queue_tenant)
    with tenant_context.use_context(queue_tenant):
        claim = generation_queue.claim_next_generation_task(
            "worker-submitting",
            lease_seconds=30,
        )
        admission = generation_queue.acquire_generation_admission(
            task["id"],
            claim["claimToken"],
            provider="mock",
            model="mock-video",
            kind="video",
            lease_seconds=30,
        )
        canvas_store.update_generation_task(
            task["id"],
            lease_expires_at="2000-01-01T00:00:00.000Z",
        )
        renewed = canvas_store.renew_generation_task_lease(
            task["id"],
            claim["claimToken"],
            lease_seconds=30,
        )
        current = canvas_store.get_generation_task(task["id"])

    assert admission["acquired"] is True
    assert renewed is True
    assert current["status"] == "submitting"
    assert current["lease_owner"] == claim["claimToken"]
    assert current["lease_expires_at"] > "2000-01-01T00:00:00.000Z"


def test_terminal_commit_succeeds_while_submitting(queue_tenant):
    task = _task(queue_tenant)
    with tenant_context.use_context(queue_tenant):
        claim = generation_queue.claim_next_generation_task(
            "worker-terminal-submitting",
            lease_seconds=30,
        )
        admission = generation_queue.acquire_generation_admission(
            task["id"],
            claim["claimToken"],
            provider="mock",
            model="mock-video",
            kind="video",
            lease_seconds=30,
        )
        before = canvas_store.get_generation_task(task["id"])
        result = canvas_store.commit_generation_terminal(
            task["project_id"],
            task["node_id"],
            {
                "status": "done",
                "content": "/uploads/mock-submitting.mp4",
                "generationFinishedAt": canvas_store._now_iso(),
            },
            task_id=task["id"],
            task_updates={
                "status": "succeeded",
                "phase": "已完成",
                "delivery_status": "ready",
                "provider_task_id": "mock-provider-task",
            },
            lease_owner=claim["claimToken"],
        )
        current = canvas_store.get_generation_task(task["id"])

    assert admission["acquired"] is True
    assert result["committed"] is True, {
        "result": result,
        "admission": admission,
        "claimToken": claim["claimToken"],
        "beforeStatus": before["status"],
        "beforeOwner": before["lease_owner"],
        "beforeExpires": before["lease_expires_at"],
    }
    assert result["reason"] == ""
    assert current["status"] == "succeeded"
    assert current["provider_task_id"] == "mock-provider-task"
    assert current["lease_owner"] == ""


def test_provider_identity_and_lease_release_succeed_while_submitting(
    queue_tenant,
):
    task = _task(queue_tenant)
    with tenant_context.use_context(queue_tenant):
        claim = generation_queue.claim_next_generation_task(
            "worker-provider-submitting",
            lease_seconds=30,
        )
        admission = generation_queue.acquire_generation_admission(
            task["id"],
            claim["claimToken"],
            provider="mock",
            model="mock-video",
            kind="video",
            lease_seconds=30,
        )
        accepted = canvas_store.update_generation_task(
            task["id"],
            status="running",
            phase="排队中",
            provider_task_id="mock-provider-task",
            _lease_owner=claim["claimToken"],
        )

    assert admission["acquired"] is True
    assert accepted["provider_task_id"] == "mock-provider-task"
    assert accepted["status"] == "running"
    assert accepted["lease_owner"] == claim["claimToken"]


def test_worker_heartbeat_is_visible_to_other_processes(queue_tenant):
    with tenant_context.use_context(queue_tenant):
        generation_queue.heartbeat_worker(
            "worker-heartbeat",
            active_tasks=2,
            metadata={"release": "test"},
        )
        workers = generation_queue.worker_heartbeats()

    assert len(workers) == 1
    assert workers[0]["workerId"] == "worker-heartbeat"
    assert workers[0]["activeTasks"] == 2
    assert workers[0]["metadata"]["release"] == "test"


def test_runtime_reconcile_restores_journal_provider_identity(
    queue_tenant,
    monkeypatch,
):
    task = _task(queue_tenant)
    with tenant_context.use_context(queue_tenant):
        canonical = canvas_store.update_generation_task(
            task["id"],
            provider_task_id="mock-provider-task",
            status="running",
            phase="等待恢复",
        )
        assert canonical["provider_task_id"] == "mock-provider-task"
        canvas_store.update_generation_task(
            task["id"],
            status="failed",
            phase="服务中断",
            error="旧任务未取得可恢复的供应商任务 ID",
            provider_task_id="",
            delivery_status="pending",
        )
        journal_entry = canvas_store.remember_provider_task(
            task["id"],
            "mock-provider-task",
            project_id=task["project_id"],
            node_id=task["node_id"],
            provider="mock",
            model="mock-video",
        )
        monkeypatch.setattr(
            server,
            "_active_tenants",
            lambda: [None],
        )
        recovered = server.reconcile_generation_runtime_once()
        current = canvas_store.get_generation_task(task["id"])

    assert journal_entry["providerTaskId"] == "mock-provider-task"
    assert recovered["journalRecovered"] >= 1
    assert current["provider_task_id"] == "mock-provider-task"
    assert current["status"] == "interrupted"
    assert current["phase"] == "结果待确认"
