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

import canvas_feed_contract
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

    def request(self, method, path, host, cookie="", body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": host}
        if cookie:
            headers["Cookie"] = cookie
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection.request(method, path, headers=headers, body=payload)
        response = connection.getresponse()
        body = response.read()
        headers_out = dict(response.getheaders())
        connection.close()
        return response.status, headers_out, body

    def raw_request(self, method, path, host, cookie="", body=b"", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        request_headers = {"Host": host, **(headers or {})}
        if cookie:
            request_headers["Cookie"] = cookie
        connection.request(method, path, headers=request_headers, body=body)
        response = connection.getresponse()
        response_body = response.read()
        headers_out = dict(response.getheaders())
        connection.close()
        return response.status, headers_out, response_body

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

    def test_stream_upload_accepts_real_media_bytes_and_rejects_fake_image(self):
        tenant = tenant_registry.get_tenant_by_slug("alpha")
        invite = tenant_registry.create_invite(tenant["id"], ttl_seconds=3600)
        status, headers, _ = self.request(
            "GET",
            f"/invite/{invite['token']}",
            "alpha.example.com",
        )
        self.assertEqual(status, 302)
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 80
        status, _, body = self.raw_request(
            "POST",
            "/api/upload/stream",
            "alpha.example.com",
            cookie,
            png,
            {
                "Content-Type": "application/octet-stream",
                "X-File-Name": "test-image.png",
                "X-File-Type": "image/png",
            },
        )
        self.assertEqual(status, 200)
        uploaded = json.loads(body)
        self.assertTrue(uploaded["url"].startswith("/uploads/"))
        self.assertEqual(uploaded["sha256"], __import__("hashlib").sha256(png).hexdigest())

        status, _, body = self.raw_request(
            "POST",
            "/api/upload/stream",
            "alpha.example.com",
            cookie,
            b"not-an-image",
            {
                "Content-Type": "application/octet-stream",
                "X-File-Name": "fake.png",
                "X-File-Type": "image/png",
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("文件内容与扩展名不匹配", json.loads(body)["error"])

    def test_position_save_uses_position_version_not_generic_updated_at(self):
        tenant = tenant_registry.get_tenant_by_slug("alpha")
        invite = tenant_registry.create_invite(tenant["id"], ttl_seconds=3600)
        status, headers, _ = self.request(
            "GET",
            f"/invite/{invite['token']}",
            "alpha.example.com",
        )
        self.assertEqual(status, 302)
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        status, _, body = self.request(
            "POST",
            "/api/projects",
            "alpha.example.com",
            cookie,
            {"title": "位置冲突测试"},
        )
        self.assertEqual(status, 200)
        pid = json.loads(body)["id"]
        node = {
            "id": "node-position-test",
            "type": "image",
            "title": "角色｜测试（图1）",
            "position": {"x": 100, "y": 80},
            "metadata": {
                "prompt": "first",
                "status": "idle",
                "params": {"ratio": "1:1"},
            },
        }
        status, _, _ = self.request(
            "PUT",
            f"/api/projects/{pid}/nodes",
            "alpha.example.com",
            cookie,
            {"nodes": [node]},
        )
        self.assertEqual(status, 200)
        status, _, project_body = self.request(
            "GET",
            f"/api/projects/{pid}",
            "alpha.example.com",
            cookie,
        )
        self.assertEqual(status, 200)
        stored = next(
            item for item in json.loads(project_body)["payload"]["nodes"]
            if item["id"] == node["id"]
        )
        original_updated_at = stored["updatedAt"]
        original_position_updated_at = stored["positionUpdatedAt"]

        status, _, _ = self.request(
            "PUT",
            f"/api/projects/{pid}/nodes/{node['id']}",
            "alpha.example.com",
            cookie,
            {
                "baseUpdatedAt": original_updated_at,
                "metadata": {"prompt": "second", "status": "done", "params": {"ratio": "1:1"}},
            },
        )
        self.assertEqual(status, 200)

        status, _, move_body = self.request(
            "PUT",
            f"/api/projects/{pid}/nodes/positions",
            "alpha.example.com",
            cookie,
            {"positions": [{
                "id": node["id"],
                "position": {"x": 240, "y": 180},
                "basePosition": {"x": 100, "y": 80},
                "basePositionUpdatedAt": original_position_updated_at,
            }]},
        )
        self.assertEqual(status, 200)
        moved = json.loads(move_body)
        self.assertEqual(moved["updated"], 1)
        self.assertTrue(moved["updates"][0]["positionUpdatedAt"])

        status, _, conflict_body = self.request(
            "PUT",
            f"/api/projects/{pid}/nodes/positions",
            "alpha.example.com",
            cookie,
            {"positions": [{
                "id": node["id"],
                "position": {"x": 320, "y": 260},
                "basePosition": {"x": 100, "y": 80},
            }]},
        )
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(conflict_body)["code"], "NODE_POSITION_CONFLICT")

    def test_import_preview_and_commit_share_validation_and_revision(self):
        tenant = tenant_registry.get_tenant_by_slug("alpha")
        invite = tenant_registry.create_invite(tenant["id"], ttl_seconds=3600)
        status, headers, _ = self.request(
            "GET",
            f"/invite/{invite['token']}",
            "alpha.example.com",
        )
        self.assertEqual(status, 302)
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        status, _, body = self.request(
            "POST",
            "/api/projects",
            "alpha.example.com",
            cookie,
            {"title": "HTTP 导入测试"},
        )
        self.assertEqual(status, 200)
        pid = json.loads(body)["id"]
        feed = json.loads(
            (Path(__file__).parent / "tests/fixtures/canvas-feed-valid/generic.json")
            .read_text(encoding="utf-8")
        )

        status, _, preview_body = self.request(
            "POST",
            f"/api/projects/{pid}/import/preview",
            "alpha.example.com",
            cookie,
            {"feed_json": feed, "source": {"name": "generic.json"}},
        )
        self.assertEqual(status, 200)
        preview = json.loads(preview_body)
        self.assertTrue(preview["dryRun"])
        self.assertEqual(len(preview["summary"]["diff"]["addedNodeIds"]), 4)

        status, _, _ = self.request(
            "PUT",
            f"/api/projects/{pid}",
            "alpha.example.com",
            cookie,
            {"title": "HTTP 导入测试（并发更新）"},
        )
        self.assertEqual(status, 200)

        status, _, commit_body = self.request(
            "POST",
            f"/api/projects/{pid}/import",
            "alpha.example.com",
            cookie,
            {
                "feed_json": feed,
                "if_revision": preview["revision"],
                "source": {"name": "generic.json"},
            },
        )
        self.assertEqual(status, 200)
        committed = json.loads(commit_body)
        self.assertEqual(committed["revision"], preview["revision"] + 2)
        self.assertIn(
            "WARN_RETRIED_AFTER_REVISION",
            {warning["code"] for warning in committed["warnings"]},
        )

        status, _, project_body = self.request(
            "GET",
            f"/api/projects/{pid}",
            "alpha.example.com",
            cookie,
        )
        self.assertEqual(status, 200)
        project = json.loads(project_body)
        group = project["payload"]["episodeImports"][0]
        self.assertEqual(group["sourceFeedName"], "generic.json")
        self.assertEqual(group["skillVersion"], "7.4.29")

        status, _, assets_body = self.request(
            "GET",
            f"/api/projects/{pid}/assets/export",
            "alpha.example.com",
            cookie,
        )
        self.assertEqual(status, 200)
        asset_record = json.loads(assets_body)
        self.assertEqual(asset_record["schema"], "drama-canvas-assets/v1")
        self.assertTrue(asset_record["assets"])
        self.assertEqual(asset_record["episodes"][0]["skillVersion"], "7.4.29")

        commit_preconditions = []

        def reject_concurrent_commit(plan, if_revision=None):
            commit_preconditions.append((plan.revision, if_revision))
            raise canvas_feed_contract.RevisionConflictError("concurrent write")

        with patch.object(
            server.canvas_feed_contract,
            "commit_import",
            side_effect=reject_concurrent_commit,
        ):
            status, _, conflict_body = self.request(
                "POST",
                f"/api/projects/{pid}/import",
                "alpha.example.com",
                cookie,
                {
                    "feed_json": feed,
                    "if_revision": committed["revision"],
                    "source": {"name": "generic.json"},
                },
            )
        self.assertEqual(status, 409)
        conflict = json.loads(conflict_body)
        self.assertEqual(conflict["code"], "REVISION_CONFLICT")
        self.assertEqual(commit_preconditions[-1], (committed["revision"], committed["revision"]))

        invalid_feed = json.loads(
            (
                Path(__file__).parent
                / "tests/fixtures/canvas-feed-invalid/02-missing-voice-prompts.json"
            ).read_text(encoding="utf-8")
        )
        status, _, invalid_body = self.request(
            "POST",
            f"/api/projects/{pid}/import/preview",
            "alpha.example.com",
            cookie,
            {"feed_json": invalid_feed},
        )
        self.assertEqual(status, 422)
        invalid_result = json.loads(invalid_body)
        self.assertIn(
            "VOICE_PROMPT_MISSING",
            {item["code"] for item in invalid_result["issues"]},
        )
        self.assertEqual(invalid_result["revision"], committed["revision"])


if __name__ == "__main__":
    unittest.main()

