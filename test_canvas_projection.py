# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import canvas_store
import tenant_context


class CanvasProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            "CANVAS_MULTI_TENANT": "1",
            "CANVAS_TENANT_DATA_ROOT": str(self.root),
        })
        self.env.start()
        self.context = tenant_context.make_context("projection", "projection")
        tenant_context.ensure_context_dirs(self.context)
        self.scope = tenant_context.use_context(self.context)
        self.scope.__enter__()

    def tearDown(self):
        self.scope.__exit__(None, None, None)
        self.env.stop()
        self.temp.cleanup()

    def _payload(self):
        return {
            "title": "projection",
            "nodes": [
                {
                    "id": "image-1",
                    "type": "image",
                    "title": "角色｜甲",
                    "position": {"x": 10, "y": 20},
                    "metadata": {
                        "prompt": "角色",
                        "content": "/uploads/a.png",
                        "episodeId": "EP001",
                    },
                },
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段1",
                    "position": {"x": 210, "y": 20},
                    "metadata": {
                        "prompt": "视频",
                        "status": "failed",
                        "lastError": "参考素材缺失",
                        "episodeId": "EP001",
                    },
                },
                {
                    "id": "image-2",
                    "type": "image",
                    "title": "角色｜乙",
                    "position": {"x": 10, "y": 260},
                    "metadata": {"prompt": "第二集", "episodeId": "EP002"},
                },
            ],
            "connections": [
                {
                    "id": "edge-1",
                    "fromNodeId": "image-1",
                    "toNodeId": "video-1",
                    "source": "user",
                },
            ],
            "episodeImports": [
                {
                    "id": "EP001",
                    "title": "第01集",
                    "nodeIds": ["image-1", "video-1"],
                    "videoNodeIds": ["video-1"],
                    "connectionIds": ["edge-1"],
                },
                {
                    "id": "EP002",
                    "title": "第02集",
                    "nodeIds": ["image-2"],
                    "videoNodeIds": [],
                },
            ],
            "episodeViewports": {
                "EP001": {"x": 1, "y": 2, "zoom": 0.7},
                "EP002": {"x": 3, "y": 4, "zoom": 0.5},
            },
            "episodeLayouts": {
                "EP001": {"image-1": {"x": 10, "y": 20}},
            },
        }

    def test_projection_round_trip_matches_payload(self):
        project_id = canvas_store.create_project("projection", self._payload())
        projected = canvas_store.get_project_projection(project_id)
        self.assertIsNotNone(projected)
        self.assertEqual(
            [node["id"] for node in projected["payload"]["nodes"]],
            ["image-1", "video-1", "image-2"],
        )
        self.assertEqual(
            projected["payload"]["connections"],
            self._payload()["connections"],
        )
        self.assertEqual(
            projected["payload"]["episodeViewports"]["EP002"]["zoom"],
            0.5,
        )

    def test_episode_read_returns_only_current_episode_nodes(self):
        project_id = canvas_store.create_project("projection", self._payload())
        workspace = canvas_store.get_project_workspace_nodes(
            project_id,
            "EP001",
        )
        self.assertEqual(
            [node["id"] for node in workspace["nodes"]],
            ["image-1", "video-1"],
        )
        self.assertEqual(
            [edge["id"] for edge in workspace["connections"]],
            ["edge-1"],
        )

    def test_projection_counts_failed_nodes_without_payload_parse(self):
        project_id = canvas_store.create_project("projection", self._payload())
        summary = next(
            item for item in canvas_store.list_project_summaries()
            if item["id"] == project_id
        )
        self.assertEqual(summary["node_count"], 3)
        self.assertEqual(summary["connection_count"], 1)
        self.assertEqual(summary["episode_count"], 2)
        self.assertEqual(summary["local_blocked_count"], 1)

    def test_viewport_write_does_not_replace_newer_node_result(self):
        project_id = canvas_store.create_project("projection", self._payload())
        current = canvas_store.get_project(project_id)
        stale_revision = current["revision"]
        node_result = canvas_store.update_project_node(
            project_id,
            "video-1",
            patch_node={
                "metadata": {
                    "status": "done",
                    "content": "/uploads/final.mp4",
                },
            },
            if_revision=current["revision"],
        )
        self.assertIsNotNone(node_result)
        with self.assertRaises(canvas_store.RevisionConflictError):
            canvas_store.update_project_viewport(
                project_id,
                {"x": 3, "y": 4, "zoom": 0.75},
                episode_id="EP001",
                if_revision=stale_revision,
            )
        workspace = canvas_store.get_project_workspace_nodes(
            project_id,
            "EP001",
        )
        video = next(
            node for node in workspace["nodes"]
            if node["id"] == "video-1"
        )
        self.assertEqual(video["metadata"]["status"], "done")
        self.assertEqual(video["metadata"]["content"], "/uploads/final.mp4")

    def test_legacy_project_stats_are_backfilled_on_real_upgrade(self):
        context = tenant_context.make_context("legacy-stats", "legacy-stats")
        tenant_context.ensure_context_dirs(context)
        with tenant_context.use_context(context):
            path = canvas_store._db_path()
            connection = canvas_store.sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE canvas_projects("
                "id TEXT PRIMARY KEY,user_id TEXT,title TEXT,payload_json TEXT,"
                "revision INTEGER DEFAULT 1,created_at TEXT,updated_at TEXT)"
            )
            connection.execute(
                "INSERT INTO canvas_projects("
                "id,title,payload_json,revision,created_at,updated_at"
                ") VALUES(?,?,?,?,?,?)",
                (
                    "legacy-project",
                    "legacy",
                    json.dumps(self._payload()),
                    1,
                    "2026-10-30T00:00:00Z",
                    "2026-10-30T00:00:00Z",
                ),
            )
            connection.commit()
            connection.close()
            canvas_store._SCHEMA_READY = False
            canvas_store._SCHEMA_READY_PATHS.discard(path)
            connection = canvas_store._connect()
            row = connection.execute(
                "SELECT node_count,connection_count,episode_count,"
                "failed_count,local_blocked_count FROM canvas_projects "
                "WHERE id=?",
                ("legacy-project",),
            ).fetchone()
            connection.close()
        self.assertEqual(tuple(row), (3, 1, 2, 0, 1))

    def test_update_keeps_projection_in_sync(self):
        project_id = canvas_store.create_project("projection", self._payload())
        current = canvas_store.get_project(project_id)
        current["payload"]["nodes"][0]["metadata"]["prompt"] = "updated"
        canvas_store.update_project(project_id, payload=current["payload"])
        workspace = canvas_store.get_project_workspace_nodes(
            project_id,
            "EP001",
        )
        first = next(
            node for node in workspace["nodes"] if node["id"] == "image-1"
        )
        self.assertEqual(first["metadata"]["prompt"], "updated")

    def test_empty_project_projection_accepts_new_nodes(self):
        project_id = canvas_store.create_project("empty-projection", {
            "nodes": [],
            "connections": [],
            "episodeImports": [],
        })
        inserted = canvas_store.insert_project_nodes(
            project_id,
            [{
                "id": "new-node",
                "type": "image",
                "title": "新节点",
                "position": {"x": 0, "y": 0},
                "metadata": {"prompt": "new"},
            }],
            episode_id="EP001",
            if_revision=1,
        )
        self.assertEqual(inserted["created"], 1)
        project = canvas_store.get_project(project_id)
        self.assertEqual(
            [node["id"] for node in project["payload"]["nodes"]],
            ["new-node"],
        )
        self.assertEqual(
            project["payload"]["episodeImports"][0]["nodeIds"],
            ["new-node"],
        )

    def test_old_generation_tasks_schema_migrates_before_episode_index(self):
        context = tenant_context.make_context("legacy-schema", "legacy-schema")
        tenant_context.ensure_context_dirs(context)
        with tenant_context.use_context(context):
            path = canvas_store._db_path()
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            connection = canvas_store.sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE generation_tasks("
                "id TEXT PRIMARY KEY,project_id TEXT NOT NULL,"
                "node_id TEXT NOT NULL,status TEXT NOT NULL,"
                "created_at TEXT,node_type TEXT)"
            )
            connection.commit()
            connection.close()
            canvas_store._SCHEMA_READY = False
            canvas_store._SCHEMA_READY_PATHS.discard(path)
            migrated = canvas_store._connect()
            columns = {
                row[1]
                for row in migrated.execute(
                    "PRAGMA table_info(generation_tasks)"
                )
            }
            migrated.close()
        self.assertIn("episode_id", columns)


if __name__ == "__main__":
    unittest.main()
