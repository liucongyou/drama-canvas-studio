# -*- coding: utf-8 -*-
import os
import io
import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import canvas_store
import canvas_feed_contract
import import_feed
import server
import tenant_context


class AssetRenameContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            "CANVAS_MULTI_TENANT": "1",
            "CANVAS_TENANT_DATA_ROOT": str(self.root),
            "CANVAS_DB_BACKEND": "sqlite",
            "CANVAS_ALLOW_SQLITE": "1",
        })
        self.env.start()
        self.context = tenant_context.make_context("rename", "rename")
        tenant_context.ensure_context_dirs(self.context)
        self.scope = tenant_context.use_context(self.context)
        self.scope.__enter__()

    def tearDown(self):
        self.scope.__exit__(None, None, None)
        self.env.stop()
        self.temp.cleanup()

    def _project(self):
        return {
            "nodes": [
                {
                    "id": "audio-old",
                    "type": "audio",
                    "title": "音频｜贾富贵（图3）",
                    "position": {"x": 10, "y": 600},
                    "metadata": {
                        "episodeId": "EP001",
                        "assetId": "voice:贾富贵:default",
                        "seriesId": "voice:贾富贵",
                        "variantKey": "default",
                        "content": "/uploads/voice.mp3",
                        "status": "done",
                    },
                },
                {
                    "id": "asset-old",
                    "type": "image",
                    "title": "角色｜贾富贵（图3）",
                    "position": {"x": 10, "y": 20},
                    "metadata": {
                        "episodeId": "EP001",
                        "assetId": "character:贾富贵:default",
                        "seriesId": "character:贾富贵",
                        "variantKey": "default",
                        "content": "/uploads/old.png",
                        "status": "done",
                    },
                },
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "EP001 · 段1 · 视频",
                    "position": {"x": 300, "y": 20},
                    "metadata": {
                        "episodeId": "EP001",
                        "prompt": (
                            "【核心人物】@角色｜贾富贵（图3）"
                            "\n参考@角色｜贾富贵（图3）走入。"
                            "\n<贾富贵> 对应@角色｜贾富贵（图3），仅采用人物外貌。"
                            "\n<贾富贵>的音色对应@音频｜贾富贵（图3）。"
                            "\n短引用@贾富贵（图3），裸引用@贾富贵。"
                            "\n台词：贾富贵说“不引用@贾富贵也不改”。"
                            "\n旁白：贾富贵没有图号，不能改。"
                        ),
                        "refs": [{
                            "nodeId": "asset-old",
                            "assetId": "character:贾富贵:default",
                            "label": "贾富贵",
                        }],
                    },
                },
                {
                    "id": "video-2",
                    "type": "video",
                    "title": "EP002 · 段1 · 视频",
                    "position": {"x": 300, "y": 400},
                    "metadata": {
                        "episodeId": "EP002",
                        "prompt": "台词：贾富贵说“走”。",
                        "refs": [],
                    },
                },
            ],
            "connections": [
                {"id": "edge-1", "fromNodeId": "asset-old", "toNodeId": "video-1"},
            ],
            "episodeImports": [
                {
                    "id": "EP001",
                    "nodeIds": ["asset-old", "audio-old", "video-1"],
                    "videoNodeIds": ["video-1"],
                },
                {"id": "EP002", "nodeIds": ["video-2"], "videoNodeIds": ["video-2"]},
            ],
        }

    def test_rename_migrates_typed_mentions_and_reference_labels(self):
        project_id = canvas_store.create_project("rename", self._project())
        result = canvas_store.rename_project_asset_atomic(
            project_id,
            "asset-old",
            "贾富贵新名",
            expected_asset_id="character:贾富贵:default",
        )
        self.assertFalse(result.get("error"), result)
        self.assertEqual(result["oldName"], "贾富贵")
        self.assertEqual(result["newName"], "贾富贵新名")
        self.assertEqual(result["figure"], "图3")
        self.assertGreaterEqual(result["updatedNodes"], 2)
        self.assertGreaterEqual(result["updatedReferences"], 7)

        saved = canvas_store.get_project(project_id)
        nodes = {node["id"]: node for node in saved["payload"]["nodes"]}
        self.assertEqual(nodes["asset-old"]["title"], "角色｜贾富贵新名（图3）")
        self.assertEqual(nodes["audio-old"]["title"], "音频｜贾富贵新名（图3）")
        self.assertEqual(
            nodes["audio-old"]["metadata"]["assetId"],
            "voice:贾富贵:default",
        )
        self.assertEqual(
            nodes["audio-old"]["metadata"]["seriesId"],
            "voice:贾富贵",
        )
        self.assertEqual(
            nodes["asset-old"]["metadata"]["assetId"],
            "character:贾富贵:default",
        )
        prompt = nodes["video-1"]["metadata"]["prompt"]
        self.assertNotIn("@角色｜贾富贵（图3）", prompt)
        self.assertNotIn("@音频｜贾富贵（图3）", prompt)
        self.assertIn("@音频｜贾富贵新名（图3）", prompt)
        self.assertNotIn("@贾富贵（图3）", prompt)
        self.assertNotIn("裸引用@贾富贵", prompt)
        self.assertIn(
            "<贾富贵新名> 对应@角色｜贾富贵新名（图3）",
            prompt,
        )
        self.assertIn(
            "<贾富贵新名>的音色对应@音频｜贾富贵新名（图3）",
            prompt,
        )
        self.assertEqual(
            prompt.count("@角色｜贾富贵新名（图3）"),
            5,
        )
        self.assertIn('台词：贾富贵说“不引用@贾富贵也不改”。', prompt)
        self.assertIn("旁白：贾富贵没有图号，不能改。", prompt)
        self.assertEqual(
            nodes["video-1"]["metadata"]["refs"][0]["label"],
            "贾富贵新名",
        )
        self.assertEqual(
            nodes["video-2"]["metadata"]["prompt"],
            "台词：贾富贵说“走”。",
        )

    def test_rename_rejects_identity_mismatch_and_same_figure_conflict(self):
        payload = self._project()
        payload["nodes"].append({
            "id": "asset-conflict",
            "type": "image",
            "title": "角色｜另一张（图3）",
            "position": {"x": 0, "y": 0},
            "metadata": {
                "episodeId": "EP001",
                "assetId": "character:另一张:default",
                "seriesId": "character:另一张",
                "variantKey": "default",
            },
        })
        project_id = canvas_store.create_project("rename-conflict", payload)
        identity_error = canvas_store.rename_project_asset_atomic(
            project_id,
            "asset-old",
            "新名",
            expected_asset_id="character:wrong:default",
        )
        self.assertEqual(identity_error["error"], "asset_identity_conflict")
        name_error = canvas_store.rename_project_asset_atomic(
            project_id,
            "asset-old",
            "另一张",
            expected_asset_id="character:贾富贵:default",
        )
        self.assertEqual(name_error["error"], "asset_name_conflict")

    def test_rename_rejects_placeholder_names(self):
        project_id = canvas_store.create_project("rename-placeholder", self._project())
        for name in ("未命名", "未命名节点", "未命名资产"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "占位名称"):
                    canvas_store.rename_project_asset_atomic(
                        project_id,
                        "asset-old",
                        name,
                        expected_asset_id="character:贾富贵:default",
                    )

    def test_role_rename_syncs_old_audio_card_by_series_identity(self):
        payload = self._project()
        audio = next(
            node for node in payload["nodes"]
            if node["id"] == "audio-old"
        )
        image = next(
            node for node in payload["nodes"]
            if node["id"] == "asset-old"
        )
        # This reproduces a real migrated project: the image was renamed but
        # the existing voice card still carried its original title and no
        # per-node alias list.
        image["metadata"].pop("seriesId", None)
        audio["metadata"].pop("displayName", None)
        audio["metadata"].pop("seriesId", None)
        audio["metadata"]["assetKey"] = "角色:贾富贵"
        project_id = canvas_store.create_project("rename-old-audio", payload)
        result = canvas_store.rename_project_asset_atomic(
            project_id,
            "asset-old",
            "贾富贵新名",
            expected_asset_id="character:贾富贵:default",
        )
        self.assertFalse(result.get("error"), result)
        saved = canvas_store.get_project(project_id)
        node = next(
            item for item in saved["payload"]["nodes"]
            if item["id"] == "audio-old"
        )
        self.assertEqual(node["title"], "音频｜贾富贵新名（图3）")
        self.assertEqual(node["metadata"]["displayName"], "贾富贵新名")
        self.assertEqual(node["metadata"]["assetId"], "voice:贾富贵:default")
        self.assertTrue(
            node["metadata"]["seriesId"].startswith("voice:")
            or node["metadata"]["seriesId"] == ""
        )

    def test_asset_identity_kind_never_borrows_other_media_type(self):
        payload = self._project()
        payload["nodes"].append({
            "id": "legacy-image",
            "type": "image",
            "title": "角色｜贾富贵（图3）",
            "metadata": {
                "episodeId": "EP001",
                "assetId": "voice:贾富贵:default",
                "seriesId": "voice:贾富贵",
                "variantKey": "default",
            },
        })
        canvas_store._ensure_asset_identities(payload)
        nodes = {node["id"]: node for node in payload["nodes"]}
        self.assertEqual(
            nodes["legacy-image"]["metadata"]["assetId"],
            "character:贾富贵:default",
        )
        self.assertEqual(
            nodes["legacy-image"]["metadata"]["seriesId"],
            "character:贾富贵",
        )

    def test_sqlite_temp_store_is_tenant_private(self):
        project_id = canvas_store.create_project("rename-temp", self._project())
        self.assertTrue(project_id)
        connection = canvas_store._connect()
        try:
            temp_dir = Path(connection.execute(
                "PRAGMA temp_store_directory"
            ).fetchone()[0]).resolve()
        finally:
            connection.close()
        self.assertEqual(
            temp_dir,
            (self.context.data_root / "tmp").resolve(),
        )

    def test_rename_api_migrates_references_and_emits_one_audit(self):
        project_id = canvas_store.create_project("rename-api", self._project())
        project = canvas_store.get_project(project_id)
        handler = object.__new__(server.Handler)
        handler.path = f"/api/projects/{project_id}/assets/asset-old/rename"
        handler.command = "PUT"
        handler.headers = {}
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        handler.wfile = mock.Mock()
        handler._cors_headers = mock.Mock(return_value={})
        body = json.dumps({
            "name": "贾富贵（新）",
            "assetId": "character:贾富贵:default",
            "revision": project["revision"],
        }).encode("utf-8")
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler._handle_tenant_preferences_update = mock.Mock()
        with mock.patch.object(server, "project_write_lock", return_value=nullcontext()):
            handler._do_put_locked()
        payload = json.loads(handler.wfile.write.call_args.args[0])
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["updatedNodes"], 3)
        self.assertGreaterEqual(payload["updatedReferences"], 7)
        saved = canvas_store.get_project(project_id)
        nodes = {node["id"]: node for node in saved["payload"]["nodes"]}
        self.assertEqual(nodes["asset-old"]["title"], "角色｜贾富贵（新）（图3）")
        self.assertEqual(
            nodes["video-1"]["metadata"]["refs"][0]["label"],
            "贾富贵（新）",
        )
        self.assertIn(
            "@角色｜贾富贵（新）（图3）",
            nodes["video-1"]["metadata"]["prompt"],
        )
        audit = canvas_store.list_audit_events(
            limit=20,
            action="asset.rename",
        )
        events = audit["events"]
        self.assertEqual(len(events), 1)

    def test_rename_survives_reimport_of_old_display_name(self):
        project_id = canvas_store.create_project("rename-reimport", self._project())
        result = canvas_store.rename_project_asset_atomic(
            project_id,
            "asset-old",
            "贾富贵新名",
            expected_asset_id="character:贾富贵:default",
        )
        self.assertFalse(result.get("error"), result)
        renamed = canvas_store.get_project(project_id)
        payload = renamed["payload"]
        asset_node = next(
            node for node in payload["nodes"]
            if node["id"] == "asset-old"
        )
        aliases = asset_node["metadata"].get("aliases") or []
        self.assertEqual(aliases[-1]["newName"], "贾富贵新名")
        self.assertEqual(
            import_feed._asset_alias_names(asset_node["metadata"]),
            ["贾富贵新名", "贾富贵"],
        )
        self.assertEqual(
            asset_node["metadata"]["assetId"],
            "character:贾富贵:default",
        )

    def test_rename_twice_preserves_alias_history(self):
        project_id = canvas_store.create_project("rename-twice", self._project())
        first = canvas_store.rename_project_asset_atomic(
            project_id,
            "asset-old",
            "贾富贵新名",
            expected_asset_id="character:贾富贵:default",
        )
        self.assertFalse(first.get("error"), first)
        second = canvas_store.rename_project_asset_atomic(
            project_id,
            "asset-old",
            "贾富贵终名",
            expected_asset_id="character:贾富贵:default",
        )
        self.assertFalse(second.get("error"), second)
        saved = canvas_store.get_project(project_id)
        nodes = {node["id"]: node for node in saved["payload"]["nodes"]}
        aliases = nodes["asset-old"]["metadata"].get("aliases") or []
        self.assertEqual(
            [entry.get("newName") for entry in aliases],
            ["贾富贵终名"],
        )
        self.assertGreaterEqual(
            len(saved["payload"].get("assetAliases") or []),
            2,
        )
        self.assertIn("@角色｜贾富贵终名（图3）", nodes["video-1"]["metadata"]["prompt"])
        self.assertNotIn("@角色｜贾富贵新名（图3）", nodes["video-1"]["metadata"]["prompt"])

    def test_reimport_old_feed_keeps_renamed_asset_identity(self):
        project_id = canvas_store.create_project("rename-import", self._project())
        result = canvas_store.rename_project_asset_atomic(
            project_id,
            "asset-old",
            "贾富贵新名",
            expected_asset_id="character:贾富贵:default",
        )
        self.assertFalse(result.get("error"), result)
        current = canvas_store.get_project(project_id)
        feed = {
            "title": "改名后重导",
            "canvas_contract": {
                "schema": "drama-canvas-feed/v2",
                "generator": {
                    "name": "test",
                    "skillVersion": "test",
                    "packageRevision": "test",
                },
            },
            "assets": [{
                "code": "CHR-003",
                "semanticCode": "CHR-003",
                "name": "贾富贵",
                "img": "图3",
                "view": "角色",
            }],
            "asset_prompts": [{
                "code": "图3",
                "semanticCode": "CHR-003",
                "img": "图3",
                "name": "贾富贵（角色立绘）",
                "raw": (
                    "一名 45 岁男性。\ncharacter sheet\n"
                    "**Voice Description 完整版**： "
                    "A 45-year-old low, nervous male voice.\n"
                    "**Voice Description Label**： low nervous male"
                ),
            }],
            "voice_prompts": [],
            "segments": [{
                "no": 1,
                "name": "段1",
                "raw": (
                    "【画幅风格】16:9\n"
                    "【核心人物】@角色｜贾富贵（图3）\n"
                    "【站位声明】@角色｜贾富贵（图3）（画面左）\n"
                    "台词：无"
                ),
                "shots": [],
            }],
        }
        plan = canvas_feed_contract.prepare_import(
            current,
            feed,
            project_id,
            current["revision"],
        )
        self.assertTrue(
            plan.ok,
            [canvas_feed_contract.issue_dict(item) for item in plan.errors],
        )
        matched = [
            node for node in plan.merged_payload["nodes"]
            if node.get("type") == "image"
            and (node.get("metadata") or {}).get("assetId")
            == "character:贾富贵:default"
            and (node.get("metadata") or {}).get("displayName")
            == "贾富贵新名"
        ]
        self.assertGreaterEqual(len(matched), 1)
        self.assertEqual(
            matched[0]["metadata"]["displayName"],
            "贾富贵新名",
        )
        image_matches = [
            node for node in plan.merged_payload["nodes"]
            if node.get("type") == "image"
            and "图3" in str(node.get("title") or "")
        ]
        self.assertTrue(image_matches)
        self.assertTrue(all(
            str(node["metadata"]["assetId"]).startswith("character:")
            for node in image_matches
        ))
        self.assertEqual(
            [node["id"] for node in image_matches],
            ["asset-old"],
        )

    def test_post_merge_repairs_explicit_ref_for_renamed_asset(self):
        payload = self._project()
        payload["nodes"] = [
            {
                "id": "renamed-image",
                "type": "image",
                "title": "角色｜贾富贵新名（图3）",
                "metadata": {
                    "assetId": "character:贾富贵:default",
                    "displayName": "贾富贵新名",
                },
            },
            {
                "id": "video-new",
                "type": "video",
                "title": "段5",
                "metadata": {
                    "prompt": "【核心人物】@角色｜贾富贵新名（图3），走入。",
                    "params": {"refs": []},
                },
            },
        ]
        repaired = import_feed.repair_missing_explicit_video_refs(payload)
        self.assertEqual(repaired, 1)
        video = next(node for node in payload["nodes"] if node["id"] == "video-new")
        self.assertEqual(
            [ref["nodeId"] for ref in video["metadata"]["params"]["refs"]],
            ["renamed-image"],
        )
        self.assertTrue(any(
            edge.get("fromNodeId") == "renamed-image"
            and edge.get("toNodeId") == "video-new"
            for edge in payload["connections"]
        ))

    def test_figure_slot_dedupe_preserves_distinct_asset_conflict(self):
        payload = {
            "nodes": [
                {
                    "id": "image-a",
                    "type": "image",
                    "title": "角色｜贾富贵（图3）",
                    "metadata": {"assetId": "character:贾富贵:default"},
                },
                {
                    "id": "image-b",
                    "type": "image",
                    "title": "角色｜老会计（图3）",
                    "metadata": {"assetId": "character:老会计:default"},
                },
                {
                    "id": "video-a",
                    "type": "video",
                    "title": "EP001 · 段1 · 视频",
                    "metadata": {
                        "params": {
                            "refs": [{
                                "nodeId": "image-b",
                                "assetId": "character:老会计:default",
                                "label": "老会计",
                            }],
                        },
                    },
                },
            ],
            "connections": [{
                "id": "edge-a",
                "fromNodeId": "image-b",
                "toNodeId": "video-a",
            }],
        }
        removed = import_feed.dedupe_asset_nodes_by_stable_identity(payload)
        self.assertEqual(removed, 0)
        self.assertEqual(
            {node["id"] for node in payload["nodes"]},
            {"image-a", "image-b", "video-a"},
        )
        self.assertEqual(
            payload["nodes"][2]["metadata"]["params"]["refs"][0]["nodeId"],
            "image-b",
        )
        self.assertEqual(
            payload["connections"][0]["fromNodeId"],
            "image-b",
        )

    def test_reimport_after_rename_handles_multi_episode_asset_pool(self):
        project_id = canvas_store.create_project("rename-multiep", self._project())
        renamed = canvas_store.rename_project_asset_atomic(
            project_id,
            "asset-old",
            "贾富贵新名",
            expected_asset_id="character:贾富贵:default",
        )
        self.assertFalse(renamed.get("error"), renamed)
        current = canvas_store.get_project(project_id)
        payload = current["payload"]
        payload["nodes"].append({
            "id": "asset-second-episode",
            "type": "image",
            "title": "角色｜贾富贵（图3）",
            "episodeId": "EP002",
            "metadata": {
                "episodeId": "EP002",
                "assetId": "character:贾富贵:default",
                "displayName": "贾富贵新名",
                "aliases": [{"newName": "贾富贵新名", "oldName": "贾富贵"}],
                "status": "idle",
            },
        })
        payload["episodeImports"].append({
            "id": "EP003",
            "nodeIds": ["asset-second-episode"],
            "videoNodeIds": [],
        })
        canvas_store.update_project(
            project_id,
            payload=payload,
            if_revision=current["revision"],
        )
        current = canvas_store.get_project(project_id)
        feed = {
            "title": "跨集重导",
            "canvas_contract": {
                "schema": "drama-canvas-feed/v2",
                "generator": {
                    "name": "test",
                    "skillVersion": "test",
                    "packageRevision": "test",
                },
            },
            "assets": [{
                "code": "CHR-003",
                "semanticCode": "CHR-003",
                "name": "贾富贵",
                "img": "图3",
                "view": "角色",
            }],
            "asset_prompts": [{
                "code": "图3",
                "semanticCode": "CHR-003",
                "img": "图3",
                "name": "贾富贵（角色立绘）",
                "raw": "人物.",
            }],
            "voice_prompts": [],
            "segments": [{
                "no": 1,
                "name": "段1",
                "raw": (
                    "【画幅风格】16:9\n"
                    "【核心人物】@角色｜贾富贵（图3）\n"
                    "台词：无"
                ),
                "shots": [],
            }],
        }
        plan = canvas_feed_contract.prepare_import(
            current,
            feed,
            project_id,
            current["revision"],
        )
        self.assertTrue(
            plan.ok,
            [canvas_feed_contract.issue_dict(item) for item in plan.errors],
        )


if __name__ == "__main__":
    unittest.main()
