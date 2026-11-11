from __future__ import annotations

import os
import uuid

import pytest

import pg_compat


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)


@pytest.fixture()
def pg(monkeypatch):
    monkeypatch.setenv("CANVAS_DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_POSTGRES_URL"])
    schema = "compat_" + uuid.uuid4().hex[:12]
    connection = pg_compat.PGConnection(
        f"test/{schema}/canvas.db",
        "canvas",
        schema,
    )
    try:
        yield connection
    finally:
        connection.rollback()
        connection.raw.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        connection.raw.commit()
        connection.close()


def test_sqlite_compatible_crud_and_json_path(pg) -> None:
    pg.executescript(
        """
        CREATE TABLE items(
            id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL DEFAULT '{}',
            value INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE counters(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        );
        """
    )
    pg.execute(
        "INSERT INTO items(id,payload_json,value) VALUES(?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json, value=excluded.value",
        ("p1", '{"projectId":"project-a"}', 1),
    )
    pg.execute(
        "INSERT INTO items(id,payload_json,value) VALUES(?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json, value=excluded.value",
        ("p1", '{"projectId":"project-b"}', 2),
    )
    row = pg.execute(
        "SELECT id,value,rowid FROM items WHERE json_extract(payload_json, '$.projectId')=?",
        ("project-b",),
    ).fetchone()
    assert row["id"] == "p1"
    assert row[1] == 2
    assert row["rowid"] > 0

    cursor = pg.execute("INSERT INTO counters(name) VALUES(?)", ("one",))
    assert cursor.lastrowid == 1
    assert pg.execute("PRAGMA table_info(items)").fetchall()[0]["name"] == "id"
    pg.commit()


def test_json_patch_and_insert_or_replace(pg) -> None:
    pg.executescript(
        """
        CREATE TABLE preferences(
            id TEXT PRIMARY KEY,
            preferences_json TEXT NOT NULL
        );
        CREATE TABLE snapshots(
            id TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    pg.execute(
        "INSERT INTO preferences(id,preferences_json) VALUES(?,?)",
        ("t1", '{"theme":"graphite","zoom":1}'),
    )
    pg.execute(
        "INSERT INTO preferences(id,preferences_json) VALUES(?,?) "
        "ON CONFLICT(id) DO UPDATE SET preferences_json="
        "json_patch(preferences.preferences_json,excluded.preferences_json)",
        ("t1", '{"zoom":2}'),
    )
    import json

    merged = pg.execute(
        "SELECT preferences_json FROM preferences WHERE id=?",
        ("t1",),
    ).fetchone()[0]
    assert json.loads(merged) == {"theme": "graphite", "zoom": 2}

    pg.execute(
        "INSERT OR REPLACE INTO snapshots(id,value) VALUES(?,?)",
        ("s1", "old"),
    )
    pg.execute(
        "INSERT OR REPLACE INTO snapshots(id,value) VALUES(?,?)",
        ("s1", "new"),
    )
    assert pg.execute(
        "SELECT value FROM snapshots WHERE id=?",
        ("s1",),
    ).fetchone()[0] == "new"


def test_canvas_schema_name_is_independent_of_absolute_app_path() -> None:
    first = pg_compat.schema_for_path(
        "D:/deploy-a/data/tenants/u-demo/canvas.db",
        "canvas",
    )
    second = pg_compat.schema_for_path(
        "E:/deploy-b/data/tenants/u-demo/canvas.db",
        "canvas",
    )
    assert first == second
    assert first.startswith("tenant_u_demo_")
    assert first != pg_compat.schema_for_path(
        "D:/deploy-a/data/tenants/u_demo/canvas.db",
        "canvas",
    )
