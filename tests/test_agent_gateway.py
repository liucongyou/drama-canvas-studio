import json

import pytest

import agent_gateway


def test_gateway_signing_headers_round_trip(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_INTERNAL_SECRET", "secret")
    headers = agent_gateway.sign_context_headers(
        method="POST",
        path="/api/internal/agent/projects/p1/feed/preview",
        tenant_id="tenant-a",
        account_id="account-a",
        body=b'{"feed":{}}',
        request_id="req-1",
        timestamp="1700000000",
    )
    context = agent_gateway.verify_context_headers(
        headers,
        method="POST",
        path="/api/internal/agent/projects/p1/feed/preview",
        body=b'{"feed":{}}',
        now=1700000000,
    )
    assert context.tenant_id == "tenant-a"
    assert context.account_id == "account-a"
    assert context.request_id == "req-1"


def test_gateway_rejects_tampered_body(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_INTERNAL_SECRET", "secret")
    headers = agent_gateway.sign_context_headers(
        method="POST",
        path="/api/internal/agent/projects/p1/feed/preview",
        tenant_id="tenant-a",
        account_id="account-a",
        body=b'{"feed":{}}',
        timestamp="1700000000",
    )
    with pytest.raises(ValueError):
        agent_gateway.verify_context_headers(
            headers,
            method="POST",
            path="/api/internal/agent/projects/p1/feed/preview",
            body=b'{"feed":{"x":1}}',
            now=1700000000,
        )


def test_project_index_omits_full_payload() -> None:
    project = {
        "id": "p1",
        "title": "项目",
        "revision": 7,
        "payload": {
            "nodes": [{"id": "n1", "type": "image"}, {"id": "n2", "type": "video"}],
            "connections": [{"id": "c1", "fromNodeId": "n1", "toNodeId": "n2"}],
            "episodeImports": [{"id": "EP001", "title": "第01集", "nodeIds": ["n1", "n2"]}],
        },
    }
    result = agent_gateway.project_index_from_project(project)
    assert result["nodeCount"] == 2
    assert result["connectionCount"] == 1
    assert "payload" not in result
    json.dumps(result, ensure_ascii=False)
