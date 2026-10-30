from __future__ import annotations

import base64
import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import server
import tenant_bootstrap
import tenant_context
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
        self.tenant = tenant
        beta = tenant_bootstrap.create_tenant("beta", name="Beta")
        tenant_registry.create_account(
            tenant["id"],
            "alice",
            "correct-horse",
            role="admin",
        )
        tenant_registry.create_account(beta["id"], "bob", "correct-horse")
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

    def request(
        self,
        method,
        path,
        body="",
        cookie="",
        origin="https://yj.pduoduo.vip",
        extra_headers=None,
    ):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": "yjapi.pduoduo.vip"}
        if origin:
            headers["Origin"] = origin
        if cookie:
            headers["Cookie"] = cookie
        if body:
            headers["Content-Type"] = "application/json"
        headers.update(extra_headers or {})
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

    def test_tenant_media_supports_cors_head_and_video_ranges(self):
        login_status, login_headers, _ = self.request(
            "POST",
            "/api/auth/login",
            '{"username":"alice","password":"correct-horse"}',
        )
        self.assertEqual(login_status, 200)
        cookie = login_headers.get("Set-Cookie", "").split(";", 1)[0]

        context = tenant_context.make_context(self.tenant["id"], "alpha")
        upload_dir = Path(context.upload_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)
        filename = "media-range-test.png"
        payload = b"\x89PNG\r\n\x1a\n0123456789"
        (upload_dir / filename).write_bytes(payload)

        status, _, _ = self.request("GET", f"/uploads/{filename}")
        self.assertEqual(status, 401)

        status, headers, body = self.request(
            "GET",
            f"/uploads/{filename}",
            cookie=cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, payload)
        self.assertEqual(headers.get("Accept-Ranges"), "bytes")
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "https://yj.pduoduo.vip")
        etag = headers.get("ETag")
        self.assertTrue(etag)
        self.assertTrue(headers.get("Last-Modified"))

        status, headers, body = self.request(
            "GET",
            f"/uploads/{filename}",
            cookie=cookie,
            extra_headers={"If-None-Match": etag},
        )
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")

        status, headers, body = self.request(
            "GET",
            f"/uploads/{filename}",
            cookie=cookie,
            extra_headers={"Range": "bytes=8-12", "If-Range": etag},
        )
        self.assertEqual(status, 206)
        self.assertEqual(body, payload[8:13])
        self.assertEqual(headers.get("Content-Range"), f"bytes 8-12/{len(payload)}")

        status, headers, body = self.request(
            "HEAD",
            f"/uploads/{filename}",
            cookie=cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(headers.get("Content-Length"), str(len(payload)))

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

    def test_local_frontend_can_write_when_accounts_are_bound_to_local_api(self):
        headers = {
            "Origin": "http://127.0.0.1:4175",
            "Host": "127.0.0.1:8890",
        }
        handler = object.__new__(server.Handler)
        handler.headers = headers
        with patch.dict(
            os.environ,
            {
                "CANVAS_AUTH_MODE": "host",
                "CANVAS_ALLOWED_ORIGINS": "http://127.0.0.1:8890",
            },
        ):
            self.assertTrue(handler._origin_allowed())

    def test_change_password_invalidates_old_password_and_renews_session(self):
        status, headers, _ = self.request(
            "POST",
            "/api/auth/login",
            json.dumps({"username": "alice", "password": "correct-horse"}),
        )
        self.assertEqual(status, 200)
        old_cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        status, headers, _ = self.request(
            "POST",
            "/api/auth/change-password",
            json.dumps({"currentPassword": "correct-horse", "newPassword": "new-horse-123"}),
            cookie=old_cookie,
        )
        self.assertEqual(status, 200)
        new_cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        self.assertTrue(new_cookie)
        status, _, _ = self.request("GET", "/api/projects", cookie=old_cookie)
        self.assertEqual(status, 401)
        status, _, _ = self.request(
            "POST",
            "/api/auth/login",
            json.dumps({"username": "alice", "password": "correct-horse"}),
        )
        self.assertEqual(status, 401)
        status, _, _ = self.request(
            "POST",
            "/api/auth/login",
            json.dumps({"username": "alice", "password": "new-horse-123"}),
        )
        self.assertEqual(status, 200)

    def test_admin_can_reset_password_and_toggle_account_status(self):
        status, headers, _ = self.request(
            "POST",
            "/api/auth/login",
            json.dumps({"username": "alice", "password": "correct-horse"}),
        )
        self.assertEqual(status, 200)
        admin_cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        bob = tenant_registry.get_account_by_username("bob")
        self.assertIsNotNone(bob)

        status, _, _ = self.request(
            "POST",
            f"/api/admin/accounts/{bob['id']}/reset-password",
            json.dumps({"password": "bob-new-password"}),
            cookie=admin_cookie,
        )
        self.assertEqual(status, 200)
        status, _, _ = self.request(
            "POST",
            "/api/auth/login",
            json.dumps({"username": "bob", "password": "bob-new-password"}),
        )
        self.assertEqual(status, 200)

        status, _, _ = self.request(
            "POST",
            f"/api/admin/accounts/{bob['id']}/status",
            json.dumps({"status": "disabled"}),
            cookie=admin_cookie,
        )
        self.assertEqual(status, 200)
        status, _, body = self.request(
            "POST",
            "/api/auth/login",
            json.dumps({"username": "bob", "password": "bob-new-password"}),
        )
        self.assertEqual(status, 401)
        self.assertIn(b"AUTH_INVALID", body)

        status, _, _ = self.request(
            "POST",
            f"/api/admin/accounts/{bob['id']}/status",
            json.dumps({"status": "active"}),
            cookie=admin_cookie,
        )
        self.assertEqual(status, 200)
        status, _, _ = self.request(
            "POST",
            "/api/auth/login",
            json.dumps({"username": "bob", "password": "bob-new-password"}),
        )
        self.assertEqual(status, 200)

    def test_admin_cannot_disable_the_only_active_admin(self):
        status, headers, _ = self.request(
            "POST",
            "/api/auth/login",
            json.dumps({"username": "alice", "password": "correct-horse"}),
        )
        self.assertEqual(status, 200)
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        alice = tenant_registry.get_account_by_username("alice")
        status, _, body = self.request(
            "POST",
            f"/api/admin/accounts/{alice['id']}/status",
            json.dumps({"status": "disabled"}),
            cookie=cookie,
        )
        self.assertEqual(status, 400)
        self.assertIn("不能停用当前登录", body.decode("utf-8"))

    def test_projects_are_isolated_between_two_accounts(self):
        def login(username: str) -> str:
            status, headers, _ = self.request(
                "POST",
                "/api/auth/login",
                json.dumps({"username": username, "password": "correct-horse"}),
            )
            self.assertEqual(status, 200)
            return headers.get("Set-Cookie", "").split(";", 1)[0]

        def create_project(cookie: str, title: str) -> str:
            status, _, body = self.request(
                "POST",
                "/api/projects",
                json.dumps({"title": title}),
                cookie=cookie,
            )
            self.assertEqual(status, 200)
            return json.loads(body)["id"]

        def list_project_ids(cookie: str) -> list[str]:
            status, _, body = self.request("GET", "/api/projects", cookie=cookie)
            self.assertEqual(status, 200)
            return [item["id"] for item in json.loads(body)["projects"]]

        alice_cookie = login("alice")
        bob_cookie = login("bob")
        alice_project = create_project(alice_cookie, "alice-only")
        bob_project = create_project(bob_cookie, "bob-only")

        self.assertEqual(list_project_ids(alice_cookie), [alice_project])
        self.assertEqual(list_project_ids(bob_cookie), [bob_project])
        self.assertNotEqual(alice_project, bob_project)

        status, _, _ = self.request(
            "GET",
            f"/api/projects/{alice_project}",
            cookie=bob_cookie,
        )
        self.assertEqual(status, 404)

    def test_only_admin_can_create_managed_accounts(self):
        alice_login = self.request(
            "POST",
            "/api/auth/login",
            '{"username":"alice","password":"correct-horse"}',
        )
        self.assertEqual(alice_login[0], 200)
        alice_cookie = alice_login[1].get("Set-Cookie", "").split(";", 1)[0]
        status, _, body = self.request(
            "POST",
            "/api/admin/accounts",
            '{"username":"charlie","password":"charlie-pass-123"}',
            cookie=alice_cookie,
        )
        self.assertEqual(status, 200)
        self.assertIn(b"charlie", body)

        bob_login = self.request(
            "POST",
            "/api/auth/login",
            '{"username":"bob","password":"correct-horse"}',
        )
        bob_cookie = bob_login[1].get("Set-Cookie", "").split(";", 1)[0]
        status, _, body = self.request(
            "POST",
            "/api/admin/accounts",
            '{"username":"dave","password":"dave-pass-123"}',
            cookie=bob_cookie,
        )
        self.assertEqual(status, 403)
        self.assertIn(b"ADMIN_REQUIRED", body)

    def test_admin_account_list_reports_online_state_and_last_activity(self):
        status, headers, _ = self.request(
            "POST",
            "/api/auth/login",
            json.dumps({"username": "alice", "password": "correct-horse"}),
        )
        self.assertEqual(status, 200)
        admin_cookie = headers.get("Set-Cookie", "").split(";", 1)[0]

        status, headers, _ = self.request(
            "POST",
            "/api/auth/login",
            json.dumps({"username": "bob", "password": "correct-horse"}),
        )
        self.assertEqual(status, 200)
        bob_cookie = headers.get("Set-Cookie", "").split(";", 1)[0]

        status, _, body = self.request("GET", "/api/admin/accounts", cookie=admin_cookie)
        self.assertEqual(status, 200)
        snapshot = json.loads(body)
        accounts = {item["username"]: item for item in snapshot["accounts"]}
        self.assertTrue(accounts["alice"]["online"])
        self.assertTrue(accounts["bob"]["online"])
        self.assertTrue(accounts["bob"]["lastSeenAt"])
        self.assertGreaterEqual(snapshot["onlineCount"], 2)
        online_names = {item["username"] for item in snapshot["onlineUsers"]}
        self.assertTrue({"alice", "bob"}.issubset(online_names))

        status, _, _ = self.request("POST", "/api/auth/logout", "{}", cookie=bob_cookie)
        self.assertEqual(status, 200)
        status, _, body = self.request("GET", "/api/admin/accounts", cookie=admin_cookie)
        self.assertEqual(status, 200)
        accounts = {item["username"]: item for item in json.loads(body)["accounts"]}
        self.assertFalse(accounts["bob"]["online"])


if __name__ == "__main__":
    unittest.main()

