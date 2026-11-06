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

    def request(
        self,
        method,
        path,
        body="",
        cookie="",
        share_token="",
        extra_headers=None,
    ):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": "yjapi.pduoduo.vip"}
        if cookie:
            headers["Cookie"] = cookie
        if share_token:
            headers[server.CANVAS_SHARE_HEADER] = share_token
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

    def create_share(self, role="viewer"):
        cookie = self.login()
        status, _, body = self.request(
            "POST",
            f"/api/projects/{self.project_id}/shares",
            json.dumps({"role": role}),
            cookie=cookie,
            extra_headers={"Origin": "https://yj.pduoduo.vip"},
        )
        self.assertEqual(status, 200, body.decode("utf-8"))
        return json.loads(body)

    def open_share(self, share):
        status, _, body = self.request(
            "GET",
            f"/api/share/{share['token']}/project",
        )
        self.assertEqual(status, 200, body.decode("utf-8"))
        return share["token"]

    def create_referenced_upload(self):
        with tenant_context.use_tenant(
            self.alpha["id"],
            self.alpha["slug"],
        ):
            upload_dir = server._upload_dir()
            os.makedirs(upload_dir, exist_ok=True)
            filename = "shared-media.png"
            with open(os.path.join(upload_dir, filename), "wb") as stream:
                stream.write(b"%PNG\r\nshared-share-media")
            project = canvas_store.get_project(self.project_id)
            payload = project["payload"]
            node = {
                "id": "image-shared-media",
                "type": "image",
                "title": "角色｜分享测试（图1）",
                "position": {"x": 0, "y": 0},
                "metadata": {
                    "status": "done",
                    "content": f"/uploads/{filename}",
                },
            }
            payload.setdefault("nodes", []).append(node)
            group = payload.setdefault("episodeImports", [{}])[0]
            group.setdefault("nodeIds", []).append(node["id"])
            canvas_store.update_project(
                self.project_id,
                payload=payload,
                if_revision=project["revision"],
            )
        return filename

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

    def test_viewer_cookie_cannot_write(self):
        share = self.create_share("viewer")
        share_cookie = self.open_share(share)
        status, _, body = self.request(
            "PUT",
            f"/api/projects/{self.project_id}/nodes",
            json.dumps({
                "episodeId": "EP001",
                "nodes": [{
                    "id": "viewer-write",
                    "type": "text",
                    "title": "只读不能写",
                }],
            }),
            share_token=share_cookie,
            extra_headers={"Origin": "https://yj.pduoduo.vip"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["code"], "SHARE_READ_ONLY")

    def test_editor_cookie_can_write_but_cannot_manage_shares(self):
        share = self.create_share("editor")
        self.assertTrue(share["canWrite"])
        share_cookie = self.open_share(share)
        node = {
            "id": "editor-write",
            "type": "text",
            "title": "协作可写",
            "position": {"x": 10, "y": 20},
            "metadata": {"prompt": "协作编辑"},
        }
        status, _, body = self.request(
            "PUT",
            f"/api/projects/{self.project_id}/nodes",
            json.dumps({"episodeId": "EP001", "nodes": [node]}),
            share_token=share_cookie,
            extra_headers={"Origin": "https://yj.pduoduo.vip"},
        )
        self.assertEqual(status, 200, body.decode("utf-8"))

        status, _, body = self.request(
            "GET",
            f"/api/projects/{self.project_id}",
            share_token=share_cookie,
        )
        self.assertEqual(status, 200, body.decode("utf-8"))
        self.assertTrue(any(
            item.get("id") == "editor-write"
            for item in json.loads(body)["payload"]["nodes"]
        ))

        status, _, body = self.request(
            "DELETE",
            f"/api/projects/{self.project_id}/shares/{share['id']}",
            share_token=share_cookie,
            extra_headers={"Origin": "https://yj.pduoduo.vip"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(
            json.loads(body)["code"],
            "SHARE_MANAGEMENT_FORBIDDEN",
        )

        for method, path, payload in (
            (
                "PUT",
                f"/api/projects/{self.project_id}",
                json.dumps({"title": "越权重命名"}),
            ),
            (
                "POST",
                f"/api/projects/{self.project_id}/import",
                json.dumps({"feed_json": {}}),
            ),
            (
                "DELETE",
                f"/api/projects/{self.project_id}/episodes/EP001",
                "",
            ),
            (
                "DELETE",
                f"/api/projects/{self.project_id}",
                "",
            ),
        ):
            status, _, body = self.request(
                method,
                path,
                payload,
                share_token=share_cookie,
                extra_headers={"Origin": "https://yj.pduoduo.vip"},
            )
            self.assertEqual(status, 403, (method, path, body))
            self.assertEqual(
                json.loads(body)["code"],
                "SHARE_OWNER_REQUIRED",
            )

    def test_editor_cookie_is_project_scoped(self):
        share = self.create_share("editor")
        share_cookie = self.open_share(share)
        with tenant_context.use_tenant(self.alpha["id"], self.alpha["slug"]):
            other_project_id = canvas_store.create_project("Other project")
        status, _, body = self.request(
            "GET",
            f"/api/projects/{other_project_id}",
            share_token=share_cookie,
        )
        self.assertEqual(status, 410)
        self.assertEqual(json.loads(body)["code"], "SHARE_INVALID")

    def test_viewer_and_editor_tabs_do_not_share_authority(self):
        viewer = self.create_share("viewer")
        editor = self.create_share("editor")
        node = {
            "id": "tab-isolation",
            "type": "text",
            "title": "标签页隔离",
            "metadata": {"prompt": "tab isolation"},
        }
        payload = json.dumps({"episodeId": "EP001", "nodes": [node]})

        status, _, body = self.request(
            "PUT",
            f"/api/projects/{self.project_id}/nodes",
            payload,
            share_token=editor["token"],
            extra_headers={"Origin": "https://yj.pduoduo.vip"},
        )
        self.assertEqual(status, 200, body.decode("utf-8"))

        status, _, body = self.request(
            "PUT",
            f"/api/projects/{self.project_id}/nodes",
            payload,
            share_token=viewer["token"],
            extra_headers={"Origin": "https://yj.pduoduo.vip"},
        )
        self.assertEqual(status, 403, body.decode("utf-8"))
        self.assertEqual(json.loads(body)["code"], "SHARE_READ_ONLY")

    def test_batch_position_sort_survives_fresh_read(self):
        owner_cookie = self.login()
        nodes = [
            {
                "id": f"sort-node-{index}",
                "type": "text",
                "title": f"排序节点 {index}",
                "position": {"x": index * 300, "y": 0},
                "metadata": {"prompt": f"sort {index}"},
            }
            for index in range(3)
        ]
        status, _, body = self.request(
            "PUT",
            f"/api/projects/{self.project_id}/nodes",
            json.dumps({"episodeId": "EP001", "nodes": nodes}),
            cookie=owner_cookie,
            extra_headers={"Origin": "https://yj.pduoduo.vip"},
        )
        self.assertEqual(status, 200, body.decode("utf-8"))

        moves = [
            {
                "id": node["id"],
                "position": {"x": index * 20, "y": 500 + index * 20},
                "seq": index + 1,
            }
            for index, node in enumerate(nodes)
        ]
        status, _, body = self.request(
            "PUT",
            f"/api/projects/{self.project_id}/nodes/positions",
            json.dumps({"episodeId": "EP001", "positions": moves}),
            cookie=owner_cookie,
            extra_headers={"Origin": "https://yj.pduoduo.vip"},
        )
        self.assertEqual(status, 200, body.decode("utf-8"))

        status, _, body = self.request(
            "GET",
            f"/api/projects/{self.project_id}/workspace-nodes?episodeId=EP001",
            cookie=owner_cookie,
        )
        self.assertEqual(status, 200, body.decode("utf-8"))
        positions = {
            row["id"]: row["position"]
            for row in json.loads(body)["nodes"]
        }
        for index, node in enumerate(nodes):
            self.assertEqual(
                positions[node["id"]],
                {"x": index * 20, "y": 500 + index * 20},
            )

    def test_revoked_share_invalidates_cookie_and_new_share_works(self):
        first = self.create_share("editor")
        share_cookie = self.open_share(first)
        owner_cookie = self.login()
        status, _, _ = self.request(
            "DELETE",
            f"/api/projects/{self.project_id}/shares/{first['id']}",
            cookie=owner_cookie,
            extra_headers={"Origin": "https://yj.pduoduo.vip"},
        )
        self.assertEqual(status, 200)

        status, _, body = self.request(
            "GET",
            f"/api/projects/{self.project_id}",
            share_token=share_cookie,
        )
        self.assertEqual(status, 410)
        self.assertEqual(json.loads(body)["code"], "SHARE_INVALID")

        second = self.create_share("viewer")
        self.assertNotEqual(first["token"], second["token"])
        self.assertEqual(self.open_share(second), second["token"])

    def test_anonymous_share_reads_only_project_media(self):
        filename = self.create_referenced_upload()
        share = self.create_share()
        status, headers, body = self.request(
            "GET",
            f"/api/share/{share['token']}/media/{filename}",
            extra_headers={"Range": "bytes=1-4"},
        )
        self.assertEqual(status, 206)
        self.assertEqual(body, b"PNG\r")
        self.assertTrue(headers.get("Content-Range", "").startswith("bytes 1-4/"))
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "*")
        self.assertEqual(headers.get("Cache-Control"), "private, no-cache")

        status, _, body = self.request(
            "GET",
            f"/api/share/{share['token']}/media/not-referenced.png",
        )
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body)["error"], "文件不存在")

    def test_share_media_rejects_invalid_share_token(self):
        filename = self.create_referenced_upload()
        status, _, body = self.request(
            "GET",
            f"/api/share/not-a-real-share-token/media/{filename}",
        )
        self.assertEqual(status, 410)
        self.assertEqual(json.loads(body)["code"], "SHARE_INVALID")


if __name__ == "__main__":
    unittest.main()
