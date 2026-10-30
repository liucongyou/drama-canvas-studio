from __future__ import annotations

import http.client
import json
import os
import socket
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

    def test_range_client_disconnect_is_not_reported_as_server_error(self):
        payload = os.urandom(2 * 1024 * 1024)
        path = os.path.join(self.temp.name, "generated.mp4")
        with open(path, "wb") as stream:
            stream.write(payload)

        with mock.patch.object(server, "_upload_dir", return_value=self.temp.name):
            sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            sock.sendall(
                b"GET /uploads/generated.mp4 HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Range: bytes=0-1048575\r\n"
                b"Connection: close\r\n\r\n"
            )
            headers = sock.recv(128)
            self.assertNotIn(b"Traceback", headers)
            sock.close()

            followup = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            followup.request(
                "GET",
                "/uploads/generated.mp4",
                headers={"Range": "bytes=1048576-2097151"},
            )
            response = followup.getresponse()
            body = response.read()
            followup.close()

        self.assertEqual(response.status, 206)
        self.assertEqual(body, payload[1024 * 1024:])

    def test_range_write_disconnect_closes_only_the_response(self):
        path = os.path.join(self.temp.name, "disconnect.mp4")
        with open(path, "wb") as stream:
            stream.write(b"x" * 16)
        handler = object.__new__(server.Handler)
        handler.command = "GET"
        handler.headers = {"Range": "bytes=0-7"}
        handler.wfile = mock.Mock()
        handler.wfile.write.side_effect = ConnectionResetError(
            104, "Connection reset by peer"
        )
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        handler.close_connection = False
        handler._cors_headers = mock.Mock(return_value={})

        handler._send_upload_file(path, "disconnect.mp4")

        self.assertTrue(handler.close_connection)
        handler.wfile.write.assert_called_once()

    def test_range_write_does_not_hide_real_io_errors(self):
        path = os.path.join(self.temp.name, "io-error.mp4")
        with open(path, "wb") as stream:
            stream.write(b"x" * 16)
        handler = object.__new__(server.Handler)
        handler.command = "GET"
        handler.headers = {"Range": "bytes=0-7"}
        handler.wfile = mock.Mock()
        handler.wfile.write.side_effect = OSError(5, "Input/output error")
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        handler.close_connection = False
        handler._cors_headers = mock.Mock(return_value={})

        with self.assertRaises(OSError):
            handler._send_upload_file(path, "io-error.mp4")

        self.assertFalse(handler.close_connection)


if __name__ == "__main__":
    unittest.main()
