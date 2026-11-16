from __future__ import annotations

import os
import tempfile
import threading
import time
import uuid

import psycopg
import pytest
from psycopg import sql

import generation_budget_guard
import pg_compat
import tenant_context


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)


@pytest.fixture()
def budget_guard_tenant(monkeypatch):
    root = tempfile.TemporaryDirectory()
    suffix = "budget_" + uuid.uuid4().hex[:12]
    monkeypatch.setenv("CANVAS_DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_POSTGRES_URL"])
    monkeypatch.setenv("CANVAS_MULTI_TENANT", "1")
    monkeypatch.setenv("CANVAS_TENANT_DATA_ROOT", root.name)
    monkeypatch.setenv("CANVAS_PG_SCHEMA_SUFFIX", suffix)
    context = tenant_context.make_context("budget-guard", "budget-guard")
    tenant_context.ensure_context_dirs(context)
    schema = pg_compat.schema_for_path(context.db_path, "canvas")
    control_schema = pg_compat.schema_for_path(
        context.data_root / "control.db",
        "control",
    )
    try:
        yield context
    finally:
        with psycopg.connect(os.environ["TEST_POSTGRES_URL"]) as connection:
            for schema_name in (schema, control_schema):
                connection.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(schema_name)
                    )
                )
        root.cleanup()


def test_postgres_budget_guard_serializes_separate_connections(
    budget_guard_tenant,
):
    state = {"inside": 0, "peak": 0}
    state_lock = threading.Lock()
    barrier = threading.Barrier(9)
    errors = []

    def reserve():
        try:
            with tenant_context.use_context(budget_guard_tenant):
                barrier.wait(timeout=5)
                with generation_budget_guard.acquire(timeout_seconds=10):
                    with state_lock:
                        state["inside"] += 1
                        state["peak"] = max(state["peak"], state["inside"])
                    time.sleep(0.02)
                    with state_lock:
                        state["inside"] -= 1
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=reserve) for _ in range(8)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=20)

    assert not errors
    assert state["peak"] == 1
    assert state["inside"] == 0


def test_postgres_budget_guard_times_out_without_overlap(
    budget_guard_tenant,
):
    with tenant_context.use_context(budget_guard_tenant):
        with generation_budget_guard.acquire(timeout_seconds=2):
            with pytest.raises(generation_budget_guard.BudgetGuardTimeout):
                with generation_budget_guard.acquire(timeout_seconds=0.2):
                    pass
