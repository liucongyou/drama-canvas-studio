from __future__ import annotations

import os
import json
import tempfile
import unittest
import hashlib
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


if __name__ == "__main__":
    unittest.main()
