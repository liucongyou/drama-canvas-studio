from __future__ import annotations

import unittest

import gate_check


class GateCheckTests(unittest.TestCase):
    def test_current_full_asset_syntax_matches_image_edges(self):
        payload = {
            "nodes": [
                {"id": "img-1", "type": "image", "title": "角色｜赵铁柱（图1）"},
                {"id": "audio-1", "type": "audio", "title": "音频｜赵铁柱（图1）"},
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段1",
                    "metadata": {
                        "prompt": (
                            "【核心人物】@角色｜赵铁柱（图1）\n\n"
                            "【音色引用】@音频｜赵铁柱（图1）"
                        ),
                        "params": {
                            "voiceRefs": [{"nodeId": "audio-1", "label": "音频｜赵铁柱（图1）"}],
                        },
                    },
                },
            ],
            "connections": [
                {"fromNodeId": "img-1", "toNodeId": "video-1"},
                {"fromNodeId": "audio-1", "toNodeId": "video-1"},
            ],
            "episodeImports": [{
                "id": "EP001",
                "nodeIds": ["img-1", "audio-1", "video-1"],
                "videoNodeIds": ["video-1"],
            }],
        }
        result = gate_check.run_check_json(payload, episode_id="EP001")
        self.assertTrue(result["passed"])
        self.assertEqual(result["results"][0]["script"], ["图1"])
        self.assertEqual(result["results"][0]["linked"], ["图1"])
        self.assertEqual(result["results"][0]["voiceScript"], ["图1"])
        self.assertEqual(result["results"][0]["voiceRefs"], ["图1"])
        self.assertEqual(result["results"][0]["voiceLinked"], ["图1"])

    def test_audio_reference_without_edge_fails(self):
        payload = {
            "nodes": [
                {"id": "audio-1", "type": "audio", "title": "音频｜赵铁柱（图1）"},
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段1",
                    "metadata": {
                        "prompt": "【音色引用】@音频｜赵铁柱（图1）",
                        "params": {
                            "voiceRefs": [{"nodeId": "audio-1", "label": "音频｜赵铁柱（图1）"}],
                        },
                    },
                },
            ],
            "connections": [],
            "episodeImports": [{
                "id": "EP001",
                "nodeIds": ["audio-1", "video-1"],
                "videoNodeIds": ["video-1"],
            }],
        }
        result = gate_check.run_check_json(payload, episode_id="EP001")
        self.assertFalse(result["passed"])
        self.assertFalse(result["results"][0]["audioOk"])

    def test_episode_filter_ignores_other_episode_failures(self):
        payload = {
            "nodes": [
                {"id": "img-1", "type": "image", "title": "角色｜赵铁柱（图1）"},
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段1",
                    "metadata": {"prompt": "@角色｜赵铁柱（图1）"},
                },
                {
                    "id": "video-2",
                    "type": "video",
                    "title": "段2",
                    "metadata": {"prompt": "@角色｜钱万金（图2）"},
                },
            ],
            "connections": [{"fromNodeId": "img-1", "toNodeId": "video-1"}],
            "episodeImports": [
                {"id": "EP001", "nodeIds": ["img-1", "video-1"], "videoNodeIds": ["video-1"]},
                {"id": "EP002", "nodeIds": ["video-2"], "videoNodeIds": ["video-2"]},
            ],
        }
        result = gate_check.run_check_json(payload, episode_id="EP001")
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["results"]), 1)

    def test_legacy_asset_syntax_uses_shared_contract_parser(self):
        payload = {
            "nodes": [
                {"id": "img-1", "type": "image", "title": "角色｜赵铁柱（图1）"},
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段1",
                    "metadata": {"prompt": "@//chr/-赵铁柱（图1）"},
                },
            ],
            "connections": [{"fromNodeId": "img-1", "toNodeId": "video-1"}],
            "episodeImports": [{
                "id": "EP001",
                "nodeIds": ["img-1", "video-1"],
                "videoNodeIds": ["video-1"],
            }],
        }

        result = gate_check.run_check_json(payload, episode_id="EP001")

        self.assertTrue(result["passed"])
        self.assertEqual(result["results"][0]["script"], ["图1"])

    def test_stale_declared_edge_is_not_reported_as_attached(self):
        payload = {
            "nodes": [
                {"id": "img-1", "type": "image", "title": "角色｜赵铁柱（图1）"},
                {"id": "img-2", "type": "image", "title": "角色｜钱万金（图2）"},
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段13",
                    "metadata": {
                        "prompt": "@角色｜赵铁柱（图1）在门口。",
                        "params": {
                            "refs": [
                                {"nodeId": "img-1", "label": "角色｜赵铁柱（图1）"},
                            ],
                        },
                    },
                },
            ],
            "connections": [
                {"fromNodeId": "img-1", "toNodeId": "video-1"},
                {"fromNodeId": "img-2", "toNodeId": "video-1"},
            ],
            "episodeImports": [{
                "id": "EP001",
                "nodeIds": ["img-1", "img-2", "video-1"],
                "videoNodeIds": ["video-1"],
            }],
        }

        result = gate_check.run_check_json(payload, episode_id="EP001")
        row = result["results"][0]
        self.assertFalse(result["passed"])
        self.assertTrue(row["imageOk"])
        self.assertEqual(row["linked"], ["图1"])
        self.assertEqual(row["staleLinked"], ["图2"])
        self.assertFalse(row["imageStaleOk"])

    def test_stale_asset_mapping_is_not_an_effective_scene_reference(self):
        payload = {
            "nodes": [
                {"id": "img-1", "type": "image", "title": "角色｜赵铁柱（图1）"},
                {"id": "img-2", "type": "image", "title": "角色｜钱万金（图2）"},
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段13",
                    "metadata": {
                        "prompt": (
                            "### 素材引用\n"
                            "<钱万金> 对应@角色｜钱万金（图2）\n"
                            "【时间轴分镜】\n"
                            "00:00-00:02 画面主体：@角色｜赵铁柱（图1）在门口。"
                        ),
                        "params": {
                            "refs": [
                                {"nodeId": "img-1", "label": "角色｜赵铁柱（图1）"},
                            ],
                        },
                    },
                },
            ],
            "connections": [
                {"fromNodeId": "img-1", "toNodeId": "video-1"},
                {"fromNodeId": "img-2", "toNodeId": "video-1"},
            ],
            "episodeImports": [{
                "id": "EP001",
                "nodeIds": ["img-1", "img-2", "video-1"],
                "videoNodeIds": ["video-1"],
            }],
        }

        result = gate_check.run_check_json(payload, episode_id="EP001")
        row = result["results"][0]
        self.assertEqual(row["script"], ["图1"])
        self.assertEqual(row["linked"], ["图1"])
        self.assertEqual(row["staleLinked"], ["图2"])

    def test_dialogue_only_code_is_removed_even_when_params_refs_is_stale(self):
        payload = {
            "nodes": [
                {"id": "img-1", "type": "image", "title": "角色｜赵铁柱（图1）"},
                {"id": "img-2", "type": "image", "title": "角色｜钱万金（图2）"},
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段13",
                    "metadata": {
                        "prompt": (
                            "### 素材引用\n"
                            "<钱万金> 对应@角色｜钱万金（图2）\n"
                            "【时间轴分镜】\n"
                            "00:00-00:02 画面主体：@角色｜赵铁柱（图1）在门口。"
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
            ],
            "connections": [
                {"fromNodeId": "img-1", "toNodeId": "video-1"},
                {"fromNodeId": "img-2", "toNodeId": "video-1"},
            ],
            "episodeImports": [{
                "id": "EP001",
                "nodeIds": ["img-1", "img-2", "video-1"],
                "videoNodeIds": ["video-1"],
            }],
        }

        row = gate_check.run_check_json(
            payload,
            episode_id="EP001",
        )["results"][0]
        self.assertEqual(row["script"], ["图1"])
        self.assertEqual(row["linked"], ["图1"])
        self.assertEqual(row["staleLinked"], ["图2"])


if __name__ == "__main__":
    unittest.main()
