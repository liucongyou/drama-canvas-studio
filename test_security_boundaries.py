# -*- coding: utf-8 -*-
import email.message
import io
import json
import urllib.request
import unittest
from unittest.mock import patch

import network_security
import server


def addrinfo(address):
    family = 10 if ":" in address else 2
    return [(family, 1, 6, "", (address, 443))]


class NetworkSecurityTests(unittest.TestCase):
    def test_rejects_loopback_private_link_local_and_reserved_literals(self):
        urls = [
            "http://127.0.0.1:8890/models",
            "http://10.0.0.1/models",
            "http://192.168.1.10/models",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]:8890/models",
            "http://0.0.0.0/models",
        ]
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    network_security.resolve_remote_url(url)

    def test_rejects_hostname_when_any_resolved_address_is_private(self):
        with patch(
            "network_security.socket.getaddrinfo",
            return_value=[
                *addrinfo("93.184.216.34"),
                *addrinfo("127.0.0.1"),
            ],
        ):
            with self.assertRaises(ValueError):
                network_security.resolve_remote_url("https://example.com/models")

    def test_accepts_public_hostname(self):
        with patch(
            "network_security.socket.getaddrinfo",
            return_value=addrinfo("93.184.216.34"),
        ):
            target = network_security.resolve_remote_url(
                "https://example.com/models?x=1"
            )
        self.assertEqual(target.host, "example.com")
        self.assertEqual(target.port, 443)
        self.assertEqual(target.request_target, "/models?x=1")

    def test_rejects_non_http_protocols_and_userinfo(self):
        for url in (
            "file:///etc/passwd",
            "ftp://example.com/file",
            "https://user:pass@example.com/models",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    network_security.resolve_remote_url(url)

    def test_safe_urlopen_blocks_localhost_before_socket_connect(self):
        request = urllib.request.Request("http://localhost:8890/models")
        with patch(
            "network_security.socket.getaddrinfo",
            return_value=addrinfo("127.0.0.1"),
        ):
            with patch("network_security.socket.create_connection") as connect:
                with self.assertRaises(ValueError):
                    network_security.safe_urlopen(request)
        connect.assert_not_called()


class JsonRequestBoundaryTests(unittest.TestCase):
    def handler(self, content_length: str, body: bytes = b""):
        handler = object.__new__(server.Handler)
        headers = email.message.Message()
        headers["Content-Length"] = str(content_length)
        handler.headers = headers
        handler.rfile = io.BytesIO(body)
        return handler

    def test_rejects_oversized_body_before_reading(self):
        handler = self.handler(server.MAX_JSON_BODY_BYTES + 1)
        with self.assertRaises(server.RequestBoundaryError) as caught:
            handler._read_json()
        self.assertEqual(caught.exception.status, 413)
        self.assertEqual(caught.exception.code, "JSON_BODY_TOO_LARGE")
        self.assertEqual(handler.rfile.tell(), 0)

    def test_rejects_invalid_and_negative_content_length(self):
        for value in ("not-a-number", "-1"):
            with self.subTest(value=value):
                handler = self.handler(value)
                with self.assertRaises(server.RequestBoundaryError) as caught:
                    handler._read_json()
                self.assertEqual(caught.exception.code, "INVALID_CONTENT_LENGTH")

    def test_rejects_excessive_nesting(self):
        value = "leaf"
        for _index in range(server.MAX_JSON_DEPTH + 2):
            value = [value]
        body = json.dumps(value).encode("utf-8")
        handler = self.handler(len(body), body)
        with self.assertRaises(server.RequestBoundaryError) as caught:
            handler._read_json()
        self.assertEqual(caught.exception.code, "JSON_DEPTH_EXCEEDED")

    def test_accepts_valid_json(self):
        body = json.dumps({"ok": True}).encode("utf-8")
        handler = self.handler(len(body), body)
        self.assertEqual(handler._read_json(), {"ok": True})


if __name__ == "__main__":
    unittest.main()
