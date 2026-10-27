from __future__ import annotations

import unittest

import gate_check


class GateCheckTests(unittest.TestCase):
    def test_current_full_asset_syntax_matches_image_edges(self):
        payload = {
            "nodes": [
                {"id": "img-1", "type": "image", "title": "角色｜赵铁柱（图1）"},
                {
                    "id": "video-1",
                    "type": "video",
                    "title": "段1",
                    "metadata": {
                        "prompt": (
                            "【核心人物】@角色｜赵铁柱（图1）\n\n"
                            "【音色引用】@音频｜赵铁柱（图1）"
                        )
                    },
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
        self.assertEqual(result["results"][0]["linked"], ["图1"])

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


if __name__ == "__main__":
    unittest.main()
