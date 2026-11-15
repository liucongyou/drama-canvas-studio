from __future__ import annotations

import threading

import pytest

import canvas_store
import pg_compat


class _Row:
    def __init__(self, values):
        self._values = values

    def __getitem__(self, index):
        return self._values[index]


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _RecordingPostgresConnection(pg_compat.PGConnection):
    def __init__(self, row):
        self.statements = []
        self._row = row

    def execute(self, sql, params=()):
        self.statements.append((sql, tuple(params)))
        return _Cursor(self._row)


def test_postgres_project_lock_uses_row_level_for_update():
    connection = _RecordingPostgresConnection(_Row(("项目", "{}", 3)))

    row = canvas_store.lock_project_row(connection, "project-1")

    assert row[2] == 3
    sql, params = connection.statements[0]
    assert "FOR UPDATE" in sql
    assert params == ("project-1",)


def test_project_lock_rejects_missing_project():
    connection = _RecordingPostgresConnection(None)

    with pytest.raises(KeyError, match="项目不存在"):
        canvas_store.lock_project_row(connection, "missing")


def test_concurrent_node_patches_merge_without_lost_updates():
    project_id = canvas_store.create_project(
        "写边界并发",
        payload={
            "nodes": [
                {
                    "id": "node-1",
                    "type": "text",
                    "title": "节点",
                    "position": {"x": 0, "y": 0},
                    "metadata": {
                        "episodeId": "EP001",
                        "prompt": "",
                    },
                }
            ],
            "connections": [],
            "episodeImports": [
                {
                    "id": "EP001",
                    "title": "EP001",
                    "nodeIds": ["node-1"],
                    "videoNodeIds": [],
                    "connectionIds": [],
                }
            ],
        },
    )
    barrier = threading.Barrier(3)
    errors: list[BaseException] = []

    def patch(key: str) -> None:
        try:
            barrier.wait()
            canvas_store.update_project_node(
                project_id,
                "node-1",
                episode_id="EP001",
                patch_node={"metadata": {key: f"value-{key}"}},
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=patch, args=("left",)),
        threading.Thread(target=patch, args=("right",)),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    node = canvas_store.get_projection_node(project_id, "node-1")
    assert node is not None
    assert node["metadata"]["left"] == "value-left"
    assert node["metadata"]["right"] == "value-right"


def test_projection_metadata_merge_does_not_rewrite_project_payload():
    project_id = canvas_store.create_project(
        "生成状态投影写",
        payload={
            "nodes": [
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "视频一",
                    "position": {"x": 0, "y": 0},
                    "metadata": {
                        "episodeId": "EP001",
                        "status": "idle",
                        "params": {"model": "mock-video"},
                    },
                },
                {
                    "id": "text-1",
                    "type": "text",
                    "title": "文本一",
                    "position": {"x": 200, "y": 0},
                    "metadata": {
                        "episodeId": "EP001",
                        "prompt": "保持不变",
                    },
                },
            ],
            "connections": [],
            "episodeImports": [
                {
                    "id": "EP001",
                    "title": "EP001",
                    "nodeIds": ["video-1", "text-1"],
                    "videoNodeIds": ["video-1"],
                    "connectionIds": [],
                }
            ],
        },
    )
    connection = canvas_store._connect()
    try:
        before_payload = connection.execute(
            "SELECT payload_json FROM canvas_projects WHERE id=?",
            (project_id,),
        ).fetchone()[0]
    finally:
        connection.close()

    saved = canvas_store.merge_projection_node_metadata(
        project_id,
        "video-1",
        {
            "status": "running",
            "providerTaskStatus": "queued",
            "_paramsPatch": {"progress": 10},
        },
        episode_id="EP001",
    )

    assert saved is not None
    node = canvas_store.get_projection_node(project_id, "video-1")
    assert node["metadata"]["status"] == "running"
    assert node["metadata"]["params"]["model"] == "mock-video"
    assert node["metadata"]["params"]["progress"] == 10
    connection = canvas_store._connect()
    try:
        after_payload = connection.execute(
            "SELECT payload_json FROM canvas_projects WHERE id=?",
            (project_id,),
        ).fetchone()[0]
    finally:
        connection.close()
    assert after_payload == before_payload
