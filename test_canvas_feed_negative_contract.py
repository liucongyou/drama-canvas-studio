# -*- coding: utf-8 -*-
import json
import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from canvas_feed_contract import (
    ErrorCode,
    RevisionConflictError,
    WarningCode,
    analyze_declared_feed,
    canonical_code,
    commit_import,
    issue_dict,
    prepare_import,
    validate_declared_feed,
    validate_final_payload,
)


FIXTURES = Path(__file__).parent / "tests" / "fixtures" / "canvas-feed-invalid"
VALID_FIXTURES = Path(__file__).parent / "tests" / "fixtures" / "canvas-feed-valid"


class CanvasFeedNegativeContractTests(unittest.TestCase):
    def two_segment_feed(self):
        feed = json.loads((VALID_FIXTURES / "generic.json").read_text(encoding="utf-8"))
        feed["asset_prompts"].append({
            "code": "图10",
            "name": "Zhou Ye（角色立绘）",
            "raw": "Voice Description 完整版: A bright voice with a firm cadence.",
        })
        feed["voice_prompts"].append({
            "code": "图10",
            "name": "Zhou Ye",
            "format": "audio-prompt-v1",
            "prompt": (
                "bright firm male voice. A male voice, bright and firm. "
                "A male aged 35, speaking standard Mandarin, short drama dubbing "
                "reference clip, 4.5 seconds clean voice, no background noise."
            ),
            "label": "bright firm male voice",
            "description": "A male voice, bright and firm.",
            "age": "35",
            "gender": "male",
            "dialect": "standard",
        })
        feed["segments"].append({
            "no": 2,
            "name": "段2",
            "raw": (
                "【核心人物】@角色｜Zhou Ye（图10）\n"
                "【音色引用】@音频｜Zhou Ye（图10）\n"
                '台词：Zhou Ye（firm）："Go."'
            ),
        })
        return feed

    def assert_fixture_rejected(self, name: str, expected: ErrorCode):
        payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        actual = {
            issue.code
            for issue in validate_declared_feed(payload)
        }
        self.assertIn(
            expected,
            actual,
            f"{name} 应被 {expected} 拒绝，实际错误码：{sorted(actual)}",
        )

    def test_rejects_old_schema(self):
        self.assert_fixture_rejected(
            "01-old-schema.json",
            ErrorCode.SCHEMA_VERSION_UNSUPPORTED,
        )

    def test_rejects_missing_voice_prompts(self):
        self.assert_fixture_rejected(
            "02-missing-voice-prompts.json",
            ErrorCode.VOICE_PROMPT_MISSING,
        )

    def test_rejects_old_age_syntax(self):
        self.assert_fixture_rejected(
            "03-old-age-syntax.json",
            ErrorCode.VOICE_PROMPT_INCOMPLETE,
        )

    def test_rejects_missing_voice_reference(self):
        self.assert_fixture_rejected(
            "04-missing-voice-ref.json",
            ErrorCode.VOICE_DECLARED_MISSING,
        )

    def test_rejects_duplicate_asset_code(self):
        self.assert_fixture_rejected(
            "05-duplicate-asset-code.json",
            ErrorCode.ASSET_CODE_DUPLICATE,
        )

    def test_rejects_zero_padded_duplicate_asset_code(self):
        feed = json.loads(
            (VALID_FIXTURES / "generic.json").read_text(encoding="utf-8")
        )
        duplicate = copy.deepcopy(feed["asset_prompts"][0])
        duplicate["code"] = "图09"
        duplicate["name"] = "Lin Xia（零填充重复）"
        feed["asset_prompts"].append(duplicate)

        result = analyze_declared_feed(feed)

        self.assertIn(
            ErrorCode.ASSET_CODE_DUPLICATE,
            {issue.code for issue in result.errors},
        )

    def test_canonical_code_normalizes_figure_variants(self):
        self.assertEqual(canonical_code("图9"), "图9")
        self.assertEqual(canonical_code("图09"), "图9")
        self.assertEqual(canonical_code("图 9"), "图9")

    def test_zero_padded_declarations_match_unpadded_prompt_references(self):
        feed = json.loads(
            (VALID_FIXTURES / "generic.json").read_text(encoding="utf-8")
        )
        feed["asset_prompts"][0]["code"] = "图09"
        feed["voice_prompts"][0]["code"] = "图09"

        plan = prepare_import(
            {"nodes": [], "connections": [], "episodeImports": []},
            feed,
            "project-1",
            1,
        )

        self.assertTrue(plan.ok, [issue_dict(item) for item in plan.errors])
        image = next(
            node
            for node in plan.merged_payload["nodes"]
            if node.get("type") == "image"
        )
        self.assertIn("（图9）", image["title"])
        self.assertNotIn("（图09）", image["title"])

    def test_rejects_missing_dialect(self):
        self.assert_fixture_rejected(
            "06-missing-dialect.json",
            ErrorCode.VOICE_DIALECT_MISSING,
        )

    def test_prepare_import_is_pure_and_passes_both_gates(self):
        feed = json.loads((VALID_FIXTURES / "generic.json").read_text(encoding="utf-8"))
        existing = {
            "nodes": [],
            "connections": [],
            "episodeImports": [],
            "title": "empty",
        }
        before = copy.deepcopy(existing)

        plan = prepare_import(existing, feed, "project-1", 7)

        self.assertTrue(plan.ok, [issue_dict(item) for item in plan.errors])
        self.assertEqual(existing, before)
        self.assertEqual(plan.revision, 7)
        self.assertEqual(plan.episode_id, "EP001")
        self.assertGreater(plan.summary["diff"]["nodesAdded"], 0)

    def test_final_gate_rejects_missing_voice_refs(self):
        feed = json.loads((VALID_FIXTURES / "generic.json").read_text(encoding="utf-8"))
        gate = analyze_declared_feed(feed)
        plan = prepare_import({"nodes": [], "connections": [], "episodeImports": []}, feed, "project-1", 1)
        self.assertTrue(plan.ok)
        merged = copy.deepcopy(plan.merged_payload)
        video = next(node for node in merged["nodes"] if node.get("type") == "video")
        metadata = video.setdefault("metadata", {})
        metadata.setdefault("params", {})["voiceRefs"] = []
        metadata["params"]["voiceRef"] = None
        metadata["voiceRefs"] = []
        issues = validate_final_payload(merged, gate.expectations, plan.episode_id)
        self.assertIn(ErrorCode.VOICE_REF_MISMATCH, {issue.code for issue in issues})

    def test_final_gate_rejects_missing_audio_edge(self):
        feed = json.loads((VALID_FIXTURES / "generic.json").read_text(encoding="utf-8"))
        gate = analyze_declared_feed(feed)
        plan = prepare_import({"nodes": [], "connections": [], "episodeImports": []}, feed, "project-1", 1)
        self.assertTrue(plan.ok)
        merged = copy.deepcopy(plan.merged_payload)
        video = next(node for node in merged["nodes"] if node.get("type") == "video")
        audio = next(node for node in merged["nodes"] if node.get("type") == "audio")
        merged["connections"] = [
            connection for connection in merged.get("connections") or []
            if not (
                connection.get("fromNodeId") == audio["id"]
                and connection.get("toNodeId") == video["id"]
            )
        ]
        issues = validate_final_payload(merged, gate.expectations, plan.episode_id)
        self.assertIn(ErrorCode.VOICE_EDGE_MISMATCH, {issue.code for issue in issues})

    def test_final_gate_allows_repaired_bare_asset_mention(self):
        feed = json.loads((VALID_FIXTURES / "generic.json").read_text(encoding="utf-8"))
        feed["asset_prompts"].append({
            "code": "图10",
            "name": "Zhou Ye（角色立绘）",
            "raw": "Voice Description 完整版: A male voice, low and restrained.",
        })
        feed["voice_prompts"].append({
            "code": "图10",
            "name": "Zhou Ye",
            "format": "audio-prompt-v1",
            "prompt": (
                "low restrained male voice. A male voice, low and restrained. "
                "A male aged 35, speaking standard Mandarin, short drama dubbing "
                "reference clip, 4.5 seconds clean voice, no background noise."
            ),
            "label": "low restrained male voice",
            "description": "A male voice, low and restrained.",
            "age": "35",
            "gender": "male",
            "dialect": "standard",
        })
        feed["segments"][0]["raw"] += "\nZhou Ye stands behind the door."

        plan = prepare_import(
            {"nodes": [], "connections": [], "episodeImports": []},
            feed,
            "project-1",
            1,
        )

        self.assertTrue(plan.ok, [issue_dict(item) for item in plan.errors])
        self.assertNotIn(
            ErrorCode.ASSET_EDGE_MISMATCH,
            {issue.code for issue in plan.errors},
        )

    def test_final_gate_rejects_unrelated_extra_asset_edge(self):
        feed = json.loads((VALID_FIXTURES / "generic.json").read_text(encoding="utf-8"))
        gate = analyze_declared_feed(feed)
        plan = prepare_import(
            {"nodes": [], "connections": [], "episodeImports": []},
            feed,
            "project-1",
            1,
        )
        self.assertTrue(plan.ok)
        merged = copy.deepcopy(plan.merged_payload)
        video = next(node for node in merged["nodes"] if node.get("type") == "video")
        extra = {
            "id": "image-extra",
            "type": "image",
            "title": "角色｜无关角色（图99）",
            "metadata": {"content": "", "status": "idle", "params": {}},
        }
        merged["nodes"].append(extra)
        merged["episodeImports"][-1].setdefault("nodeIds", []).append(extra["id"])
        merged["connections"].append({
            "id": "edge-extra",
            "fromNodeId": extra["id"],
            "toNodeId": video["id"],
        })

        issues = validate_final_payload(merged, gate.expectations, plan.episode_id)

        self.assertIn(
            ErrorCode.ASSET_EDGE_MISMATCH,
            {issue.code for issue in issues},
        )

    def test_final_gate_is_independent_of_global_video_node_order(self):
        feed = self.two_segment_feed()
        gate = analyze_declared_feed(feed)
        plan = prepare_import(
            {"nodes": [], "connections": [], "episodeImports": []},
            feed,
            "project-1",
            1,
        )
        self.assertTrue(plan.ok, [issue_dict(item) for item in plan.errors])
        before = validate_final_payload(
            plan.merged_payload,
            gate.expectations,
            plan.episode_id,
        )
        self.assertFalse(before)

        reordered = copy.deepcopy(plan.merged_payload)
        group = next(
            item for item in reordered["episodeImports"]
            if item.get("id") == plan.episode_id
        )
        video_ids = set(group.get("videoNodeIds") or [])
        positions = [
            index for index, node in enumerate(reordered["nodes"])
            if node.get("id") in video_ids
        ]
        first, second = positions[:2]
        reordered["nodes"][first], reordered["nodes"][second] = (
            reordered["nodes"][second],
            reordered["nodes"][first],
        )

        after = validate_final_payload(
            reordered,
            gate.expectations,
            plan.episode_id,
        )
        self.assertFalse(after, [issue_dict(item) for item in after])

    def test_explicit_asset_without_prompt_uses_placeholder(self):
        feed = json.loads((VALID_FIXTURES / "generic.json").read_text(encoding="utf-8"))
        feed["segments"][0]["raw"] = (
            "@道具｜幽灵账本（图99）\n" + feed["segments"][0]["raw"]
        )

        plan = prepare_import(
            {"nodes": [], "connections": [], "episodeImports": []},
            feed,
            "project-1",
            1,
        )

        self.assertTrue(plan.ok, [issue_dict(item) for item in plan.errors])
        placeholder = next(
            node for node in plan.merged_payload["nodes"]
            if "图99" in str(node.get("title") or "")
        )
        self.assertTrue(placeholder["metadata"]["autoPlaceholder"])

    def test_final_gate_rejects_declared_asset_missing_from_product(self):
        feed = json.loads((VALID_FIXTURES / "generic.json").read_text(encoding="utf-8"))
        gate = analyze_declared_feed(feed)
        plan = prepare_import(
            {"nodes": [], "connections": [], "episodeImports": []},
            feed,
            "project-1",
            1,
        )
        self.assertTrue(plan.ok)
        merged = copy.deepcopy(plan.merged_payload)
        image = next(
            node for node in merged["nodes"]
            if node.get("type") == "image" and "图9" in str(node.get("title") or "")
        )
        merged["nodes"] = [node for node in merged["nodes"] if node.get("id") != image["id"]]
        for group in merged["episodeImports"]:
            group["nodeIds"] = [
                node_id for node_id in group.get("nodeIds") or []
                if node_id != image["id"]
            ]

        issues = validate_final_payload(merged, gate.expectations, plan.episode_id)

        self.assertIn(
            ErrorCode.ASSET_DECLARED_MISSING,
            {issue.code for issue in issues},
        )

    def test_rejects_dialogue_without_labeled_audio_reference(self):
        feed = json.loads((VALID_FIXTURES / "generic.json").read_text(encoding="utf-8"))
        feed["segments"][0]["raw"] = (
            "@角色｜Lin Xia（图9）\n"
            'Lin Xia（calm）："Start."'
        )

        result = analyze_declared_feed(feed)

        self.assertIn(
            ErrorCode.VOICE_DECLARED_MISSING,
            {issue.code for issue in result.errors},
        )

    def test_rejects_canvas_asset_manifest_drift(self):
        feed = json.loads((VALID_FIXTURES / "generic.json").read_text(encoding="utf-8"))
        feed["segments"][0]["canvas"] = {
            "asset_codes": ["图9", "图10"],
            "asset_refs": [
                {"code": "图9", "name": "Lin Xia"},
                {"code": "图10", "name": "Zhou Ye"},
            ],
        }

        result = analyze_declared_feed(feed)

        self.assertIn(
            ErrorCode.ASSET_DECLARED_MISSING,
            {issue.code for issue in result.errors},
        )

    def test_gate1_warns_when_dialect_is_derived_from_explicit_text(self):
        feed = json.loads((VALID_FIXTURES / "generic.json").read_text(encoding="utf-8"))
        feed["voice_prompts"][0].pop("dialect")
        result = analyze_declared_feed(feed)
        self.assertFalse(result.errors)
        self.assertIn(
            WarningCode.WARN_DERIVED_DIALECT,
            {issue.code for issue in result.warnings},
        )

    def test_gate1_warns_for_deprecated_voice_alias(self):
        feed = json.loads((VALID_FIXTURES / "generic.json").read_text(encoding="utf-8"))
        voice = feed["voice_prompts"][0]
        voice["voicePrompt"] = voice.pop("prompt")
        result = analyze_declared_feed(feed)
        self.assertFalse(result.errors)
        self.assertIn(
            WarningCode.WARN_DEPRECATED_VOICE_FIELD,
            {issue.code for issue in result.warnings},
        )

    def test_missing_generator_warns_but_remains_compatible(self):
        feed = json.loads(
            (VALID_FIXTURES / "generic.json").read_text(encoding="utf-8")
        )
        feed["canvas_contract"].pop("generator", None)

        result = analyze_declared_feed(feed)
        plan = prepare_import(
            {"nodes": [], "connections": [], "episodeImports": []},
            feed,
            "project-1",
            1,
            source={"name": "compatible.json"},
        )

        self.assertFalse(result.errors)
        self.assertIn(
            WarningCode.WARN_GENERATOR_METADATA_MISSING,
            {issue.code for issue in result.warnings},
        )
        self.assertTrue(plan.ok, [issue_dict(item) for item in plan.errors])
        group = plan.merged_payload["episodeImports"][0]
        self.assertIn(
            "WARN_GENERATOR_METADATA_MISSING",
            {
                item["code"]
                for item in group.get("importWarnings") or []
            },
        )

    def test_provenance_hash_uses_canonical_figure_codes(self):
        feed = json.loads(
            (VALID_FIXTURES / "generic.json").read_text(encoding="utf-8")
        )
        reordered = copy.deepcopy(feed)
        reordered["asset_prompts"][0]["code"] = "图09"
        reordered["voice_prompts"][0]["code"] = "图09"
        for segment in reordered["segments"]:
            segment["raw"] = segment["raw"].replace("图9", "图09")

        first = prepare_import(
            {"nodes": [], "connections": [], "episodeImports": []},
            feed,
            "project-1",
            1,
        )
        second = prepare_import(
            {"nodes": [], "connections": [], "episodeImports": []},
            reordered,
            "project-1",
            1,
        )

        self.assertEqual(first.provenance["feedSha256"], second.provenance["feedSha256"])

    def test_provenance_is_attached_to_episode_group(self):
        feed = json.loads((VALID_FIXTURES / "generic.json").read_text(encoding="utf-8"))
        plan = prepare_import(
            {"nodes": [], "connections": [], "episodeImports": []},
            feed,
            "project-1",
            3,
            source={"name": "feed.json"},
        )
        self.assertTrue(plan.ok)
        group = plan.merged_payload["episodeImports"][-1]
        self.assertEqual(group["sourceFeedName"], "feed.json")
        self.assertEqual(group["schemaVersion"], "drama-canvas-feed/v2")
        self.assertEqual(group["skillVersion"], "7.4.29")
        self.assertEqual(len(group["feedSha256"]), 64)
        self.assertEqual(group["lastImportedAt"], group["importedAt"])
        self.assertTrue(group["firstImportedAt"])

    def test_provenance_keeps_first_import_and_latest_import(self):
        first_feed = json.loads((VALID_FIXTURES / "generic.json").read_text(encoding="utf-8"))
        first = prepare_import(
            {"nodes": [], "connections": [], "episodeImports": []},
            first_feed,
            "project-1",
            1,
            source={"name": "first.json"},
        )
        self.assertTrue(first.ok)
        first_group = first.merged_payload["episodeImports"][0]
        first_imported_at = first_group["firstImportedAt"]

        second_feed = copy.deepcopy(first_feed)
        second_feed["title"] += " · changed"
        second = prepare_import(
            first.merged_payload,
            second_feed,
            "project-1",
            2,
            source={"name": "second.json"},
        )

        self.assertTrue(second.ok)
        second_group = second.merged_payload["episodeImports"][0]
        self.assertEqual(second_group["firstImportedAt"], first_imported_at)
        self.assertEqual(second_group["lastImportedAt"], second_group["importedAt"])
        self.assertEqual(second_group["sourceFeedName"], "second.json")

    def test_commit_rejects_stale_revision(self):
        feed = json.loads((VALID_FIXTURES / "generic.json").read_text(encoding="utf-8"))
        plan = prepare_import(
            {"nodes": [], "connections": [], "episodeImports": []},
            feed,
            "project-1",
            3,
        )
        with patch("canvas_store.get_project", return_value={
            "id": "project-1",
            "title": "changed elsewhere",
            "payload": {},
            "revision": 4,
        }):
            with self.assertRaises(RevisionConflictError):
                commit_import(plan, if_revision=3)


if __name__ == "__main__":
    unittest.main()
