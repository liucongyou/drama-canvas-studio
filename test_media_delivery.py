from __future__ import annotations

import http.client
import json
import tempfile
import threading
from pathlib import Path

import pytest

import canvas_store
import media_delivery
import media_storage
import provider_catalog
import server
import tenant_context


@pytest.fixture()
def delivery_tenant(monkeypatch):
    root = tempfile.TemporaryDirectory()
    context = tenant_context.make_context("delivery-test", "delivery-test")
    context = context.__class__(
        **{
            **context.__dict__,
            "data_root": Path(root.name),
            "db_path": Path(root.name) / "canvas.db",
            "upload_dir": Path(root.name) / "uploads",
            "config_path": Path(root.name) / "config.local.json",
            "log_dir": Path(root.name) / "logs",
        }
    )
    tenant_context.ensure_context_dirs(context)
    with tenant_context.use_context(context):
        yield context
    root.cleanup()


def _storage(*, ready=True, verified=True):
    return {
        "enabled": True,
        "provider": "tos",
        "last_test_ok": verified,
    }


def test_auto_mode_accepts_local_delivery_without_object_storage(
    delivery_tenant,
    monkeypatch,
):
    monkeypatch.setenv("CANVAS_MEDIA_DELIVERY_MODE", "auto")
    monkeypatch.setattr(
        provider_catalog,
        "storage_config",
        lambda: {"enabled": False, "provider": "tos"},
    )
    monkeypatch.setattr(media_storage, "is_ready", lambda cfg: False)

    status = media_delivery.readiness()

    assert status["ok"] is True
    assert status["mode"] == "auto"
    assert status["local"]["ok"] is True
    assert status["required"] is False


def test_auto_mode_keeps_local_readiness_when_storage_config_is_unreadable(
    delivery_tenant,
    monkeypatch,
):
    monkeypatch.setenv("CANVAS_MEDIA_DELIVERY_MODE", "auto")
    monkeypatch.setattr(
        provider_catalog,
        "storage_config",
        lambda: (_ for _ in ()).throw(RuntimeError("key mismatch")),
    )

    status = media_delivery.readiness()

    assert status["ok"] is True
    assert status["object"]["enabled"] is False
    assert "key mismatch" in status["object"]["error"]


def test_object_mode_requires_verified_storage(
    delivery_tenant,
    monkeypatch,
):
    monkeypatch.setenv("CANVAS_MEDIA_DELIVERY_MODE", "object")
    monkeypatch.setattr(
        provider_catalog,
        "storage_config",
        lambda: _storage(verified=False),
    )
    monkeypatch.setattr(media_storage, "is_ready", lambda cfg: True)

    status = media_delivery.readiness()

    assert status["ok"] is False
    assert status["required"] is True
    assert status["object"]["ready"] is True
    assert status["object"]["verified"] is False
    with pytest.raises(
        media_delivery.MediaDeliveryUnavailable,
        match="不会产生费用",
    ):
        media_delivery.assert_submission_ready("video")


def test_object_mode_accepts_verified_storage(
    delivery_tenant,
    monkeypatch,
):
    monkeypatch.setenv("CANVAS_MEDIA_DELIVERY_MODE", "object")
    monkeypatch.setattr(
        provider_catalog,
        "storage_config",
        lambda: _storage(),
    )
    monkeypatch.setattr(media_storage, "is_ready", lambda cfg: True)

    assert media_delivery.readiness()["ok"] is True
    media_delivery.assert_submission_ready("image")


def test_delivery_state_uses_empty_object_local_temporary_then_missing(
    delivery_tenant,
):
    assert media_delivery.delivery_state({
        "storage": {"status": "stored", "provider": "tos", "key": "a/b.mp4"},
        "content": "https://signed.example/a.mp4",
    }) == "object"

    local_name = "local.mp4"
    (delivery_tenant.upload_dir / local_name).write_bytes(b"media")
    assert media_delivery.delivery_state({
        "content": f"/uploads/{local_name}",
    }) == "local"

    assert media_delivery.delivery_state({
        "content": "https://provider.example/temp.mp4",
        "providerResultUrl": "https://provider.example/temp.mp4",
    }) == "temporary"
    assert media_delivery.delivery_state({}) == "empty"
    assert media_delivery.delivery_state({"status": "idle"}) == "empty"
    assert media_delivery.delivery_state({"storage": {}}) == "empty"
    assert media_delivery.delivery_state({
        "content": "/uploads/not-present.mp4",
    }) == "missing"


def test_durable_state_depends_on_deployment_mode():
    assert media_delivery.durable("local", "auto") is True
    assert media_delivery.durable("temporary", "auto") is False
    assert media_delivery.durable("local", "object") is False
    assert media_delivery.durable("object", "object") is True


def test_server_projection_exposes_delivery_state_for_node_and_versions(
    delivery_tenant,
    monkeypatch,
):
    monkeypatch.setenv("CANVAS_MEDIA_DELIVERY_MODE", "object")
    local_name = "projected.mp4"
    (delivery_tenant.upload_dir / local_name).write_bytes(b"media")
    meta = {
        "content": f"/uploads/{local_name}",
        "params": {
            "mediaVersions": [
                {
                    "url": f"/uploads/{local_name}",
                },
                {
                    "url": "https://signed.example/object.mp4",
                    "storage": {
                        "status": "stored",
                        "provider": "tos",
                        "key": "video/object.mp4",
                    },
                },
            ],
        },
    }

    server._project_media_delivery_state(meta)

    assert meta["deliveryState"] == "local"
    assert meta["durable"] is False
    assert meta["durabilityRequired"] is True
    assert meta["params"]["mediaVersions"][0]["deliveryState"] == "local"
    assert meta["params"]["mediaVersions"][1]["deliveryState"] == "object"
    assert meta["params"]["mediaVersions"][1]["durable"] is True


def test_object_mode_submission_is_blocked_before_task_or_provider(
    monkeypatch,
):
    monkeypatch.setenv("CANVAS_MEDIA_DELIVERY_MODE", "object")
    monkeypatch.setattr(
        media_delivery,
        "storage_status",
        lambda: {
            "provider": "tos",
            "enabled": True,
            "ready": True,
            "verified": False,
            "error": "",
        },
    )
    project_id = canvas_store.create_project("media-delivery-http-gate")
    canvas_store.insert_project_nodes(
        project_id,
        [{
            "id": "node-media-gate",
            "type": "video",
            "title": "媒体门禁测试",
            "metadata": {"episodeId": "EP001", "status": "idle"},
        }],
        episode_id="EP001",
    )

    httpd = server.CanvasHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            httpd.server_address[1],
            timeout=10,
        )
        connection.request(
            "POST",
            f"/api/projects/{project_id}/nodes/node-media-gate/generate",
            body=json.dumps({
                "type": "video",
                "prompt": "媒体门禁测试，不应提交供应商",
                "provider": "mock",
                "model": "mock-video",
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        connection.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)

    assert response.status == 503
    assert payload["code"] == "MEDIA_DELIVERY_UNAVAILABLE"
    assert "不会产生费用" in payload["error"]
    assert canvas_store.list_generation_tasks(project_id) == []
