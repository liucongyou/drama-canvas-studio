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
    monkeypatch.setenv("CANVAS_ALLOW_SQLITE", "1")
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


def test_recipe_catalog_and_search_api(client: TestClient) -> None:
    catalog = client.get(
        "/api/agent/recipes?kind=creative&limit=5",
        headers=LOCAL_HEADERS,
    )
    assert catalog.status_code == 200
    body = catalog.json()
    assert body["catalog"]["recipeCount"] == 295
    assert len(body["recipes"]) == 5
    search = client.get(
        "/api/agent/recipes?query=群演聚焦接力",
        headers=LOCAL_HEADERS,
    ).json()
    assert search["recipes"][0]["id"] == "handoff-ensemble-focus"


def test_recipe_binding_is_persisted_on_run(client: TestClient) -> None:
    created = client.post(
        "/api/agent/runs",
        json={
            "projectId": "project-1",
            "skillId": "demo-director",
            "prompt": "使用群像镜头",
            "modelId": "mock-text-model",
            "recipeBindings": [{
                "id": "handoff-ensemble-focus",
                "version": "1.0.0",
                "role": "primary",
                "segmentNo": 1,
                "slots": {},
            }],
        },
        headers={**LOCAL_HEADERS, "Idempotency-Key": "recipe-run"},
    )
    assert created.status_code == 200
    run = created.json()["run"]
    assert run["recipeBindings"][0]["id"] == "handoff-ensemble-focus"
    assert run["recipeBindings"][0]["segmentNo"] == 1
