# -*- coding: utf-8 -*-
import re
import unittest

import import_feed


class ImportFeedContractTests(unittest.TestCase):
    def test_prompt_repair_removes_stale_media_edge(self):
        payload = {
            "nodes": [
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段13",
                    "metadata": {
                        "prompt": "@角色｜赵铁柱（图1）在门口。",
                        "params": {
                            "refs": [{
                                "nodeId": "img-1",
                                "label": "角色｜赵铁柱（图1）",
                            }],
                        },
                    },
                },
                {
                    "id": "img-1",
                    "type": "image",
                    "title": "角色｜赵铁柱（图1）",
                },
                {
                    "id": "img-2",
                    "type": "image",
                    "title": "角色｜钱万金（图2）",
                },
            ],
            "connections": [
                {"id": "keep", "fromNodeId": "img-1", "toNodeId": "video-1"},
                {"id": "stale", "fromNodeId": "img-2", "toNodeId": "video-1"},
            ],
        }

        import_feed.repair_payload_prompt_mentions(payload)

        self.assertEqual(
            [edge["id"] for edge in payload["connections"]],
            ["keep"],
        )

    def test_prompt_repair_does_not_keep_dialogue_only_image_ref(self):
        payload = {
            "nodes": [
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段13",
                    "metadata": {
                        "prompt": (
                            "【时间轴分镜】00:00-00:02 画面主体："
                            "@角色｜赵铁柱（图1）在门口。"
                            "台词：【老会计（急）：\"要是在@角色｜钱万金（图2）手里。\"】"
                        ),
                        "params": {
                            "refs": [
                                {"nodeId": "img-1", "label": "角色｜赵铁柱（图1）"},
                                {"nodeId": "img-2", "label": "角色｜钱万金（图2）"},
                            ],
                        },
                    },
                },
                {"id": "img-1", "type": "image", "title": "角色｜赵铁柱（图1）"},
                {"id": "img-2", "type": "image", "title": "角色｜钱万金（图2）"},
            ],
            "connections": [
                {"id": "keep", "fromNodeId": "img-1", "toNodeId": "video-1"},
                {"id": "dialogue", "fromNodeId": "img-2", "toNodeId": "video-1"},
            ],
        }

        import_feed.repair_payload_prompt_mentions(payload)

        self.assertEqual(
            [edge["id"] for edge in payload["connections"]],
            ["keep"],
        )
        self.assertEqual(
            [ref["nodeId"] for ref in payload["nodes"][0]["metadata"]["params"]["refs"]],
            ["img-1"],
        )

    def test_dialog_parser_handles_bracket_wrapped_speakers(self):
        text = "\n".join([
            '台词：赵铁柱（低沉）："普通格式。"',
            '台词：【钱万金（急）："整句包裹格式。"】',
            '台词：【贾富贵】（陪笑）："仅姓名包裹格式。"',
            '台词：[陈大强]（压声）："方括号格式。"',
            '台词：无',
        ])

        self.assertEqual(
            import_feed._dialog_speaker_names(text),
            {"赵铁柱", "钱万金", "贾富贵", "陈大强"},
        )

    def test_os_dialogue_does_not_treat_mentioned_character_as_speaker(self):
        prompt = (
            "【核心人物】@角色｜赵铁柱（图1）；@角色｜陈大强（图4）\n"
            "【音色引用】@音频｜赵铁柱（图1）\n"
            "【音效】画外声清单：\n"
            '赵铁柱（OS·内心独白）："我背陈大强从塌方里爬出来那年，他喊我一声哥。"'
        )

        speaking = import_feed._prompt_speaking_roles(
            prompt,
            {"赵铁柱", "陈大强"},
        )

        self.assertEqual(speaking, {"赵铁柱"})

    def test_bracketed_dialog_generates_character_audio(self):
        feed = {
            "title": "括号台词测试 · 第01集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [
                {
                    "code": "图1",
                    "name": "赵铁柱（角色立绘）",
                    "raw": "一名 40~45 岁 男性。Voice Description 完整版: low steady voice.",
                },
                {
                    "code": "图2",
                    "name": "钱万金（角色立绘）",
                    "raw": "一名 48~52 岁 男性。Voice Description 完整版: cold oily voice.",
                },
            ],
            "segments": [{
                "no": 1,
                "name": "段1 · 括号台词",
                "raw": (
                    "【核心人物】@角色｜赵铁柱（图1）；@角色｜钱万金（图2）\n"
                    "台词：【赵铁柱（低沉）：\"走。\"】\n"
                    "台词：【钱万金】（急）：\"站住。\""
                ),
            }],
        }

        payload = import_feed.build_payload(feed)
        audio_titles = {
            node["title"]
            for node in payload["nodes"]
            if node.get("type") == "audio"
        }

        self.assertEqual(
            audio_titles,
            {"音频｜赵铁柱（图1）", "音频｜钱万金（图2）"},
        )

    def test_reused_voice_prompt_uses_series_voice_catalog(self):
        feed = {
            "title": "复用角色音色测试 · 第03集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [{
                "code": "图13",
                "name": "周警官（角色立绘）",
                "raw": "一名 45~50 岁 男性。Voice Description 完整版: calm official voice.",
            }],
            "voice_prompts": [{
                "code": "图1",
                "name": "赵铁柱",
                "title": "角色｜赵铁柱（图1）",
                "prompt": (
                    "low, steady, northeastern-accented baritone. A male voice, low and steady "
                    "with a broad northeastern accent and unhurried rhythm; quiet and deliberate "
                    "when confronting authority. A male aged 38-42, speaking "
                    "northeastern-accented Mandarin, short drama dubbing reference clip, "
                    "4.5 seconds clean voice, no background noise."
                ),
                "description": (
                    "A male voice, low and steady with a broad northeastern accent "
                    "and unhurried rhythm; quiet and deliberate when confronting authority."
                ),
                "label": "low, steady, northeastern-accented baritone",
                "format": "audio-prompt-v1",
            }],
            "segments": [{
                "no": 1,
                "name": "段1 · 复用音色",
                "raw": (
                    "【核心人物】@角色｜赵铁柱（图1）\n"
                    "台词：【赵铁柱（低沉）：\"把账本拿来。\"】"
                ),
            }],
        }

        payload = import_feed.build_payload(feed)
        audio = next(
            node for node in payload["nodes"]
            if node.get("type") == "audio" and node.get("title") == "音频｜赵铁柱（图1）"
        )

        self.assertIn("A male voice, low and steady", audio["metadata"]["prompt"])
        self.assertIn("northeastern-accented baritone", audio["metadata"]["prompt"])
        self.assertIn("A male aged 38-42", audio["metadata"]["prompt"])
        self.assertTrue(
            audio["metadata"]["prompt"].endswith(
                "short drama dubbing reference clip, 4.5 seconds clean voice, no background noise."
            )
        )
        self.assertEqual(
            audio["metadata"]["params"]["voicePrompt"],
            audio["metadata"]["prompt"],
        )
        self.assertFalse(audio["metadata"].get("autoPlaceholder"))

    def test_audio_prompt_uses_grammatical_age_phrases(self):
        range_prompt = import_feed._compose_audio_prompt(
            "cold, oily, command tone",
            "A male voice, cold and measured.",
            "male",
            "48~54",
            "standard",
        )
        single_prompt = import_feed._compose_audio_prompt(
            "low, restrained male voice",
            "A male voice, low and restrained.",
            "male",
            "42",
            "standard",
        )

        self.assertIn("A male aged 48-54", range_prompt)
        self.assertIn("A male aged 42", single_prompt)
        self.assertNotIn("in his 48", range_prompt)
        self.assertNotIn("in his 42", single_prompt)
        self.assertNotIn("42-year-old male", single_prompt)

    def test_voice_catalog_rebuilds_missing_age_identity(self):
        feed = {
            "title": "通用音色自愈测试",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [],
            "voice_prompts": [{
                "code": "图8",
                "name": "周野",
                "format": "audio-prompt-v1",
                "prompt": (
                    "A low, restrained male voice with measured pacing. "
                    "speaking standard Mandarin, short drama dubbing reference clip, "
                    "4.5 seconds clean voice, no background noise."
                ),
                "description": "A low, restrained male voice with measured pacing.",
                "label": "low restrained male voice",
                "age": "31",
                "gender": "male",
                "dialect": "standard",
            }],
            "segments": [{
                "no": 1,
                "name": "段1",
                "raw": (
                    "【核心人物】@角色｜周野（图8）\n"
                    "台词：周野（平静）：\"开始。\""
                ),
            }],
        }

        payload = import_feed.build_payload(feed)
        audio = next(node for node in payload["nodes"] if node.get("type") == "audio")
        self.assertIn("A male aged 31", audio["metadata"]["prompt"])
        self.assertNotIn("speaking standard Mandarin", audio["metadata"]["prompt"].split("A male aged 31", 1)[0])

    def test_attach_copied_node_to_current_episode(self):
        payload = {
            "episodeId": "EP002",
            "episodeTitle": "第02集",
            "episodeImports": [
                {"id": "EP001", "nodeIds": ["old-video"], "videoNodeIds": ["old-video"]},
                {"id": "EP002", "nodeIds": ["video-2"], "videoNodeIds": ["video-2"]},
            ],
            "episodeLayouts": {"EP001": {}, "EP002": {}},
            "nodes": [
                {"id": "old-video", "type": "video", "position": {"x": 0, "y": 0}},
                {"id": "video-2", "type": "video", "position": {"x": 0, "y": 0}},
                {"id": "m-copy-1", "type": "video", "position": {"x": 312, "y": 88}},
            ],
        }

        added = import_feed.attach_nodes_to_episode(payload, "EP002", ["m-copy-1"])

        self.assertEqual(added, 1)
        group = next(item for item in payload["episodeImports"] if item["id"] == "EP002")
        self.assertEqual(group["nodeIds"], ["video-2", "m-copy-1"])
        self.assertEqual(group["videoNodeIds"], ["video-2", "m-copy-1"])
        self.assertEqual(
            payload["episodeLayouts"]["EP002"]["m-copy-1"],
            {"x": 312.0, "y": 88.0},
        )
        self.assertNotIn("m-copy-1", payload["episodeImports"][0]["nodeIds"])

    def test_generic_character_names_and_canvas_metadata(self):
        feed = {
            "title": "通用角色兼容测试",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.5"},
            "canvas_contract": {
                "schema": "drama-canvas-feed/v1",
                "asset_aspect": "16:9",
                "video_aspect": "9:16",
                "video_model": "doubao-seedance-2-5",
            },
            "asset_prompts": [
                {
                    "code": "图10",
                    "name": "王五（角色立绘）",
                    "raw": "整板固定 16:9 横版；一名 30~35 岁男性。"
                           "**① Voice Description 完整版（不进图 · 纯文本交付）**: low calm voice. "
                           "**② Voice Description Label 速览标签（进图 · 唯一入图文字）**: low calm voice",
                    "canvas": {
                        "kind": "character", "name": "王五",
                        "ratio": "16:9", "model": "doubao-seedream-5-0-pro",
                    },
                },
                {
                    "code": "图11",
                    "name": "旧车间（场景母版）",
                    "raw": "整板固定 16:9 横版场景母版，无人。",
                    "canvas": {
                        "kind": "scene", "name": "旧车间·母版",
                        "ratio": "16:9", "model": "doubao-seedream-5-0-pro",
                    },
                },
                {
                    "code": "图12",
                    "name": "旧车间（九宫格）",
                    "raw": "3×3 九宫格摄影机矩阵，16:9。",
                    "canvas": {
                        "kind": "scene", "name": "旧车间·九宫格",
                        "ratio": "16:9", "model": "doubao-seedream-5-0-pro",
                    },
                },
            ],
            "segments": [{
                "no": 1,
                "name": "段1 · 通用测试",
                "raw": (
                    "【画幅风格】9:16 竖屏（model=seedance-2.5）\n"
                    "【场景资产】@场景｜旧车间·母版（图11）\n"
                    "【核心人物】@角色｜王五（图10）（声线锁：low calm voice）\n"
                    "- 00:00-00:04 [镜头1] 王五开口。台词：王五（低沉）：\"你来晚了。\""
                ),
                "shots": [
                    {"no": 1, "start": 0, "end": 4, "text": "王五开口。"},
                    {"no": 2, "start": 4, "end": 8, "text": "王五转身。"},
                ],
                "canvas": {
                    "ratio": "9:16", "model": "doubao-seedance-2-5",
                    "duration": 8, "asset_codes": ["图11", "图10"],
                },
            }],
        }

        payload = import_feed.build_payload(feed)
        nodes = payload["nodes"]
        image_nodes = [node for node in nodes if node["type"] == "image"]
        video = next(node for node in nodes if node["type"] == "video")
        audio = [node for node in nodes if node["type"] == "audio"]

        self.assertEqual(len(image_nodes), 3)
        self.assertTrue(all(node["metadata"]["params"]["ratio"] == "16:9" for node in image_nodes))
        self.assertTrue(all(node["metadata"]["params"]["size"] == "1536x864" for node in image_nodes))
        self.assertTrue(all(node["metadata"]["prompt"].strip() for node in image_nodes))
        self.assertTrue(all(
            node["metadata"]["params"]["model"] == "doubao-seedream-5-0-pro"
            for node in image_nodes
        ))
        self.assertEqual(len(audio), 1)
        self.assertIn("王五", audio[0]["title"])
        self.assertEqual(audio[0]["metadata"]["content"], "")
        self.assertIn("Audio Prompt", audio[0]["metadata"]["composerContent"])
        self.assertEqual(video["metadata"]["params"]["model"], "doubao-seedance-2-5")
        self.assertEqual(video["metadata"]["params"]["duration"], 8)
        self.assertEqual(video["metadata"]["params"]["ratio"], "9:16")
        self.assertTrue(any(
            ref["label"].startswith("角色｜王五")
            for ref in video["metadata"]["params"]["refs"]
        ))
        self.assertTrue(video["metadata"]["params"]["voiceRefs"])
        self.assertFalse(any("剧本头卡" in ref["label"] for ref in video["metadata"]["refs"]))
        self.assertNotIn("@//chr/", video["metadata"]["prompt"])
        self.assertNotIn("@//scn/", video["metadata"]["prompt"])
        self.assertIn("@角色｜王五（图10）", video["metadata"]["prompt"])
        self.assertIn("@场景｜旧车间·母版（图11）", video["metadata"]["prompt"])

    def test_empty_or_unknown_model_uses_seedance_20(self):
        payload = import_feed.build_payload({
            "title": "默认模型",
            "meta": {},
            "asset_prompts": [],
            "segments": [{"no": 1, "name": "段1", "raw": "9:16", "shots": []}],
        })
        video = next(node for node in payload["nodes"] if node["type"] == "video")
        self.assertEqual(video["metadata"]["params"]["model"], "doubao-seedance-2-0")

    def test_placeholder_card_keeps_imported_asset_prompt_and_params(self):
        payload = {
            "nodes": [{
                "id": "video-1",
                "type": "video",
                "title": "段1",
                "metadata": {
                    "prompt": "画面主体：@道具｜账本（图42）被放在桌上。",
                    "params": {"model": "doubao-seedance-2-0"},
                },
            }],
            "episodeId": "EP010",
            "episodeImports": [{
                "id": "EP010",
                "nodeIds": ["video-1"],
                "videoNodeIds": ["video-1"],
            }],
        }
        asset_prompt = "图42 道具设定图：全图四格，旧皮账本，棕黑皮革，边缘磨损，真实材质。"
        import_feed._ensure_referenced_assets(payload, "EP010", [{
            "code": "图42",
            "name": "账本（道具设定图）",
            "raw": asset_prompt,
            "canvas": {
                "kind": "prop",
                "name": "账本",
                "model": "doubao-seedream-5-0-pro",
                "ratio": "16:9",
                "clarity": "2K",
                "size": "2048x1152",
                "guidance": "高",
                "watermark": False,
            },
        }])

        image = next(node for node in payload["nodes"] if node["type"] == "image")
        self.assertIn("旧皮账本", image["metadata"]["prompt"])
        self.assertEqual(image["metadata"]["params"]["model"], "doubao-seedream-5-0-pro")
        self.assertEqual(image["metadata"]["params"]["ratio"], "16:9")
        self.assertEqual(image["metadata"]["params"]["clarity"], "2K")
        self.assertEqual(image["metadata"]["params"]["size"], "2048x1152")
        self.assertEqual(image["metadata"]["params"]["guidance"], "高")

    def test_project_title_uses_episode_title_not_asset_like_top_level_title(self):
        expected = "铁西硬汉 · 第01集 · 投喂看板源（V7.4.29 · 全新重跑）"
        payload = import_feed.build_payload({
            "title": "音频｜工人甲（图5）",
            "episode_title": expected,
            "meta": {},
            "asset_prompts": [],
            "segments": [],
        })
        self.assertEqual(payload["title"], expected)
        self.assertEqual(payload["episodeTitle"], expected)
        self.assertEqual(payload["episodeImports"][0]["title"], expected)

    def test_connects_canvas_role_scene_prop_and_voice_mentions(self):
        payload = import_feed.build_payload({
            "title": "连线回归 · 第1集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [
                {"code": "图1", "name": "赵铁柱（角色立绘）", "raw": "男性角色"},
                {"code": "图2", "name": "厂区（场景母版）", "raw": "旧厂房"},
                {"code": "图3", "name": "账本（道具设定图）", "raw": "旧账本"},
            ],
            "segments": [{
                "no": 1,
                "name": "段1 · 连线",
                "raw": (
                    "@场景｜厂区·母版（图2）\n"
                    "@角色｜赵铁柱（图1）\n"
                    "@道具｜账本（图3）\n"
                    '台词：赵铁柱（低沉）："走。"'
                ),
                "shots": [],
            }],
        })
        video = next(node for node in payload["nodes"] if node["type"] == "video")
        incoming = [
            next(node for node in payload["nodes"] if node["id"] == conn["fromNodeId"])
            for conn in payload["connections"]
            if conn["toNodeId"] == video["id"]
        ]
        labels = {node["title"] for node in incoming}
        self.assertIn("角色｜赵铁柱（图1）", labels)
        self.assertIn("场景｜厂区·母版（图2）", labels)
        self.assertIn("道具｜账本（图3）", labels)
        self.assertIn("音频｜赵铁柱（图1）", labels)
        ref_pairs = {
            (ref["nodeId"], ref["label"])
            for ref in video["metadata"]["params"]["refs"]
        }
        self.assertEqual(
            ref_pairs,
            {
                (next(node["id"] for node in incoming if node["title"].startswith("角色｜")), "角色｜赵铁柱（图1）"),
                (next(node["id"] for node in incoming if node["title"].startswith("场景｜")), "场景｜厂区·母版（图2）"),
                (next(node["id"] for node in incoming if node["title"].startswith("道具｜")), "道具｜账本（图3）"),
            },
        )

    def test_synthesizes_missing_referenced_assets_and_connects_them(self):
        payload = import_feed.build_payload({
            "title": "缺资产兜底 · 第1集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [
                {"code": "图1", "name": "赵铁柱（角色立绘）", "raw": "男性角色"},
            ],
            "segments": [{
                "no": 1,
                "name": "段1 · 缺资产",
                "raw": (
                    "@场景｜厂区·母版（图2）\n"
                    "@角色｜赵铁柱（图1）\n"
                    "@道具｜账本（图3）\n"
                    '台词：赵铁柱（低沉）："走。"'
                ),
                "shots": [],
            }],
        })
        video = next(node for node in payload["nodes"] if node["type"] == "video")
        incoming_labels = {
            next(node for node in payload["nodes"] if node["id"] == conn["fromNodeId"])["title"]
            for conn in payload["connections"]
            if conn["toNodeId"] == video["id"]
        }
        self.assertIn("场景｜厂区·母版（图2）", incoming_labels)
        self.assertIn("道具｜账本（图3）", incoming_labels)
        self.assertIn("音频｜赵铁柱（图1）", incoming_labels)
        ref_labels = {
            ref["label"] for ref in video["metadata"]["params"]["refs"]
        }
        self.assertEqual(
            ref_labels,
            {"角色｜赵铁柱（图1）", "场景｜厂区·母版（图2）", "道具｜账本（图3）"},
        )

    def test_backfill_repairs_single_episode_missing_assets(self):
        payload = {
            "episodeId": "EP010",
            "nodes": [{
                "id": "video-1",
                "type": "video",
                "title": "段1 · 缺资产",
                "metadata": {
                    "prompt": (
                        "@场景｜厂区·母版（图2）\n"
                        "@角色｜赵铁柱（图1）\n"
                        "@道具｜账本（图3）\n"
                        '台词：赵铁柱（低沉）："走。"'
                    ),
                    "params": {"refs": [], "voiceRefs": [], "model": "doubao-seedance-2-0"},
                },
            }],
            "connections": [],
            "episodeImports": [{
                "id": "EP010",
                "nodeIds": ["video-1"],
                "videoNodeIds": ["video-1"],
            }],
        }
        added = import_feed.bind_shared_assets_for_all_episodes(payload)
        self.assertGreaterEqual(added, 4)
        group = payload["episodeImports"][0]
        self.assertGreaterEqual(len(group["nodeIds"]), 5)
        labels = {
            next(node for node in payload["nodes"] if node["id"] == conn["fromNodeId"])["title"]
            for conn in payload["connections"]
            if conn["toNodeId"] == "video-1"
        }
        self.assertIn("角色｜赵铁柱（图1）", labels)
        self.assertIn("场景｜厂区·母版（图2）", labels)
        self.assertIn("道具｜账本（图3）", labels)
        self.assertIn("音频｜赵铁柱（图1）", labels)

    def test_backfill_repairs_group_only_when_asset_nodes_already_exist(self):
        payload = {
            "episodeId": "EP010",
            "nodes": [
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段1 · 缺线",
                    "metadata": {
                        "prompt": "画面：赵铁柱走进厂区。",
                        "composerContent": (
                            "@角色｜赵铁柱（图1）\n"
                            "@场景｜厂区·母版（图2）\n"
                            "@道具｜账本（图3）\n"
                            '台词：赵铁柱（低沉）："走。"'
                        ),
                        "params": {"refs": [], "voiceRefs": [], "model": "doubao-seedance-2-0"},
                    },
                },
                {"id": "role-1", "type": "image", "title": "角色｜赵铁柱（图1）", "metadata": {"assetKey": "角色:赵铁柱"}},
                {"id": "scene-2", "type": "image", "title": "场景｜厂区·母版（图2）", "metadata": {"assetKey": "场景:厂区·母版"}},
                {"id": "prop-3", "type": "image", "title": "道具｜账本（图3）", "metadata": {"assetKey": "道具:账本"}},
                {"id": "voice-1", "type": "audio", "title": "音频｜赵铁柱（图1）", "metadata": {}},
            ],
            "connections": [],
            "episodeImports": [{"id": "EP010", "nodeIds": ["video-1"], "videoNodeIds": ["video-1"]}],
        }
        changed = import_feed.bind_shared_assets_for_all_episodes(payload)
        self.assertGreater(changed, 0)
        self.assertEqual(len(payload["episodeImports"][0]["nodeIds"]), 5)
        self.assertEqual(
            {(conn["fromNodeId"], conn["toNodeId"]) for conn in payload["connections"]},
            {
                ("role-1", "video-1"),
                ("scene-2", "video-1"),
                ("prop-3", "video-1"),
                ("voice-1", "video-1"),
            },
        )

    def test_duplicate_scene_placeholder_is_removed_in_favor_of_real_node(self):
        payload = {
            "nodes": [
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段1",
                    "metadata": {
                        "prompt": "@场景｜厂办门口小广场·母版（图13）",
                        "params": {
                            "model": "doubao-seedance-2-0",
                            "refs": [
                                {"nodeId": "scene-real", "label": "场景｜厂办门口小广场·母版（图13）"},
                                {"nodeId": "scene-placeholder", "label": "场景｜厂办门口小广场·母版（图13）"},
                            ],
                            "voiceRefs": [],
                        },
                    },
                },
                {
                    "id": "scene-real",
                    "type": "image",
                    "title": "场景｜厂办门口小广场·母版（图13）",
                    "metadata": {"assetKey": "场景:厂办门口小广场·母版", "status": "idle", "content": ""},
                },
                {
                    "id": "scene-placeholder",
                    "type": "image",
                    "title": "场景｜厂办门口小广场·母版（图13）",
                    "metadata": {"assetKey": "场景:厂办门口小广场·母版", "status": "idle", "content": "", "autoPlaceholder": True},
                },
            ],
            "connections": [
                {"id": "e-real", "fromNodeId": "scene-real", "toNodeId": "video-1"},
                {"id": "e-placeholder", "fromNodeId": "scene-placeholder", "toNodeId": "video-1"},
            ],
            "episodeImports": [{"id": "EP010", "nodeIds": ["video-1", "scene-real", "scene-placeholder"], "videoNodeIds": ["video-1"]}],
        }
        import_feed.bind_shared_assets_for_episode(payload, "EP010")
        self.assertEqual([node["id"] for node in payload["nodes"] if node["type"] == "image"], ["scene-real"])
        self.assertEqual(
            {(conn["fromNodeId"], conn["toNodeId"]) for conn in payload["connections"]},
            {("scene-real", "video-1")},
        )

    def test_does_not_reuse_completed_character_from_another_project(self):
        feed = {
            "title": "角色复用测试",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [{
                "code": "图1",
                "name": "赵铁柱（角色立绘）",
                "raw": "16:9 角色四视图，不另生成。",
            }],
            "segments": [{"no": 1, "name": "段1", "raw": "9:16 @赵铁柱（图1）", "shots": []}],
        }
        pool = [{
            "id": "old-character-node",
            "projectId": "old-project",
            "title": "角色｜赵铁柱（图3）",
            "content": "/uploads/zhaotiezhu.jpg",
            "status": "done",
            "params": {
                "provider": "ark", "model": "doubao-seedream-5-0-pro",
                "ratio": "16:9", "size": "1536x864",
            },
            "updatedAt": "2026-10-25T01:00:00Z",
        }]
        payload = import_feed.build_payload(
            feed,
            asset_pool=pool,
            target_project_id="new-project",
        )
        image = next(node for node in payload["nodes"] if node["type"] == "image")
        self.assertEqual(image["metadata"]["status"], "idle")
        self.assertEqual(image["metadata"]["content"], "")
        self.assertIsNone(image["metadata"].get("reusedAsset"))
        self.assertEqual(payload["importSummary"]["assetsReused"], 0)

    def test_detaches_legacy_cross_project_image(self):
        payload = {
            "nodes": [{
                "id": "image-1",
                "type": "image",
                "title": "角色｜赵铁柱（图1）",
                "metadata": {
                    "status": "done",
                    "content": "/uploads/from-other-project.png",
                    "reusedAsset": {
                        "sourceProjectId": "other-project",
                        "sourceNodeId": "source-node",
                    },
                    "params": {
                        "mediaVersions": [{"url": "/uploads/from-other-project.png"}],
                        "mediaVersionIndex": 0,
                    },
                },
            }],
        }

        changed = import_feed.detach_cross_project_reused_assets(payload, "new-project")

        image = payload["nodes"][0]["metadata"]
        self.assertEqual(changed, 1)
        self.assertEqual(image["status"], "idle")
        self.assertEqual(image["content"], "")
        self.assertNotIn("reusedAsset", image)
        self.assertNotIn("mediaVersions", image["params"])

    def test_same_project_reuses_stable_node_id(self):
        feed = {
            "title": "同项目复用",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [{"code": "图1", "name": "沈雨（角色立绘）", "raw": "16:9 角色四视图"}],
            "segments": [{"no": 1, "name": "段1", "raw": "9:16 @沈雨（图1）", "shots": []}],
        }
        pool = [{
            "id": "stable-node-id",
            "projectId": "same-project",
            "title": "角色｜沈雨（图1）",
            "content": "/uploads/shenyu.jpg",
            "status": "done",
            "params": {"ratio": "16:9", "size": "1536x864"},
            "updatedAt": "2026-10-25T02:00:00Z",
        }]
        payload = import_feed.build_payload(feed, asset_pool=pool, target_project_id="same-project")
        image = next(node for node in payload["nodes"] if node["type"] == "image")
        self.assertEqual(image["id"], "stable-node-id")
        self.assertEqual(image["metadata"]["status"], "done")
        self.assertEqual(payload["importSummary"]["assetsReused"], 1)

    def test_same_project_reuse_keeps_first_locked_figure_identity(self):
        feed = {
            "title": "同角色改图号测试",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [{
                "code": "图27",
                "name": "钱万金（角色立绘）",
                "raw": "### 图27 · 钱万金（角色立绘）",
            }],
            "segments": [{
                "no": 1,
                "name": "段1 · 对账",
                "raw": "@//chr/- 钱万金（图27）走进办公室。",
                "shots": [],
            }],
        }
        pool = [{
            "id": "qian-node",
            "projectId": "same-project",
            "title": "角色｜钱万金（图27）",
            "prompt": "### 图2 · 钱万金（角色立绘）\n\n首次锁定角色。",
            "composerContent": "### 图2 · 钱万金（角色立绘）\n\n首次锁定角色。",
            "content": "/uploads/qian.jpg",
            "status": "done",
            "params": {"ratio": "16:9", "size": "1536x864"},
            "reusedAsset": {"sourceTitle": "角色｜钱万金（图2）"},
            "updatedAt": "2026-10-25T02:00:00Z",
        }]

        payload = import_feed.build_payload(
            feed,
            asset_pool=pool,
            target_project_id="same-project",
        )
        image = next(node for node in payload["nodes"] if node["type"] == "image")
        video = next(node for node in payload["nodes"] if node["type"] == "video")
        self.assertEqual(image["id"], "qian-node")
        self.assertEqual(image["title"], "角色｜钱万金（图2）")
        self.assertIn("图2", image["metadata"]["prompt"])
        self.assertIn("@角色｜钱万金（图2）", video["metadata"]["prompt"])
        self.assertEqual(
            image["metadata"]["reusedAsset"]["sourceTitle"],
            "角色｜钱万金（图2）",
        )
        self.assertIn(
            "首次锁定角色",
            image["metadata"]["reusedAsset"]["sourcePrompt"],
        )

    def test_voice_reuse_is_keyed_by_role_not_changed_figure_code(self):
        first = import_feed.build_payload({
            "title": "铁西硬汉 · 第01集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [{
                "code": "图2",
                "name": "钱万金（角色立绘）",
                "raw": "男性角色。",
            }],
            "segments": [{
                "no": 1,
                "name": "段1 · 第一集",
                "raw": '@//chr/- 钱万金（图2）。台词：钱万金（冷）："说。"',
                "shots": [],
            }],
        })
        second = import_feed.build_payload({
            "title": "铁西硬汉 · 第07集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [{
                "code": "图27",
                "name": "钱万金（角色立绘）",
                "raw": "男性角色。",
            }],
            "segments": [{
                "no": 1,
                "name": "段1 · 第七集",
                "raw": '@//chr/- 钱万金（图27）。台词：钱万金（冷）："改。"',
                "shots": [],
            }],
        })
        merged = import_feed.merge_payload_by_episode(first, second)
        ep7 = next(group for group in merged["episodeImports"] if group["id"] == "EP007")
        nodes = {node["id"]: node for node in merged["nodes"]}
        voice_titles = [
            nodes[node_id]["title"]
            for node_id in ep7["nodeIds"]
            if nodes[node_id]["type"] == "audio" and "钱万金" in nodes[node_id]["title"]
        ]
        self.assertEqual(voice_titles, ["音频｜钱万金（图2）"])

    def test_repairs_import_placeholders_with_and_without_figure_numbers(self):
        refs = [
            {"nodeId": "scene-1", "label": "场景｜旧车间·母版（图11）"},
            {"nodeId": "char-1", "label": "角色｜王五（图10）"},
        ]
        prompt = (
            "【场景资产】@//scn/ - 旧车间（母版+九宫格双挂）\n"
            "【核心人物】@//chr/ - 王五（图10，声线锁：low calm voice）\n"
            "【时间轴】画面主体：王五（图10）站在旧车间（图11）门口。"
        )
        repaired = import_feed.repair_prompt_mentions(prompt, refs)
        self.assertIn("@场景｜旧车间·母版（图11）（母版）", repaired)
        self.assertIn("@角色｜王五（图10）（声线锁：low calm voice）", repaired)
        self.assertIn("画面主体：@角色｜王五（图10）站在@场景｜旧车间·母版（图11）门口", repaired)
        self.assertNotIn("@角色｜@角色｜", repaired)
        self.assertNotIn("@//", repaired)

    def test_repairs_short_scene_alias_alongside_canonical_variant(self):
        refs = [
            {"nodeId": "scene-12", "label": "场景｜早点摊·母版（图12）"},
            {"nodeId": "scene-120", "label": "场景｜夜宵摊·母版（图120）"},
        ]
        prompt = (
            "@场景｜早点摊（图12）与@场景｜夜宵摊（图120）不是同一个场景。\n"
            "完整引用：@场景｜早点摊·母版（图12）"
        )
        repaired = import_feed.repair_prompt_mentions(prompt, refs)

        self.assertEqual(repaired.count("@场景｜早点摊·母版（图12）"), 2)
        self.assertNotIn("@场景｜早点摊（图12）", repaired)
        self.assertIn("@场景｜夜宵摊·母版（图120）", repaired)
        self.assertEqual(
            import_feed.repair_prompt_mentions(repaired, refs),
            repaired,
        )

    def test_preserves_angle_bracket_mapping_labels_without_at_prefix(self):
        refs = [
            {"nodeId": "scene-10", "label": "场景｜厂区后街·母版（图10）"},
        ]
        prompt = (
            "<@场景｜厂区后街·母版（图10）> "
            "对应@场景｜厂区后街·母版（图10），仅采用空间布局。"
        )
        repaired = import_feed.repair_prompt_mentions(prompt, refs)

        self.assertTrue(repaired.startswith("<场景｜厂区后街·母版（图10）>"))
        self.assertNotIn("<@场景", repaired)

    def test_explicit_at_reference_does_not_pull_short_name_into_refs(self):
        payload = import_feed.build_payload({
            "title": "引用边界 · 第01集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [
                {
                    "code": "图10",
                    "name": "厂区后街（场景母版）",
                    "raw": "厂区后街场景。",
                    "canvas": {"kind": "scene", "name": "厂区后街·母版"},
                },
                {
                    "code": "图11",
                    "name": "名册（道具设定图）",
                    "raw": "名册道具。",
                    "canvas": {"kind": "prop", "name": "名册"},
                },
            ],
            "segments": [{
                "no": 1,
                "name": "段1 · 引用边界",
                "raw": (
                    "<厂区后街> 对应@场景｜厂区后街·母版（图10），"
                    "仅采用空间布局与建筑。\n"
                    "道具｜名册（图11）只是说明，不是图片引用。"
                ),
            }],
        })
        video = next(node for node in payload["nodes"] if node["type"] == "video")
        refs = video["metadata"]["params"]["refs"]
        self.assertEqual(
            {ref["label"] for ref in refs},
            {"场景｜厂区后街·母版（图10）"},
        )

    def test_explicit_reference_in_one_segment_does_not_leak_into_another(self):
        payload = import_feed.build_payload({
            "title": "引用边界 · 第01集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [
                {
                    "code": "图10",
                    "name": "厂区后街（场景母版）",
                    "raw": "厂区后街场景。",
                    "canvas": {"kind": "scene", "name": "厂区后街·母版"},
                },
                {
                    "code": "图11",
                    "name": "名册（道具设定图）",
                    "raw": "名册道具。",
                    "canvas": {"kind": "prop", "name": "名册"},
                },
            ],
            "segments": [
                {
                    "no": 1,
                    "name": "段1 · 只提到裸文本",
                    "raw": (
                        "@场景｜厂区后街（图10）\n"
                        "道具｜名册（图11）只是说明，没有 @。"
                    ),
                },
                {
                    "no": 2,
                    "name": "段2 · 显式引用",
                    "raw": "@道具｜名册（图11）被角色拿起。",
                },
            ],
        })
        videos = {
            node["title"]: node
            for node in payload["nodes"]
            if node["type"] == "video"
        }
        seg1_refs = videos["段1 · 只提到裸文本"]["metadata"]["params"]["refs"]
        seg2_refs = videos["段2 · 显式引用"]["metadata"]["params"]["refs"]
        self.assertEqual(
            {ref["label"] for ref in seg1_refs},
            {"场景｜厂区后街·母版（图10）"},
        )
        self.assertIn("道具｜名册（图11）", {ref["label"] for ref in seg2_refs})

    def test_reimport_removes_stale_extra_reference_and_edge(self):
        feed = {
            "title": "引用边界 · 第01集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [
                {
                    "code": "图10",
                    "name": "厂区后街（场景母版）",
                    "raw": "厂区后街场景。",
                    "canvas": {"kind": "scene", "name": "厂区后街·母版"},
                },
                {
                    "code": "图11",
                    "name": "名册（道具设定图）",
                    "raw": "名册道具。",
                    "canvas": {"kind": "prop", "name": "名册"},
                },
            ],
            "segments": [{
                "no": 1,
                "name": "段1 · 引用边界",
                "raw": (
                    "@场景｜厂区后街（图10）\n"
                    "道具｜名册（图11）只是说明，没有 @。"
                ),
            }],
        }
        first = import_feed.build_payload(feed)
        video = next(node for node in first["nodes"] if node["type"] == "video")
        stale_source = next(
            node for node in first["nodes"]
            if node["type"] == "image" and "图11" in node["title"]
        )
        video["metadata"]["params"]["refs"].append({
            "nodeId": stale_source["id"],
            "label": stale_source["title"],
        })
        first["connections"].append({
            "id": "edge-stale-11",
            "fromNodeId": stale_source["id"],
            "toNodeId": video["id"],
        })

        reimported = import_feed.merge_payload_by_episode(
            first,
            import_feed.build_payload(feed),
        )
        merged_video = next(
            node for node in reimported["nodes"]
            if node["type"] == "video"
        )
        refs = merged_video["metadata"]["params"]["refs"]
        incoming = [
            edge for edge in reimported["connections"]
            if edge["toNodeId"] == merged_video["id"]
        ]

        self.assertEqual(
            {ref["label"] for ref in refs},
            {"场景｜厂区后街·母版（图10）"},
        )
        self.assertNotIn("edge-stale-11", {edge["id"] for edge in incoming})

    def test_repairs_existing_payload_idempotently(self):
        payload = {
            "nodes": [{
                "id": "video-1",
                "type": "video",
                "metadata": {
                    "prompt": (
                        "@//chr/ - 王五（图10，声线锁：low calm voice）\n"
                        "画面主体：王五（图10）"
                    ),
                    "composerContent": "@//chr/ - 王五（图10，声线锁：low calm voice）",
                    "params": {"refs": [{"nodeId": "char-1", "label": "角色｜王五（图10）"}]},
                },
            }]
        }
        self.assertEqual(import_feed.repair_payload_prompt_mentions(payload), 1)
        self.assertEqual(
            payload["nodes"][0]["metadata"]["prompt"],
            (
                "@角色｜王五（图10）（声线锁：low calm voice）\n"
                "画面主体：@角色｜王五（图10）"
            ),
        )
        self.assertEqual(import_feed.repair_payload_prompt_mentions(payload), 0)

    def test_repairs_figure_aliases_and_chinese_inline_asset_names(self):
        refs = [
            {"nodeId": "qian", "label": "角色｜钱万金（图2）"},
            {"nodeId": "ledger", "label": "道具｜账本（图18）"},
            {"nodeId": "note", "label": "道具｜匿名纸条（图32）"},
            {"nodeId": "he", "label": "角色｜郝秀兰（图9）"},
        ]
        prompt = (
            "画面主体：钱万金（图27）打开保险柜，抽出账本（图18）。\n"
            "钱万金把纸条（图32）压进账本。\n"
            "郝秀兰（图9·画右·在场）进了屋。\n"
            "站位声明：@钱万金（图27）（保险柜前）\n"
            "短引用：@钱万金（画面右）\n"
            "核心人物：@//chr/ - @角色｜钱万金（图2）（图27，声线锁：cold）"
        )
        repaired = import_feed.repair_prompt_mentions(prompt, refs)
        self.assertEqual(
            repaired,
            (
                "画面主体：@角色｜钱万金（图2）打开保险柜，抽出"
                "@道具｜账本（图18）。\n"
                "钱万金把@道具｜匿名纸条（图32）压进账本。\n"
                "@角色｜郝秀兰（图9）（画右·在场）进了屋。\n"
                "站位声明：@角色｜钱万金（图2）（保险柜前）\n"
                "短引用：@角色｜钱万金（图2）（画面右）\n"
                "核心人物：@角色｜钱万金（图2）（图2，声线锁：cold）"
            ),
        )
        self.assertEqual(import_feed.repair_prompt_mentions(repaired, refs), repaired)

    def test_binds_asset_by_unique_figure_when_prompt_uses_short_alias(self):
        payload = {
            "nodes": [{
                "id": "video-1",
                "type": "video",
                "title": "段8 · 黑影",
                "metadata": {
                    "prompt": "赵铁柱把@道具｜匿名纸条（图32）压进账本。",
                    "params": {"model": "doubao-seedance-2-0", "refs": [], "voiceRefs": []},
                },
            }, {
                "id": "note-32",
                "type": "image",
                "title": "道具｜匿名纸条（图32）",
                "metadata": {"assetKey": "prop:匿名纸条:default", "status": "idle", "content": ""},
            }],
            "connections": [],
            "episodeImports": [{
                "id": "EP009",
                "nodeIds": ["video-1", "note-32"],
                "videoNodeIds": ["video-1"],
            }],
        }
        import_feed.bind_shared_assets_for_episode(payload, "EP009")
        import_feed.repair_payload_prompt_mentions(payload)
        video = payload["nodes"][0]
        refs = video["metadata"]["params"]["refs"]
        self.assertEqual([ref["nodeId"] for ref in refs], ["note-32"])
        self.assertIn("@道具｜匿名纸条（图32）", video["metadata"]["prompt"])

    def test_repairs_duplicate_figure_suffix(self):
        refs = [{"nodeId": "char-1", "label": "角色｜赵铁柱（图1）"}]
        prompt = "画面主体：@角色｜赵铁柱（图1）（图1）把存折压回账本。"
        repaired = import_feed.repair_prompt_mentions(prompt, refs)
        self.assertEqual(
            repaired,
            "画面主体：@角色｜赵铁柱（图1）把存折压回账本。",
        )
        self.assertEqual(import_feed.repair_prompt_mentions(repaired, refs), repaired)

    def test_inlines_audio_references_into_video_prompt(self):
        payload = {
            "nodes": [
                {
                    "id": "role-a",
                    "type": "image",
                    "title": "角色｜工人甲（图5）",
                    "metadata": {"status": "done", "content": "x"},
                },
                {
                    "id": "role-b",
                    "type": "image",
                    "title": "角色｜陈大强（图4）",
                    "metadata": {"status": "done", "content": "x"},
                },
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段1 · 围门",
                    "metadata": {
                        "prompt": (
                            "【核心人物】@角色｜工人甲（图5）；@角色｜陈大强（图4）\n\n"
                            "【站位声明】@角色｜工人甲（图5）；@角色｜陈大强（图4）\n\n"
                            '台词：工人甲（扬声）："钱呢？"\n'
                            '台词：陈大强（低沉）："别急。"\n\n【音效】环境音：人群底噪'
                        ),
                        "composerContent": (
                            "【核心人物】@角色｜工人甲（图5）；@角色｜陈大强（图4）\n\n"
                            "【站位声明】@角色｜工人甲（图5）；@角色｜陈大强（图4）\n\n"
                            '台词：工人甲（扬声）："钱呢？"\n'
                            '台词：陈大强（低沉）："别急。"\n\n【音效】环境音：人群底噪'
                        ),
                        "params": {
                            "refs": [],
                            "voiceRefs": [
                                {"nodeId": "audio-1", "label": "音频｜工人甲（图5）"},
                                {"nodeId": "audio-2", "label": "音频｜陈大强（图4）"},
                            ],
                        },
                    },
                },
            ],
        }
        self.assertEqual(import_feed.repair_payload_prompt_mentions(payload), 1)
        meta = payload["nodes"][-1]["metadata"]
        expected = "【音色引用】@音频｜工人甲（图5）；@音频｜陈大强（图4）"
        self.assertIn(expected, meta["prompt"])
        self.assertIn(expected, meta["composerContent"])
        self.assertLess(meta["prompt"].index("【核心人物】"), meta["prompt"].index(expected))
        self.assertLess(meta["prompt"].index(expected), meta["prompt"].index("【站位声明】"))
        self.assertEqual(import_feed.repair_payload_prompt_mentions(payload), 0)

    def test_removes_model_marker_from_canvas_prompt(self):
        prompt = "【画幅风格】9:16 竖屏（model=seedance-2.0）\n画面主体：赵铁柱进门。"
        repaired = import_feed.repair_prompt_mentions(prompt, [])
        self.assertNotIn("model=", repaired)
        self.assertIn("【画幅风格】9:16 竖屏", repaired)

    def test_reuses_idle_image_from_same_project_but_not_from_other_projects(self):
        feed = {
            "title": "铁西硬汉 · 第09集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [{
                "code": "图32",
                "name": "匿名纸条（道具设定图）",
                "raw": "打印纸条。",
            }],
            "segments": [{
                "no": 1,
                "name": "段1 · 匿名条",
                "raw": "门缝里塞进来一张@道具｜匿名纸条（图32）。",
                "shots": [],
            }],
        }
        asset = {
            "id": "stable-note",
            "projectId": "target-project",
            "title": "道具｜匿名纸条（图32）",
            "content": "",
            "status": "idle",
        }
        same_project = import_feed.build_payload(
            feed,
            asset_pool=[asset],
            target_project_id="target-project",
        )
        self.assertEqual(
            next(
                node["id"] for node in same_project["nodes"]
                if node.get("title") == "道具｜匿名纸条（图32）"
            ),
            "stable-note",
        )
        other_project = import_feed.build_payload(
            feed,
            asset_pool=[asset],
            target_project_id="another-project",
        )
        self.assertNotEqual(
            next(
                node["id"] for node in other_project["nodes"]
                if node.get("title") == "道具｜匿名纸条（图32）"
            ),
            "stable-note",
        )

    def test_import_summary_prefers_generated_asset_and_counts_reuse(self):
        feed = {
            "title": "铁西硬汉 · 第09集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [{
                "code": "图9",
                "name": "郝秀兰（角色立绘）",
                "raw": "女性角色。",
            }],
            "segments": [{
                "no": 1,
                "name": "段1 · 饭馆",
                "raw": "郝秀兰站在饭馆门口。",
                "shots": [],
            }],
        }
        payload = import_feed.build_payload(
            feed,
            asset_pool=[{
                "id": "generated-he-xiulan",
                "projectId": "target-project",
                "title": "角色｜郝秀兰（图9）",
                "content": "/uploads/he-xiulan.png",
                "status": "done",
                "updatedAt": "2026-10-25T01:00:00Z",
            }, {
                "id": "idle-he-xiulan",
                "projectId": "target-project",
                "title": "角色｜郝秀兰（图9）",
                "content": "",
                "status": "idle",
                "updatedAt": "2026-10-26T01:00:00Z",
            }],
            target_project_id="target-project",
        )
        node = next(
            item for item in payload["nodes"]
            if item.get("type") == "image" and item.get("title") == "角色｜郝秀兰（图9）"
        )
        self.assertEqual(node["id"], "generated-he-xiulan")
        self.assertEqual(payload["importSummary"]["assetsReused"], 1)
        self.assertEqual(payload["importSummary"]["assetsReusedGenerated"], 1)

    def test_merge_episode_imports_preserves_previous_episode(self):
        def feed(episode, title):
            return {
                "title": f"铁西硬汉 · 第{episode:02d}集 · 测试",
                "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
                "asset_prompts": [],
                "segments": [{
                    "no": 1,
                    "name": title,
                    "raw": f"9:16 第{episode}集测试画面",
                    "shots": [],
                }],
            }

        first = import_feed.build_payload(feed(1, "段1 · 第一集"))
        second = import_feed.build_payload(feed(2, "段1 · 第二集"))
        merged = import_feed.merge_payload_by_episode(first, second)
        videos = [node for node in merged["nodes"] if node["type"] == "video"]
        self.assertEqual([group["id"] for group in merged["episodeImports"]], ["EP001", "EP002"])
        self.assertEqual({node["title"] for node in videos}, {"段1 · 第一集", "段1 · 第二集"})
        self.assertEqual(len(merged["connections"]), 0)

        replacement = import_feed.build_payload(feed(2, "段1 · 第二集重导"))
        ep2_before = next(
            group for group in merged["episodeImports"]
            if group["id"] == "EP002"
        )
        ep2_before_video_id = ep2_before["videoNodeIds"][0]
        ep2_before_text_ids = {
            node["id"] for node in merged["nodes"]
            if node["type"] == "text"
            and str(((node.get("metadata") or {}).get("episodeId")) or "").upper() == "EP002"
        }
        replaced = import_feed.merge_payload_by_episode(merged, replacement)
        replaced_videos = [node for node in replaced["nodes"] if node["type"] == "video"]
        self.assertIn("段1 · 第一集", {node["title"] for node in replaced_videos})
        self.assertIn("段1 · 第二集重导", {node["title"] for node in replaced_videos})
        self.assertNotIn("段1 · 第二集", {node["title"] for node in replaced_videos})
        self.assertEqual(len(replaced["episodeImports"]), 2)
        ep2_after = next(
            group for group in replaced["episodeImports"]
            if group["id"] == "EP002"
        )
        self.assertEqual(ep2_after["videoNodeIds"], [ep2_before_video_id])
        self.assertEqual(
            {
                node["id"] for node in replaced["nodes"]
                if node["type"] == "text"
                and str(((node.get("metadata") or {}).get("episodeId")) or "").upper() == "EP002"
            },
            ep2_before_text_ids,
        )

    def test_same_episode_reimport_preserves_node_edge_and_media_identity(self):
        feed = {
            "title": "铁西硬汉 · 第09集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [{
                "code": "图32",
                "name": "匿名纸条（道具设定图）",
                "raw": "打印纸条。",
            }],
            "segments": [{
                "no": 1,
                "name": "段1 · 匿名条",
                "raw": '赵铁柱拿起@道具｜匿名纸条（图32）。台词：赵铁柱（低沉）："走。"',
                "shots": [],
            }],
        }
        first = import_feed.build_payload(feed)
        import_feed.bind_shared_assets_for_episode(first, "EP001")
        first_episode = first["episodeImports"][0]
        video_id = first_episode["videoNodeIds"][0]
        first_by_id = {node["id"]: node for node in first["nodes"]}
        image = next(node for node in first["nodes"] if node["type"] == "image")
        video = first_by_id[video_id]
        image_ref = {"nodeId": image["id"], "label": image["title"]}
        video["metadata"]["params"]["refs"] = [image_ref]
        video["metadata"]["refs"] = [image_ref]
        if not any(
            conn["fromNodeId"] == image["id"] and conn["toNodeId"] == video_id
            for conn in first["connections"]
        ):
            edge = {
                "fromNodeId": image["id"],
                "id": "edge-stable-note-video",
                "toNodeId": video_id,
            }
            first["connections"].append(edge)
            first_episode["connectionIds"].append(edge["id"])
        first_by_id[video_id]["metadata"].update({
            "content": "/uploads/generated-test.mp4",
            "status": "done",
            "params": {
                **first_by_id[video_id]["metadata"]["params"],
                "videoVersions": [{"content": "/uploads/generated-test.mp4"}],
                "videoVersionIndex": 0,
            },
        })
        before_node_ids = {
            (node.get("type"), node.get("title")): node.get("id")
            for node in first["nodes"]
            if node.get("type") in {"image", "text", "video"}
        }
        before_edge_ids = {
            (conn["fromNodeId"], conn["toNodeId"]): conn["id"]
            for conn in first["connections"]
        }

        reimported = import_feed.merge_payload_by_episode(
            first,
            import_feed.build_payload(feed),
        )
        after_node_ids = {
            (node.get("type"), node.get("title")): node.get("id")
            for node in reimported["nodes"]
            if node.get("type") in {"image", "text", "video"}
        }
        after_edge_ids = {
            (conn["fromNodeId"], conn["toNodeId"]): conn["id"]
            for conn in reimported["connections"]
        }
        video = next(node for node in reimported["nodes"] if node["id"] == video_id)
        self.assertEqual(after_node_ids, before_node_ids)
        self.assertEqual(after_edge_ids, before_edge_ids)
        self.assertEqual(video["metadata"]["content"], "/uploads/generated-test.mp4")
        self.assertEqual(video["metadata"]["status"], "done")
        self.assertGreaterEqual(
            int(reimported.get("importSummary", {}).get("assetsReused") or 0),
            1,
        )
        self.assertEqual(
            video["metadata"]["params"]["videoVersions"],
            [{"content": "/uploads/generated-test.mp4"}],
        )

    def test_same_episode_reimport_keeps_segment_chain_edges(self):
        def ordered_video_ids(payload):
            group = next(
                item for item in payload["episodeImports"]
                if item["id"] == "EP001"
            )
            nodes = {node["id"]: node for node in payload["nodes"]}
            return sorted(
                group["videoNodeIds"],
                key=lambda node_id: int(
                    re.search(r"段\s*(\d+)", nodes[node_id]["title"]).group(1)
                ),
            )

        feed = {
            "title": "重导连线 · 第01集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [],
            "segments": [
                {"no": 1, "name": "段1", "raw": "段1画面。"},
                {"no": 2, "name": "段2", "raw": "段2画面。"},
                {"no": 3, "name": "段3", "raw": "段3画面。"},
            ],
        }
        first = import_feed.build_payload(feed)
        first_video_ids = ordered_video_ids(first)
        merged = import_feed.merge_payload_by_episode(
            first,
            import_feed.build_payload(feed),
        )
        second_video_ids = ordered_video_ids(merged)
        chain_pairs = {
            (video_ids[index], video_ids[index + 1])
            for video_ids in (first_video_ids, second_video_ids)
            for index in range(len(video_ids) - 1)
        }
        actual_pairs = {
            (edge["fromNodeId"], edge["toNodeId"])
            for edge in merged["connections"]
            if edge["fromNodeId"] in second_video_ids
            and edge["toNodeId"] in second_video_ids
        }

        self.assertEqual(second_video_ids, first_video_ids)
        self.assertEqual(actual_pairs, chain_pairs)
        self.assertEqual(
            ordered_video_ids(merged),
            [
                node["id"]
                for node in merged["nodes"]
                if node["type"] == "video"
            ],
        )

    def test_same_episode_reimport_remaps_shared_image_refs_to_existing_nodes(self):
        feed = {
            "title": "共享资产重导 · 第01集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [{
                "code": "图1",
                "name": "赵铁柱（角色立绘）",
                "raw": "赵铁柱。",
                "canvas": {"kind": "character", "name": "赵铁柱"},
            }],
            "segments": [{
                "no": 1,
                "name": "段1",
                "raw": "@角色｜赵铁柱（图1）站在门口。",
                "shots": [],
            }],
        }
        first = import_feed.build_payload(feed)
        image = next(node for node in first["nodes"] if node["type"] == "image")
        video = next(node for node in first["nodes"] if node["type"] == "video")
        image_id = image["id"]
        video["metadata"]["params"]["refs"] = [{
            "nodeId": image_id,
            "label": image["title"],
        }]
        first["connections"] = [{
            "id": "edge-shared-image",
            "fromNodeId": image_id,
            "toNodeId": video["id"],
        }]
        incoming = import_feed.build_payload(feed)
        incoming_image = next(
            node for node in incoming["nodes"]
            if node["type"] == "image"
        )
        self.assertNotEqual(incoming_image["id"], image_id)

        merged = import_feed.merge_payload_by_episode(first, incoming)
        merged_video = next(
            node for node in merged["nodes"]
            if node["type"] == "video"
        )
        refs = merged_video["metadata"]["params"]["refs"]
        node_ids = {node["id"] for node in merged["nodes"]}

        self.assertIn(image_id, node_ids)
        self.assertEqual(
            [ref["nodeId"] for ref in refs],
            [image_id],
        )
        self.assertTrue(
            any(
                edge["fromNodeId"] == image_id
                and edge["toNodeId"] == merged_video["id"]
                for edge in merged["connections"]
            )
        )

    def test_second_episode_carries_only_mentioned_shared_assets_and_links(self):
        first = import_feed.build_payload({
            "title": "铁西硬汉 · 第01集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [{
                "code": "图1",
                "name": "赵铁柱（角色立绘）",
                "raw": "男性角色。台词：赵铁柱（低沉）：\"走。\"",
            }],
            "segments": [{
                "no": 1,
                "name": "段1 · 第一集",
                "raw": '@角色｜赵铁柱（图1）站在门口。台词：赵铁柱（低沉）："走。"',
                "shots": [],
            }],
        })
        second_feed = {
            "title": "铁西硬汉 · 第02集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [{
                "code": "图2",
                "name": "郝秀兰（角色立绘）",
                "raw": "女性角色。台词：郝秀兰（温和）：\"好。\"",
            }],
            "segments": [{
                "no": 1,
                "name": "段1 · 第二集",
                "raw": (
                    '@角色｜赵铁柱（图1）站在门口。台词：赵铁柱（低沉）："走。"'
                    '郝秀兰点头。台词：郝秀兰（温和）："好。"'
                ),
                "shots": [],
            }],
        }
        second = import_feed.build_payload(second_feed)
        merged = import_feed.merge_payload_by_episode(first, second)
        ep2 = next(group for group in merged["episodeImports"] if group["id"] == "EP002")
        nodes_by_id = {node["id"]: node for node in merged["nodes"]}
        video = next(nodes_by_id[node_id] for node_id in ep2["videoNodeIds"])
        role_refs = [ref for ref in video["metadata"]["params"]["refs"] if ref["label"].startswith("角色｜赵铁柱")]
        voice_refs = [ref for ref in video["metadata"]["params"].get("voiceRefs", []) if ref["label"].startswith("音频｜赵铁柱")]
        self.assertEqual(len(role_refs), 1)
        self.assertEqual(len(voice_refs), 1)
        self.assertIn("@角色｜赵铁柱（图1）", video["metadata"]["prompt"])
        self.assertIn(role_refs[0]["nodeId"], ep2["nodeIds"])
        self.assertIn(voice_refs[0]["nodeId"], ep2["nodeIds"])
        self.assertTrue(any(
            conn["fromNodeId"] == role_refs[0]["nodeId"] and conn["toNodeId"] == video["id"]
            for conn in merged["connections"]
        ))

        reimported = import_feed.merge_payload_by_episode(
            merged,
            import_feed.build_payload(second_feed),
        )
        ep2_reimported = next(
            group for group in reimported["episodeImports"]
            if group["id"] == "EP002"
        )
        reimported_nodes = {
            node["id"]: node for node in reimported["nodes"]
        }
        reimported_video = next(
            reimported_nodes[node_id]
            for node_id in ep2_reimported["videoNodeIds"]
        )
        reimported_voices = (
            reimported_video["metadata"]["params"].get("voiceRefs") or []
        )
        self.assertEqual(len(reimported["nodes"]), len(merged["nodes"]))
        self.assertEqual(
            {ref["label"] for ref in reimported_voices},
            {"音频｜赵铁柱（图1）", "音频｜郝秀兰（图2）"},
        )
        self.assertTrue(all(
            ref["nodeId"] in ep2_reimported["nodeIds"]
            for ref in reimported_voices
        ))

    def test_merge_repairs_referenced_voice_missing_from_episode_group(self):
        existing = {
            "episodeId": "EP004",
            "nodes": [
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段1",
                    "metadata": {
                        "params": {
                            "refs": [],
                            "voiceRefs": [
                                {"nodeId": "audio-shared", "label": "音频｜赵铁柱（图1）"},
                            ],
                        },
                    },
                },
                {
                    "id": "audio-shared",
                    "type": "audio",
                    "title": "音频｜赵铁柱（图1）",
                    "metadata": {"prompt": "low steady voice"},
                },
            ],
            "episodeImports": [{
                "id": "EP004",
                "nodeIds": ["video-1"],
                "videoNodeIds": ["video-1"],
            }],
        }
        incoming = {
            **existing,
            "nodes": [existing["nodes"][0]],
            "episodeImports": [{
                "id": "EP004",
                "nodeIds": ["video-1"],
                "videoNodeIds": ["video-1"],
            }],
        }

        merged = import_feed.merge_payload_by_episode(existing, incoming)
        group = merged["episodeImports"][0]

        self.assertIn("audio-shared", group["nodeIds"])

    def test_scene_master_and_grid_collapse_to_master_and_reimport_is_idempotent(self):
        def feed():
            return {
                "title": "铁西硬汉 · 第02集",
                "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
                "asset_prompts": [
                    {
                        "code": "图10",
                        "name": "厂区后街（场景母版）",
                        "raw": "厂区后街母版。",
                        "canvas": {
                            "code": "图10",
                            "kind": "scene",
                            "name": "厂区后街",
                            "ratio": "16:9",
                        },
                    },
                    {
                        "code": "图10",
                        "name": "厂区后街（九宫格）",
                        "raw": "厂区后街九宫格。",
                        "canvas": {
                            "code": "图10",
                            "kind": "scene",
                            "name": "厂区后街",
                            "ratio": "16:9",
                        },
                    },
                ],
                "segments": [{
                    "no": 1,
                    "name": "段1 · 街口",
                    "raw": "角色走进@//scn/- 厂区后街（图10）。",
                    "shots": [],
                }],
            }

        first = import_feed.build_payload(feed())
        image_titles = {
            node["title"]
            for node in first["nodes"]
            if node["type"] == "image"
        }
        self.assertEqual(image_titles, {"场景｜厂区后街·母版（图10）"})
        image_keys = {
            node["metadata"]["assetKey"]
            for node in first["nodes"]
            if node["type"] == "image"
        }
        self.assertEqual(len(image_keys), 1)

        first["nodes"].append({
            "height": 405, "width": 720, "id": "legacy-generic-scene",
            "metadata": {
                "assetKey": "场景:厂区后街", "content": "", "prompt": "旧导入残留",
                "status": "idle",
            },
            "position": {"x": 0, "y": 0},
            "title": "场景｜厂区后街（图10）", "type": "image",
        })
        merged = import_feed.merge_payload_by_episode(first, import_feed.build_payload(feed()))
        reimported = import_feed.merge_payload_by_episode(merged, import_feed.build_payload(feed()))
        ep2 = next(group for group in reimported["episodeImports"] if group["id"] == "EP002")
        ep2_images = [
            node for node in reimported["nodes"]
            if node["id"] in set(ep2["nodeIds"]) and node["type"] == "image"
        ]
        self.assertEqual(len(reimported["nodes"]), len(merged["nodes"]))
        self.assertEqual(len(ep2_images), 1)
        self.assertEqual(
            {node["title"] for node in ep2_images},
            {"场景｜厂区后街·母版（图10）"},
        )
        self.assertNotIn("legacy-generic-scene", {
            node["id"] for node in reimported["nodes"]
        })
        self.assertNotIn("legacy-generic-scene", ep2["nodeIds"])

    def test_same_figure_code_never_reuses_a_different_series_asset(self):
        first = import_feed.build_payload({
            "title": "铁西硬汉 · 第01集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [{
                "code": "图28",
                "semanticCode": "CHR-028",
                "name": "郑大头（角色立绘）",
                "raw": "男性角色。",
                "canvas": {
                    "kind": "character",
                    "name": "郑大头",
                    "assetId": "character:郑大头:default",
                    "seriesId": "character:郑大头",
                    "variantKey": "default",
                },
            }],
            "segments": [{
                "no": 1,
                "name": "段1 · 旧集",
                "raw": "@角色｜郑大头（图28）站在门口。",
            }],
        })
        second = import_feed.build_payload({
            "title": "铁西硬汉 · 第02集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [{
                "code": "图28",
                "semanticCode": "PRP-028",
                "name": "半张名册纸（道具设定图）",
                "raw": "半张泛黄的名册纸。",
                "canvas": {
                    "kind": "prop",
                    "name": "半张名册纸",
                    "assetId": "prop:半张名册纸:default",
                    "seriesId": "prop:半张名册纸",
                    "variantKey": "default",
                },
            }],
            "segments": [{
                "no": 1,
                "name": "段1 · 新集",
                "raw": "@道具｜半张名册纸（图28）被举到眼前。",
            }],
        })

        merged = import_feed.merge_payload_by_episode(first, second)
        ep2 = next(
            group for group in merged["episodeImports"]
            if group["id"] == "EP002"
        )
        nodes_by_id = {
            node["id"]: node for node in merged["nodes"]
        }
        video = nodes_by_id[ep2["videoNodeIds"][0]]
        refs = video["metadata"]["params"]["refs"]
        paper_refs = [
            ref for ref in refs
            if ref["label"].startswith("道具｜半张名册纸")
        ]

        self.assertEqual(len(paper_refs), 1)
        paper = nodes_by_id[paper_refs[0]["nodeId"]]
        self.assertEqual(paper["metadata"]["seriesId"], "prop:半张名册纸")
        self.assertNotEqual(paper["metadata"]["seriesId"], "character:郑大头")

    def test_import_layout_keeps_media_types_in_vertical_columns(self):
        payload = import_feed.build_payload({
            "title": "铁西硬汉 · 第01集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [
                {
                    "code": "图1",
                    "name": "赵铁柱（角色立绘）",
                    "raw": "男性角色。",
                },
                {
                    "code": "图2",
                    "name": "钱万金（角色立绘）",
                    "raw": "男性角色。",
                },
                {
                    "code": "图3",
                    "name": "铁西厂大门口（场景母版）",
                    "raw": "场景母版。",
                },
                {
                    "code": "图3",
                    "name": "铁西厂大门口（九宫格）",
                    "raw": "场景九宫格。",
                },
                {
                    "code": "图4",
                    "name": "买断协议（道具设定图）",
                    "raw": "道具设定。",
                },
            ],
            "segments": [{
                "no": 1,
                "name": "段1 · 门口",
                "raw": (
                    "@//chr/- 赵铁柱（图1）与@//chr/- 钱万金（图2）"
                    "站在@//scn/- 铁西厂大门口（图3），"
                    "@//prp/- 买断协议（图4）放在桌上。"
                    '台词：赵铁柱（低沉）："走。"'
                    '台词：钱万金（急）："站住。"'
                ),
                "shots": [],
            }],
        })

        nodes = payload["nodes"]
        expectations = [
            ("角色｜", import_feed.COL_ROLE),
            ("场景｜", import_feed.COL_SCENE),
            ("道具｜", import_feed.COL_PROP),
        ]
        for prefix, expected_x in expectations:
            matching = [
                node for node in nodes
                if node["type"] == "image" and node["title"].startswith(prefix)
            ]
            self.assertGreater(len(matching), 0)
            self.assertEqual({node["position"]["x"] for node in matching}, {expected_x})
            ys = sorted(node["position"]["y"] for node in matching)
            self.assertEqual(len(ys), len(set(ys)))

        for node_type, expected_x in [
            ("audio", import_feed.COL_AUDIO),
            ("video", import_feed.COL_VIDEO),
        ]:
            matching = [node for node in nodes if node["type"] == node_type]
            self.assertGreater(len(matching), 0)
            self.assertEqual({node["position"]["x"] for node in matching}, {expected_x})
            ys = [node["position"]["y"] for node in matching]
            self.assertEqual(len(ys), len(set(ys)))

    def test_import_layout_puts_crowd_assets_in_an_independent_column(self):
        payload = import_feed.build_payload({
            "title": "铁西硬汉 · 第01集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [
                {
                    "code": "图1",
                    "name": "赵铁柱（角色立绘）",
                    "raw": "男性角色。",
                },
                {
                    "code": "图16",
                    "name": "群像资产卡 GRP-01 街坊群",
                    "raw": "街坊群像。",
                    "canvas": {
                        "kind": "crowd",
                        "name": "街坊群",
                        "ratio": "16:9",
                    },
                },
                {
                    "code": "图15",
                    "name": "厂办门口小广场（场景母版）",
                    "raw": "场景母版。",
                },
            ],
            "segments": [{
                "no": 1,
                "name": "段1 · 门口",
                "raw": (
                    "@角色｜赵铁柱（图1）与@群像｜街坊群（图16）"
                    "站在@场景｜厂办门口小广场（图15）。"
                ),
            }],
        })
        nodes = {
            node["title"]: node
            for node in payload["nodes"]
            if node.get("type") == "image"
        }
        crowd = nodes["群像｜街坊群（图16）"]
        role = nodes["角色｜赵铁柱（图1）"]
        scene = nodes["场景｜厂办门口小广场·母版（图15）"]
        by_id = {
            node["id"]: node
            for node in payload["nodes"]
        }
        layout = payload["episodeLayouts"]["EP001"]
        crowd_position = layout[crowd["id"]]
        role_position = layout[role["id"]]
        scene_position = layout[scene["id"]]
        self.assertEqual(crowd_position["x"], import_feed.COL_CROWD)
        self.assertGreater(
            crowd_position["x"] - role_position["x"],
            280,
        )
        self.assertGreater(
            scene_position["x"] - crowd_position["x"],
            280,
        )
        self.assertEqual(
            by_id[crowd["id"]]["position"],
            crowd_position,
        )

    def test_existing_scene_grid_is_collapsed_into_master(self):
        payload = {
            "nodes": [
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段1 · 饭馆",
                    "metadata": {
                        "prompt": (
                            "@场景｜街口饭馆·母版（图29）"
                            "与@场景｜街口饭馆·九宫格（图29）"
                            "（母版+九宫格双挂）"
                        ),
                        "params": {
                            "refs": [
                                {"nodeId": "scene-master", "label": "场景｜街口饭馆·母版（图29）"},
                                {"nodeId": "scene-grid", "label": "场景｜街口饭馆·九宫格（图29）"},
                            ],
                            "voiceRefs": [],
                        },
                    },
                },
                {
                    "id": "scene-master",
                    "type": "image",
                    "title": "场景｜街口饭馆·母版（图29）",
                    "metadata": {"status": "done", "content": "/uploads/master.png"},
                },
                {
                    "id": "scene-grid",
                    "type": "image",
                    "title": "场景｜街口饭馆·九宫格（图29）",
                    "metadata": {"status": "done", "content": "/uploads/grid.png"},
                },
            ],
            "connections": [
                {"id": "e-master", "fromNodeId": "scene-master", "toNodeId": "video-1"},
                {"id": "e-grid", "fromNodeId": "scene-grid", "toNodeId": "video-1"},
            ],
            "episodeImports": [{
                "id": "EP007",
                "nodeIds": ["video-1", "scene-master", "scene-grid"],
                "videoNodeIds": ["video-1"],
            }],
            "episodeLayouts": {
                "EP007": {
                    "video-1": {"x": 1240, "y": 0},
                    "scene-master": {"x": 320, "y": 0},
                    "scene-grid": {"x": 320, "y": 360},
                },
            },
        }

        self.assertEqual(import_feed._remove_redundant_scene_grids(payload), 1)
        video = payload["nodes"][0]
        self.assertEqual(
            [ref["nodeId"] for ref in video["metadata"]["params"]["refs"]],
            ["scene-master"],
        )
        self.assertNotIn("九宫格", video["metadata"]["prompt"])
        self.assertNotIn("scene-grid", [node["id"] for node in payload["nodes"]])
        self.assertEqual(
            payload["episodeImports"][0]["nodeIds"],
            ["video-1", "scene-master"],
        )
        self.assertEqual(
            {(conn["fromNodeId"], conn["toNodeId"]) for conn in payload["connections"]},
            {("scene-master", "video-1")},
        )

    def test_future_episode_asset_cannot_backfill_into_earlier_episode(self):
        payload = {
            "nodes": [
                {
                    "id": "ep1-video",
                    "type": "video",
                    "title": "段1 · 对账",
                    "createdAt": "2026-01-01T00:00:00.000Z",
                    "metadata": {
                        "prompt": "@道具｜账本（图18）摊在桌上。",
                        "params": {
                            "model": "doubao-seedance-2-0",
                            "refs": [{"nodeId": "ep4-ledger", "label": "道具｜账本（图18）"}],
                            "voiceRefs": [],
                        },
                    },
                },
                {
                    "id": "ep4-ledger",
                    "type": "image",
                    "title": "道具｜账本（图18）",
                    "createdAt": "2026-02-01T00:00:00.000Z",
                    "metadata": {
                        "assetKey": "道具:账本",
                        "status": "idle",
                        "content": "",
                    },
                },
            ],
            "connections": [{
                "id": "edge-ledger",
                "fromNodeId": "ep4-ledger",
                "toNodeId": "ep1-video",
            }],
            "episodeImports": [
                {
                    "id": "EP001",
                    "importedAt": "2026-01-02T00:00:00.000Z",
                    "nodeIds": ["ep1-video", "ep4-ledger"],
                    "videoNodeIds": ["ep1-video"],
                },
                {
                    "id": "EP004",
                    "importedAt": "2026-02-02T00:00:00.000Z",
                    "nodeIds": ["ep4-ledger"],
                    "videoNodeIds": [],
                },
            ],
        }

        import_feed.bind_shared_assets_for_all_episodes(payload)
        ep1 = next(group for group in payload["episodeImports"] if group["id"] == "EP001")
        video = next(node for node in payload["nodes"] if node["id"] == "ep1-video")
        self.assertNotIn("ep4-ledger", ep1["nodeIds"])
        self.assertEqual(video["metadata"]["params"]["refs"], [])
        self.assertEqual(payload["connections"], [])
        self.assertEqual(video["metadata"]["prompt"], "账本摊在桌上。")

    def test_video_segments_use_compact_three_column_grid(self):
        payload = import_feed.build_payload({
            "title": "铁西硬汉 · 第04集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [],
            "segments": [
                {
                    "no": index,
                    "name": f"段{index} · 测试",
                    "raw": f"第{index}段画面。",
                    "shots": [],
                }
                for index in range(1, 8)
            ],
        })
        videos = sorted(
            (node for node in payload["nodes"] if node["type"] == "video"),
            key=lambda node: node["title"],
        )
        positions = [
            (node["position"]["x"], node["position"]["y"])
            for node in videos
        ]
        self.assertEqual(positions, [
            (import_feed.COL_VIDEO, 0),
            (import_feed.COL_VIDEO + 260, 0),
            (import_feed.COL_VIDEO + 520, 0),
            (import_feed.COL_VIDEO, 440),
            (import_feed.COL_VIDEO + 260, 440),
            (import_feed.COL_VIDEO + 520, 440),
            (import_feed.COL_VIDEO, 880),
        ])

    def test_sequence_video_is_not_a_generation_ref_without_prompt_mention(self):
        payload = import_feed.build_payload({
            "title": "铁西硬汉 · 第07集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [],
            "segments": [
                {"no": 1, "name": "段1 · 前序", "raw": "前序画面。", "shots": []},
                {"no": 2, "name": "段2 · 当前", "raw": "当前画面。", "shots": []},
            ],
        })
        videos = sorted(
            (node for node in payload["nodes"] if node["type"] == "video"),
            key=lambda node: node["title"],
        )
        current_refs = videos[1]["metadata"]["params"].get("refs") or []
        self.assertEqual(current_refs, [])
        self.assertTrue(any(
            conn.get("fromNodeId") == videos[0]["id"]
            and conn.get("toNodeId") == videos[1]["id"]
            for conn in payload["connections"]
        ))

        videos[1]["metadata"]["prompt"] += f"\n参考@{videos[0]['title']}的末帧动作。"
        videos[1]["metadata"]["params"]["refs"] = [{
            "nodeId": videos[0]["id"],
            "label": videos[0]["title"],
        }]
        import_feed._remove_unmentioned_video_refs(payload)
        self.assertEqual(
            [ref["nodeId"] for ref in videos[1]["metadata"]["params"]["refs"]],
            [videos[0]["id"]],
        )

    def test_episode_layout_is_compact_with_shared_assets(self):
        first = import_feed.build_payload({
            "title": "铁西硬汉 · 第01集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [{
                "code": "图1",
                "name": "赵铁柱（角色立绘）",
                "raw": "男性角色。",
            }],
            "segments": [{
                "no": 1,
                "name": "段1 · 第一集",
                "raw": (
                    "@角色｜赵铁柱（图1）站在门口。"
                    '台词：赵铁柱（低沉）："走。"'
                ),
                "shots": [],
            }],
        })
        second = import_feed.build_payload({
            "title": "铁西硬汉 · 第02集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [{
                "code": "图9",
                "name": "郝秀兰（角色立绘）",
                "raw": "女性角色。",
            }],
            "segments": [{
                "no": 1,
                "name": "段1 · 第二集",
                "raw": (
                    "@角色｜赵铁柱（图1）与@角色｜郝秀兰（图9）站在门口。"
                    '台词：赵铁柱（低沉）："走。"'
                    '台词：郝秀兰（温和）："好。"'
                ),
                "shots": [],
            }],
        })
        merged = import_feed.merge_payload_by_episode(first, second)
        ep2 = next(group for group in merged["episodeImports"] if group["id"] == "EP002")
        nodes = {node["id"]: node for node in merged["nodes"]}
        positions = merged["episodeLayouts"]["EP002"]
        roles = [
            (nodes[node_id]["title"], positions[node_id])
            for node_id in ep2["nodeIds"]
            if node_id in nodes
            and node_id in positions
            and nodes[node_id]["type"] == "image"
            and nodes[node_id]["title"].startswith("角色｜")
        ]
        roles.sort(key=lambda item: item[1]["y"])
        self.assertEqual([position["y"] for _, position in roles], [0, 360])
        self.assertEqual({position["x"] for _, position in roles}, {import_feed.COL_ROLE})

    def test_speaking_voice_refs_win_seedance_reference_budget(self):
        roles = ["赵铁柱", "贾富贵", "郝秀兰"]
        nodes = [{
            "id": "video-1",
            "type": "video",
            "title": "段1 · 三人对白",
            "metadata": {
                "prompt": (
                    "场景：街口。道具：协议、名册。"
                    "人物：赵铁柱、贾富贵、郝秀兰。\n"
                    '台词：赵铁柱（低沉）："一。"\n'
                    '台词：贾富贵（紧张）："二。"\n'
                    '台词：郝秀兰（温和）："三。"'
                ),
                "params": {
                    "model": "doubao-seedance-2-0",
                    "refs": [],
                    "voiceRefs": [],
                },
            },
        }]
        asset_titles = [
            "角色｜赵铁柱（图1）",
            "角色｜贾富贵（图2）",
            "角色｜郝秀兰（图3）",
            "场景｜街口·母版（图10）",
            "场景｜街口·九宫格（图10）",
            "道具｜协议（图11）",
            "道具｜名册（图12）",
        ]
        for index, title in enumerate(asset_titles, start=1):
            nodes.append({
                "id": f"image-{index}",
                "type": "image",
                "title": title,
                "metadata": {"assetKey": title, "status": "done", "content": "x"},
            })
        for role in roles:
            nodes.append({
                "id": f"audio-{role}",
                "type": "audio",
                "title": f"音频｜{role}",
                "metadata": {"status": "done", "content": "x"},
            })
        payload = {
            "nodes": nodes,
            "connections": [],
            "episodeImports": [{
                "id": "EP002",
                "nodeIds": [node["id"] for node in nodes],
                "videoNodeIds": ["video-1"],
            }],
        }

        import_feed.bind_shared_assets_for_episode(payload, "EP002")
        params = payload["nodes"][0]["metadata"]["params"]
        voice_labels = {ref["label"] for ref in params["voiceRefs"]}
        self.assertEqual(voice_labels, {
            f"音频｜{role}" for role in roles
        })
        self.assertLessEqual(
            len(params["refs"]) + len(params["voiceRefs"]),
            9,
        )
        self.assertEqual(len(params["voiceRefs"]), 3)

    def test_repair_drops_audio_for_characters_without_nonempty_dialogue(self):
        payload = {
            "nodes": [
                {
                    "id": "role-a",
                    "type": "image",
                    "title": "角色｜钱万金（图2）",
                    "metadata": {"status": "done", "content": "x"},
                },
                {
                    "id": "role-b",
                    "type": "image",
                    "title": "角色｜贾富贵（图3）",
                    "metadata": {"status": "done", "content": "x"},
                },
                {
                    "id": "audio-a",
                    "type": "audio",
                    "title": "音频｜钱万金（图2）",
                    "metadata": {"status": "done", "content": "x"},
                },
                {
                    "id": "audio-b",
                    "type": "audio",
                    "title": "音频｜贾富贵（图3）",
                    "metadata": {"status": "done", "content": "x"},
                },
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段5 · 账本在哪",
                    "metadata": {
                        "prompt": (
                            "【核心人物】@角色｜钱万金（图2）；@角色｜贾富贵（图3）\n"
                            "【音色引用】@音频｜钱万金（图2）；@音频｜贾富贵（图3）\n"
                            "【时间轴分镜】\n"
                            '台词：钱万金（高声）："账本拿来。"\n'
                            "贾富贵猛地抬头。台词：无"
                        ),
                        "params": {
                            "model": "doubao-seedance-2-0",
                            "refs": [],
                            "voiceRefs": [
                                {"nodeId": "audio-a", "label": "音频｜钱万金（图2）"},
                                {"nodeId": "audio-b", "label": "音频｜贾富贵（图3）"},
                            ],
                        },
                    },
                },
            ],
            "connections": [
                {"id": "edge-a", "fromNodeId": "audio-a", "toNodeId": "video-1"},
                {"id": "edge-b", "fromNodeId": "audio-b", "toNodeId": "video-1"},
            ],
        }

        import_feed.repair_payload_prompt_mentions(payload)
        video = payload["nodes"][-1]
        params = video["metadata"]["params"]
        self.assertEqual(
            [ref["label"] for ref in params["voiceRefs"]],
            ["音频｜钱万金（图2）"],
        )
        self.assertIn("【音色引用】@音频｜钱万金（图2）", video["metadata"]["prompt"])
        self.assertNotIn("贾富贵（图3）\n【站位声明】", video["metadata"]["prompt"])
        self.assertNotIn("@音频｜贾富贵（图3）", video["metadata"]["prompt"])
        self.assertEqual(
            [conn["id"] for conn in payload["connections"]],
            ["edge-a"],
        )

    def test_repair_removes_voice_section_when_segment_has_no_dialogue(self):
        payload = {
            "nodes": [
                {
                    "id": "role-a",
                    "type": "image",
                    "title": "角色｜钱万金（图2）",
                    "metadata": {"status": "done", "content": "x"},
                },
                {
                    "id": "audio-a",
                    "type": "audio",
                    "title": "音频｜钱万金（图2）",
                    "metadata": {"status": "done", "content": "x"},
                },
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段6 · 无对白",
                    "metadata": {
                        "prompt": (
                            "【核心人物】@角色｜钱万金（图2）\n"
                            "【音色引用】@音频｜钱万金（图2）\n"
                            "【时间轴分镜】钱万金环顾四周。台词：无"
                        ),
                        "params": {
                            "model": "doubao-seedance-2-0",
                            "refs": [],
                            "voiceRefs": [
                                {"nodeId": "audio-a", "label": "音频｜钱万金（图2）"},
                            ],
                        },
                    },
                },
            ],
            "connections": [
                {"id": "edge-a", "fromNodeId": "audio-a", "toNodeId": "video-1"},
            ],
        }

        import_feed.repair_payload_prompt_mentions(payload)
        video = payload["nodes"][-1]
        self.assertEqual(video["metadata"]["params"]["voiceRefs"], [])
        self.assertNotIn("【音色引用】", video["metadata"]["prompt"])
        self.assertEqual(payload["connections"], [])

    def test_readiness_reports_unresolved_media_and_voice_dependencies(self):
        payload = {
            "nodes": [
                {
                    "id": "image-idle",
                    "type": "image",
                    "title": "角色｜甲（图1）",
                    "metadata": {"status": "idle", "content": ""},
                },
                {
                    "id": "audio-done",
                    "type": "audio",
                    "title": "音频｜甲（图1）",
                    "metadata": {"status": "done", "content": "/uploads/a.mp3"},
                },
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段1",
                    "metadata": {
                        "prompt": "参考 @角色｜甲（图1）",
                        "params": {
                            "refs": [{"nodeId": "image-idle", "label": "角色｜甲（图1）"}],
                            "voiceRefs": [{"nodeId": "audio-done", "label": "音频｜甲（图1）"}],
                        },
                    },
                },
            ],
            "episodeImports": [{
                "id": "EP001",
                "nodeIds": ["image-idle", "audio-done", "video-1"],
                "videoNodeIds": ["video-1"],
            }],
        }
        blockers = import_feed.node_dependency_blockers(payload, payload["nodes"][2])
        self.assertEqual([item["nodeId"] for item in blockers], ["image-idle"])
        readiness = import_feed.episode_readiness(payload, "EP001")
        self.assertEqual(readiness["videoTotal"], 1)
        self.assertEqual(readiness["videoReady"], 0)
        self.assertEqual(readiness["dependencyPending"], 1)
        self.assertFalse(readiness["readyToGenerate"])

    def test_prompt_stale_media_remains_usable_for_generation(self):
        payload = {
            "nodes": [
                {
                    "id": "image-stale",
                    "type": "image",
                    "title": "角色｜甲（图1）",
                    "metadata": {
                        "status": "done",
                        "content": "/uploads/old.png",
                        "promptStale": True,
                        "promptStaleReason": "源提示词已更新",
                    },
                },
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段1",
                    "metadata": {
                        "prompt": "参考 @角色｜甲（图1）",
                        "params": {
                            "refs": [
                                {"nodeId": "image-stale", "label": "角色｜甲（图1）"},
                            ],
                        },
                    },
                },
            ],
            "episodeImports": [{
                "id": "EP001",
                "nodeIds": ["image-stale", "video-1"],
                "videoNodeIds": ["video-1"],
            }],
        }

        readiness = import_feed.episode_readiness(payload, "EP001")

        self.assertEqual(readiness["dependencyPending"], 0)
        self.assertTrue(readiness["readyToGenerate"])

    def test_prompt_mention_without_binding_is_not_considered_ready(self):
        payload = {
            "nodes": [
                {
                    "id": "image-ready",
                    "type": "image",
                    "title": "角色｜甲（图1）",
                    "metadata": {"status": "done", "content": "/uploads/a.png"},
                },
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段1",
                    "metadata": {
                        "prompt": "参考 @角色｜甲（图1）",
                        "params": {"refs": []},
                    },
                },
            ],
            "episodeImports": [{
                "id": "EP001",
                "nodeIds": ["image-ready", "video-1"],
                "videoNodeIds": ["video-1"],
            }],
        }
        blockers = import_feed.node_dependency_blockers(payload, payload["nodes"][1])
        self.assertEqual(len(blockers), 1)
        self.assertIn("未绑定", blockers[0]["reason"])
        self.assertFalse(import_feed.episode_readiness(payload, "EP001")["readyToGenerate"])

    def test_repair_generated_audio_prompt_overwritten_by_placeholder(self):
        detailed = (
            "cold, oily, command tone. A male voice, cold and oily with a smooth "
            "command tone and clipped, confident rhythm."
        )
        placeholder = (
            "钱万金，普通话对白参考音色，短剧配音参考，4.5 秒清晰人声，"
            "无背景噪声。请根据角色补充音色描述后再生成。"
        )
        payload = {
            "nodes": [{
                "id": "audio-1",
                "type": "audio",
                "title": "音频｜钱万金（图2）",
                "metadata": {
                    "content": "/uploads/generated.mp3",
                    "status": "done",
                    "prompt": placeholder,
                    "composerContent": placeholder,
                    "promptStale": True,
                    "promptStaleReason": "源音色提示词已更新，当前参考音频仍是旧版本",
                    "sourcePromptUpdatedAt": "2026-10-28T09:02:36.025Z",
                    "params": {"voicePrompt": detailed},
                },
            }],
        }

        changed = import_feed.repair_reused_asset_prompts(payload)

        audio = payload["nodes"][0]
        self.assertEqual(changed, 1)
        self.assertEqual(audio["metadata"]["prompt"], detailed)
        self.assertIn(detailed, audio["metadata"]["composerContent"])
        self.assertFalse(audio["metadata"]["promptStale"])
        self.assertNotIn("sourcePromptUpdatedAt", audio["metadata"])

    def test_later_episode_placeholder_keeps_generated_audio_prompt(self):
        detailed = "low, steady, northeastern-accented baritone."
        placeholder = (
            "钱万金，普通话对白参考音色，短剧配音参考，4.5 秒清晰人声，"
            "无背景噪声。请根据角色补充音色描述后再生成。"
        )
        existing = {
            "title": "系列画布",
            "episodeId": "EP001",
            "episodeImports": [{
                "id": "EP001",
                "nodeIds": ["audio-1", "video-1"],
                "videoNodeIds": ["video-1"],
            }],
            "nodes": [
                {
                    "id": "audio-1",
                    "type": "audio",
                    "title": "音频｜钱万金（图2）",
                    "metadata": {
                        "content": "/uploads/generated.mp3",
                        "status": "done",
                        "prompt": detailed,
                        "composerContent": detailed,
                        "params": {"voicePrompt": detailed},
                    },
                },
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段1 · 旧视频",
                    "metadata": {"prompt": "旧视频"},
                },
            ],
            "connections": [],
        }
        incoming = {
            "title": "系列画布",
            "episodeId": "EP002",
            "episodeImports": [{
                "id": "EP002",
                "nodeIds": ["audio-2", "video-2"],
                "videoNodeIds": ["video-2"],
            }],
            "nodes": [
                {
                    "id": "audio-2",
                    "type": "audio",
                    "title": "音频｜钱万金（图2）",
                    "metadata": {
                        "content": "",
                        "status": "idle",
                        "prompt": placeholder,
                        "composerContent": placeholder,
                        "autoPlaceholder": True,
                        "params": {"voicePrompt": placeholder},
                    },
                },
                {
                    "id": "video-2",
                    "type": "video",
                    "title": "段1 · 新视频",
                    "metadata": {"prompt": "新视频"},
                },
            ],
            "connections": [],
        }

        merged = import_feed.merge_payload_by_episode(existing, incoming)
        audio = next(node for node in merged["nodes"] if node["type"] == "audio")

        self.assertEqual(audio["metadata"]["prompt"], detailed)
        self.assertEqual(audio["metadata"]["composerContent"], detailed)
        self.assertFalse(audio["metadata"].get("promptStale"))
        self.assertFalse(audio["metadata"].get("autoPlaceholder"))
        self.assertEqual(audio["metadata"]["content"], "/uploads/generated.mp3")

    def test_reimport_updates_reused_image_prompt_and_marks_generated_media_stale(self):
        old_feed = {
            "title": "铁西硬汉 · 第10集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "asset_prompts": [{
                "code": "图1",
                "name": "办事员（角色立绘）",
                "raw": "旧提示词：站姿端坐。",
            }],
            "segments": [{
                "no": 1,
                "name": "段1",
                "raw": "@角色｜办事员（图1）站在窗口。",
            }],
        }
        old_payload = import_feed.build_payload(old_feed)
        image = next(node for node in old_payload["nodes"] if node["type"] == "image")
        image["metadata"]["content"] = "/uploads/old.png"
        image["metadata"]["status"] = "done"
        new_feed = {
            **old_feed,
            "asset_prompts": [{
                "code": "图1",
                "name": "办事员（角色立绘）",
                "raw": "新提示词：站姿站立于桌前。",
            }],
        }
        merged = import_feed.merge_payload_by_episode(
            old_payload,
            import_feed.build_payload(new_feed),
        )
        image = next(node for node in merged["nodes"] if node["id"] == image["id"])
        self.assertIn("新提示词", image["metadata"]["prompt"])
        self.assertEqual(image["metadata"]["content"], "/uploads/old.png")
        self.assertTrue(image["metadata"]["promptStale"])
        readiness = import_feed.episode_readiness(
            merged,
            (merged.get("episodeImports") or [{}])[0].get("id"),
        )
        self.assertEqual(readiness["dependencyPending"], 0)
        self.assertTrue(readiness["readyToGenerate"])

        merged_again = import_feed.merge_payload_by_episode(
            merged,
            import_feed.build_payload(new_feed),
        )
        image = next(node for node in merged_again["nodes"] if node["id"] == image["id"])
        self.assertTrue(image["metadata"]["promptStale"])


if __name__ == "__main__":
    unittest.main()
