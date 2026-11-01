# -*- coding: utf-8 -*-
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import canvas_feed_contract
import canvas_store
import tenant_context
from canvas_feed_contract import issue_dict, prepare_import
from import_feed import repair_payload_prompt_mentions, repair_prompt_mentions


FIXTURES = Path(__file__).parent / "tests" / "fixtures" / "canvas-feed-valid"

VOLATILE_KEYS = {
    "createdAt",
    "updatedAt",
    "positionUpdatedAt",
    "importedAt",
    "firstImportedAt",
    "lastImportedAt",
    "generatedAt",
    "sourcePromptUpdatedAt",
    "generationStartedAt",
    "generationFinishedAt",
}


def semantic_snapshot(payload):
    def normalize(value):
        if isinstance(value, dict):
            return {
                key: normalize(item)
                for key, item in sorted(value.items())
                if key not in VOLATILE_KEYS
            }
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    nodes = {
        node["id"]: normalize(node)
        for node in payload.get("nodes") or []
    }
    connections = sorted(
        (
            connection.get("fromNodeId"),
            connection.get("toNodeId"),
        )
        for connection in payload.get("connections") or []
    )
    groups = {
        group.get("id"): {
            "nodeIds": sorted(group.get("nodeIds") or []),
            "videoNodeIds": sorted(group.get("videoNodeIds") or []),
            "title": group.get("title"),
        }
        for group in payload.get("episodeImports") or []
    }
    return {
        "title": payload.get("title"),
        "episodeId": payload.get("episodeId"),
        "nodes": nodes,
        "connections": connections,
        "groups": groups,
    }


def node_id_set(payload):
    return {node.get("id") for node in payload.get("nodes") or []}


