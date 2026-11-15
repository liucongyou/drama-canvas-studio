from __future__ import annotations

import os
import threading
import uuid

import pytest

import pg_compat
import schema_migrations


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)


@pytest.fixture()
def migration_schema(monkeypatch):
    monkeypatch.setenv("CANVAS_DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_POSTGRES_URL"])
    schema = "migration_" + uuid.uuid4().hex[:12]
    path = f"test/{schema}/canvas.db"
    connection = pg_compat.PGConnection(path, "canvas", schema)
    connection.executescript(
        """
        CREATE TABLE generation_tasks(
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            created_at TEXT,
            heartbeat_at TEXT,
            lease_expires_at TEXT
        )
        """
    )
    connection.commit()
    try:
        yield schema, path
    finally:
        connection.rollback()
        connection.raw.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        connection.raw.commit()
        connection.close()


def _connect(path, schema):
    return pg_compat.PGConnection(path, "canvas", schema)


def test_migrations_apply_once_and_second_run_is_noop(migration_schema):
    schema, path = migration_schema
    connection = _connect(path, schema)
    try:
        first = schema_migrations.apply_migrations(connection, "canvas")
    finally:
        connection.close()

    connection = _connect(path, schema)
    try:
        second = schema_migrations.apply_migrations(connection, "canvas")
        verification = schema_migrations.verify_schema(connection, "canvas")
    finally:
        connection.close()

    assert first["applied"] == ["0001", "0002"]
    assert second["applied"] == []
    assert second["skipped"] == ["0001", "0002"]
    assert verification["ok"] is True
    assert verification["appliedVersion"] == "0002"


def test_checksum_mismatch_fails_closed(migration_schema):
    schema, path = migration_schema
    connection = _connect(path, schema)
    try:
        schema_migrations.apply_migrations(connection, "canvas")
        connection.execute(
            "UPDATE schema_migrations SET checksum='changed' "
            "WHERE version='0001'"
        )
        connection.commit()
        verification = schema_migrations.verify_schema(connection, "canvas")
        with pytest.raises(
            schema_migrations.MigrationError,
            match="校验和不一致",
        ):
            schema_migrations.apply_migrations(connection, "canvas")
    finally:
        connection.close()

    assert verification["ok"] is False
    assert verification["mismatched"] == ["0001"]


def test_concurrent_migration_callers_do_not_duplicate_versions(
    migration_schema,
):
    schema, path = migration_schema
    barrier = threading.Barrier(3)
    reports = []
    errors = []

    def migrate():
        connection = _connect(path, schema)
        try:
            barrier.wait(timeout=5)
            reports.append(
                schema_migrations.apply_migrations(connection, "canvas")
            )
        except BaseException as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=migrate) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=20)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    applied = [
        version
        for report in reports
        for version in report["applied"]
    ]
    assert sorted(applied) == ["0001", "0002"]

    connection = _connect(path, schema)
    try:
        rows = connection.execute(
            "SELECT version,COUNT(*) FROM schema_migrations "
            "GROUP BY version ORDER BY version"
        ).fetchall()
    finally:
        connection.close()
    assert [(str(row[0]), int(row[1])) for row in rows] == [
        ("0001", 1),
        ("0002", 1),
    ]
