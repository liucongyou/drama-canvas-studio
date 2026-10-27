from __future__ import annotations

import base64
import http.client
import os
import tempfile
import threading
import unittest

import server
import tenant_bootstrap
import tenant_context
import tenant_registry


class TenantHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_env = {
            key: os.environ.get(key)
            for key in (
                "CANVAS_MULTI_TENANT",
                "CANVAS_TENANT_DATA_ROOT",
                "CANVAS_BASE_DOMAIN",
                "CANVAS_MASTER_KEY",
                "CANVAS_COOKIE_SECURE",
            )
        }
        os.environ["CANVAS_MULTI_TENANT"] = "1"
        os.environ["CANVAS_TENANT_DATA_ROOT"] = self.temp.name
        os.environ["CANVAS_BASE_DOMAIN"] = "example.com"
        os.environ["CANVAS_COOKIE_SECURE"] = "0"
        os.environ["CANVAS_MASTER_KEY"] = "base64:" + base64.urlsafe_b64encode(
            b"abcdef0123456789abcdef0123456789"
        ).decode("ascii")
        tenant_bootstrap.create_tenant("alpha", name="Alpha")
        self.httpd = server.CanvasHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def request(self, method, path, host, cookie=""):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": host}
        if cookie:
            headers["Cookie"] = cookie
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        body = response.read()
        headers_out = dict(response.getheaders())
        connection.close()
        return response.status, headers_out, body

    def test_main_domain_is_404_and_invite_establishes_session(self):
        status, _, _ = self.request("GET", "/", "example.com")
        self.assertEqual(status, 404)

        status, _, _ = self.request("GET", "/api/projects", "alpha.example.com")
        self.assertEqual(status, 401)

        tenant = tenant_registry.get_tenant_by_slug("alpha")
        invite = tenant_registry.create_invite(tenant["id"], ttl_seconds=3600)
        status, headers, _ = self.request(
            "GET",
            f"/invite/{invite['token']}",
            "alpha.example.com",
        )
        self.assertEqual(status, 302)
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        self.assertTrue(cookie.startswith("canvas_session="))

        status, _, body = self.request(
            "GET",
            "/api/tenant/session",
            "alpha.example.com",
            cookie,
        )
        self.assertEqual(status, 200)
        self.assertIn(b'"slug": "alpha"', body)


if __name__ == "__main__":
    unittest.main()

