from __future__ import annotations

import unittest

import gate_check


def _payload(prompt: str, refs: list[dict], edge_source: str):
    return {
        "nodes": [
            {
                "id": "image-1",
                "type": "image",
                "title": "角色｜贾富贵（图1）",
                "metadata": {},
            },
            {
                "id": "image-24",
                "type": "image",
                "title": "道具｜账本（图24）",
                "metadata": {},
            },
            {
                "id": "video-1",
                "type": "video",
                "title": "段1 · 白纸黑字",
                "metadata": {
                    "prompt": prompt,
                    "params": {"refs": refs},
                },
            },
        ],
        "connections": [
            {
                "id": "edge-1",
                "fromNodeId": "image-1",
                "toNodeId": "video-1",
            },
            {
                "id": "edge-24",
                "fromNodeId": edge_source,
                "toNodeId": "video-1",
            },
        ],
    }


class GateCheckReferenceScopeTests(unittest.TestCase):
    def test_carried_but_unmentioned_asset_is_not_a_failure(self):
        payload = _payload(
            "画面主体：@角色｜贾富贵（图1）站在桌前。",
            [
                {"nodeId": "image-1", "label": "角色｜贾富贵（图1）"},
                {"nodeId": "image-24", "label": "道具｜账本（图24）"},
            ],
            "image-24",
        )

        result = gate_check.run_check_json(payload)

        self.assertTrue(result["passed"])
        self.assertEqual(result["results"][0]["carriedOnly"], ["图24"])
        self.assertEqual(result["results"][0]["missing"], [])

    def test_prompt_asset_without_attachment_still_fails(self):
        payload = _payload(
            "画面主体：@角色｜贾富贵（图1）拿起@道具｜账本（图24）。",
            [
                {"nodeId": "image-1", "label": "角色｜贾富贵（图1）"},
                {"nodeId": "image-24", "label": "道具｜账本（图24）"},
            ],
            "image-1",
        )

        result = gate_check.run_check_json(payload)

        self.assertFalse(result["passed"])
        self.assertEqual(result["results"][0]["missing"], ["图24"])


if __name__ == "__main__":
    unittest.main()
