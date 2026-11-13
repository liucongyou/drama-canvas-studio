import unittest

import server


class RequestReferenceCandidateTests(unittest.TestCase):
    def setUp(self):
        self.nodes = [
            {
                "id": "asset-image",
                "type": "image",
                "title": "角色｜张桂芝（图3）",
                "metadata": {"assetId": "character:张桂芝:default"},
            },
            {
                "id": "previous-video",
                "type": "video",
                "title": "段6",
                "metadata": {},
            },
        ]
        self.connections = [
            {
                "fromNodeId": "asset-image",
                "toNodeId": "video-node",
            },
            {
                "fromNodeId": "previous-video",
                "toNodeId": "video-node",
            },
        ]

    def test_explicit_video_refs_do_not_merge_canvas_connections(self):
        refs, explicit = server.request_reference_candidates(
            params={"refs": [{"nodeId": "asset-image"}]},
            meta={},
            node_type="video",
            node_id="video-node",
            connections=self.connections,
            nodes=self.nodes,
        )

        self.assertTrue(explicit)
        self.assertEqual([ref["nodeId"] for ref in refs], ["asset-image"])

    def test_empty_explicit_video_refs_do_not_derive_connections(self):
        refs, explicit = server.request_reference_candidates(
            params={"refs": []},
            meta={},
            node_type="video",
            node_id="video-node",
            connections=self.connections,
            nodes=self.nodes,
        )

        self.assertTrue(explicit)
        self.assertEqual(refs, [])

    def test_legacy_video_without_explicit_refs_keeps_connection_fallback(self):
        refs, explicit = server.request_reference_candidates(
            params={},
            meta={},
            node_type="video",
            node_id="video-node",
            connections=self.connections,
            nodes=self.nodes,
        )

        self.assertFalse(explicit)
        self.assertEqual(
            [ref["nodeId"] for ref in refs],
            ["asset-image", "previous-video"],
        )


if __name__ == "__main__":
    unittest.main()
