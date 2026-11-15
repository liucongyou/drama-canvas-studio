from __future__ import annotations

import io
import json

import canvas_store
import server
import telemetry


def _init_store(monkeypatch, tmp_path):
    monkeypatch.setenv("CANVAS_DB_BACKEND", "sqlite")
    monkeypatch.setenv("CANVAS_ALLOW_SQLITE", "1")
    monkeypatch.setattr(canvas_store, "DB_PATH", str(tmp_path / "canvas.db"))
    monkeypatch.setattr(canvas_store, "_SCHEMA_READY", False)
    canvas_store._connect().close()


def test_sanitizer_removes_secrets_prompts_and_signed_queries():
    clean = telemetry.sanitize_frontend_telemetry({
        "message": (
            "failed https://bucket.example/video.mp4?X-Tos-Signature=secret "
            "Authorization: Bearer abc"
        ),
        "stack": "at save (https://app.example/main.js?token=secret:1:2)",
        "cookie": "session=secret",
        "token": "secret",
        "prompt": "完整提示词",
        "projectId": "p1",
        "nodeId": "n1",
        "taskId": "t1",
        "route": "/?ep=EP001",
    })

    assert clean["projectId"] == "p1"
    assert clean["nodeId"] == "n1"
    assert "prompt" not in clean
    assert "cookie" not in clean
    assert "token" not in clean
    assert "X-Tos-Signature" not in clean["message"]
    assert "Bearer abc" not in clean["message"]
    assert len(json.dumps(clean, ensure_ascii=False).encode("utf-8")) <= 16 * 1024


def test_frontend_error_is_tenant_scoped_counted_and_rotated(
    monkeypatch,
    tmp_path,
):
    _init_store(monkeypatch, tmp_path)
    event_id = canvas_store.record_frontend_error({
        "message": "render failed",
        "stack": "Error: render failed",
        "projectId": "project-a",
        "nodeId": "node-a",
        "route": "/?ep=EP001",
    }, account_id="account-a")

    events = canvas_store.list_frontend_errors(10, project_id="project-a")
    assert [event["id"] for event in events] == [event_id]
    assert events[0]["accountId"] == "account-a"
    assert canvas_store.runtime_metrics()["frontendErrors"] == 1

    con = canvas_store._connect()
    try:
        con.execute(
            "UPDATE frontend_error_events SET created_at=? WHERE id=?",
            ("2000-01-01T00:00:00.000Z", event_id),
        )
        con.commit()
    finally:
        con.close()
    assert canvas_store.rotate_frontend_errors(ttl_days=30) == 1
    assert canvas_store.list_frontend_errors(10) == []


def test_frontend_telemetry_endpoint_accepts_sanitized_body(
    monkeypatch,
    tmp_path,
):
    _init_store(monkeypatch, tmp_path)
    body = json.dumps({
        "message": "save failed",
        "prompt": "must not persist",
        "projectId": "project-a",
        "nodeId": "node-a",
    }).encode("utf-8")
    handler = object.__new__(server.Handler)
    handler.path = "/api/telemetry/frontend-errors"
    handler.headers = {"Content-Length": str(len(body))}
    handler.rfile = io.BytesIO(body)
    handler.command = "POST"
    handler.client_address = ("127.0.0.1", 12345)
    sent = {}

    def send(code, payload=None, **_kwargs):
        sent["code"] = code
        sent["payload"] = payload

    handler._send = send
    handler._current_session = lambda: None
    server.Handler._do_post(handler)

    assert sent["code"] == 202
    events = canvas_store.list_frontend_errors(10)
    assert len(events) == 1
    assert events[0]["message"] == "save failed"
    assert "must not persist" not in json.dumps(events, ensure_ascii=False)
