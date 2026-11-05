# -*- coding: utf-8 -*-
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import importlib.util
from pathlib import Path

import canvas_feed_contract
from canvas_feed_contract import issue_dict, prepare_import


ROOT = Path(__file__).parent
SKILL_ROOT = (
    ROOT
    / "deliverables"
    / "manju-laoli-skill-V7.4.29"
    / "short-drama-director"
)
GENERATOR = SKILL_ROOT / "scripts" / "build_board_lite.py"


class CanvasFeedGeneratorContractTests(unittest.TestCase):
    def test_generator_rejects_slot_code_as_semantic_identity(self):
        spec = importlib.util.spec_from_file_location(
            "canvas_feed_generator_under_test",
            GENERATOR,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with self.assertRaisesRegex(ValueError, "缺稳定语义码"):
            module.validate_export_contract({
                "assets": [{
                    "code": "图1",
                    "semanticCode": "",
                    "img": "图1",
                    "name": "赵铁柱（角色立绘）",
                }],
                "asset_prompts": [{
                    "code": "图1",
                    "semanticCode": "",
                    "name": "赵铁柱（角色立绘）",
                }],
                "voice_prompts": [],
            })

    def test_tiexi_source_exports_and_imports(self):
        source = ROOT.parent / "铁西硬汉_第01集_看板源.md"
        if not source.exists():
            self.skipTest("真实源稿不在当前工作区")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "board.html"
            feed_path = Path(tmp) / "feed.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(GENERATOR),
                    str(source),
                    "--out",
                    str(out),
                    "--export-json",
                    str(feed_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            feed = json.loads(feed_path.read_text(encoding="utf-8"))

        self.assertEqual(
            [(item["semanticCode"], item["code"]) for item in feed["asset_prompts"]],
            [
                ("CHR-001", "图1"),
                ("CHR-002", "图2"),
                ("CHR-003", "图3"),
                ("CHR-004", "图4"),
                ("CHR-005", "图5"),
                ("SCN-006", "图6"),
                ("PRP-007", "图7"),
                ("PRP-008", "图8"),
            ],
        )
        self.assertEqual(
            [(item["semanticCode"], item["code"]) for item in feed["voice_prompts"]],
            [
                ("CHR-001", "图1"),
                ("CHR-002", "图2"),
                ("CHR-003", "图3"),
                ("CHR-004", "图4"),
                ("CHR-005", "图5"),
            ],
        )
        plan = prepare_import(
            {"nodes": [], "connections": [], "episodeImports": []},
            feed,
            "project-tiexi",
            1,
        )
        self.assertTrue(plan.ok, [issue_dict(item) for item in plan.errors])
        payload = plan.merged_payload
        counts = {}
        for node in payload["nodes"]:
            counts[node["type"]] = counts.get(node["type"], 0) + 1
        self.assertEqual(
            counts,
            {"text": 1, "image": 8, "video": 8, "audio": 5},
        )
        images = [
            node for node in payload["nodes"] if node.get("type") == "image"
        ]
        audios = [
            node for node in payload["nodes"] if node.get("type") == "audio"
        ]
        videos = [
            node for node in payload["nodes"] if node.get("type") == "video"
        ]
        self.assertEqual(len(images), 8)
        self.assertEqual(len(audios), 5)
        self.assertEqual(len(videos), 8)
        self.assertTrue(payload["connections"])
        image_ids = {node["id"] for node in images}
        audio_ids = {node["id"] for node in audios}
        for video in videos:
            params = (video.get("metadata") or {}).get("params") or {}
            self.assertTrue(
                {ref.get("nodeId") for ref in params.get("refs") or []}
                & image_ids
            )
            self.assertTrue(
                {ref.get("nodeId") for ref in params.get("voiceRefs") or []}
                & audio_ids
            )

    def test_schema_copy_hashes_match(self):
        server = ROOT / "schemas" / "canvas_feed.schema.json"
        package = SKILL_ROOT / "references" / "canvas-feed.schema.json"
        self.assertEqual(
            hashlib.sha256(server.read_bytes()).hexdigest(),
            hashlib.sha256(package.read_bytes()).hexdigest(),
            "生成器 Schema 与服务端 Schema 已分叉",
        )

    def test_generator_self_test_passes(self):
        result = subprocess.run(
            [sys.executable, str(GENERATOR), "--self-test"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("self-test PASSED", result.stdout)

    def test_generator_artifact_imports_without_errors(self):
        script = """
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(r"{skill_root}") / "scripts"))
import build_board_lite

feed = build_board_lite.enrich_canvas_contract({{
    "title": "生成导入联调",
    "meta": {{"画幅": "16:9", "模型版本": "2.0"}},
    "assets": [
        {{"code": "CHR-001", "name": "赵铁柱（角色立绘）", "img": "图1"}},
    ],
    "asset_prompts": [{{
        "code": "CHR-001",
        "name": "赵铁柱（角色立绘）",
        "raw": (
            "一名 35 岁 男性。\\ncharacter sheet\\n"
            "**Voice Description 完整版**： "
            "A 35-year-old low, steady, threatening male voice.\\n"
            "**Voice Description Label**： low steady male"
        ),
    }}],
    "segments": [{{
        "no": 1,
        "name": "段1",
        "raw": (
            "【画幅风格】16:9\\n"
            "【核心人物】@角色｜赵铁柱（图1）\\n"
            "【站位声明】@角色｜赵铁柱（图1）（画面左）\\n"
            "台词：赵铁柱（平静）：\\"走。\\""
        ),
        "shots": [],
    }}],
}})
print(json.dumps(feed, ensure_ascii=False))
""".format(skill_root=str(SKILL_ROOT))
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        feed = json.loads(result.stdout)

        self.assertEqual(feed["asset_prompts"][0]["code"], "图1")
        self.assertEqual(feed["asset_prompts"][0]["semanticCode"], "CHR-001")

    def test_generated_feed_contains_material_reference_contract(self):
        script = """
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(r"{skill_root}") / "scripts"))
import build_board_lite

feed = build_board_lite.enrich_canvas_contract({{
    "title": "素材引用契约测试",
    "meta": {{"画幅": "9:16", "模型版本": "2.0"}},
    "assets": [
        {{"code": "图1", "name": "赵铁柱（角色立绘）", "img": "图1"}},
        {{"code": "图9", "name": "街口（场景母版）", "img": "图9"}},
    ],
    "asset_prompts": [
        {{
            "code": "图1",
            "name": "赵铁柱（角色立绘）",
            "raw": (
                "一名 35 岁 男性。\\ncharacter sheet\\n"
                "**Voice Description 完整版**： "
                "A 35-year-old low, steady, threatening male voice.\\n"
                "**Voice Description Label**： low steady male"
            ),
        }},
        {{
            "code": "图9",
            "name": "街口（场景母版）",
            "raw": "9:16 场景母版，无人。",
        }},
    ],
    "segments": [{{
        "no": 1,
        "name": "段1",
        "raw": (
            "【场景资产】@街口（图9）\\n"
            "【核心人物】@赵铁柱（图1）\\n"
            "【音色引用】@音频｜赵铁柱（图1）\\n"
            "台词：赵铁柱（沉声）：\\"走。\\"\\n\\n"
            "### 素材引用\\n"
            "【人物】\\n"
            "<角色｜旧占位（图99）> 对应角色参考图"
        ),
        "shots": [],
    }}],
}})
print(json.dumps(feed, ensure_ascii=False))
""".format(skill_root=str(SKILL_ROOT))
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, (result.stdout or "") + (result.stderr or ""))
        feed = json.loads(result.stdout)
        raw = feed["segments"][0]["raw"]
        self.assertIn("### 素材引用", raw)
        self.assertIn("【人物】", raw)
        self.assertIn("【场景】", raw)
        self.assertIn("仅采用该人物的外貌、发型和服装", raw)
        self.assertIn("不采用图中人物", raw)
        self.assertEqual(raw.count("### 素材引用"), 1)
        self.assertNotIn("旧占位（图99）", raw)
        self.assertEqual(feed["voice_prompts"][0]["code"], "图1")
        self.assertEqual(feed["voice_prompts"][0]["semanticCode"], "CHR-001")
        self.assertIn("【音色引用】@音频｜赵铁柱（图1）", feed["segments"][0]["raw"])
        self.assertEqual(canvas_feed_contract.validate_declared_feed(feed), [])

        plan = prepare_import(
            {"nodes": [], "connections": [], "episodeImports": []},
            feed,
            "project-generated",
            1,
        )
        self.assertTrue(plan.ok, [issue_dict(item) for item in plan.errors])
        payload = plan.merged_payload
        image = next(node for node in payload["nodes"] if node["type"] == "image")
        audio = next(node for node in payload["nodes"] if node["type"] == "audio")
        video = next(node for node in payload["nodes"] if node["type"] == "video")
        self.assertIn("（图1）", image["title"])
        self.assertIn("（图1）", audio["title"])
        refs = video["metadata"]["params"]["refs"]
        voices = video["metadata"]["params"]["voiceRefs"]
        ref_titles = {
            next(
                node["title"]
                for node in payload["nodes"]
                if node["id"] == ref["nodeId"]
            )
            for ref in refs
        }
        self.assertEqual(
            ref_titles,
            {"角色｜赵铁柱（图1）", "场景｜街口·母版（图9）"},
        )
        self.assertEqual({ref["nodeId"] for ref in voices}, {audio["id"]})


if __name__ == "__main__":
    unittest.main()
