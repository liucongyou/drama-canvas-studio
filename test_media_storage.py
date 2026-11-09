from __future__ import annotations

import os
import json
import tempfile
import unittest
import hashlib
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import media_storage
import provider_catalog


class FakeResponse:
    etag = "etag-value"
    request_id = "request-value"


class FakeOssBucket:
    def __init__(self):
        self.put = None
        self.signed = None

    def put_object(self, key, data, headers=None):
        self.put = (key, data, headers)
        return FakeResponse()

    def put_object_from_file(self, key, filename, headers=None):
        with open(filename, "rb") as handle:
            data = handle.read()
        self.put = (key, data, headers)
        return FakeResponse()

    def sign_url(self, method, key, expires, slash_safe=False):
        self.signed = (method, key, expires, slash_safe)
        return f"https://bucket.oss-cn-beijing.aliyuncs.com/{key}?signed=1"

class MediaStorageTests(unittest.TestCase):
    def test_oss_signature_error_is_actionable(self):
        config = {
            "enabled": True,
            "provider": "aliyun_oss",
            "bucket": "media-bucket",
            "endpoint": "oss-cn-beijing.aliyuncs.com",
            "access_key": "ak",
            "secret_key": "wrong",
        }
        with (
            mock.patch.object(media_storage, "_config", return_value=config),
            mock.patch.object(
                media_storage,
                "_oss_client",
                side_effect=RuntimeError("SignatureDoesNotMatch: signature mismatch"),
            ),
            self.assertRaisesRegex(RuntimeError, "AccessKeySecret.*不匹配"),
        ):
            media_storage.persist_bytes(
                b"data",
                mime="text/plain",
                fallback_ext=".txt",
                kind="test",
            )

    def test_tos_external_public_url_uses_public_endpoint(self):
        descriptor = {
            "provider": "tos",
            "bucket": "media-bucket",
            "key": "drama-canvas/image/example.png",
        }
        with mock.patch.object(
            media_storage.tos_storage,
            "public_presigned_url",
            return_value="https://media-bucket.tos-cn-beijing.volces.com/key?sig=ok",
        ) as sign:
            url = media_storage.public_url(descriptor)
        self.assertIn("tos-cn-beijing.volces.com", url)
        sign.assert_called_once()

    def test_tos_node_read_projects_fresh_public_url(self):
        media_storage.clear_playback_url_cache()
        node = {
            "id": "video-1",
            "type": "video",
            "metadata": {
                "content": "/uploads/video.mp4",
                "storage": {
                    "status": "stored",
                    "provider": "tos",
                    "bucket": "media-bucket",
                    "key": "drama-canvas/video.mp4",
                },
                "params": {
                    "mediaVersions": [{
                        "versionId": "video:task-1",
                        "url": "/uploads/video.mp4",
                        "storage": {
                            "status": "stored",
                            "provider": "tos",
                            "bucket": "media-bucket",
                            "key": "drama-canvas/video.mp4",
                        },
                    }],
                },
            },
        }
        with mock.patch.object(
            media_storage,
            "public_url",
            return_value="https://media-bucket.tos-cn-beijing.volces.com/video?signature=fresh",
        ) as sign:
            projected = media_storage.project_node_media_url(node)
        self.assertEqual(
            projected,
            "https://media-bucket.tos-cn-beijing.volces.com/video?signature=fresh",
        )
        self.assertEqual(node["metadata"]["content"], projected)
        self.assertEqual(
            node["metadata"]["storage"]["key"],
            "drama-canvas/video.mp4",
        )
        self.assertEqual(
            node["metadata"]["params"]["mediaVersions"][0]["url"],
            projected,
        )
        sign.assert_called_once()

        with mock.patch.object(
            media_storage,
            "public_url",
            return_value="https://media-bucket.tos-cn-beijing.volces.com/video?signature=second",
        ) as second_sign:
            repeated = media_storage.project_node_media_url({
                "metadata": {
                    "content": "/uploads/video.mp4",
                    "storage": {
                        "status": "stored",
                        "provider": "tos",
                        "bucket": "media-bucket",
                        "key": "drama-canvas/video.mp4",
                    },
                },
            })
        self.assertEqual(repeated, projected)
        second_sign.assert_not_called()

    def test_legacy_tos_history_version_recovers_from_active_storage_receipt(self):
        media_storage.clear_playback_url_cache()
        descriptor = {
            "status": "stored",
            "provider": "tos",
            "bucket": "media-bucket",
            "key": "drama-canvas/image/stable.png",
        }
        node = {
            "metadata": {
                "content": (
                    "https://media-bucket.tos-cn-beijing.volces.com/"
                    "drama-canvas/image/stable.png?X-Tos-Expires=1"
                ),
                "storage": descriptor,
                "params": {
                    "activeVersionId": "image:task-1",
                    "mediaVersions": [{
                        "versionId": "image:task-1",
                        "url": (
                            "https://media-bucket.tos-cn-beijing.volces.com/"
                            "drama-canvas/image/stable.png?X-Tos-Expires=1"
                        ),
                    }],
                },
            },
        }
        fresh = (
            "https://media-bucket.tos-cn-beijing.volces.com/"
            "drama-canvas/image/stable.png?X-Tos-Expires=999999"
        )
        with mock.patch.object(media_storage, "public_url", return_value=fresh):
            media_storage.project_node_media_url(node)
        version = node["metadata"]["params"]["mediaVersions"][0]
        self.assertEqual(version["url"], fresh)
        self.assertEqual(version["mediaAvailability"], "available")

    def test_legacy_history_does_not_borrow_receipt_from_same_basename_on_other_host(self):
        descriptor = {
            "status": "stored",
            "provider": "tos",
            "bucket": "media-bucket",
            "key": "drama-canvas/image/stable.png",
        }
        node = {
            "metadata": {
                "content": (
                    "https://media-bucket.tos-cn-beijing.volces.com/"
                    "drama-canvas/image/stable.png"
                ),
                "storage": descriptor,
                "params": {
                    "mediaVersions": [{
                        "versionId": "image:unrelated",
                        "url": "https://other-host.example/stable.png",
                    }],
                },
            },
        }
        with mock.patch.object(media_storage, "public_url") as sign:
            media_storage.project_node_media_url(node)
        version = node["metadata"]["params"]["mediaVersions"][0]
        self.assertNotIn("storage", version)
        self.assertNotEqual(version["mediaAvailability"], "available")
        sign.assert_called()

    def test_history_version_without_durable_receipt_is_missing_when_signature_expired(self):
        node = {
            "metadata": {
                "content": "",
                "params": {
                    "mediaVersions": [{
                        "versionId": "image:legacy-1",
                        "url": (
                            "https://bucket.tos-cn-beijing.volces.com/"
                            "image.png?X-Tos-Expires=1&X-Tos-Signature=dead"
                        ),
                    }],
                },
            },
        }
        media_storage.project_node_media_url(node)
        version = node["metadata"]["params"]["mediaVersions"][0]
        self.assertEqual(version["mediaAvailability"], "missing")

    def test_history_version_with_future_tos_expiry_is_not_marked_missing(self):
        node = {
            "metadata": {
                "content": "",
                "params": {
                    "mediaVersions": [{
                        "versionId": "image:live-signature",
                        "url": (
                            "https://bucket.tos-cn-beijing.volces.com/"
                            "image.png?X-Tos-Expires=4102444800"
                        ),
                    }],
                },
            },
        }
        media_storage.project_node_media_url(node)
        version = node["metadata"]["params"]["mediaVersions"][0]
        self.assertEqual(version["mediaAvailability"], "unknown")

    def test_storage_receipt_keeps_local_history_available_after_local_cleanup(self):
        node = {
            "metadata": {
                "content": "/uploads/current.png",
                "params": {
                    "mediaVersions": [{
                        "versionId": "image:tos-backed",
                        "url": "/uploads/generated-old.jpg",
                        "storage": {
                            "status": "stored",
                            "provider": "tos",
                            "bucket": "media-bucket",
                            "key": "drama-canvas/image/generated-old.jpg",
                        },
                    }],
                },
            },
        }
        with (
            mock.patch.object(
                media_storage,
                "public_url",
                return_value="https://media-bucket.tos-cn-beijing.volces.com/generated-old.jpg?signature=fresh",
            ),
            mock.patch.object(
                media_storage,
                "_local_path",
                side_effect=media_storage.MediaStorageError("本地素材不存在"),
            ),
        ):
            media_storage.project_node_media_url(node)
        version = node["metadata"]["params"]["mediaVersions"][0]
        self.assertEqual(version["mediaAvailability"], "available")
        self.assertIn("signature=fresh", version["url"])

    def test_local_history_without_bytes_or_receipt_is_missing(self):
        node = {
            "metadata": {
                "content": "/uploads/current.png",
                "params": {
                    "mediaVersions": [{
                        "versionId": "image:gone",
                        "url": "/uploads/generated-gone.jpg",
                    }],
                },
            },
        }
        with mock.patch.object(
            media_storage,
            "_local_path",
            side_effect=media_storage.MediaStorageError("本地素材不存在"),
        ):
            media_storage.project_node_media_url(node)
        version = node["metadata"]["params"]["mediaVersions"][0]
        self.assertEqual(version["mediaAvailability"], "missing")

    def test_storage_receipt_with_failed_projection_is_not_available(self):
        node = {
            "metadata": {
                "content": "/uploads/image.png",
                "storage": {
                    "status": "stored",
                    "provider": "tos",
                    "bucket": "media-bucket",
                    "key": "drama-canvas/image.png",
                },
                "params": {
                    "mediaVersions": [{
                        "versionId": "image:task-1",
                        "url": "/uploads/image.png",
                        "storage": {
                            "status": "stored",
                            "provider": "tos",
                            "bucket": "media-bucket",
                            "key": "drama-canvas/image.png",
                        },
                    }],
                },
            },
        }
        with mock.patch.object(
            media_storage,
            "public_url",
            side_effect=RuntimeError("signature failed"),
        ):
            media_storage.project_node_media_url(node)
        version = node["metadata"]["params"]["mediaVersions"][0]
        self.assertEqual(version["mediaAvailability"], "missing")

    def test_concurrent_tos_reads_share_one_signature(self):
        media_storage.clear_playback_url_cache()

        def project():
            return media_storage.project_node_media_url({
                "metadata": {
                    "content": "/uploads/video.mp4",
                    "storage": {
                        "status": "stored",
                        "provider": "tos",
                        "bucket": "media-bucket",
                        "key": "drama-canvas/video.mp4",
                    },
                },
            })

        with (
            mock.patch.object(
                media_storage,
                "public_url",
                return_value=(
                    "https://media-bucket.tos-cn-beijing.volces.com/video"
                    "?signature=stable"
                ),
            ) as public_url,
            ThreadPoolExecutor(max_workers=8) as executor,
        ):
            results = list(executor.map(lambda _: project(), range(8)))
        self.assertEqual(len(results), 8)
        self.assertEqual(set(results), {
            "https://media-bucket.tos-cn-beijing.volces.com/video?signature=stable",
        })
        self.assertEqual(public_url.call_count, 1)

    def test_oss_upload_and_signed_url(self):
        config = {
            "enabled": True,
            "provider": "aliyun_oss",
            "bucket": "media-bucket",
            "endpoint": "oss-cn-beijing.aliyuncs.com",
            "access_key": "ak",
            "secret_key": "sk",
            "prefix": "drama-canvas/",
            "url_ttl_seconds": 3600,
        }
        bucket = FakeOssBucket()
        with (
            mock.patch.object(media_storage, "_config", return_value=config),
            mock.patch.object(media_storage, "_oss_client", return_value=bucket),
        ):
            descriptor = media_storage.persist_bytes(
                b"image-bytes",
                mime="image/png",
                fallback_ext=".png",
                kind="image",
            )
            url = media_storage.public_url(descriptor)
        self.assertEqual(descriptor["provider"], "aliyun_oss")
        self.assertTrue(descriptor["key"].startswith("drama-canvas/image/"))
        self.assertEqual(bucket.put[1], b"image-bytes")
        self.assertIn("signed=1", url)

    def test_qiniu_upload_uses_public_domain(self):
        config = {
            "enabled": True,
            "provider": "qiniu",
            "bucket": "media-bucket",
            "public_domain": "https://cdn.example.com",
            "access_key": "ak",
            "secret_key": "sk",
            "prefix": "drama-canvas/",
            "url_ttl_seconds": 3600,
        }

        class FakeAuth:
            def upload_token(self, bucket, key, expires):
                self.args = (bucket, key, expires)
                return "token"

        class FakeInfo:
            status_code = 200
            error = ""

        auth = FakeAuth()
        with (
            mock.patch.object(media_storage, "_config", return_value=config),
            mock.patch.object(media_storage, "_qiniu_auth", return_value=auth),
            mock.patch("qiniu.put_data", return_value=({"hash": "h"}, FakeInfo())),
        ):
            descriptor = media_storage.persist_bytes(
                b"audio-bytes",
                mime="audio/mpeg",
                fallback_ext=".mp3",
                kind="audio",
            )
            url = media_storage.public_url(descriptor)
        self.assertEqual(descriptor["provider"], "qiniu")
        self.assertTrue(descriptor["key"].startswith("drama-canvas/audio/"))
        self.assertTrue(url.startswith("https://cdn.example.com/"))

    def test_local_video_upload_uses_streaming_sdk_and_keeps_hash(self):
        config = {
            "enabled": True,
            "provider": "aliyun_oss",
            "bucket": "media-bucket",
            "endpoint": "oss-cn-beijing.aliyuncs.com",
            "access_key": "ak",
            "secret_key": "sk",
            "prefix": "drama-canvas/",
            "url_ttl_seconds": 3600,
        }
        raw = b"video-stream" * 10000
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "video.mp4")
            with open(path, "wb") as handle:
                handle.write(raw)
            bucket = FakeOssBucket()
            with (
                mock.patch.object(media_storage, "_config", return_value=config),
                mock.patch.object(media_storage, "_oss_client", return_value=bucket),
                mock.patch.object(
                    media_storage.tenant_context,
                    "current_upload_dir",
                    return_value=tmp,
                ),
            ):
                descriptor = media_storage.persist_local_url(
                    "/uploads/video.mp4",
                    kind="video",
                    fallback_ext=".mp4",
                )
        self.assertEqual(descriptor["size"], len(raw))
        self.assertEqual(descriptor["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(bucket.put[1], raw)

    def test_storage_config_accepts_oss_and_qiniu(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.local.json")
            with mock.patch.object(provider_catalog, "LOCAL_CONFIG_PATH", path):
                oss = provider_catalog.save_storage_config({
                    "enabled": True,
                    "provider": "aliyun_oss",
                    "bucket": "bucket",
                    "endpoint": "oss-cn-beijing.aliyuncs.com",
                    "access_key": "ak",
                    "secret_key": "sk",
                })
                self.assertFalse(oss["enabled"])
                oss = provider_catalog.activate_storage_provider("aliyun_oss")
                self.assertTrue(oss["ready"])

                qiniu = provider_catalog.save_storage_config({
                    "enabled": True,
                    "provider": "qiniu",
                    "bucket": "bucket",
                    "public_domain": "https://cdn.example.com",
                    "access_key": "ak",
                    "secret_key": "sk",
                })
                self.assertEqual(qiniu["provider"], "aliyun_oss")
                self.assertTrue(qiniu["enabled"])
                qiniu = provider_catalog.activate_storage_provider("qiniu")
                self.assertTrue(qiniu["ready"])

    def test_storage_runtime_uses_active_provider_profile(self):
        storage = {
            "enabled": True,
            "provider": "aliyun_oss",
            "active_provider": "aliyun_oss",
            "bucket": "",
            "endpoint": "api.imgbb.com",
            "access_key": "",
            "secret_key": "",
            "providers": {
                "aliyun_oss": {
                    "bucket": "active-bucket",
                    "region": "cn-beijing",
                    "endpoint": "oss-cn-beijing.aliyuncs.com",
                    "access_key": "profile-ak",
                    "secret_key": "profile-sk",
                },
            },
        }
        with mock.patch.object(
            provider_catalog,
            "load_config",
            return_value={"storage": storage},
        ):
            resolved = provider_catalog.storage_config()

        self.assertEqual(resolved["bucket"], "active-bucket")
        self.assertEqual(resolved["endpoint"], "oss-cn-beijing.aliyuncs.com")
        self.assertEqual(resolved["access_key"], "profile-ak")
        self.assertEqual(resolved["secret_key"], "profile-sk")
        self.assertTrue(media_storage.is_ready(resolved))

    def test_storage_test_failure_is_persisted_without_exposing_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.local.json")
            with mock.patch.object(provider_catalog, "LOCAL_CONFIG_PATH", path):
                provider_catalog.save_storage_config({
                    "enabled": True,
                    "provider": "aliyun_oss",
                    "bucket": "bucket",
                    "endpoint": "oss-cn-beijing.aliyuncs.com",
                    "access_key": "secret-access",
                    "secret_key": "secret-key",
                })
                provider_catalog.record_storage_test_failure(
                    "AccountDisable: overdue"
                )
                storage = provider_catalog.public_storage_config()
                self.assertFalse(storage["ready"])
                self.assertFalse(storage["last_test_ok"])
                self.assertIn("overdue", storage["last_test_error"])
                self.assertNotIn("secret-access", json.dumps(storage))

    def test_switching_storage_provider_clears_previous_test_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.local.json")
            with mock.patch.object(provider_catalog, "LOCAL_CONFIG_PATH", path):
                provider_catalog.save_storage_config({
                    "enabled": True,
                    "provider": "tos",
                    "bucket": "bucket",
                    "region": "cn-beijing",
                    "endpoint": "tos-cn-beijing.volces.com",
                    "internal_endpoint": "tos-cn-beijing.ivolces.com",
                    "access_key": "ak",
                    "secret_key": "sk",
                })
                provider_catalog.record_storage_test_failure("TOS overdue")
                qiniu = provider_catalog.save_storage_config({
                    "enabled": True,
                    "provider": "qiniu",
                    "bucket": "bucket",
                    "public_domain": "https://cdn.example.com",
                    "access_key": "qak",
                    "secret_key": "qsk",
                })
                self.assertFalse(qiniu["enabled"])
                self.assertIn("qiniu", qiniu["profiles"])

    def test_each_storage_provider_keeps_its_own_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.local.json")
            with mock.patch.object(provider_catalog, "LOCAL_CONFIG_PATH", path):
                provider_catalog.save_storage_config({
                    "enabled": True,
                    "provider": "qiniu",
                    "bucket": "qiniu-bucket",
                    "public_domain": "https://cdn.qiniu.example",
                    "access_key": "qak",
                    "secret_key": "qsk",
                })
                provider_catalog.save_storage_config({
                    "enabled": True,
                    "provider": "aliyun_oss",
                    "bucket": "oss-bucket",
                    "endpoint": "oss-cn-beijing.aliyuncs.com",
                    "access_key": "oak",
                    "secret_key": "osk",
                })
                qiniu = provider_catalog.save_storage_config({
                    "enabled": True,
                    "provider": "qiniu",
                })
                self.assertFalse(qiniu["enabled"])
                self.assertEqual(qiniu["profiles"]["qiniu"]["bucket"], "qiniu-bucket")
                self.assertEqual(qiniu["profiles"]["qiniu"]["public_domain"], "https://cdn.qiniu.example")
                self.assertTrue(qiniu["qiniu_ready"])
                self.assertIn("aliyun_oss", qiniu["profiles"])
                self.assertEqual(qiniu["profiles"]["aliyun_oss"]["bucket"], "oss-bucket")
                self.assertEqual(qiniu["profiles"]["qiniu"]["bucket"], "qiniu-bucket")
                self.assertEqual(qiniu["profiles"]["qiniu"]["public_domain"], "https://cdn.qiniu.example")

    def test_deactivated_storage_is_not_reported_as_ready_or_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.local.json")
            with mock.patch.object(provider_catalog, "LOCAL_CONFIG_PATH", path):
                provider_catalog.save_storage_config({
                    "provider": "tos",
                    "bucket": "tos-bucket",
                    "region": "cn-beijing",
                    "endpoint": "tos-cn-beijing.volces.com",
                    "internal_endpoint": "tos-cn-beijing.ivolces.com",
                    "access_key": "ak",
                    "secret_key": "sk",
                })
                active = provider_catalog.activate_storage_provider("tos")
                self.assertTrue(active["enabled"])
                self.assertTrue(active["ready"])
                self.assertTrue(active["active"])

                disabled = provider_catalog.deactivate_storage()
                self.assertFalse(disabled["enabled"])
                self.assertFalse(disabled["ready"])
                self.assertFalse(disabled["active"])
                self.assertTrue(disabled["configured"])
                self.assertIn("tos", disabled["profiles"])

                reloaded = provider_catalog.public_storage_config()
                self.assertFalse(reloaded["enabled"])
                self.assertFalse(reloaded["ready"])
                self.assertFalse(reloaded["active"])
                self.assertTrue(reloaded["tos_ready"])


if __name__ == "__main__":
    unittest.main()
