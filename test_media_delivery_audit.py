from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

import canvas_store
import media_delivery
import pytest
import tenant_context
import tenant_registry


ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "audit_media_delivery",
    ROOT / "scripts" / "audit_media_delivery.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


@pytest.fixture()
def delivery_tenant():
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


def test_audit_ignores_empty_placeholders_and_groups_real_problems_by_project(
    delivery_tenant,
    monkeypatch,
):
    monkeypatch.setenv("CANVAS_MEDIA_DELIVERY_MODE", "auto")
    monkeypatch.setattr(
        tenant_registry,
        "list_tenants",
        lambda include_disabled=True: [{
            "id": "delivery-test",
            "slug": "delivery-test",
        }],
    )
    monkeypatch.setattr(
        tenant_context,
        "make_context",
        lambda tenant_id, slug: delivery_tenant,
    )
    monkeypatch.setattr(
        canvas_store,
        "list_projects",
        lambda: [{"id": "project-a", "title": "项目 A"}],
    )
    monkeypatch.setattr(
        canvas_store,
        "get_project",
        lambda project_id: {
            "id": project_id,
            "payload": {
                "nodes": [
                    {
                        "id": "empty-image",
                        "type": "image",
                        "title": "空白占位",
                        "metadata": {"status": "idle"},
                    },
                    {
                        "id": "temporary-video",
                        "type": "video",
                        "title": "临时视频",
                        "metadata": {
                            "content": "https://provider.example/temp.mp4",
                            "params": {
                                "videoVersions": [{
                                    "url": "https://provider.example/temp.mp4",
                                }],
                            },
                        },
                    },
                    {
                        "id": "missing-image",
                        "type": "image",
                        "title": "失效图片",
                        "metadata": {"content": "/uploads/not-present.png"},
                    },
                ],
            },
        },
    )
    monkeypatch.setattr(
        media_delivery,
        "readiness",
        lambda: {"ok": True, "mode": "auto"},
    )

    report = MODULE._summary(ignore_empty=True)

    assert report["nonDurableCount"] == 2
    assert report["totalNodes"] == {"temporary": 1, "missing": 1}
    assert report["totalVersions"] == {"temporary": 1}
    project = report["tenants"][0]["projects"][0]
    assert project["projectId"] == "project-a"
    assert project["nonDurableCount"] == 2
    assert project["states"] == {"temporary": 1, "missing": 1}
    assert len(project["samples"]) == 2
