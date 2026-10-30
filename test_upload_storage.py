from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from unittest import mock

import server


class UploadStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.handler = object.__new__(server.Handler)
        self.sent = []

    def tearDown(self):
        self.temp.cleanup()

    def send(self, status, payload):
        self.sent.append((status, payload))
        return status, payload

    def test_stream_upload_archives_to_active_storage(self):
        image = b"\x89PNG\r\n\x1a\n" + b"a" * 32
        self.handler.headers = {
            "X-File-Name": "role.png",
            "X-File-Type": "image/png",
            "Content-Length": str(len(image)),
        }
        self.handler.rfile = io.BytesIO(image)
        storage = {
            "status": "stored",
            "provider": "aliyun_oss",
            "bucket": "bucket",
            "key": "drama-canvas/image/2026/10/hash.png",
        }
        with (
            mock.patch.object(server, "_upload_dir", return_value=self.temp.name),
            mock.patch.object(
                server.provider_catalog,
                "storage_config",
                return_value={"enabled": True, "provider": "aliyun_oss"},
            ),
            mock.patch.object(server.media_storage, "is_ready", return_value=True),
            mock.patch.object(
                server.media_storage,
                "persist_local_url",
                return_value=storage,
            ) as persist,
            mock.patch.object(
                server.media_storage,
                "public_url",
                return_value="https://bucket.oss-cn-beijing.aliyuncs.com/key",
            ),
            mock.patch.object(server.Handler, "_send", side_effect=self.send),
        ):
            server.Handler._handle_stream_upload(self.handler)

        self.assertEqual(self.sent[0][0], 200)
        payload = self.sent[0][1]
        self.assertEqual(payload["storage"], storage)
        self.assertTrue(payload["remoteUrl"].startswith("https://bucket."))
        persist.assert_called_once()
        self.assertEqual(persist.call_args.args[0], payload["url"])

    def test_storage_failure_keeps_local_file_and_reports_remote_failure(self):
        image = b"\x89PNG\r\n\x1a\n" + b"b" * 32
        self.handler.headers = {
            "X-File-Name": "role.png",
            "X-File-Type": "image/png",
            "Content-Length": str(len(image)),
        }
        self.handler.rfile = io.BytesIO(image)
        with (
            mock.patch.object(server, "_upload_dir", return_value=self.temp.name),
            mock.patch.object(
                server.provider_catalog,
                "storage_config",
                return_value={"enabled": True, "provider": "aliyun_oss"},
            ),
            mock.patch.object(server.media_storage, "is_ready", return_value=True),
            mock.patch.object(
                server.media_storage,
                "persist_local_url",
                side_effect=RuntimeError("SignatureDoesNotMatch"),
            ),
            mock.patch.object(server.Handler, "_send", side_effect=self.send),
        ):
            server.Handler._handle_stream_upload(self.handler)

        self.assertEqual(self.sent[0][0], 502)
        self.assertEqual(self.sent[0][1]["code"], "STORAGE_UPLOAD_FAILED")
        local_url = str(self.sent[0][1]["localUrl"])
        self.assertTrue(local_url.startswith("/uploads/"))
        self.assertTrue(
            __import__("os").path.isfile(
                __import__("os").path.join(
                    self.temp.name, local_url.removeprefix("/uploads/")
                )
            )
        )

    def test_json_upload_archives_to_active_storage(self):
        image = b"\x89PNG\r\n\x1a\n" + b"c" * 32
        self.handler.headers = {}
        storage = {
            "status": "stored",
            "provider": "aliyun_oss",
            "bucket": "bucket",
            "key": "drama-canvas/image/2026/10/hash.png",
        }
        with (
            mock.patch.object(server, "_upload_dir", return_value=self.temp.name),
            mock.patch.object(
                server.provider_catalog,
                "storage_config",
                return_value={"enabled": True, "provider": "aliyun_oss"},
            ),
            mock.patch.object(server.media_storage, "is_ready", return_value=True),
            mock.patch.object(
                server.media_storage,
                "persist_local_url",
                return_value=storage,
            ),
            mock.patch.object(
                server.media_storage,
                "public_url",
                return_value="https://bucket.example/key",
            ),
            mock.patch.object(server.Handler, "_send", side_effect=self.send),
        ):
            server.Handler._handle_upload(self.handler, {
                "name": "role.png",
                "dataUrl": "data:image/png;base64,"
                + base64.b64encode(image).decode("ascii"),
            })

        self.assertEqual(self.sent[0][0], 200)
        self.assertEqual(self.sent[0][1]["storage"], storage)


if __name__ == "__main__":
    unittest.main()
