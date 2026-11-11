import pytest
from fastapi.testclient import TestClient

from app import db, runtime
from app.config import get_settings
from app.main import create_app

LOCAL_HEADERS = {
    "X-Agent-Service": "canvas",
    "X-Agent-Tenant-Id": "owner",
    "X-Agent-Account-Id": "admin",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "agent.db"))
    monkeypatch.setenv("LLM_MODE", "mock")
    monkeypatch.setenv("AGENT_INTERNAL_SECRET", "")
    monkeypatch.setattr(runtime, "execute_run", lambda _run_id: None)
    get_settings.cache_clear()
    db.init_db()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_append_message_is_idempotent(client: TestClient) -> None:
    created = client.post(
        "/api/agent/conversations",
        json={"projectId": "project-1", "title": "会话"},
        headers=LOCAL_HEADERS,
    )
    assert created.status_code == 200
    conversation_id = created.json()["conversation"]["id"]
    body = {
        "content": [{"type": "text", "text": "继续"}],
        "skillId": "demo-director",
        "modelId": "",
        "routeId": "",
        "locks": {},
        "fileIds": [],
        "referenceNodeIds": [],
    }
    first = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json=body,
        headers={**LOCAL_HEADERS, "Idempotency-Key": "same"},
    )
    second = client.post(
        f"/api/agent/conversations/{conversation_id}/messages",
        json=body,
        headers={**LOCAL_HEADERS, "Idempotency-Key": "same"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["run"]["id"] == second.json()["run"]["id"]
    assert second.json()["idempotent"] is True


def test_draft_survives_reload(client: TestClient) -> None:
    created = client.post(
        "/api/agent/conversations",
        json={"projectId": "project-1"},
        headers=LOCAL_HEADERS,
    )
    conversation_id = created.json()["conversation"]["id"]
    saved = client.put(
        f"/api/agent/conversations/{conversation_id}/draft",
        json={
            "text": "中文未发送草稿",
            "attachments": [{"id": "file-1", "name": "剧本.txt"}],
            "references": [{"assetId": "character:陈大强:default"}],
            "locks": {"track": "国内剧"},
        },
        headers=LOCAL_HEADERS,
    )
    assert saved.status_code == 200
    loaded = client.get(
        f"/api/agent/conversations/{conversation_id}/draft",
        headers=LOCAL_HEADERS,
    )
    assert loaded.status_code == 200
    assert loaded.json()["draft"]["text"] == "中文未发送草稿"
    assert loaded.json()["draft"]["locks"]["track"] == "国内剧"
