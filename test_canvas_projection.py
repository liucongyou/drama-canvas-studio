# -*- coding: utf-8 -*-
import concurrent.futures
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import canvas_store
import import_feed
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

    def test_episode_read_returns_only_episode_nodes_without_refs(self):
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

    def test_episode_read_includes_shared_assets_from_video_contract(self):
        payload = self._payload()
        payload["nodes"][1]["metadata"]["params"] = {
            "refs": [{"nodeId": "image-2", "label": "角色｜乙"}],
            "voiceRefs": [],
        }
        project_id = canvas_store.create_project(
            "projection-shared-assets",
            payload,
        )

        workspace = canvas_store.get_project_workspace_nodes(
            project_id,
            "EP001",
        )
        nodes = {node["id"]: node for node in workspace["nodes"]}

        self.assertEqual(set(nodes), {"image-1", "video-1", "image-2"})
        self.assertTrue(nodes["image-2"]["metadata"]["sharedAcrossEpisodes"])
        self.assertEqual(nodes["image-2"]["metadata"]["episodeId"], "EP002")
        ep001 = canvas_store.get_project_workspace_nodes(project_id, "EP001")
        row = canvas_store._connect()
        try:
            owner = row.execute(
                "SELECT episode_id FROM canvas_projection_nodes "
                "WHERE project_id=? AND node_id='image-2'",
                (project_id,),
            ).fetchone()[0]
        finally:
            row.close()
        self.assertEqual(owner, "EP002")
        self.assertEqual(
            {node["id"] for node in ep001["nodes"]},
            {"image-1", "video-1", "image-2"},
        )

    def test_episode_read_includes_shared_assets_from_video_edges(self):
        payload = self._payload()
        payload["connections"].append({
            "id": "edge-shared-image",
            "fromNodeId": "image-2",
            "toNodeId": "video-1",
            "source": "user",
        })
        project_id = canvas_store.create_project(
            "projection-shared-edge-assets",
            payload,
        )

        workspace = canvas_store.get_project_workspace_nodes(
            project_id,
            "EP001",
        )

        self.assertEqual(
            {node["id"] for node in workspace["nodes"]},
            {"image-1", "video-1", "image-2"},
        )
        self.assertIn(
            "edge-shared-image",
            {edge["id"] for edge in workspace["connections"]},
        )

    def test_reimport_persists_shared_role_refs_through_projection(self):
        feed = {
            "title": "共享角色重导 · 第02集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [
                {
                    "code": "图1",
                    "name": "赵铁柱（角色立绘）",
                    "raw": "赵铁柱。",
                    "canvas": {"kind": "character", "name": "赵铁柱"},
                },
                {
                    "code": "图9",
                    "name": "郝秀兰（角色立绘）",
                    "raw": "郝秀兰。",
                    "canvas": {"kind": "character", "name": "郝秀兰"},
                },
            ],
            "segments": [{
                "no": 1,
                "name": "段1",
                "raw": (
                    "@角色｜赵铁柱（图1）和@角色｜郝秀兰（图9）同框。\n"
                    '台词：赵铁柱（低沉）："走。"'
                ),
                "shots": [],
            }],
        }
        first = import_feed.build_payload(feed)
        for node in first["nodes"]:
            if node["type"] in {"image", "audio"}:
                node["metadata"]["episodeId"] = "EP001"
            if node["type"] == "video":
                node["metadata"]["episodeId"] = "EP001"

        project_id = canvas_store.create_project("shared-role-reimport", first)
        incoming = import_feed.build_payload(feed)
        incoming["id"] = project_id
        incoming["episodeId"] = "EP002"
        for group in incoming["episodeImports"]:
            group["id"] = "EP002"
        for node in incoming["nodes"]:
            if node["type"] in {"image", "audio", "video", "text"}:
                node["metadata"]["episodeId"] = "EP002"

        existing = canvas_store.get_project(project_id)["payload"]
        merged = import_feed.merge_payload_by_episode(existing, incoming)
        canvas_store.update_project(project_id, payload=merged)

        stored = canvas_store.get_project(project_id)["payload"]
        episode = next(
            group for group in stored["episodeImports"]
            if group["id"] == "EP002"
        )
        video_id = episode["videoNodeIds"][0]
        video = next(node for node in stored["nodes"] if node["id"] == video_id)
        refs = video["metadata"]["params"]["refs"]
        labels = {ref["label"] for ref in refs}
        self.assertIn("角色｜赵铁柱（图1）", labels)
        self.assertIn("角色｜郝秀兰（图9）", labels)

        workspace = canvas_store.get_project_workspace_nodes(
            project_id,
            "EP002",
        )
        workspace_by_id = {node["id"]: node for node in workspace["nodes"]}
        workspace_video = workspace_by_id[video_id]
        workspace_labels = {
            ref["label"]
            for ref in workspace_video["metadata"]["params"]["refs"]
        }
        self.assertIn("角色｜赵铁柱（图1）", workspace_labels)
        self.assertIn("角色｜郝秀兰（图9）", workspace_labels)
        role_images = {
            node["title"]
            for node in workspace["nodes"]
            if node["type"] == "image"
        }
        self.assertIn("角色｜赵铁柱（图1）", role_images)
        self.assertIn("角色｜郝秀兰（图9）", role_images)

    def test_episode_read_removes_references_to_deleted_nodes(self):
        payload = self._payload()
        payload["nodes"][1]["metadata"]["params"] = {
            "refs": [
                {"nodeId": "image-1", "label": "角色｜甲"},
                {"nodeId": "deleted-asset", "label": "资产图"},
            ],
            "voiceRefs": [],
        }
        project_id = canvas_store.create_project(
            "projection-dangling-refs",
            payload,
        )

        workspace = canvas_store.get_project_workspace_nodes(
            project_id,
            "EP001",
        )
        video = next(
            node for node in workspace["nodes"]
            if node["id"] == "video-1"
        )
        refs = video["metadata"]["params"]["refs"]

        self.assertEqual(
            [ref["nodeId"] for ref in refs],
            ["image-1"],
        )
        self.assertEqual(
            workspace["referenceCleanup"]["removed"],
            1,
        )
        stored = canvas_store.get_project(project_id)
        stored_video = next(
            node for node in stored["payload"]["nodes"]
            if node["id"] == "video-1"
        )
        self.assertEqual(
            len(stored_video["metadata"]["params"]["refs"]),
            2,
        )

    def test_episode_read_strips_legacy_at_inside_mapping_brackets(self):
        payload = self._payload()
        payload["nodes"][1]["metadata"]["prompt"] = (
            "<@场景｜厂区后街·母版（图10）> "
            "对应@场景｜厂区后街·母版（图10），仅采用空间布局。"
        )
        project_id = canvas_store.create_project(
            "projection-bracket-cleanup",
            payload,
        )

        workspace = canvas_store.get_project_workspace_nodes(
            project_id,
            "EP001",
        )
        video = next(
            node for node in workspace["nodes"]
            if node["id"] == "video-1"
        )
        prompt = video["metadata"]["prompt"]

        self.assertIn("<场景｜厂区后街·母版（图10）>", prompt)
        self.assertNotIn("<@", prompt)

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
        payload = self._payload()
        payload["connections"] = []
        project_id = canvas_store.create_project(
            "projection-ownership-move",
            payload,
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

    def test_atomic_node_edit_targets_project_and_preserves_owner(self):
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

        self.assertEqual(wrong_episode["episodeId"], "EP001")
        self.assertEqual(wrong_episode["metadata"]["episodeId"], "EP001")
        self.assertEqual(right_project["metadata"]["prompt"], "second project")
        self.assertEqual(first_node["metadata"]["prompt"], "wrong episode")
        self.assertEqual(first_node["metadata"]["episodeId"], "EP001")
        self.assertEqual(second_node["metadata"]["prompt"], "second project")

    def test_atomic_shared_asset_edit_from_referencing_episode_preserves_owner(self):
        project_id = canvas_store.create_project(
            "projection-shared-atomic-edit",
            self._payload(),
        )

        saved = canvas_store.patch_project_node_atomic(
            project_id,
            "EP001",
            "image-2",
            {"metadata": {"prompt": "shared update"}},
            merge_metadata=lambda target, incoming: target.update(incoming),
        )

        self.assertEqual(saved["metadata"]["prompt"], "shared update")
        self.assertEqual(saved["episodeId"], "EP002")
        self.assertEqual(saved["metadata"]["episodeId"], "EP002")
        workspace = canvas_store.get_project_workspace_nodes(
            project_id,
            "EP002",
        )
        node = next(
            item for item in workspace["nodes"]
            if item["id"] == "image-2"
        )
        self.assertEqual(node["metadata"]["prompt"], "shared update")
        self.assertEqual(node["metadata"]["episodeId"], "EP002")

    def test_atomic_shared_asset_edit_still_rejects_stale_non_asset_fields(self):
        project_id = canvas_store.create_project(
            "projection-shared-atomic-conflict",
            self._payload(),
        )
        stale = canvas_store.get_projection_node(project_id, "image-2")
        stale_updated_at = "2026-01-01T00:00:00+00:00"

        def mark_stale(con):
            con.execute(
                "UPDATE canvas_projection_nodes SET updated_at=?,node_json=? "
                "WHERE project_id=? AND node_id=?",
                (
                    stale_updated_at,
                    json.dumps({
                        **stale,
                        "updatedAt": stale_updated_at,
                    }, ensure_ascii=False),
                    project_id,
                    "image-2",
                ),
            )

        canvas_store._run_write_transaction(mark_stale)
        stale["updatedAt"] = stale_updated_at
        canvas_store.patch_project_node_atomic(
            project_id,
            "EP001",
            "image-2",
            {"metadata": {"prompt": "latest", "status": "done"}},
            merge_metadata=lambda target, incoming: target.update(incoming),
        )

        with self.assertRaises(canvas_store.RevisionConflictError):
            canvas_store.patch_project_node_atomic(
                project_id,
                "EP001",
                "image-2",
                {
                    "_baseUpdatedAt": stale_updated_at,
                    "metadata": {"prompt": "stale", "status": "failed"},
                },
                merge_metadata=lambda target, incoming: target.update(incoming),
            )

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

    def test_viewport_write_does_not_rewrite_legacy_payload_or_other_episodes(self):
        project_id = canvas_store.create_project("projection", self._payload())
        before = canvas_store.get_project(project_id)
        connection = canvas_store._connect()
        try:
            payload_before = connection.execute(
                "SELECT payload_json FROM canvas_projects WHERE id=?",
                (project_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        saved = canvas_store.update_project_viewport(
            project_id,
            {"x": 77, "y": 88, "zoom": 0.42},
            episode_id="EP002",
            if_revision=before["revision"],
        )
        self.assertIsNotNone(saved)
        connection = canvas_store._connect()
        try:
            payload_after = connection.execute(
                "SELECT payload_json FROM canvas_projects WHERE id=?",
                (project_id,),
            ).fetchone()[0]
            ep1 = connection.execute(
                "SELECT viewport_json FROM canvas_projection_episodes "
                "WHERE project_id=? AND episode_id='EP001'",
                (project_id,),
            ).fetchone()[0]
            ep2 = connection.execute(
                "SELECT viewport_json FROM canvas_projection_episodes "
                "WHERE project_id=? AND episode_id='EP002'",
                (project_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(payload_after, payload_before)
        self.assertEqual(json.loads(ep1)["zoom"], 0.7)
        self.assertEqual(json.loads(ep2), {"x": 77, "y": 88, "zoom": 0.42})
        workspace = canvas_store.get_project_workspace_index(project_id)
        self.assertEqual(workspace["episodeViewports"]["EP002"]["zoom"], 0.42)

    def test_unchanged_viewport_does_not_create_database_write_or_revision(self):
        project_id = canvas_store.create_project("projection", self._payload())
        before = canvas_store.get_project(project_id)
        first = canvas_store.update_project_viewport(
            project_id,
            {"x": 77, "y": 88, "zoom": 0.42},
            episode_id="EP002",
            if_revision=before["revision"],
        )
        self.assertFalse(first["unchanged"])
        revision_after_first = first["revision"]
        second = canvas_store.update_project_viewport(
            project_id,
            {"x": 77.1, "y": 88.1, "zoom": 0.42001},
            episode_id="EP002",
            if_revision=revision_after_first,
        )
        self.assertTrue(second["unchanged"])
        self.assertEqual(second["revision"], revision_after_first)
        current = canvas_store.get_project(project_id)
        self.assertEqual(current["revision"], revision_after_first)

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

    def test_projection_backfills_legacy_asset_identity_without_rotation(self):
        payload = self._payload()
        payload["nodes"][0]["metadata"].pop("assetId", None)
        payload["nodes"][0]["metadata"].pop("seriesId", None)
        payload["nodes"][0]["metadata"].pop("variantKey", None)
        project_id = canvas_store.create_project(
            "projection-asset-identity-backfill",
            payload,
        )
        row = canvas_store._connect()
        try:
            metadata = json.loads(row.execute(
                "SELECT metadata_json FROM canvas_projection_nodes "
                "WHERE project_id=? AND node_id='image-1'",
                (project_id,),
            ).fetchone()[0])
            metadata.pop("assetId", None)
            metadata.pop("seriesId", None)
            metadata.pop("variantKey", None)
            node_json = json.loads(row.execute(
                "SELECT node_json FROM canvas_projection_nodes "
                "WHERE project_id=? AND node_id='image-1'",
                (project_id,),
            ).fetchone()[0])
            node_json["metadata"] = metadata
            row.execute(
                "UPDATE canvas_projection_nodes SET metadata_json=?,node_json=? "
                "WHERE project_id=? AND node_id='image-1'",
                (
                    json.dumps(metadata, ensure_ascii=False),
                    json.dumps(node_json, ensure_ascii=False),
                    project_id,
                ),
            )
            row.commit()
        finally:
            row.close()

        result = canvas_store.backfill_asset_identities()
        self.assertEqual(result["nodes"], 1)
        stored = canvas_store.get_projection_node(project_id, "image-1")
        first_identity = stored["metadata"]["assetId"]
        self.assertEqual(first_identity, "character:甲:default")
        self.assertEqual(
            stored["metadata"]["seriesId"],
            "character:甲",
        )

        self.assertEqual(
            canvas_store.backfill_asset_identities()["nodes"],
            0,
        )
        stored = canvas_store.get_projection_node(project_id, "image-1")
        self.assertEqual(stored["metadata"]["assetId"], first_identity)

    def test_projection_backfills_reference_asset_identity(self):
        payload = self._payload()
        payload["nodes"][1]["metadata"]["params"] = {
            "refs": [{
                "nodeId": "image-1",
                "label": "角色｜甲",
            }],
            "voiceRefs": [],
        }
        project_id = canvas_store.create_project(
            "projection-reference-identity-backfill",
            payload,
        )
        row = canvas_store._connect()
        try:
            video = json.loads(row.execute(
                "SELECT node_json FROM canvas_projection_nodes "
                "WHERE project_id=? AND node_id='video-1'",
                (project_id,),
            ).fetchone()[0])
            video["metadata"]["params"]["refs"] = [{
                "nodeId": "image-1",
                "label": "角色｜甲",
            }]
            video["metadata"]["refs"] = video["metadata"]["params"]["refs"]
            row.execute(
                "UPDATE canvas_projection_nodes SET metadata_json=?,node_json=? "
                "WHERE project_id=? AND node_id='video-1'",
                (
                    json.dumps(video["metadata"], ensure_ascii=False),
                    json.dumps(video, ensure_ascii=False),
                    project_id,
                ),
            )
            row.commit()
        finally:
            row.close()

        canvas_store.backfill_asset_identities()
        stored = canvas_store.get_projection_node(project_id, "video-1")
        ref = stored["metadata"]["params"]["refs"][0]

        self.assertEqual(ref["assetId"], "character:甲:default")
        self.assertEqual(ref["seriesId"], "character:甲")
        self.assertEqual(ref["variantKey"], "default")

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
        self.assertEqual(
            project["payload"]["nodes"][0]["metadata"]["assetId"],
            "image:新节点:default",
        )
        self.assertEqual(
            project["payload"]["nodes"][0]["metadata"]["seriesId"],
            "image:新节点",
        )

    def test_new_project_workspace_index_is_empty_and_non_breaking(self):
        project_id = canvas_store.create_project("new-empty-project")
        workspace = canvas_store.get_project_workspace_index(project_id)
        self.assertIsNotNone(workspace)
        self.assertEqual(len(workspace["episodes"]), 1)
        self.assertEqual(workspace["episodes"][0]["id"], "EP001")
        self.assertEqual(workspace["episodes"][0]["nodeIds"], [])
        self.assertEqual(workspace["episodes"][0]["videoNodeIds"], [])
        self.assertEqual(workspace["episodes"][0]["shot_count"], 0)

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
            indexes = {
                row[1]
                for row in migrated.execute(
                    "PRAGMA index_list(generation_tasks)"
                )
            }
            migrated.close()
        self.assertIn("episode_id", columns)
        self.assertIn("provider_task_id", columns)
        self.assertIn("lease_expires_at", columns)
        self.assertIn("next_poll_at", columns)
        self.assertIn("idx_generation_tasks_active_lease", indexes)
        self.assertIn("idx_generation_tasks_next_poll", indexes)

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
