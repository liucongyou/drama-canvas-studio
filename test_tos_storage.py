from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

import provider_catalog
import provider_client
import tos_storage
from requests.structures import CaseInsensitiveDict


class FakePutResult:
    hash_crc64_ecma = "crc64-value"
    etag = "etag-value"
    request_id = "request-value"


class FakeSignedResult:
    signed_url = "https://bucket.tos-cn-beijing.ivolces.com/key?signature=ok"


class FakeHeadResult:
    request_id = "head-request"


class FakeTosClient:
    def __init__(self):
        self.put_args = None
        self.put_file_args = None

    def put_object(self, *args, **kwargs):
        self.put_args = (args, kwargs)
        return FakePutResult()

    def put_object_from_file(self, *args, **kwargs):
        self.put_file_args = (args, kwargs)
        return FakePutResult()

    def pre_signed_url(self, *args, **kwargs):
        self.pre_args = (args, kwargs)
        return FakeSignedResult()

    def head_bucket(self, *args, **kwargs):
        self.head_args = (args, kwargs)
        return FakeHeadResult()


class StorageConfigTests(unittest.TestCase):
    def test_storage_config_redacts_keys_and_preserves_blanks(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "config.local.json")
            with mock.patch.object(provider_catalog, "LOCAL_CONFIG_PATH", config_path):
                storage = provider_catalog.save_storage_config({
                    "enabled": True,
                    "bucket": "drama-originals",
                    "region": "cn-beijing",
                    "endpoint": "https://tos-cn-beijing.volces.com",
                    "internal_endpoint": "tos-cn-beijing.ivolces.com",
                    "prefix": "/drama-canvas/",
                    "access_key": "AK-test-value",
                    "secret_key": "SK-test-value",
                })
                self.assertFalse(storage["enabled"])
                storage = provider_catalog.activate_storage_provider("tos")
                self.assertTrue(storage["ready"])
                self.assertFalse(storage["has_secret_key"] is False)
                self.assertNotIn("AK-test-value", json.dumps(storage))
                self.assertNotIn("SK-test-value", json.dumps(storage))
                self.assertTrue(storage["auto_persist_images"])
                self.assertTrue(storage["auto_persist_videos"])

                storage = provider_catalog.save_storage_config({
                    "bucket": "drama-originals",
                    "access_key": "",
                    "secret_key": "",
                })
                self.assertTrue(storage["has_access_key"])
                self.assertTrue(storage["has_secret_key"])
                saved = provider_catalog.storage_config()
                profile = saved["providers"]["tos"]
                self.assertEqual(profile["access_key"], "AK-test-value")
                self.assertEqual(profile["secret_key"], "SK-test-value")

class TosStorageTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeTosClient()
        self.config = {
            "enabled": True,
            "provider": "tos",
            "bucket": "drama-originals",
            "region": "cn-beijing",
            "endpoint": "tos-cn-beijing.volces.com",
            "internal_endpoint": "tos-cn-beijing.ivolces.com",
            "prefix": "drama-canvas/",
            "access_key": "ak",
            "secret_key": "sk",
            "url_ttl_seconds": 3600,
        }
        self.patches = [
            mock.patch.object(tos_storage, "_config", return_value=self.config),
            mock.patch.object(tos_storage, "_client", return_value=self.client),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()

    def test_original_bytes_are_uploaded_private_and_hash_addressed(self):
        raw = b"\x89PNG\r\n\x1a\noriginal-bytes"
        storage = tos_storage.persist_bytes(
            raw,
            mime="image/png",
            fallback_ext=".png",
            kind="image",
            source={"provider": "ark", "model": "doubao-seedream-5-0-pro", "taskId": "task-1"},
        )
        args, kwargs = self.client.put_args
        self.assertEqual(args[0], "drama-originals")
        self.assertIn(hashlib.sha256(raw).hexdigest(), args[1])
        self.assertEqual(kwargs["content"].getvalue(), raw)
        self.assertEqual(kwargs["content_type"], "image/png")
        self.assertTrue(kwargs["acl"].name.endswith("ACL_Private"))
        self.assertEqual(storage["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertTrue(storage["originalBytes"])
        self.assertEqual(storage["source"]["model"], "doubao-seedream-5-0-pro")

    def test_local_original_uploads_without_reading_whole_file(self):
        raw = b"video-bytes" * 1000
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "video.mp4")
            with open(path, "wb") as handle:
                handle.write(raw)
            storage = tos_storage.persist_local_path(
                path,
                mime="video/mp4",
                fallback_ext=".mp4",
                kind="video",
                source={"provider": "new-apl", "taskId": "task-video"},
            )
        args, kwargs = self.client.put_file_args
        self.assertIn(hashlib.sha256(raw).hexdigest(), args[1])
        self.assertEqual(args[2], path)
        self.assertEqual(kwargs["content_length"], len(raw))
        self.assertEqual(storage["sha256"], hashlib.sha256(raw).hexdigest())

    def test_internal_presigned_url_uses_internal_client(self):
        url = tos_storage.internal_presigned_url({
            "provider": "tos",
            "bucket": "drama-originals",
            "key": "drama-canvas/image/2026/10/test.png",
        })
        self.assertIn("ivolces.com", url)
        self.assertEqual(self.client.pre_args[0][1], "drama-originals")

    def test_connection_uses_head_bucket(self):
        result = tos_storage.test_connection()
        self.assertTrue(result["ok"])
        self.assertEqual(self.client.head_args[0][0], "drama-originals")

    def test_overdue_error_is_mapped_with_request_id(self):
        class FakeServerError(Exception):
            status_code = 403
            message = ""
            request_id = "request-overdue"
            header = CaseInsensitiveDict({"x-tos-ec": "0003-00000024"})

        error = tos_storage._tos_error(FakeServerError(), "TOS 连接测试")
        self.assertIsInstance(error, tos_storage.TosAccountOverdueError)
        self.assertIn("账号已欠费", str(error))
        self.assertIn("request-overdue", str(error))

    def test_ark_reference_uses_tos_presigned_url(self):
        with mock.patch.object(
            tos_storage,
            "internal_presigned_url",
            return_value="https://bucket.tos-cn-beijing.ivolces.com/key?sig=ok",
        ) as sign:
            url = provider_client._ark_reference_url({
                "url": "/uploads/role.png",
                "kind": "image",
                "storage": {
                    "provider": "tos",
                    "bucket": "drama-originals",
                    "key": "drama-canvas/image/role.png",
                },
            })
        self.assertIn("ivolces.com", url)
        sign.assert_called_once()

    def test_seedream_reference_without_tos_is_blocked(self):
        with (
            mock.patch.object(tos_storage, "is_ready", return_value=False),
            mock.patch.object(
                provider_catalog,
                "storage_config",
                return_value={"require_for_ark_portrait": True},
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "TOS"):
                provider_client._ark_reference_url({
                    "url": "/uploads/role.png",
                    "kind": "image",
                    "sourceModel": "doubao-seedream-5-0-pro-260628",
                })

    def test_seedream_reference_older_than_trust_window_is_blocked(self):
        with mock.patch.object(
            provider_catalog,
            "storage_config",
            return_value={"require_for_ark_portrait": True},
        ):
            with self.assertRaisesRegex(RuntimeError, "30 天"):
                provider_client._ark_reference_url({
                    "url": "/uploads/role.png",
                    "kind": "image",
                    "sourceModel": "doubao-seedream-5-0-pro-260628",
                    "generatedAt": "2020-01-01T00:00:00Z",
                    "storage": {
                        "provider": "tos",
                        "bucket": "drama-originals",
                        "key": "drama-canvas/image/role.png",
                    },
                })


if __name__ == "__main__":
    unittest.main()
