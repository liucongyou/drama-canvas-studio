from __future__ import annotations

import os
import uuid

import pytest

import pg_compat
import tenant_registry


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)


@pytest.fixture()
def pg_control(monkeypatch):
    monkeypatch.setenv("CANVAS_DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_POSTGRES_URL"])
    schema = "control_" + uuid.uuid4().hex[:12]

    def connect():
        return pg_compat.PGConnection(
            f"test/{schema}/control.db",
            "control",
            schema,
        )

    tenant_registry._SCHEMA_READY.clear()
    monkeypatch.setattr(tenant_registry, "_connect", connect)
    initial = connect()
    tenant_registry._create_schema(initial)
    initial.close()
    try:
        yield schema
    finally:
        connection = pg_compat.PGConnection(
            f"test/{schema}/control.db",
            "control",
            schema,
        )
        connection.raw.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        connection.raw.commit()
        connection.close()


def test_postgres_control_registry_lifecycle(pg_control) -> None:
    tenant = tenant_registry.create_tenant("owner", name="Owner")
    account = tenant_registry.create_account(
        tenant["id"],
        "admin",
        "TmpCanvas2026!",
        role="admin",
    )
    assert tenant_registry.authenticate_account(
        "admin",
        "TmpCanvas2026!",
    )["id"] == account["id"]

    preferences = tenant_registry.update_tenant_preferences(
        tenant["id"],
        {"theme": "graphite", "zoom": 1},
    )
    assert preferences["theme"] == "graphite"
    preferences = tenant_registry.update_tenant_preferences(
        tenant["id"],
        {"zoom": 2},
    )
    assert preferences == {"theme": "graphite", "zoom": 2}

    session = tenant_registry.create_session(
        tenant["id"],
        account_id=account["id"],
        user_agent="pytest",
        ip="127.0.0.1",
    )
    assert tenant_registry.get_session(session["token"])["account_id"] == account["id"]
    assert tenant_registry.revoke_session(session["token"])
    assert tenant_registry.get_session(session["token"]) is None
