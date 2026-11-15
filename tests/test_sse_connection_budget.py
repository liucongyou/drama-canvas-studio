from __future__ import annotations

import server


def test_project_stream_limit_rejects_extra_connection(monkeypatch):
    monkeypatch.setattr(server, "SSE_PROJECT_LIMIT", 1)
    monkeypatch.setattr(server, "SSE_TENANT_LIMIT", 10)
    server._EVENT_SUBSCRIBERS.clear()
    server._EVENT_TENANT_CONNECTIONS.clear()
    server._EVENT_SUBSCRIBER_TENANTS.clear()

    first, first_code = server._acquire_event_subscriber("project-1")
    second, second_code = server._acquire_event_subscriber("project-1")

    assert first is not None
    assert first_code == ""
    assert second is None
    assert second_code == "SSE_CONNECTION_LIMIT"

    server._release_event_subscriber("project-1", first)
    replacement, replacement_code = server._acquire_event_subscriber("project-1")
    assert replacement is not None
    assert replacement_code == ""
    server._release_event_subscriber("project-1", replacement)


def test_tenant_stream_limit_applies_across_projects(monkeypatch):
    monkeypatch.setattr(server, "SSE_PROJECT_LIMIT", 4)
    monkeypatch.setattr(server, "SSE_TENANT_LIMIT", 1)
    server._EVENT_SUBSCRIBERS.clear()
    server._EVENT_TENANT_CONNECTIONS.clear()
    server._EVENT_SUBSCRIBER_TENANTS.clear()

    first, first_code = server._acquire_event_subscriber("project-a")
    second, second_code = server._acquire_event_subscriber("project-b")

    assert first is not None
    assert first_code == ""
    assert second is None
    assert second_code == "SSE_CONNECTION_LIMIT"
    server._release_event_subscriber("project-a", first)
