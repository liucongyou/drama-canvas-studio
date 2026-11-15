from __future__ import annotations

import time
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from psycopg.pq import TransactionStatus

import pg_compat


class _FakeRaw:
    def __init__(self):
        self.closed = False
        self.info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)

    def close(self):
        self.closed = True

    def commit(self):
        pass

    def rollback(self):
        pass


@pytest.fixture()
def bounded_pool(monkeypatch):
    monkeypatch.setenv(
        "CANVAS_DATABASE_URL",
        "postgresql://postgres@127.0.0.1:55432/pool_test",
    )
    original = pg_compat.pool_stats()
    created = []

    def new_raw(self):
        raw = _FakeRaw()
        created.append(raw)
        return raw

    monkeypatch.setattr(pg_compat.PGConnection, "_new_raw", new_raw)
    pg_compat.configure_pool(
        max_connections=2,
        wait_timeout_ms=120,
        schema_idle_max=2,
    )
    try:
        yield created
    finally:
        pg_compat.configure_pool(
            max_connections=original["max"],
            wait_timeout_ms=original["waitTimeoutMs"],
            schema_idle_max=original["schemaIdleMax"],
        )


def test_pool_never_creates_more_than_hard_budget(bounded_pool):
    path = Path("pool-a") / "canvas.db"
    first = pg_compat.connect_path(path)
    second = pg_compat.connect_path(path)

    started = time.monotonic()
    with pytest.raises(
        pg_compat.OperationalError,
        match="DATABASE_POOL_EXHAUSTED",
    ):
        pg_compat.connect_path(path)
    elapsed = time.monotonic() - started

    stats = pg_compat.pool_stats()
    assert elapsed >= 0.08
    assert len(bounded_pool) == 2
    assert stats["max"] == 2
    assert stats["active"] == 2
    assert stats["physical"] == 2
    assert stats["timeoutTotal"] == 1

    first.close()
    third = pg_compat.connect_path(path)
    assert len(bounded_pool) == 2
    assert pg_compat.pool_stats()["physical"] == 2

    third.close()
    second.close()


def test_returned_connection_is_reused(bounded_pool):
    path = Path("pool-b") / "canvas.db"
    first = pg_compat.connect_path(path)
    first.close()

    second = pg_compat.connect_path(path)
    stats = pg_compat.pool_stats()
    assert len(bounded_pool) == 1
    assert stats["active"] == 1
    assert stats["physical"] == 1
    second.close()


def test_closed_idle_connection_is_replaced(bounded_pool):
    path = Path("pool-c") / "canvas.db"
    first = pg_compat.connect_path(path)
    closed_raw = first.raw
    first.close()
    closed_raw.closed = True

    second = pg_compat.connect_path(path)
    stats = pg_compat.pool_stats()
    assert second.raw is not closed_raw
    assert len(bounded_pool) == 2
    assert stats["physical"] == 1
    second.close()


def test_failed_connection_creation_releases_budget(monkeypatch, bounded_pool):
    monkeypatch.setattr(
        pg_compat.PGConnection,
        "_new_raw",
        lambda self: (_ for _ in ()).throw(RuntimeError("connect failed")),
    )

    with pytest.raises(RuntimeError, match="connect failed"):
        pg_compat.connect_path(Path("pool-d") / "canvas.db")

    stats = pg_compat.pool_stats()
    assert stats["active"] == 0
    assert stats["waiting"] == 0
    assert stats["physical"] == 0


def test_waiter_acquires_the_slot_released_by_active_connection(bounded_pool):
    pg_compat.configure_pool(
        max_connections=1,
        wait_timeout_ms=1000,
        schema_idle_max=1,
    )
    path = Path("pool-e") / "canvas.db"
    first = pg_compat.connect_path(path)
    acquired = []
    errors = []

    def wait_for_slot():
        try:
            acquired.append(pg_compat.connect_path(path))
        except BaseException as exc:
            errors.append(exc)

    waiter = threading.Thread(target=wait_for_slot)
    waiter.start()
    deadline = time.monotonic() + 2
    while pg_compat.pool_stats()["waiting"] == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)
    first.close()
    waiter.join(timeout=2)

    assert not errors
    assert not waiter.is_alive()
    assert len(acquired) == 1
    acquired[0].close()


def test_cross_schema_idle_connections_share_physical_budget(bounded_pool):
    first = pg_compat.connect_path(Path("pool-schema-a") / "canvas.db")
    first.close()
    second = pg_compat.connect_path(Path("pool-schema-b") / "canvas.db")
    second.close()
    assert pg_compat.pool_stats()["physical"] == 2

    third = pg_compat.connect_path(Path("pool-schema-c") / "canvas.db")
    stats = pg_compat.pool_stats()
    assert stats["physical"] == 2
    assert stats["active"] == 1
    assert len(bounded_pool) == 3
    third.close()
