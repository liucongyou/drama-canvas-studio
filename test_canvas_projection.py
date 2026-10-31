# -*- coding: utf-8 -*-
import concurrent.futures
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

    def test_notes_are_isolated_by_project_and_episode(self):
        first = canvas_store.create_project("notes-a", self._payload())
        second = canvas_store.create_project("notes-b", self._payload())
        canvas_store.replace_episode_notes(
            first,
            "EP001",
            [{"id": "note-1", "text": "EP001 only", "x": 1, "y": 2}],
        )
        canvas_store.replace_episode_notes(
            first,
            "EP002",
            [{"id": "note-2", "text": "EP002 only", "x": 3, "y": 4}],
        )
        canvas_store.replace_episode_notes(
            second,
            "EP001",
            [{"id": "note-3", "text": "other project", "x": 5, "y": 6}],
        )

        self.assertEqual(
            [note["id"] for note in canvas_store.get_episode_notes(first, "EP001")],
            ["note-1"],
        )
        self.assertEqual(
            [note["id"] for note in canvas_store.get_episode_notes(first, "EP002")],
            ["note-2"],
        )
        self.assertEqual(
            [note["id"] for note in canvas_store.get_episode_notes(second, "EP001")],
            ["note-3"],
        )
        self.assertEqual(
            [
                note["id"]
                for note in canvas_store.get_project_workspace_nodes(
                    first,
                    "EP002",
                )["notes"]
            ],
            ["note-2"],
        )

    def test_note_save_requires_episode_and_preserves_empty_collection(self):
        project_id = canvas_store.create_project("notes-empty", self._payload())
        canvas_store.replace_episode_notes(
            project_id,
            "EP001",
            [{"id": "note-1", "text": "remove me", "x": 1, "y": 2}],
        )
        with self.assertRaises(ValueError):
            canvas_store.replace_episode_notes(
                project_id,
                "",
                [],
            )
        canvas_store.replace_episode_notes(project_id, "EP001", [])
        self.assertEqual(canvas_store.get_episode_notes(project_id, "EP001"), [])
        self.assertEqual(canvas_store.get_episode_notes(project_id, "EP002"), [])

    def test_concurrent_note_writes_do_not_cross_episodes(self):
        payload = self._payload()
        episodes = [f"EP{index:03d}" for index in range(1, 13)]
        payload["episodeImports"] = [
            {
                "id": episode,
                "title": episode,
                "nodeIds": [],
                "videoNodeIds": [],
            }
            for episode in episodes
        ]
        project_id = canvas_store.create_project("notes-concurrent", payload)

        def write(index_and_episode):
            index, episode = index_and_episode
            with tenant_context.use_context(self.context):
                canvas_store.replace_episode_notes(
                    project_id,
                    episode,
                    [{
                        "id": f"note-{index}",
                        "text": episode,
                        "x": index,
                        "y": index,
                    }],
                )
                return canvas_store.get_episode_notes(project_id, episode)

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            result = list(pool.map(write, enumerate(episodes, start=1)))

        self.assertTrue(all(len(notes) == 1 for notes in result))
        self.assertEqual(
            [[note["text"] for note in notes] for notes in result],
            [[episode] for episode in episodes],
        )

    def test_episode_read_ignores_tampered_json_membership_snapshot(self):
        project_id = canvas_store.create_project(
            "projection-ownership-source",
            self._payload(),
        )
        def tamper_membership(con):
            con.execute(
                "UPDATE canvas_projection_episodes "
                "SET node_ids_json=?,video_node_ids_json=? "
                "WHERE project_id=? AND episode_id='EP001'",
                (
                    json.dumps(["image-2"]),
                    json.dumps([]),
                    project_id,
                ),
            )

        canvas_store._run_write_transaction(tamper_membership)

        workspace = canvas_store.get_project_workspace_nodes(
            project_id,
            "EP001",
        )
        project = canvas_store.get_project(project_id)
        ep001 = next(
            group for group in project["payload"]["episodeImports"]
            if group["id"] == "EP001"
        )

        self.assertEqual(
            [node["id"] for node in workspace["nodes"]],
            ["image-1", "video-1"],
        )
        self.assertEqual(ep001["nodeIds"], ["image-1", "video-1"])
        self.assertEqual(ep001["videoNodeIds"], ["video-1"])

    def test_episode_node_ids_have_no_payload_json_fallback(self):
        project_id = canvas_store.create_project(
            "projection-node-ids-source",
            {
                "title": "payload fallback isolation",
                "nodes": [],
                "connections": [],
                "episodeViewports": {},
                "episodeLayouts": {},
                "episodeImports": [{
                    "id": "EP009",
                    "nodeIds": ["legacy-ghost"],
                }],
            },
        )
        project = canvas_store.get_project(project_id)

        self.assertEqual(
            canvas_store.get_episode_node_ids(project_id, "EP009"),
            [],
        )
        self.assertNotIn(
            "legacy-ghost",
            next(
                group
                for group in project["payload"]["episodeImports"]
                if group["id"] == "EP009"
            )["nodeIds"],
        )

    def test_node_moved_to_other_episode_is_absent_from_original_episode(self):
        project_id = canvas_store.create_project(
            "projection-ownership-move",
            self._payload(),
        )
        node = canvas_store.get_projection_node(project_id, "image-1")
        node["metadata"]["episodeId"] = "EP002"
        canvas_store.update_project_nodes(
            project_id,
            [node],
            episode_id="EP002",
        )

        ep001 = canvas_store.get_project_workspace_nodes(project_id, "EP001")
        ep002 = canvas_store.get_project_workspace_nodes(project_id, "EP002")

        self.assertNotIn(
            "image-1",
            {item["id"] for item in ep001["nodes"]},
        )
        self.assertIn(
            "image-1",
            {item["id"] for item in ep002["nodes"]},
        )

    def test_atomic_node_edit_bumps_revision_and_owned_snapshot(self):
        project_id = canvas_store.create_project(
            "projection-atomic-edit",
            self._payload(),
        )
        before = canvas_store.get_project(project_id)

        saved = canvas_store.patch_project_node_atomic(
            project_id,
            "EP001",
            "image-1",
            {
                "metadata": {"prompt": "atomic"},
            },
            merge_metadata=lambda target, incoming: target.update(incoming),
        )
        after = canvas_store.get_project(project_id)
        group = next(
            item for item in after["payload"]["episodeImports"]
            if item["id"] == "EP001"
        )
        row = canvas_store._connect()
        try:
            owner = row.execute(
                "SELECT episode_id FROM canvas_projection_nodes "
                "WHERE project_id=? AND node_id=?",
                (project_id, "image-1"),
            ).fetchone()
        finally:
            row.close()

        self.assertEqual(saved["metadata"]["prompt"], "atomic")
        self.assertEqual(after["revision"], before["revision"] + 1)
        self.assertEqual(owner[0], "EP001")
        self.assertIn("image-1", group["nodeIds"])
        self.assertEqual(
            after["payload"]["nodes"][0]["metadata"]["prompt"],
            "atomic",
        )

    def test_atomic_node_edit_targets_project_and_episode(self):
        first_project = canvas_store.create_project(
            "projection-atomic-project-a",
            self._payload(),
        )
        second_project = canvas_store.create_project(
            "projection-atomic-project-b",
            self._payload(),
        )

        wrong_episode = canvas_store.patch_project_node_atomic(
            first_project,
            "EP002",
            "image-1",
            {"metadata": {"prompt": "wrong episode"}},
            merge_metadata=lambda target, incoming: target.update(incoming),
        )
        right_project = canvas_store.patch_project_node_atomic(
            second_project,
            "EP001",
            "image-1",
            {"metadata": {"prompt": "second project"}},
            merge_metadata=lambda target, incoming: target.update(incoming),
        )

        first = canvas_store.get_project_workspace_nodes(first_project, "EP001")
        second = canvas_store.get_project_workspace_nodes(second_project, "EP001")
        first_node = next(item for item in first["nodes"] if item["id"] == "image-1")
        second_node = next(item for item in second["nodes"] if item["id"] == "image-1")

        self.assertIsNone(wrong_episode)
        self.assertEqual(right_project["metadata"]["prompt"], "second project")
        self.assertEqual(first_node["metadata"]["prompt"], "角色")
        self.assertEqual(second_node["metadata"]["prompt"], "second project")

    def test_same_node_id_in_two_projects_stays_isolated(self):
        first_project = canvas_store.create_project(
            "project-a",
            {
                **self._payload(),
                "nodes": [{
                    "id": "shared-node-id",
                    "type": "image",
                    "metadata": {"episodeId": "EP001"},
                }],
                "connections": [],
                "episodeImports": [{
                    "id": "EP001",
                    "nodeIds": ["shared-node-id"],
                }],
                "episodeViewports": {},
                "episodeLayouts": {},
            },
        )
        second_project = canvas_store.create_project(
            "project-b",
            {
                "nodes": [{
                    "id": "shared-node-id",
                    "type": "video",
                    "metadata": {"episodeId": "EP001"},
                }],
                "connections": [],
                "episodeImports": [{
                    "id": "EP001",
                    "nodeIds": ["shared-node-id"],
                }],
            },
        )

        first = canvas_store.get_project_workspace_nodes(first_project, "EP001")
        second = canvas_store.get_project_workspace_nodes(second_project, "EP001")

        self.assertEqual(first["nodes"][0]["type"], "image")
        self.assertEqual(second["nodes"][0]["type"], "video")

    def test_node_database_rejects_empty_episode_id(self):
        project_id = canvas_store.create_project("projection-required-owner")
        with self.assertRaises(canvas_store.sqlite3.IntegrityError):
            canvas_store._run_write_transaction(
                lambda con: con.execute(
                    "INSERT INTO canvas_projection_nodes("
                    "project_id,node_id,episode_id,node_type,node_json"
                    ") VALUES(?,?,?,?,?)",
                    (
                        project_id,
                        "empty-owner",
                        "",
                        "image",
                        "{}",
                    ),
                )
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
            episode_id="EP001",
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

    def test_legacy_project_without_episode_metadata_reads_as_ep001(self):
        context = tenant_context.make_context(
            "legacy-episode-default",
            "legacy-episode-default",
        )
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
                "id,user_id,title,payload_json,revision,created_at,updated_at"
                ") VALUES(?,?,?,?,?,?,?)",
                (
                    "legacy-episode-project",
                    "legacy",
                    "旧单集项目",
                    json.dumps({
                        "nodes": [{
                            "id": "legacy-video",
                            "type": "video",
                            "title": "段1",
                            "metadata": {"prompt": "旧视频"},
                        }],
                        "connections": [],
                    }),
                    1,
                    "2026-10-30T00:00:00Z",
                    "2026-10-30T00:00:00Z",
                ),
            )
            connection.commit()
            connection.close()
            canvas_store._SCHEMA_READY = False
            canvas_store._SCHEMA_READY_PATHS.discard(path)
            loaded = canvas_store.get_project("legacy-episode-project")
        self.assertIsNotNone(loaded)
        episodes = loaded["payload"]["episodeImports"]
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["id"], "EP001")
        self.assertEqual(episodes[0]["nodeIds"], ["legacy-video"])
        self.assertEqual(episodes[0]["videoNodeIds"], ["legacy-video"])

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

    def test_legacy_project_notes_migrate_once_to_ep001_only(self):
        context = tenant_context.make_context("legacy-notes", "legacy-notes")
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
                "id,user_id,title,payload_json,revision,created_at,updated_at"
                ") VALUES(?,?,?,?,?,?,?)",
                (
                    "legacy-notes-project",
                    "legacy",
                    "旧备注",
                    json.dumps({
                        "title": "旧备注",
                        "nodes": [],
                        "connections": [],
                        "episodeImports": [
                            {"id": "EP001", "nodeIds": []},
                            {"id": "EP002", "nodeIds": []},
                        ],
                        "extras": {
                            "notes": [{
                                "id": "legacy-note",
                                "text": "旧项目备注",
                                "x": 10,
                                "y": 20,
                            }],
                        },
                    }),
                    1,
                    "2026-10-30T00:00:00Z",
                    "2026-10-30T00:00:00Z",
                ),
            )
            connection.commit()
            connection.close()
            canvas_store._SCHEMA_READY = False
            canvas_store._SCHEMA_READY_PATHS.discard(path)
            ep001 = canvas_store.get_episode_notes(
                "legacy-notes-project",
                "EP001",
            )
            ep002 = canvas_store.get_episode_notes(
                "legacy-notes-project",
                "EP002",
            )
            canvas_store.replace_episode_notes(
                "legacy-notes-project",
                "EP001",
                [],
            )
            canvas_store._SCHEMA_READY = False
            canvas_store._SCHEMA_READY_PATHS.discard(path)
            ep001_after_restart = canvas_store.get_episode_notes(
                "legacy-notes-project",
                "EP001",
            )

        self.assertEqual(
            [note["id"] for note in ep001],
            ["legacy-note"],
        )
        self.assertEqual(ep002, [])
        self.assertEqual(ep001_after_restart, [])


if __name__ == "__main__":
    unittest.main()
