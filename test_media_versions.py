import unittest

import server


class MediaVersionTests(unittest.TestCase):
    def test_explicit_selection_persists_and_rejects_unknown_url(self):
        meta = {
            "content": "/uploads/v1.png",
            "status": "done",
            "source": {"type": "generated"},
            "params": {
                "mediaVersions": [
                    {"url": "/uploads/v1.png", "sourceType": "generated"},
                    {"url": "/uploads/v2.png", "sourceType": "generated"},
                ],
                "mediaVersionIndex": 0,
            },
        }

        server.merge_node_metadata(meta, {
            "content": "/uploads/v2.png",
            "status": "done",
            "source": {"type": "generated"},
            "params": {"mediaVersionIndex": 1},
            "_mediaVersionSelection": True,
        })

        self.assertEqual(meta["content"], "/uploads/v2.png")
        self.assertEqual(meta["params"]["mediaVersionIndex"], 1)

        server.merge_node_metadata(meta, {
            "content": "/uploads/not-a-version.png",
            "source": {"type": "generated"},
            "params": {"mediaVersionIndex": 0},
            "_mediaVersionSelection": True,
        })

        self.assertEqual(meta["content"], "/uploads/v2.png")
        self.assertEqual(meta["params"]["mediaVersionIndex"], 1)

    def test_stale_save_without_explicit_selection_cannot_change_current_version(self):
        meta = {
            "content": "/uploads/v2.png",
            "status": "done",
            "source": {"type": "generated"},
            "params": {
                "mediaVersions": [
                    {"url": "/uploads/v1.png", "sourceType": "generated"},
                    {"url": "/uploads/v2.png", "sourceType": "generated"},
                ],
                "mediaVersionIndex": 1,
            },
        }

        server.merge_node_metadata(meta, {
            "prompt": "旧标签页里的提示词",
            "content": "/uploads/v1.png",
            "status": "done",
            "source": {"type": "generated"},
            "params": {"mediaVersionIndex": 0},
        })

        self.assertEqual(meta["prompt"], "旧标签页里的提示词")
        self.assertEqual(meta["content"], "/uploads/v2.png")
        self.assertEqual(meta["params"]["mediaVersionIndex"], 1)

    def test_version_selection_repairs_stale_active_id_by_content(self):
        meta = {
            "content": "/uploads/v2.mp4",
            "status": "done",
            "source": {"type": "generated"},
            "params": {
                "mediaVersions": [
                    {
                        "versionId": "video:task-old",
                        "url": "/uploads/v1.mp4",
                        "sourceType": "generated",
                    },
                    {
                        "versionId": "video:task-new",
                        "url": "/uploads/v2.mp4",
                        "sourceType": "generated",
                    },
                ],
                "videoVersions": [
                    {
                        "versionId": "video:task-old",
                        "url": "/uploads/v1.mp4",
                        "sourceType": "generated",
                    },
                    {
                        "versionId": "video:task-new",
                        "url": "/uploads/v2.mp4",
                        "sourceType": "generated",
                    },
                ],
                "mediaVersionIndex": 1,
                "videoVersionIndex": 1,
                "activeVersionId": "video:task-old",
            },
        }

        server.merge_node_metadata(meta, {
            "content": "/uploads/v2.mp4",
            "status": "done",
            "source": {"type": "generated"},
            "params": {
                "mediaVersionIndex": 1,
                "videoVersionIndex": 1,
                "activeVersionId": "video:task-old",
            },
            "_mediaVersionSelection": True,
        })

        self.assertEqual(meta["content"], "/uploads/v2.mp4")
        self.assertEqual(meta["params"]["mediaVersionIndex"], 1)
        self.assertEqual(meta["params"]["activeVersionId"], "video:task-new")

    def test_version_selection_accepts_signed_projection_by_stable_id(self):
        meta = {
            "content": "/uploads/v2.mp4",
            "status": "done",
            "source": {"type": "generated"},
            "params": {
                "mediaVersions": [
                    {
                        "versionId": "video:task-old",
                        "url": "/uploads/v1.mp4",
                        "sourceType": "generated",
                    },
                    {
                        "versionId": "video:task-new",
                        "url": "/uploads/v2.mp4",
                        "sourceType": "generated",
                    },
                ],
                "mediaVersionIndex": 0,
                "activeVersionId": "video:task-old",
            },
        }

        server.merge_node_metadata(meta, {
            "content": "https://bucket.tos-cn-beijing.volces.com/v2.mp4?signature=temporary",
            "status": "done",
            "source": {"type": "generated"},
            "params": {
                "mediaVersionIndex": 1,
                "activeVersionId": "video:task-new",
            },
            "_mediaVersionSelection": True,
        })

        self.assertEqual(meta["content"], "/uploads/v2.mp4")
        self.assertEqual(meta["params"]["mediaVersionIndex"], 1)
        self.assertEqual(meta["params"]["activeVersionId"], "video:task-new")

    def test_local_upload_can_append_a_media_version(self):
        meta = {
            "content": "/uploads/generated.png",
            "status": "done",
            "source": {"type": "generated"},
            "params": {
                "mediaVersions": [{"url": "/uploads/generated.png", "sourceType": "generated"}],
                "mediaVersionIndex": 0,
            },
        }
        incoming = {
            "_mediaImport": True,
            "content": "/uploads/local.png",
            "status": "done",
            "source": {"type": "local", "fileName": "local.png"},
            "params": {
                "mediaVersions": [
                    {"url": "/uploads/generated.png", "sourceType": "generated"},
                    {"url": "/uploads/local.png", "sourceType": "local", "fileName": "local.png"},
                ],
                "mediaVersionIndex": 1,
            },
        }

        server.merge_node_metadata(meta, incoming)

        self.assertEqual(meta["content"], "/uploads/local.png")
        self.assertEqual(meta["source"]["type"], "local")
        self.assertEqual(meta["params"]["mediaVersionIndex"], 1)
        self.assertEqual(meta["params"]["mediaVersions"][-1]["fileName"], "local.png")

    def test_normal_edit_of_local_asset_cannot_replace_media_content(self):
        meta = {
            "content": "/uploads/current.png",
            "status": "done",
            "source": {"type": "local", "fileName": "current.png"},
            "params": {
                "mediaVersions": [{"url": "/uploads/current.png", "sourceType": "local"}],
                "mediaVersionIndex": 0,
            },
        }

        server.merge_node_metadata(meta, {
            "prompt": "只改提示词",
            "content": "/uploads/stale.png",
            "source": {"type": "local", "fileName": "stale.png"},
            "params": {
                "mediaVersions": [{"url": "/uploads/stale.png", "sourceType": "local"}],
                "mediaVersionIndex": 0,
            },
        })

        self.assertEqual(meta["prompt"], "只改提示词")
        self.assertEqual(meta["content"], "/uploads/current.png")
        self.assertEqual(meta["source"]["fileName"], "current.png")

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

    def test_recovery_receipt_updates_existing_version_without_duplicate(self):
        meta = {
            "content": "/uploads/old.png",
            "status": "done",
            "params": {
                "mediaVersions": [{
                    "url": "/uploads/old.png",
                    "taskId": "provider-task-1",
                    "sourceType": "generated",
                }],
                "mediaVersionIndex": 0,
            },
        }
        versions = server._record_media_version(
            meta,
            "image",
            "/uploads/recovered.png",
            "provider-task-1",
            "2026-11-03T00:00:00Z",
            provider_result_url="https://provider.example/result.png",
        )
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["url"], "/uploads/recovered.png")
        self.assertEqual(
            versions[0]["providerResultUrl"],
            "https://provider.example/result.png",
        )


if __name__ == "__main__":
    unittest.main()
