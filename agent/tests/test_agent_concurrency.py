from concurrent.futures import ThreadPoolExecutor

import pytest

from app import db, runtime, skills
from app.config import get_settings
from app.conversations import ensure_conversation, list_conversations


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "agent.db"))
    monkeypatch.setenv("LLM_MODE", "mock")
    get_settings.cache_clear()
    db.init_db()
    skills.install_builtin_demo()
    yield
    get_settings.cache_clear()


def test_conversation_creation_is_tenant_isolated(tmp_db) -> None:
    def create(index: int):
        tenant = "tenant-a" if index % 2 == 0 else "tenant-b"
        return ensure_conversation(tenant, f"account-{index}", "same-project")

    with ThreadPoolExecutor(max_workers=8) as executor:
        created = list(executor.map(create, range(20)))
    assert len({item["id"] for item in created}) == 20
    tenant_a = list_conversations("tenant-a", "account-0", "same-project")
    tenant_b = list_conversations("tenant-b", "account-1", "same-project")
    assert {item["id"] for item in tenant_a}.isdisjoint({item["id"] for item in tenant_b})


def test_runs_and_idempotency_are_scoped(tmp_db, monkeypatch) -> None:
    monkeypatch.setattr(runtime, "execute_run", lambda _run_id: None)
    first = runtime.create_run(
        "project-1",
        "demo-director",
        "第一租户",
        conversation_id="conv-a",
        tenant_id="tenant-a",
        account_id="account-a",
        idempotency_key="same-key",
    )
    second = runtime.create_run(
        "project-1",
        "demo-director",
        "第二租户",
        conversation_id="conv-a",
        tenant_id="tenant-b",
        account_id="account-b",
        idempotency_key="same-key",
    )
    assert first["id"] != second["id"]
    assert runtime.find_idempotent_run(
        "same-key",
        "tenant-a",
        "account-a",
        "conv-a",
    )["id"] == first["id"]
    assert runtime.find_idempotent_run(
        "same-key",
        "tenant-b",
        "account-b",
        "conv-a",
    )["id"] == second["id"]
    assert runtime.list_runs(
        "project-1",
        None,
        "tenant-a",
        "account-a",
    )[0]["id"] == first["id"]
