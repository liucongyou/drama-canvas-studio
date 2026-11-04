from __future__ import annotations

import unittest

import import_feed


class StaleReferenceGenerationTests(unittest.TestCase):
    def test_historical_media_with_changed_prompt_is_still_usable(self):
        payload = {
            "nodes": [
                {
                    "id": "image-current",
                    "type": "image",
                    "title": "角色｜钱万金（图2）",
                    "metadata": {
                        "status": "done",
                        "content": "/uploads/current.png",
                        "prompt": "新提示词",
                        "promptStale": True,
                        "promptStaleReason": "源提示词已更新，当前复用图片仍是旧版本",
                    },
                },
                {
                    "id": "video-segment-1",
                    "type": "video",
                    "title": "段1 · 大喇叭与买断宣示",
                    "metadata": {
                        "status": "idle",
                        "prompt": "使用角色图生成段1",
                        "params": {
                            "refs": [
                                {
                                    "nodeId": "image-current",
                                    "label": "角色｜钱万金（图2）",
                                },
                            ],
                        },
                    },
                },
            ],
        }

        self.assertEqual(
            import_feed.node_dependency_blockers(
                payload,
                payload["nodes"][1],
            ),
            [],
        )

    def test_missing_media_is_still_blocked(self):
        payload = {
            "nodes": [
                {
                    "id": "image-empty",
                    "type": "image",
                    "title": "角色｜钱万金（图2）",
                    "metadata": {
                        "status": "idle",
                        "content": "",
                    },
                },
                {
                    "id": "video-segment-1",
                    "type": "video",
                    "title": "段1 · 大喇叭与买断宣示",
                    "metadata": {
                        "prompt": "使用角色图生成段1",
                        "params": {
                            "refs": [
                                {
                                    "nodeId": "image-empty",
                                    "label": "角色｜钱万金（图2）",
                                },
                            ],
                        },
                    },
                },
            ],
        }

        blockers = import_feed.node_dependency_blockers(
            payload,
            payload["nodes"][1],
        )
        self.assertEqual(len(blockers), 1)
        self.assertEqual(blockers[0]["reason"], "尚未生成")


if __name__ == "__main__":
    unittest.main()
