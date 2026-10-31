# -*- coding: utf-8 -*-
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import canvas_repository
import canvas_store
import tenant_context


class CanvasRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            "CANVAS_MULTI_TENANT": "1",
            "CANVAS_TENANT_DATA_ROOT": str(self.root),
        })
        self.env.start()
        self.context = tenant_context.make_context("repository", "repository")
        tenant_context.ensure_context_dirs(self.context)
        self.context_scope = tenant_context.use_context(self.context)
        self.context_scope.__enter__()
        self.repo = canvas_repository.CanvasRepository()

    def tearDown(self):
        self.context_scope.__exit__(None, None, None)
        self.env.stop()
        self.temp.cleanup()

    def _create(self, title="Repository", payload=None):
        with tenant_context.use_context(self.context):
            project_id = self.repo.create_project(title, payload)
            return project_id, self.repo.get_project(project_id)

    def test_round_trips_existing_payload_without_structural_rewrite(self):
        original = {
            "id": "legacy-shape",
            "nodes": [{
                "id": "node-1",
                "type": "text",
                "title": "旧节点",
                "position": {"x": 12, "y": 34},
                "metadata": {"prompt": "保持原样"},
            }],
            "connections": [{"id": "edge-1", "fromNodeId": "a", "toNodeId": "b"}],
            "customLegacyField": {"keep": True},
        }
        project_id, created = self._create(payload=original)
        self.assertEqual(created["id"], project_id)
        self.assertEqual(created["payload"]["customLegacyField"], {"keep": True})
        self.assertEqual(created["payload"]["connections"][0]["id"], "edge-1")
        self.assertEqual(created["payload"]["revision"], 1)

    def test_revision_precondition_and_node_edge_layout_updates(self):
        project_id, created = self._create()
        revision = created["revision"]
        self.repo.upsert_nodes(
            project_id,
            [{
                "id": "node-a",
                "type": "image",
                "title": "角色",
                "position": {"x": 10, "y": 20},
                "metadata": {"prompt": "v1"},
            }],
            if_revision=revision,
        )
        current = self.repo.get_project(project_id)
        self.repo.patch_node(
            project_id,
            "node-a",
            {"metadata": {"prompt": "v2"}, "position": {"x": 30, "y": 40}},
            if_revision=current["revision"],
        )
        current = self.repo.get_project(project_id)
        self.repo.patch_edges(
            project_id,
            [{"id": "edge-a", "fromNodeId": "node-a", "toNodeId": "node-b"}],
            if_revision=current["revision"],
        )
        current = self.repo.get_project(project_id)
        viewport = self.repo.save_viewport(
            project_id,
            {"x": -100, "y": 50, "zoom": 0.8},
            episode_id="ep01",
            if_revision=current["revision"],
        )
        self.assertEqual(viewport["zoom"], 0.8)
        current = self.repo.get_project(project_id)
        self.assertEqual(
            current["payload"]["episodeViewports"]["EP01"],
            viewport,
        )
        self.assertEqual(
            current["payload"]["nodes"][0]["metadata"]["prompt"],
            "v2",
        )
        current = self.repo.get_project(project_id)
        stale_revision = current["revision"]
        stale_payload = copy.deepcopy(current["payload"])
        viewport["zoom"] = 0.7
        stale_payload.setdefault("episodeViewports", {})["EP01"] = viewport
        canvas_store.update_project(
            project_id,
            payload=stale_payload,
        )
        # The repository lock is not shared with a generation worker. The
        # revision CAS must reject the stale read even when the final payload
        # already contains the requested viewport change.
        concurrent = copy.deepcopy(stale_payload)
        concurrent["nodes"][0]["metadata"]["content"] = "/uploads/new-video.mp4"
        canvas_store.update_project(project_id, payload=concurrent)
        with self.assertRaises(canvas_store.RevisionConflictError):
            self.repo.save_viewport(
                project_id,
                {"x": 10, "y": 20, "zoom": 0.7},
                episode_id="EP01",
                if_revision=stale_revision,
            )
        with self.assertRaises(canvas_store.RevisionConflictError):
            self.repo.patch_project_metadata(
                project_id,
                title="过期写入",
                if_revision=revision,
            )

    def test_auto_snapshots_are_throttled_but_manual_versions_are_kept(self):
        project_id, created = self._create()
        with tenant_context.use_context(self.context):
            for index in range(5):
                payload = self.repo.get_project(project_id)["payload"]
                payload["counter"] = index
                self.repo.commit_payload(project_id, payload)
            versions = self.repo.list_versions(project_id)
            auto_before = [item for item in versions if item["kind"] == "auto"]
            self.assertEqual(len(auto_before), 1)
            manual = self.repo.create_version(project_id, "发布前")
            self.assertEqual(manual["kind"], "manual")
            versions = self.repo.list_versions(project_id)
            self.assertEqual(
                len([item for item in versions if item["kind"] == "manual"]),
                1,
            )

    def test_restore_recovers_content_and_creates_pre_restore_protection(self):
        project_id, created = self._create()
        label = "恢复目标"
        current = created
        self.repo.upsert_nodes(
            project_id,
            [{
                "id": "node-a",
                "type": "video",
                "title": "镜头",
                "position": {"x": 0, "y": 0},
                "metadata": {"prompt": "A", "content": "/uploads/a.mp4"},
            }],
            if_revision=current["revision"],
        )
        target = self.repo.create_version(project_id, label)
        current = self.repo.get_project(project_id)
        self.repo.patch_node(
            project_id,
            "node-a",
            {"metadata": {"prompt": "C", "content": "/uploads/c.mp4"}},
            if_revision=current["revision"],
        )
        before_restore = self.repo.get_project(project_id)
        restored = self.repo.restore_version(
            project_id,
            target["id"],
            if_revision=before_restore["revision"],
        )
        after = self.repo.get_project(project_id)
        self.assertEqual(restored["restoredFrom"], target["id"])
        self.assertEqual(after["payload"]["nodes"][0]["metadata"]["prompt"], "A")
        self.assertEqual(
            after["payload"]["nodes"][0]["metadata"]["content"],
            "/uploads/a.mp4",
        )
        versions = self.repo.list_versions(project_id)
        self.assertTrue(any(
            item["title"] == "恢复前自动保护" and item["kind"] == "manual"
            for item in versions
        ))
        with self.assertRaises(canvas_store.RevisionConflictError):
            self.repo.restore_version(
                project_id,
                target["id"],
                if_revision=before_restore["revision"],
            )


if __name__ == "__main__":
    unittest.main()
