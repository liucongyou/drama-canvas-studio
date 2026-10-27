from __future__ import annotations

import base64
import http.client
import os
import tempfile
import threading
import unittest

import server
import tenant_bootstrap
import tenant_registry


class AccountAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_env = {
            key: os.environ.get(key)
            for key in (
                "CANVAS_MULTI_TENANT",
                "CANVAS_AUTH_MODE",
                "CANVAS_TENANT_DATA_ROOT",
                "CANVAS_API_HOST",
                "CANVAS_ALLOWED_ORIGINS",
                "CANVAS_MASTER_KEY",
                "CANVAS_COOKIE_SECURE",
            )
        }
        os.environ["CANVAS_MULTI_TENANT"] = "1"
        os.environ["CANVAS_AUTH_MODE"] = "accounts"
        os.environ["CANVAS_TENANT_DATA_ROOT"] = self.temp.name
        os.environ["CANVAS_API_HOST"] = "yjapi.pduoduo.vip"
        os.environ["CANVAS_ALLOWED_ORIGINS"] = "https://yj.pduoduo.vip"
        os.environ["CANVAS_COOKIE_SECURE"] = "0"
        os.environ["CANVAS_MASTER_KEY"] = "base64:" + base64.urlsafe_b64encode(
            b"account-auth-0123456789abcdef01"
        ).decode("ascii")
        tenant = tenant_bootstrap.create_tenant("alpha", name="Alpha")
        tenant_registry.create_account(tenant["id"], "alice", "correct-horse")
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

    def request(self, method, path, body="", cookie="", origin="https://yj.pduoduo.vip"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": "yjapi.pduoduo.vip"}
        if origin:
            headers["Origin"] = origin
        if cookie:
            headers["Cookie"] = cookie
        if body:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        headers_out = dict(response.getheaders())
        connection.close()
        return response.status, headers_out, payload

    def test_login_sets_http_only_session_and_authorizes_api(self):
        status, headers, _ = self.request(
            "POST",
            "/api/auth/login",
            '{"username":"alice","password":"correct-horse"}',
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "https://yj.pduoduo.vip")
        self.assertIn("HttpOnly", headers.get("Set-Cookie", ""))
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]

        status, _, body = self.request("GET", "/api/projects", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn(b'"projects"', body)

    def test_unauthenticated_api_is_rejected(self):
        status, _, body = self.request("GET", "/api/projects")
        self.assertEqual(status, 401)
        self.assertIn(b"ACCOUNT_AUTH_REQUIRED", body)

    def test_wrong_password_is_rejected(self):
        status, _, body = self.request(
            "POST",
            "/api/auth/login",
            '{"username":"alice","password":"wrong-password"}',
        )
        self.assertEqual(status, 401)
        self.assertIn(b"AUTH_INVALID", body)

    def test_cors_preflight_is_allowed_only_for_configured_origin(self):
        status, headers, _ = self.request("OPTIONS", "/api/projects")
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "https://yj.pduoduo.vip")
        self.assertEqual(headers.get("Access-Control-Allow-Credentials"), "true")

        status, headers, _ = self.request(
            "OPTIONS",
            "/api/projects",
            origin="https://evil.example",
        )
        self.assertEqual(status, 204)
        self.assertNotIn("Access-Control-Allow-Origin", headers)


if __name__ == "__main__":
    unittest.main()

