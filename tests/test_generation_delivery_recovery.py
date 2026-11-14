from __future__ import annotations

import canvas_store as store


def _init_store(monkeypatch, tmp_path):
    monkeypatch.setenv("CANVAS_DB_BACKEND", "sqlite")
    monkeypatch.setenv("CANVAS_ALLOW_SQLITE", "1")
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "canvas.db"))
    monkeypatch.setattr(store, "_SCHEMA_READY", False)
    store._connect().close()


def test_failed_task_with_durable_local_result_is_recoverable(
    monkeypatch,
    tmp_path,
):
    _init_store(monkeypatch, tmp_path)
    task = store.create_generation_task(
        "project",
        "node-video",
        title="段11",
        kind="video",
    )
    store.update_generation_task(
        task["id"],
        status="failed",
        phase="任务异常结束",
        error="未形成终态",
        delivery_status="failed",
        provider_task_id="provider-1",
        provider_result_url="https://example.test/video.mp4",
        local_result_url="/uploads/video.mp4",
    )

    candidates = store.list_delivery_pending_tasks(100)
    assert [item["id"] for item in candidates] == [task["id"]]

    claimed = store.claim_local_delivery(task["id"])
    assert claimed is not None
    assert claimed["delivery_status"] == "materializing"


def test_failed_task_without_local_result_is_not_claimed(
    monkeypatch,
    tmp_path,
):
    _init_store(monkeypatch, tmp_path)
    task = store.create_generation_task(
        "project",
        "node-failed-before-result",
        title="段12",
        kind="video",
    )
    store.update_generation_task(
        task["id"],
        status="failed",
        phase="生成失败",
        error="provider failed",
        delivery_status="failed",
    )

    assert store.list_delivery_pending_tasks(100) == []
    assert store.claim_local_delivery(task["id"]) is None
