import unittest

import server


class MediaVersionTests(unittest.TestCase):
    def test_image_versions_keep_previous_generation_current(self):
        meta = {
            "content": "/uploads/old.png",
            "taskId": "task-1",
            "generatedAt": "2026-10-26T00:00:00Z",
            "params": {},
        }
        server._record_media_version(
            meta,
            "image",
            "/uploads/new.png",
            "task-2",
            "2026-10-26T00:01:00Z",
        )
        versions = meta["params"]["mediaVersions"]
        self.assertEqual(
            [item["url"] for item in versions],
            ["/uploads/old.png", "/uploads/new.png"],
        )
        self.assertEqual(meta["params"]["mediaVersionIndex"], 1)
        self.assertNotIn("videoVersions", meta["params"])

    def test_video_versions_sync_legacy_fields_without_duplicates(self):
        meta = {
            "content": "/uploads/old.mp4",
            "params": {
                "videoVersions": [
                    {"url": "/uploads/old.mp4"},
                    {"url": "/uploads/new.mp4"},
                ],
            },
        }
        server._record_media_version(
            meta,
            "video",
            "/uploads/new.mp4",
            "task-2",
            "2026-10-26T00:02:00Z",
        )
        params = meta["params"]
        self.assertEqual(
            [item["url"] for item in params["mediaVersions"]],
            ["/uploads/old.mp4", "/uploads/new.mp4"],
        )
        self.assertEqual(params["mediaVersions"], params["videoVersions"])
        self.assertEqual(params["mediaVersionIndex"], 1)
        self.assertEqual(params["videoVersionIndex"], 1)


if __name__ == "__main__":
    unittest.main()
