from __future__ import annotations

import base64
import http.client
import json
import os
import tempfile
import threading
import unittest

import canvas_store
import server
import tenant_bootstrap
import tenant_context
import tenant_registry


class ProjectShareTests(unittest.TestCase):
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
            b"project-share-0123456789abcdef01"
        ).decode("ascii")
        alpha = tenant_bootstrap.create_tenant("alpha", name="Alpha")
        beta = tenant_bootstrap.create_tenant("beta", name="Beta")
        tenant_registry.create_account(
            alpha["id"],
            "alice",
            "correct-horse",
            role="admin",
        )
        tenant_registry.create_account(beta["id"], "bob", "correct-horse")
        self.alpha = alpha
        self.beta = beta
        with tenant_context.use_tenant(alpha["id"], alpha["slug"]):
            self.project_id = canvas_store.create_project("Shared project")
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

    def request(self, method, path, body="", cookie="", extra_headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": "yjapi.pduoduo.vip"}
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

    def login(self, username="alice"):
        status, headers, _ = self.request(
            "POST",
            "/api/auth/login",
            json.dumps({"username": username, "password": "correct-horse"}),
            extra_headers={"Origin": "https://yj.pduoduo.vip"},
        )
        self.assertEqual(status, 200)
        return headers.get("Set-Cookie", "").split(";", 1)[0]

    def create_share(self):
        cookie = self.login()
        status, _, body = self.request(
            "POST",
            f"/api/projects/{self.project_id}/shares",
            "{}",
            cookie=cookie,
            extra_headers={"Origin": "https://yj.pduoduo.vip"},
        )
        self.assertEqual(status, 200, body.decode("utf-8"))
        return json.loads(body)

    def test_anonymous_share_can_read_only(self):
        share = self.create_share()
        status, _, body = self.request(
            "GET",
            f"/api/share/{share['token']}/project",
        )
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["project"]["id"], self.project_id)
        self.assertTrue(payload["share"]["readOnly"])

        status, _, _ = self.request(
            "GET",
            f"/api/share/{share['token']}/workspace-index",
        )
        self.assertEqual(status, 200)
        status, _, _ = self.request(
            "GET",
            f"/api/share/{share['token']}/workspace-nodes?episodeId=EP001",
        )
        self.assertEqual(status, 200)

        status, _, body = self.request(
            "POST",
            f"/api/share/{share['token']}/project",
            "{}",
            extra_headers={"Origin": "https://yj.pduoduo.vip"},
        )
        self.assertEqual(status, 405)
        self.assertEqual(json.loads(body)["code"], "SHARE_READ_ONLY")

    def test_revoked_share_is_immediately_unavailable(self):
        share = self.create_share()
        cookie = self.login()
        status, _, _ = self.request(
            "DELETE",
            f"/api/projects/{self.project_id}/shares/{share['id']}",
            cookie=cookie,
            extra_headers={"Origin": "https://yj.pduoduo.vip"},
        )
        self.assertEqual(status, 200)
        status, _, body = self.request(
            "GET",
            f"/api/share/{share['token']}/project",
        )
        self.assertEqual(status, 410)
        self.assertEqual(json.loads(body)["code"], "SHARE_INVALID")

    def test_share_token_is_tenant_isolated_and_guess_proof(self):
        share = self.create_share()
        status, _, body = self.request(
            "GET",
            f"/api/share/{share['token']}/project",
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["share"]["projectId"], self.project_id)

        status, _, body = self.request(
            "GET",
            "/api/share/not-a-real-token/project",
        )
        self.assertEqual(status, 410)
        self.assertEqual(json.loads(body)["code"], "SHARE_INVALID")


if __name__ == "__main__":
    unittest.main()
