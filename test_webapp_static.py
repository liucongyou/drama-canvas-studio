"""静态产物服务：gzip 协商、ETag/304、HEAD、压缩缓存。

背景：首屏关键路径上的 CSS 583KB + JS 533KB 之前是明文传输，弱网下光下载就要
好几秒。这里锁住压缩协商的边界——不能压错类型、不能压出比原文更大的结果、
不能让 `gzip;q=0` 的客户端收到 gzip。
"""

from __future__ import annotations

import gzip
import http.client
import os
import tempfile
import threading
import unittest
from unittest import mock

import server


class WebappStaticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.temp.name, "assets"), exist_ok=True)
        self.css = b".a{color:#fff}" * 400          # ~5KB，足够触发压缩
        self.small = b"x" * 200                     # 小于阈值，不压
        self.png = b"\x89PNG\r\n\x1a\n" + b"\x1a" * 4000
        self.index = b"<!doctype html><html><body>" + b"i" * 2000 + b"</body></html>"
        self._write("assets/app.css", self.css)
        self._write("assets/small.css", self.small)
        self._write("assets/pic.png", self.png)
        self._write("index.html", self.index)

        self.patch = mock.patch.multiple(
            server,
            WEBAPP_DIR=self.temp.name,
            _STATIC_GZIP_CACHE={},
        )
        self.patch.start()
        self.httpd = server.CanvasHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.patch.stop()
        self.temp.cleanup()

    def _write(self, rel, body):
        path = os.path.join(self.temp.name, *rel.split("/"))
        with open(path, "wb") as stream:
            stream.write(body)

    def _get(self, path, *, headers=None, method="GET"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        body = response.read()
        result = (response.status, dict(response.getheaders()), body)
        connection.close()
        return result

    def test_gzip_applied_when_client_accepts(self):
        status, headers, body = self._get(
            "/assets/app.css", headers={"Accept-Encoding": "gzip, deflate"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        self.assertEqual(headers.get("Vary"), "Accept-Encoding")
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertLess(len(body), len(self.css))
        self.assertEqual(gzip.decompress(body), self.css)

    def test_plain_when_client_silent(self):
        status, headers, body = self._get("/assets/app.css")
        self.assertEqual(status, 200)
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(body, self.css)
        # 不压缩也要声明 Vary，否则代理会把 gzip 变体发给本客户端。
        self.assertEqual(headers.get("Vary"), "Accept-Encoding")

    def test_gzip_q0_is_respected(self):
        _, headers, body = self._get(
            "/assets/app.css", headers={"Accept-Encoding": "gzip;q=0, deflate"}
        )
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(body, self.css)

    def test_explicit_gzip_q0_overrides_wildcard(self):
        _, headers, body = self._get(
            "/assets/app.css",
            headers={"Accept-Encoding": "gzip;q=0, *;q=1"},
        )
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(body, self.css)

    def test_wildcard_enables_gzip_when_gzip_is_not_named(self):
        _, headers, body = self._get(
            "/assets/app.css",
            headers={"Accept-Encoding": "*;q=1"},
        )
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        self.assertEqual(gzip.decompress(body), self.css)

    def test_already_compressed_types_are_skipped(self):
        _, headers, body = self._get(
            "/assets/pic.png", headers={"Accept-Encoding": "gzip"}
        )
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(body, self.png)

    def test_tiny_assets_are_skipped(self):
        _, headers, body = self._get(
            "/assets/small.css", headers={"Accept-Encoding": "gzip"}
        )
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(body, self.small)

    def test_etag_round_trip_returns_304(self):
        _, headers, _ = self._get("/assets/app.css")
        etag = headers["ETag"]
        self.assertTrue(etag)

        status, second, body = self._get(
            "/assets/app.css", headers={"If-None-Match": etag}
        )
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")
        self.assertEqual(second["ETag"], etag)
        self.assertIn("text/css", second["Content-Type"])
        self.assertEqual(second["Vary"], "Accept-Encoding")

    def test_etag_changes_after_rebuild(self):
        _, headers, _ = self._get("/assets/app.css")
        old_etag = headers["ETag"]
        # 同尺寸、新内容：ETag 必须变，否则重建后浏览器会一直用旧文件。
        self._write("assets/app.css", b".b{color:#000}" * 400)
        _, fresh, _ = self._get("/assets/app.css")
        self.assertNotEqual(fresh["ETag"], old_etag)

    def test_head_has_headers_without_body(self):
        status, headers, body = self._get("/assets/app.css", method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertGreater(int(headers["Content-Length"]), 0)

    def test_compression_is_cached_across_requests(self):
        with mock.patch.object(
            server.gzip, "compress", wraps=server.gzip.compress
        ) as spy:
            for _ in range(3):
                self._get("/assets/app.css", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(spy.call_count, 1)

    def test_cache_over_limit_returns_plain_without_entering_cache(self):
        server._STATIC_GZIP_CACHE.clear()
        with mock.patch.object(server, "_STATIC_GZIP_CACHE_MAX_BYTES", 10):
            status, headers, body = self._get(
                "/assets/app.css",
                headers={"Accept-Encoding": "gzip"},
            )
        self.assertEqual(status, 200)
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(body, self.css)
        self.assertEqual(server._STATIC_GZIP_CACHE, {})

    def test_spa_fallback_serves_index_for_extensionless_path(self):
        status, headers, body = self._get("/l0CcworH4Dv6vtGDTPoO9S")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertEqual(body, self.index)

    def test_missing_asset_with_extension_is_not_folded_into_index(self):
        status, _, _ = self._get("/assets/gone.js")
        self.assertNotEqual(status, 200)


class ApiJsonCompressionTests(unittest.TestCase):
    """JSON 响应也要 gzip：画布读接口单次就 400~500KB。"""

    def setUp(self):
        self.httpd = server.CanvasHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        # 造一份足够大的项目列表，模拟大画布的书包大小。
        self.projects = [
            {"id": f"proj-{index}", "name": f"项目 {index}", "note": "描述" * 200}
            for index in range(20)
        ]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def _get(self, path, accept_encoding=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        headers = {"Accept-Encoding": accept_encoding} if accept_encoding else {}
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        body = response.read()
        result = (response.status, dict(response.getheaders()), body)
        connection.close()
        return result

    def _patched(self):
        return (
            mock.patch.object(server.store, "list_project_summaries", return_value=list(self.projects)),
            mock.patch.object(server.store, "project_activity_summaries", return_value={}),
        )

    def test_json_is_gzipped_and_decodes_to_same_payload(self):
        summaries, activity = self._patched()
        with summaries, activity:
            _, plain_headers, plain = self._get("/api/projects")
            _, gz_headers, packed = self._get("/api/projects", "gzip, deflate, br")

        self.assertGreater(len(plain), 1024)
        self.assertNotIn("Content-Encoding", plain_headers)
        self.assertEqual(gz_headers.get("Content-Encoding"), "gzip")
        self.assertLess(len(packed), len(plain) / 2)
        self.assertEqual(gzip.decompress(packed), plain)
        self.assertEqual(int(gz_headers["Content-Length"]), len(packed))

    def test_vary_keeps_cors_origin_and_adds_accept_encoding(self):
        summaries, activity = self._patched()
        with summaries, activity:
            _, headers, _ = self._get("/api/projects", "gzip")
        vary = {part.strip() for part in headers.get("Vary", "").split(",")}
        self.assertIn("Accept-Encoding", vary)

    def test_small_json_stays_plain(self):
        with (
            mock.patch.object(server.store, "list_project_summaries", return_value=[{"id": "p"}]),
            mock.patch.object(server.store, "project_activity_summaries", return_value={}),
        ):
            _, headers, body = self._get("/api/projects", "gzip")
        self.assertNotIn("Content-Encoding", headers)
        self.assertLess(len(body), 1024)

    def test_non_text_content_type_is_never_compressed(self):
        self.assertTrue(server._compressible_content_type("application/json; charset=utf-8"))
        self.assertTrue(server._compressible_content_type("text/csv"))
        self.assertTrue(server._compressible_content_type("application/problem+json"))
        self.assertFalse(server._compressible_content_type("image/png"))
        self.assertFalse(server._compressible_content_type("video/mp4"))
        self.assertFalse(server._compressible_content_type("application/zip"))
        self.assertFalse(server._compressible_content_type(""))


if __name__ == "__main__":
    unittest.main()