class CanvasFeedRoundTripTests(unittest.TestCase):
    maxDiff = None

    def base_feed(self):
        return json.loads(
            (FIXTURES / "generic.json").read_text(encoding="utf-8")
        )

    def episode_feed(self, episode, title, raw=None):
        feed = copy.deepcopy(self.base_feed())
        feed["title"] = f"通用测试 · 第{episode:02d}集"
        feed["segments"][0]["no"] = episode
        feed["segments"][0]["name"] = f"段{episode}"
        if raw is not None:
            feed["segments"][0]["raw"] = raw
        return feed

    def import_feed(self, existing, feed, revision):
        plan = prepare_import(
            existing,
            feed,
            "roundtrip-project",
            revision,
            source={"name": feed["title"]},
        )
        self.assertTrue(
            plan.ok,
            [issue_dict(item) for item in plan.errors],
        )
        return plan

    def test_prompt_repair_is_idempotent(self):
        raw = (
            "【核心人物】@角色｜Lin Xia（图9）\n"
            "【音色引用】@音频｜Lin Xia（图9）\n"
            "【站位声明】@角色｜Lin Xia（图9）（画面左）\n"
            '台词：Lin Xia（calm）："Start."'
        )
        refs = [{"label": "角色｜Lin Xia（图9）"}]
        voices = [{"label": "音频｜Lin Xia（图9）"}]

        once = repair_prompt_mentions(raw, refs)
        twice = repair_prompt_mentions(once, refs + voices)

        self.assertEqual(once, twice)

    def test_payload_prompt_repair_is_idempotent(self):
        feed = self.episode_feed(1, "EP001")
        plan = self.import_feed(
            {"nodes": [], "connections": [], "episodeImports": []},
            feed,
            1,
        )
        payload = copy.deepcopy(plan.merged_payload)
        first_video = next(
            node for node in payload["nodes"] if node["type"] == "video"
        )
        before = first_video["metadata"]["prompt"]

        repair_payload_prompt_mentions(payload)
        once = first_video["metadata"]["prompt"]
        repair_payload_prompt_mentions(payload)
        twice = first_video["metadata"]["prompt"]

        self.assertEqual(before, once)
        self.assertEqual(once, twice)

    def test_same_episode_reimport_is_semantically_idempotent(self):
        feed = self.episode_feed(1, "EP001")
        feed_before = copy.deepcopy(feed)
        first = self.import_feed(
            {"nodes": [], "connections": [], "episodeImports": []},
            feed,
            1,
        )
        self.assertEqual(feed, feed_before)
        second = self.import_feed(first.merged_payload, feed, 2)

        self.assertEqual(
            semantic_snapshot(first.merged_payload),
            semantic_snapshot(second.merged_payload),
        )

    def test_reimport_does_not_replace_episode_node_ids(self):
        feed1 = self.episode_feed(1, "EP001")
        first = self.import_feed(
            {"nodes": [], "connections": [], "episodeImports": []},
            feed1,
            1,
        )
        feed2 = self.episode_feed(2, "EP002")
        second = self.import_feed(first.merged_payload, feed2, 2)
        second_ids = node_id_set(second.merged_payload)
        second_group = next(
            group for group in second.merged_payload["episodeImports"]
            if group["id"] == "EP002"
        )

        reimport = self.import_feed(second.merged_payload, feed2, 3)
        reimport_group = next(
            group for group in reimport.merged_payload["episodeImports"]
            if group["id"] == "EP002"
        )

        self.assertEqual(second_ids, node_id_set(reimport.merged_payload))
        self.assertEqual(
            set(second_group["nodeIds"]),
            set(reimport_group["nodeIds"]),
        )
        self.assertEqual(
            set(second_group["videoNodeIds"]),
            set(reimport_group["videoNodeIds"]),
        )

    def test_placeholder_reimport_preserves_existing_detailed_prompt(self):
        feed1 = self.episode_feed(1, "EP001")
        first = self.import_feed(
            {"nodes": [], "connections": [], "episodeImports": []},
            feed1,
            1,
        )
        image = next(
            node for node in first.merged_payload["nodes"]
            if node.get("type") == "image"
        )
        detailed_prompt = str(image["metadata"]["prompt"])

        placeholder_feed = copy.deepcopy(feed1)
        placeholder_feed["title"] = "通用测试 · 第01集（复用占位）"
        placeholder_feed["asset_prompts"] = []
        placeholder_feed["voice_prompts"] = []
        placeholder_feed["segments"][0]["raw"] = "@角色｜Lin Xia（图9）\nNo dialogue."

        second = self.import_feed(first.merged_payload, placeholder_feed, 2)
        second_image = next(
            node for node in second.merged_payload["nodes"]
            if node.get("type") == "image"
        )

        self.assertEqual(second_image["id"], image["id"])
        self.assertEqual(second_image["metadata"]["prompt"], detailed_prompt)
        self.assertFalse(second_image["metadata"].get("autoPlaceholder"))

    def test_ep2_reimport_does_not_mutate_ep1_group(self):
        feed1 = self.episode_feed(1, "EP001")
        first = self.import_feed(
            {"nodes": [], "connections": [], "episodeImports": []},
            feed1,
            1,
        )
        feed2 = self.episode_feed(2, "EP002")
        second = self.import_feed(first.merged_payload, feed2, 2)
        ep1_before = next(
            group for group in second.merged_payload["episodeImports"]
            if group["id"] == "EP001"
        )
        ep1_ids_before = set(ep1_before["nodeIds"])
        ep1_semantic_before = semantic_snapshot({
            "nodes": [
                node for node in second.merged_payload["nodes"]
                if node["id"] in ep1_ids_before
            ],
            "connections": [
                connection for connection in second.merged_payload["connections"]
                if connection["fromNodeId"] in ep1_ids_before
                or connection["toNodeId"] in ep1_ids_before
            ],
            "episodeImports": [ep1_before],
            "title": second.merged_payload["title"],
            "episodeId": second.merged_payload["episodeId"],
        })

        reimport = self.import_feed(second.merged_payload, feed2, 3)
        ep1_after = next(
            group for group in reimport.merged_payload["episodeImports"]
            if group["id"] == "EP001"
        )
        ep1_ids_after = set(ep1_after["nodeIds"])
        ep1_semantic_after = semantic_snapshot({
            "nodes": [
                node for node in reimport.merged_payload["nodes"]
                if node["id"] in ep1_ids_after
            ],
            "connections": [
                connection for connection in reimport.merged_payload["connections"]
                if connection["fromNodeId"] in ep1_ids_after
                or connection["toNodeId"] in ep1_ids_after
            ],
            "episodeImports": [ep1_after],
            "title": reimport.merged_payload["title"],
            "episodeId": reimport.merged_payload["episodeId"],
        })

        self.assertEqual(ep1_semantic_before, ep1_semantic_after)

    def test_legacy_payload_imports_into_database_authoritative_store(self):
        first = self.import_feed(
            {"nodes": [], "connections": [], "episodeImports": []},
            self.episode_feed(1, "EP001"),
            1,
        )
        legacy_payload = copy.deepcopy(first.merged_payload)
        legacy_payload["episodeImports"][0]["nodeIds"] = ["stale-ghost"]

        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {
                "CANVAS_MULTI_TENANT": "1",
                "CANVAS_TENANT_DATA_ROOT": temp,
            }):
                context = tenant_context.make_context(
                    "import-migration",
                    "import-migration",
                )
                tenant_context.ensure_context_dirs(context)
                with tenant_context.use_context(context):
                    project_id = canvas_store.create_project(
                        "legacy-import",
                        legacy_payload,
                    )
                    current = canvas_store.get_project(project_id)
                    plan = prepare_import(
                        current["payload"],
                        self.episode_feed(2, "EP002"),
                        project_id,
                        current["revision"],
                    )
                    self.assertTrue(plan.ok, [issue_dict(item) for item in plan.errors])
                    canvas_feed_contract.commit_import(
                        plan,
                        if_revision=plan.revision,
                    )
                    ep1 = canvas_store.get_project_workspace_nodes(
                        project_id,
                        "EP001",
                    )
                    ep2 = canvas_store.get_project_workspace_nodes(
                        project_id,
                        "EP002",
                    )
                    ep1_ids = {node["id"] for node in ep1["nodes"]}
                    ep2_ids = {node["id"] for node in ep2["nodes"]}
                    ep1_by_id = {node["id"]: node for node in ep1["nodes"]}
                    ep2_by_id = {node["id"]: node for node in ep2["nodes"]}
                    ep1_video_refs = {
                        str(ref.get("nodeId") or "")
                        for node in ep1["nodes"]
                        if node.get("type") == "video"
                        for key in ("refs", "voiceRefs")
                        for ref in (
                            ((node.get("metadata") or {}).get("params") or {})
                            .get(key, [])
                        )
                        if isinstance(ref, dict)
                    }
                    ep2_video_refs = {
                        str(ref.get("nodeId") or "")
                        for node in ep2["nodes"]
                        if node.get("type") == "video"
                        for key in ("refs", "voiceRefs")
                        for ref in (
                            ((node.get("metadata") or {}).get("params") or {})
                            .get(key, [])
                        )
                        if isinstance(ref, dict)
                    }

        self.assertTrue(ep1_ids)
        self.assertTrue(ep2_ids)
        shared_ids = ep1_ids & ep2_ids
        self.assertTrue(shared_ids)
        self.assertTrue(
            all(
                ep1_by_id[node_id].get("type") in {"image", "audio"}
                and ep2_by_id[node_id].get("type") in {"image", "audio"}
                for node_id in shared_ids
            )
        )
        self.assertTrue(shared_ids <= ep1_video_refs)
        self.assertTrue(shared_ids <= ep2_video_refs)
        self.assertFalse(
            {
                node_id
                for node_id in ep1_ids
                if ep1_by_id[node_id].get("type") == "video"
            }
            & {
                node_id
                for node_id in ep2_ids
                if ep2_by_id[node_id].get("type") == "video"
            }
        )
        self.assertNotIn("stale-ghost", ep1_ids)


if __name__ == "__main__":
    unittest.main()
