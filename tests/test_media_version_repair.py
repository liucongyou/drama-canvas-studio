from __future__ import annotations

import canvas_store as store


def _init_store(monkeypatch, tmp_path):
    monkeypatch.setenv("CANVAS_DB_BACKEND", "sqlite")
    monkeypatch.setenv("CANVAS_ALLOW_SQLITE", "1")
    monkeypatch.setattr(store, "DB_PATH", str(tmp_path / "canvas.db"))
    monkeypatch.setattr(store, "_SCHEMA_READY", False)
    store._connect().close()


def _seed_node(monkeypatch, tmp_path, metadata):
    _init_store(monkeypatch, tmp_path)
    project_id = store.create_project(
        "媒体版本修复",
        payload={
            "nodes": [
                {
                    "id": "node-media",
                    "type": "video",
                    "title": "视频节点",
                    "position": {"x": 0, "y": 0},
                    "metadata": {
                        "episodeId": "EP001",
                        **metadata,
                    },
                }
            ],
            "connections": [],
            "episodeImports": [
                {
                    "id": "EP001",
                    "title": "EP001",
                    "nodeIds": ["node-media"],
                    "videoNodeIds": ["node-media"],
                    "connectionIds": [],
                }
            ],
        },
    )
    return project_id


def _versions(project_id):
    node = store.get_projection_node(project_id, "node-media")
    return node["metadata"]["params"]["mediaVersions"]


def test_duplicate_version_ids_become_unique_without_losing_urls(
    monkeypatch,
    tmp_path,
):
    project_id = _seed_node(
        monkeypatch,
        tmp_path,
        {
            "content": "/uploads/v2.mp4",
            "params": {
                "activeVersionId": "video:duplicate",
                "mediaVersionIndex": 1,
                "mediaVersions": [
                    {
                        "versionId": "video:duplicate",
                        "url": "/uploads/v1.mp4",
                    },
                    {
                        "versionId": "video:duplicate",
                        "url": "/uploads/v2.mp4",
                    },
                ],
            },
        },
    )
    urls_before = [item["url"] for item in _versions(project_id)]

    dry_run = store.repair_media_version_identities(apply=False)
    assert dry_run["duplicateIdsBefore"] == 1
    assert dry_run["nodesChanged"] == 1
    assert len({item["versionId"] for item in _versions(project_id)}) == 1

    applied = store.repair_media_version_identities(apply=True)
    versions = _versions(project_id)
    node = store.get_projection_node(project_id, "node-media")

    assert applied["duplicateIdsBefore"] == 1
    assert applied["duplicateIdsAfter"] == 0
    assert len({item["versionId"] for item in versions}) == len(versions)
    assert [item["url"] for item in versions] == urls_before
    active_id = node["metadata"]["params"]["activeVersionId"]
    active = next(item for item in versions if item["versionId"] == active_id)
    assert active["url"] == "/uploads/v2.mp4"


def test_active_version_mismatch_uses_current_content_url(
    monkeypatch,
    tmp_path,
):
    project_id = _seed_node(
        monkeypatch,
        tmp_path,
        {
            "content": "/uploads/current.png",
            "params": {
                "activeVersionId": "image:missing",
                "mediaVersions": [
                    {"versionId": "image:old", "url": "/uploads/old.png"},
                    {
                        "versionId": "image:current",
                        "url": "/uploads/current.png",
                    },
                ],
            },
        },
    )

    result = store.repair_media_version_identities(apply=True)
    node = store.get_projection_node(project_id, "node-media")

    assert result["activeMismatchBefore"] == 1
    assert result["activeMismatchAfter"] == 0
    assert node["metadata"]["params"]["activeVersionId"] == "image:current"
    assert node["metadata"]["params"]["mediaVersionIndex"] == 1


def test_active_version_mismatch_falls_back_to_newest_local_version(
    monkeypatch,
    tmp_path,
):
    project_id = _seed_node(
        monkeypatch,
        tmp_path,
        {
            "content": "https://provider.example/expired-v2.png",
            "params": {
                "activeVersionId": "image:missing",
                "mediaVersions": [
                    {
                        "versionId": "image:remote",
                        "url": "https://provider.example/expired.png",
                    },
                    {
                        "versionId": "image:local",
                        "url": "/uploads/local.png",
                    },
                ],
            },
        },
    )

    store.repair_media_version_identities(apply=True)
    node = store.get_projection_node(project_id, "node-media")

    assert node["metadata"]["params"]["activeVersionId"] == "image:local"
    assert node["metadata"]["content"] == "/uploads/local.png"
    assert node["metadata"].get("mediaIdentityUnresolved") is not True


def test_unresolved_active_version_is_explicit_and_preserves_history(
    monkeypatch,
    tmp_path,
):
    project_id = _seed_node(
        monkeypatch,
        tmp_path,
        {
            "content": "/uploads/not-recorded.png",
            "params": {
                "activeVersionId": "image:missing",
                "mediaVersions": [
                    {
                        "versionId": "image:remote",
                        "url": "https://provider.example/remote.png",
                    }
                ],
            },
        },
    )
    urls_before = [item["url"] for item in _versions(project_id)]

    result = store.repair_media_version_identities(apply=True)
    node = store.get_projection_node(project_id, "node-media")

    assert result["unresolved"] == 1
    assert node["metadata"]["mediaIdentityUnresolved"] is True
    assert [item["url"] for item in _versions(project_id)] == urls_before
