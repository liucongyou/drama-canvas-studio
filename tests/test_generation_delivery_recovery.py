from __future__ import annotations

import canvas_store as store
import server


def _init_store(monkeypatch, tmp_path):
    monkeypatch.setenv("CANVAS_DB_BACKEND", "sqlite")
    monkeypatch.setenv("CANVAS_ALLOW_SQLITE", "1")
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "canvas.db"))
    monkeypatch.setattr(store, "_SCHEMA_READY", False)
    store._connect().close()


def _seed_project(monkeypatch, tmp_path, node_id):
    _init_store(monkeypatch, tmp_path)
    project_id = store.create_project(
        "交付恢复",
        payload={
            "nodes": [
                {
                    "id": node_id,
                    "type": "video",
                    "title": "视频节点",
                    "position": {"x": 0, "y": 0},
                    "metadata": {
                        "episodeId": "EP001",
                        "status": "generating",
                        "providerTaskStatus": "running",
                    },
                }
            ],
            "connections": [],
            "episodeImports": [
                {
                    "id": "EP001",
                    "title": "EP001",
                    "nodeIds": [node_id],
                    "videoNodeIds": [node_id],
                    "connectionIds": [],
                }
            ],
        },
    )
    return project_id


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


def test_failed_materialized_task_is_projected_as_succeeded(
    monkeypatch,
    tmp_path,
):
    node_id = "node-materialized"
    project_id = _seed_project(monkeypatch, tmp_path, node_id)
    upload = tmp_path / "uploads"
    upload.mkdir()
    media = upload / "video.mp4"
    media.write_bytes(b"video")
    task = store.create_generation_task(
        project_id,
        node_id,
        title="已完成视频",
        kind="video",
    )
    store.update_generation_task(
        task["id"],
        status="failed",
        phase="任务异常结束",
        error="未形成终态",
        delivery_status="failed",
        provider_task_id="provider-materialized",
        provider_result_url="https://example.test/video.mp4",
        local_result_url="/uploads/video.mp4",
    )
    monkeypatch.setattr(
        server.provider_client,
        "local_upload_path",
        lambda url: str(media) if url == "/uploads/video.mp4" else None,
    )

    recovered = server.reconcile_pending_generation_deliveries()

    saved = store.get_generation_task(task["id"])
    node = store.get_projection_node(project_id, node_id)
    assert recovered == 1
    assert saved["status"] == "succeeded"
    assert saved["delivery_status"] == "ready"
    assert node["metadata"]["status"] == "done"
    assert node["metadata"]["content"] == "/uploads/video.mp4"


def test_succeeded_task_without_any_receipt_is_marked_unrecoverable(
    monkeypatch,
    tmp_path,
):
    node_id = "node-no-receipt"
    project_id = _seed_project(monkeypatch, tmp_path, node_id)
    task = store.create_generation_task(
        project_id,
        node_id,
        title="无回执视频",
        kind="video",
    )
    store.update_generation_task(
        task["id"],
        status="succeeded",
        phase="已完成",
        delivery_status="pending",
        provider_task_id="provider-no-receipt",
    )

    recovered = server.reconcile_pending_generation_deliveries()

    saved = store.get_generation_task(task["id"])
    node = store.get_projection_node(project_id, node_id)
    assert recovered == 0
    assert saved["status"] == "succeeded"
    assert saved["delivery_status"] == "unrecoverable"
    assert "无法恢复" in saved["error"]
    assert node["metadata"]["status"] == "failed"
    assert node["metadata"]["providerTaskStatus"] == "unrecoverable"
    assert store.list_delivery_pending_tasks(100) == []


def test_missing_local_file_is_classified_once_and_not_requeued(
    monkeypatch,
    tmp_path,
):
    node_id = "node-missing-local"
    project_id = _seed_project(monkeypatch, tmp_path, node_id)
    task = store.create_generation_task(
        project_id,
        node_id,
        title="本地文件缺失",
        kind="video",
    )
    store.update_generation_task(
        task["id"],
        status="failed",
        phase="任务异常结束",
        delivery_status="failed",
        provider_task_id="provider-missing-local",
        local_result_url="/uploads/missing.mp4",
    )
    monkeypatch.setattr(
        server.provider_client,
        "local_upload_path",
        lambda url: None,
    )

    first = server.reconcile_pending_generation_deliveries()
    second = server.reconcile_pending_generation_deliveries()

    saved = store.get_generation_task(task["id"])
    assert first == 0
    assert second == 0
    assert saved["status"] == "failed"
    assert saved["delivery_status"] == "unrecoverable"
    assert saved["phase"] == "本地文件缺失"
    assert store.list_delivery_pending_tasks(100) == []
