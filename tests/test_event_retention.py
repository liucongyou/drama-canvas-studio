from __future__ import annotations

import canvas_store as store


def _init_store(monkeypatch, tmp_path):
    monkeypatch.setenv("CANVAS_DB_BACKEND", "sqlite")
    monkeypatch.setenv("CANVAS_ALLOW_SQLITE", "1")
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "canvas.db"))
    monkeypatch.setattr(store, "_SCHEMA_READY", False)
    store._connect().close()


def test_event_retention_is_scoped_per_project(monkeypatch, tmp_path):
    _init_store(monkeypatch, tmp_path)
    monkeypatch.setattr(store, "EVENT_RETENTION_PER_PROJECT", 3)

    retained = {}
    for index in range(4):
        store.append_event("project-a", "progress", payload={"index": index})
        retained[("project-a", index)] = store.append_event(
            "project-b",
            "progress",
            payload={"index": index},
        )

    events_a = store.list_events("project-a", 0, 100)
    events_b = store.list_events("project-b", 0, 100)

    assert len(events_a) == 3
    assert len(events_b) == 3
    assert {event["id"] for event in events_b} == set(
        list(retained.values())[-3:]
    )
