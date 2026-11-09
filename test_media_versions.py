import unittest
from unittest import mock

import server


class MediaVersionTests(unittest.TestCase):
    def test_active_reference_does_not_override_durable_id_from_content_url(self):
        meta = {
            "content": "/uploads/current-new.png",
            "params": {
                "activeVersionId": "image:stale-old",
                "mediaVersions": [
                    {
                        "versionId": "image:stale-old",
                        "url": "/uploads/wrong-old.png",
                    },
                    {
                        "versionId": "image:current-new",
                        "url": "/uploads/current-new.png",
                    },
                ],
            },
        }

        active = server._active_media_version(meta)

        self.assertEqual(active["versionId"], "image:stale-old")
        self.assertEqual(server._active_reference_url(meta), "/uploads/wrong-old.png")

    def test_active_reference_accepts_resigned_copy_of_same_object(self):
        meta = {
            "content": (
                "https://bucket.tos-cn-beijing.volces.com/image/current.png"
                "?X-Tos-Expires=4102444800&signature=fresh"
            ),
            "params": {
                "activeVersionId": "image:current",
                "mediaVersions": [{
                    "versionId": "image:current",
                    "url": (
                        "https://bucket.tos-cn-beijing.volces.com/"
                        "image/current.png?X-Tos-Expires=4102444800&signature=old"
                    ),
                }],
            },
        }

        active = server._active_media_version(meta)

        self.assertEqual(active["versionId"], "image:current")

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

    def test_explicit_selection_uses_version_id_without_url_precondition(self):
        meta = {
            "content": "/uploads/stale-view.png",
            "status": "done",
            "source": {"type": "generated"},
            "params": {
                "activeVersionId": "image:first",
                "mediaVersionIndex": 0,
                "mediaVersions": [
                    {"versionId": "image:first", "url": "/uploads/first.png"},
                    {"versionId": "image:second", "url": "/uploads/second.png"},
                ],
            },
        }

        server.merge_node_metadata(meta, {
            "content": "/uploads/stale-view.png",
            "status": "done",
            "source": {"type": "generated"},
            "params": {
                "activeVersionId": "image:second",
                "mediaVersionIndex": 1,
            },
            "_mediaVersionSelection": True,
        })

        self.assertEqual(meta["params"]["activeVersionId"], "image:second")
        self.assertEqual(meta["params"]["mediaVersionIndex"], 1)
        self.assertEqual(meta["content"], "/uploads/second.png")

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

    def test_version_selection_uses_active_id_without_content_override(self):
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

        self.assertEqual(meta["content"], "/uploads/v1.mp4")
        self.assertEqual(meta["params"]["mediaVersionIndex"], 0)
        self.assertEqual(meta["params"]["activeVersionId"], "video:task-old")

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

    def test_each_video_version_keeps_its_own_poster_after_selection(self):
        meta = {
            "content": "/uploads/old.mp4",
            "status": "done",
            "params": {},
        }
        server._record_video_version(
            meta,
            "/uploads/old.mp4",
            "task-old",
            "2026-11-08T01:00:00Z",
            thumb_url="/uploads/old.jpg",
        )
        server._record_video_version(
            meta,
            "/uploads/new.mp4",
            "task-new",
            "2026-11-08T02:00:00Z",
            thumb_url="/uploads/new.jpg",
        )

        versions = meta["params"]["videoVersions"]
        self.assertEqual(
            {item["taskId"]: item["thumbUrl"] for item in versions},
            {
                "task-old": "/uploads/old.jpg",
                "task-new": "/uploads/new.jpg",
            },
        )

        server.merge_node_metadata(
            meta,
            {
                "content": "/uploads/old.mp4",
                "source": {"type": "generated"},
                "_mediaVersionSelection": True,
                "params": {"activeVersionId": "video:task-old"},
            },
            node_type="video",
        )

        self.assertEqual(meta["content"], "/uploads/old.mp4")
        self.assertEqual(meta["params"]["thumbUrl"], "/uploads/old.jpg")
        self.assertEqual(
            meta["params"]["activeVersionId"],
            "video:task-old",
        )
        self.assertEqual(
            {item["taskId"]: item["thumbUrl"] for item in meta["params"]["videoVersions"]},
            {
                "task-old": "/uploads/old.jpg",
                "task-new": "/uploads/new.jpg",
            },
        )

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

    def test_select_media_version_is_authoritative_and_rejects_missing(self):
        meta = {
            "content": "/uploads/current.png",
            "status": "done",
            "source": {"type": "generated"},
            "storage": {"status": "stored", "provider": "tos", "bucket": "b", "key": "k/current"},
            "params": {
                "mediaVersions": [
                    {
                        "versionId": "image:old",
                        "url": "/uploads/old.png",
                        "sourceType": "generated",
                    },
                    {
                        "versionId": "image:current",
                        "url": "/uploads/current.png",
                        "sourceType": "generated",
                    },
                ],
                "activeVersionId": "image:current",
                "mediaVersionIndex": 1,
            },
        }
        with mock.patch.object(
            server.media_storage,
            "_local_path",
            return_value="/tmp/old.png",
        ):
            selected = server._select_media_version(meta, "image", "image:old")
        self.assertEqual(selected["versionId"], "image:old")
        self.assertEqual(meta["content"], "/uploads/old.png")
        self.assertEqual(meta["params"]["activeVersionId"], "image:old")
        self.assertEqual(meta["params"]["mediaVersionIndex"], 0)

        with self.assertRaisesRegex(server.MediaVersionError, "不存在"):
            server._select_media_version(meta, "image", "image:not-found")

    def test_select_media_version_does_not_overwrite_current_with_expired_history(self):
        meta = {
            "content": "/uploads/current.png",
            "status": "done",
            "params": {
                "mediaVersions": [
                    {
                        "versionId": "image:expired",
                        "url": "https://bucket.tos-cn-beijing.volces.com/old.png?X-Tos-Expires=1",
                    },
                    {"versionId": "image:current", "url": "/uploads/current.png"},
                ],
                "activeVersionId": "image:current",
                "mediaVersionIndex": 1,
            },
        }
        before = dict(meta)
        with self.assertRaises(server.MediaVersionError) as raised:
            server._select_media_version(meta, "image", "image:expired")
        self.assertEqual(raised.exception.code, "MEDIA_VERSION_MISSING")
        self.assertEqual(meta, before)

    def test_select_reuses_same_object_storage_receipt_and_persists_it(self):
        descriptor = {
            "status": "stored",
            "provider": "tos",
            "bucket": "media-bucket",
            "key": "drama-canvas/image/shared.png",
        }
        stale_url = (
            "https://media-bucket.tos-cn-beijing.volces.com/"
            "drama-canvas/image/shared.png?X-Tos-Expires=1"
        )
        meta = {
            "content": stale_url,
            "status": "done",
            "storage": dict(descriptor),
            "params": {
                "mediaVersions": [
                    {
                        "versionId": "image:same-object",
                        "url": stale_url,
                    },
                    {
                        "versionId": "image:current",
                        "url": stale_url,
                    },
                ],
                "activeVersionId": "image:current",
                "mediaVersionIndex": 1,
            },
        }
        fresh_url = (
            "https://media-bucket.tos-cn-beijing.volces.com/"
            "drama-canvas/image/shared.png?X-Tos-Expires=4102444800"
        )
        server.media_storage.clear_playback_url_cache()
        with mock.patch.object(
            server.media_storage,
            "public_url",
            return_value=fresh_url,
        ):
            selected = server._select_media_version(
                meta,
                "image",
                "image:same-object",
            )
        self.assertEqual(selected["versionId"], "image:same-object")
        self.assertEqual(selected["version"]["storage"], descriptor)
        self.assertEqual(meta["params"]["activeVersionId"], "image:same-object")
        self.assertEqual(meta["storage"], descriptor)
        self.assertEqual(meta["content"], fresh_url)

    def test_select_rejects_foreign_or_expired_history_without_storage_receipt(self):
        meta = {
            "content": "/uploads/current.png",
            "status": "done",
            "storage": {
                "status": "stored",
                "provider": "tos",
                "bucket": "bucket",
                "key": "current.png",
            },
            "params": {
                "mediaVersions": [
                    {
                        "versionId": "image:foreign",
                        "url": (
                            "https://bucket.tos-cn-beijing.volces.com/"
                            "foreign.png?X-Tos-Expires=4102444800"
                        ),
                    },
                    {
                        "versionId": "image:current",
                        "url": "/uploads/current.png",
                    },
                ],
                "activeVersionId": "image:current",
                "mediaVersionIndex": 1,
            },
        }
        before = dict(meta)
        with self.assertRaises(server.MediaVersionError) as raised:
            server._select_media_version(meta, "image", "image:foreign")
        self.assertEqual(raised.exception.code, "MEDIA_VERSION_MISSING")
        self.assertEqual(meta, before)

    def test_local_version_has_stable_id_and_becomes_active_reference(self):
        meta = {
            "content": "/uploads/generated.png",
            "status": "done",
            "params": {"mediaVersions": [{"url": "/uploads/generated.png"}]},
        }
        upload = {
            "url": "/uploads/local.png",
            "sha256": "a" * 64,
            "fileName": "local.png",
            "mime": "image/png",
            "size": 123,
        }
        with (
            mock.patch.object(server.media_storage, "_local_path", return_value="/tmp/local.png"),
            mock.patch.object(server.provider_client, "local_upload_path", return_value="/tmp/local.png"),
        ):
            version = server._append_local_media_version(meta, "image", upload)
        self.assertEqual(version["versionId"], "image:local:" + "a" * 32)
        self.assertEqual(meta["params"]["activeVersionId"], version["versionId"])
        self.assertEqual(server._active_reference_url(meta), "/uploads/local.png")
        self.assertEqual(
            meta["params"]["mediaVersions"][-1]["versionId"],
            version["versionId"],
        )

    def test_upload_receipt_name_fallback_is_persisted(self):
        meta = {
            "content": "/uploads/generated.png",
            "params": {"mediaVersions": [{"url": "/uploads/generated.png"}]},
        }
        upload = {
            "url": "/uploads/local.png",
            "sha256": "c" * 64,
            "name": "images_(7).png",
            "mime": "image/png",
            "size": 456,
        }
        with mock.patch.object(
            server.media_storage,
            "_local_path",
            return_value="/tmp/local.png",
        ):
            version = server._append_local_media_version(meta, "image", upload)
        self.assertEqual(version["fileName"], "images_(7).png")
        self.assertEqual(meta["source"]["fileName"], "images_(7).png")

    def test_active_version_id_wins_over_resigned_tos_url(self):
        meta = {
            "content": (
                "https://bucket.tos-cn-beijing.volces.com/image.png"
                "?X-Tos-Expires=4102444800"
            ),
            "params": {
                "activeVersionId": "image:local:latest",
                "mediaVersions": [
                    {
                        "versionId": "image:task-old",
                        "url": "https://bucket.tos-cn-beijing.volces.com/old.png",
                    },
                    {
                        "versionId": "image:local:latest",
                        "url": "/uploads/latest.png",
                        "sourceType": "local",
                    },
                ],
            },
        }
        with mock.patch.object(
            server.media_storage,
            "_local_path",
            return_value="/tmp/latest.png",
        ):
            active = server._active_media_version(meta)
            reference = server._active_reference_url(meta)
        self.assertEqual(active["versionId"], "image:local:latest")
        self.assertEqual(reference, "/uploads/latest.png")

    def test_reupload_same_sha_updates_one_local_version(self):
        meta = {"content": "", "params": {"mediaVersions": []}}
        upload = {
            "url": "/uploads/local.png",
            "sha256": "b" * 64,
            "fileName": "local.png",
            "mime": "image/png",
            "size": 123,
        }
        with (
            mock.patch.object(server.media_storage, "_local_path", return_value="/tmp/local.png"),
            mock.patch.object(server.provider_client, "local_upload_path", return_value="/tmp/local.png"),
        ):
            first = server._append_local_media_version(meta, "image", upload)
            second = server._append_local_media_version(meta, "image", upload)
        versions = meta["params"]["mediaVersions"]
        self.assertEqual(first["versionId"], second["versionId"])
        self.assertEqual(len([item for item in versions if item["versionId"] == first["versionId"]]), 1)

    def test_empty_version_list_cannot_select_an_arbitrary_current_url(self):
        meta = {
            "content": "/uploads/only-current.png",
            "status": "done",
            "params": {"mediaVersions": []},
        }
        with self.assertRaises(server.MediaVersionError) as raised:
            server._select_media_version(meta, "image", "image:not-recorded")
        self.assertEqual(raised.exception.code, "MEDIA_VERSION_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
