from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from unittest import mock

import server


class UploadStorageHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.httpd = server.CanvasHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def test_http_upload_returns_storage_descriptor(self):
        image = b"\x89PNG\r\n\x1a\n" + b"d" * 32
        storage = {
            "status": "stored",
            "provider": "aliyun_oss",
            "bucket": "bucket",
            "key": "drama-canvas/image/2026/10/hash.png",
            "requestId": "request-id",
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
        ):
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            connection.request(
                "POST",
                "/api/upload/stream",
                body=image,
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-File-Name": "role.png",
                    "X-File-Type": "image/png",
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            connection.close()

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["storage"]["provider"], "aliyun_oss")
        self.assertEqual(payload["storage"]["bucket"], "bucket")
        self.assertEqual(payload["remoteUrl"], "https://bucket.example/key")


if __name__ == "__main__":
    unittest.main()
