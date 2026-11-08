from __future__ import annotations

import base64
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

import canvas_feed_contract
import canvas_store
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

    def request(
        self,
        method,
        path,
        host,
        cookie="",
        body=None,
        extra_headers=None,
    ):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": host}
        if cookie:
            headers["Cookie"] = cookie
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        headers.update(extra_headers or {})
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

    def test_http_server_ignores_only_expected_client_disconnects(self):
        with patch.object(
            server.ThreadingHTTPServer,
            "handle_error",
        ) as default_handler:
            self.httpd.handle_error(object(), ("127.0.0.1", 1234))
        default_handler.assert_called_once()

        try:
            raise ConnectionAbortedError(10053, "client cancelled")
        except ConnectionAbortedError:
            with patch.object(
                server.ThreadingHTTPServer,
                "handle_error",
            ) as default_handler:
                self.httpd.handle_error(object(), ("127.0.0.1", 1234))
        default_handler.assert_not_called()

    def test_audit_and_metrics_are_tenant_scoped(self):
        tenant = tenant_registry.get_tenant_by_slug("alpha")
        invite = tenant_registry.create_invite(tenant["id"], ttl_seconds=3600)
        _, headers, _ = self.request(
            "GET",
            f"/invite/{invite['token']}",
            "alpha.example.com",
        )
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        status, _, body = self.request(
            "POST",
            "/api/projects",
            "alpha.example.com",
            cookie,
            {"title": "Audit Probe"},
        )
        self.assertEqual(status, 200)
        project_id = json.loads(body)["id"]

        status, _, body = self.request(
            "GET",
            "/api/audit?limit=20",
            "alpha.example.com",
            cookie,
        )
        self.assertEqual(status, 200)
        events = json.loads(body)["events"]
        self.assertTrue(any(
            event["action"] == "project.create"
            and event["targetId"] == project_id
            for event in events
        ))
        self.assertTrue(all(
            event["tenantId"] == tenant["id"] for event in events
        ))
        self.assertEqual(
            json.loads(body)["retention"],
            {"maxRows": 50000, "ttlDays": 180},
        )

        status, _, body = self.request(
            "GET",
            "/api/metrics",
            "alpha.example.com",
            cookie,
        )
        self.assertEqual(status, 200)
        metrics = json.loads(body)
        self.assertEqual(metrics["projects"], 1)
        self.assertEqual(metrics["queue"]["tenantActive"], 0)
        self.assertTrue(all(
            "\x1f" not in provider_id
            for provider_id in metrics["queue"]["providerActive"]
        ))

    def test_generation_resolve_endpoint_is_atomic_and_idempotent(self):
        tenant = tenant_registry.get_tenant_by_slug("alpha")
        invite = tenant_registry.create_invite(tenant["id"], ttl_seconds=3600)
        _, headers, _ = self.request(
            "GET",
            f"/invite/{invite['token']}",
            "alpha.example.com",
        )
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        status, _, body = self.request(
            "POST",
            "/api/projects",
            "alpha.example.com",
            cookie,
            {"title": "人工收口 HTTP 闭环"},
        )
        self.assertEqual(status, 200)
        project_id = json.loads(body)["id"]
        with tenant_context.use_tenant(tenant["id"], tenant["slug"]):
            canvas_store.insert_project_nodes(
                project_id,
                [{
                    "id": "node-http-resolve",
                    "type": "video",
                    "title": "段 HTTP",
                    "metadata": {"status": "generating"},
                }],
                episode_id="EP001",
            )
            task = canvas_store.create_generation_task(
                project_id,
                "node-http-resolve",
                kind="video",
                provider="ark",
                model="doubao-seedance-http-test",
            )
            canvas_store.update_generation_task(
                task["id"],
                status="interrupted",
                phase="结果待确认",
                provider_task_id="provider-http-resolve",
                delivery_status="pending",
            )

        status, _, body = self.request(
            "POST",
            f"/api/generation-tasks/{task['id']}/resolve",
            "alpha.example.com",
            cookie,
            {"action": "confirm_failed"},
            extra_headers={"User-Agent": "AtomicResolveHttpTest/1.0"},
        )
        self.assertEqual(status, 200, body.decode("utf-8"))
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["task"]["outcome"]["canResolve"])
        self.assertEqual(payload["task"]["status"], "failed")
        self.assertEqual(payload["task"]["phase"], "人工确认失败")

        status, _, body = self.request(
            "POST",
            f"/api/generation-tasks/{task['id']}/resolve",
            "alpha.example.com",
            cookie,
            {"action": "confirm_failed"},
            extra_headers={"User-Agent": "AtomicResolveHttpTest/1.0"},
        )
        self.assertEqual(status, 409, body.decode("utf-8"))
        self.assertEqual(json.loads(body)["code"], "TASK_OUTCOME_NOT_RESOLVABLE")

        with tenant_context.use_tenant(tenant["id"], tenant["slug"]):
            saved_task = canvas_store.get_generation_task(task["id"])
            saved_node = canvas_store.get_projection_node(
                project_id,
                "node-http-resolve",
            )
            audits = canvas_store.list_audit_events(
                project_id=project_id,
                action="generation.resolve",
            )["events"]
        self.assertEqual(saved_task["status"], "failed")
        self.assertEqual(saved_task["next_poll_at"], "")
        self.assertEqual(
            saved_node["metadata"]["providerTaskPhase"],
            "人工确认失败",
        )
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["targetId"], task["id"])
        self.assertEqual(audits[0]["actor"], "")
        self.assertEqual(
            audits[0]["details"]["userAgent"],
            "AtomicResolveHttpTest/1.0",
        )

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

    def test_position_save_is_last_writer_wins_and_preserves_metadata(self):
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
            {"title": "位置竞态测试"},
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
            {"nodes": [node], "episodeId": "EP001"},
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

        status, _, conflict_body = self.request(
            "PUT",
            f"/api/projects/{pid}/nodes/{node['id']}",
            "alpha.example.com",
            cookie,
            {
                "baseUpdatedAt": original_updated_at,
                "metadata": {"prompt": "second", "status": "done", "params": {"ratio": "1:1"}},
                "episodeId": "EP001",
            },
        )
        self.assertEqual(status, 200, conflict_body.decode("utf-8"))

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

        status, _, last_write_body = self.request(
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
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(last_write_body)["updated"], 1)
        status, _, project_body = self.request(
            "GET",
            f"/api/projects/{pid}",
            "alpha.example.com",
            cookie,
        )
        self.assertEqual(status, 200)
        final_node = next(
            item for item in json.loads(project_body)["payload"]["nodes"]
            if item["id"] == node["id"]
        )
        self.assertEqual(final_node["position"], {"x": 320.0, "y": 260.0})
        self.assertEqual(final_node["metadata"]["prompt"], "second")

    def test_stale_position_write_is_rejected_after_newer_write(self):
        tenant = tenant_registry.get_tenant_by_slug("alpha")
        invite = tenant_registry.create_invite(tenant["id"], ttl_seconds=3600)
        _, headers, _ = self.request(
            "GET",
            f"/invite/{invite['token']}",
            "alpha.example.com",
        )
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        _, _, body = self.request(
            "POST",
            "/api/projects",
            "alpha.example.com",
            cookie,
            {"title": "迟到位置写入测试"},
        )
        pid = json.loads(body)["id"]
        node = {
            "id": "stale-position-node",
            "type": "image",
            "title": "拖拽测试",
            "position": {"x": 10, "y": 10},
            "metadata": {"prompt": "x"},
        }
        self.request(
            "PUT",
            f"/api/projects/{pid}/nodes",
            "alpha.example.com",
            cookie,
            {"nodes": [node], "episodeId": "EP001"},
        )
        owner = "tab-alpha"
        self.request(
            "PUT",
            f"/api/projects/{pid}/nodes/positions",
            "alpha.example.com",
            cookie,
            {"writeOwner": owner, "positions": [{
                "id": node["id"],
                "position": {"x": 200, "y": 200},
                "seq": 2,
            }]},
        )
        status, _, stale_body = self.request(
            "PUT",
            f"/api/projects/{pid}/nodes/positions",
            "alpha.example.com",
            cookie,
            {"writeOwner": owner, "positions": [{
                "id": node["id"],
                "position": {"x": 10, "y": 10},
                "seq": 1,
            }]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(stale_body)["updated"], 0)
        _, _, project_body = self.request(
            "GET",
            f"/api/projects/{pid}",
            "alpha.example.com",
            cookie,
        )
        final_node = next(
            item for item in json.loads(project_body)["payload"]["nodes"]
            if item["id"] == node["id"]
        )
        self.assertEqual(final_node["position"], {"x": 200.0, "y": 200.0})

    def test_bulk_node_save_ignores_position_for_existing_nodes(self):
        tenant = tenant_registry.get_tenant_by_slug("alpha")
        invite = tenant_registry.create_invite(tenant["id"], ttl_seconds=3600)
        _, headers, _ = self.request(
            "GET",
            f"/invite/{invite['token']}",
            "alpha.example.com",
        )
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        _, _, body = self.request(
            "POST",
            "/api/projects",
            "alpha.example.com",
            cookie,
            {"title": "普通保存位置隔离测试"},
        )
        pid = json.loads(body)["id"]
        node = {
            "id": "position-isolated-node",
            "type": "image",
            "title": "拖拽测试",
            "position": {"x": 10, "y": 20},
            "metadata": {"prompt": "原始提示词"},
        }
        self.request(
            "PUT",
            f"/api/projects/{pid}/nodes",
            "alpha.example.com",
            cookie,
            {"nodes": [node], "episodeId": "EP001"},
        )
        # A legacy or cached client may still send position in the generic bulk
        # payload. Existing nodes must ignore it; only /nodes/positions writes.
        status, _, _ = self.request(
            "PUT",
            f"/api/projects/{pid}/nodes",
            "alpha.example.com",
            cookie,
            {"nodes": [{
                **node,
                "position": {"x": 999, "y": 888},
                "metadata": {"prompt": "更新提示词"},
            }], "episodeId": "EP001"},
        )
        self.assertEqual(status, 200)
        _, _, project_body = self.request(
            "GET",
            f"/api/projects/{pid}",
            "alpha.example.com",
            cookie,
        )
        final_node = next(
            item for item in json.loads(project_body)["payload"]["nodes"]
            if item["id"] == node["id"]
        )
        self.assertEqual(final_node["position"], {"x": 10.0, "y": 20.0})

    def test_viewport_body_episode_and_repeated_write_are_idempotent(self):
        tenant = tenant_registry.get_tenant_by_slug("alpha")
        invite = tenant_registry.create_invite(tenant["id"], ttl_seconds=3600)
        _, headers, _ = self.request(
            "GET",
            f"/invite/{invite['token']}",
            "alpha.example.com",
        )
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        _, _, body = self.request(
            "POST",
            "/api/projects",
            "alpha.example.com",
            cookie,
            {"title": "视口幂等测试"},
        )
        project_id = json.loads(body)["id"]
        payload = {"viewport": {"x": 50, "y": 60, "zoom": 0.75}, "episodeId": "EP001"}
        first_status, _, first_body = self.request(
            "PUT",
            f"/api/projects/{project_id}/viewport",
            "alpha.example.com",
            cookie,
            payload,
        )
        self.assertEqual(first_status, 200, first_body.decode("utf-8"))
        first = json.loads(first_body)
        self.assertEqual(first["episodeId"], "EP001")
        self.assertFalse(first["unchanged"])
        second_status, _, second_body = self.request(
            "PUT",
            f"/api/projects/{project_id}/viewport",
            "alpha.example.com",
            cookie,
            payload,
        )
        self.assertEqual(second_status, 200, second_body.decode("utf-8"))
        second = json.loads(second_body)
        self.assertTrue(second["unchanged"])
        self.assertEqual(second["revision"], first["revision"])

        _, _, project_body = self.request(
            "GET",
            f"/api/projects/{project_id}",
            "alpha.example.com",
            cookie,
        )
        project = json.loads(project_body)
        self.assertEqual(
            project["payload"]["episodeViewports"]["EP001"],
            {"x": 50.0, "y": 60.0, "zoom": 0.75},
        )

    def test_media_download_ticket_sets_filename_and_keeps_ranges(self):
        tenant = tenant_registry.get_tenant_by_slug("alpha")
        invite = tenant_registry.create_invite(tenant["id"], ttl_seconds=3600)
        _, headers, _ = self.request(
            "GET",
            f"/invite/{invite['token']}",
            "alpha.example.com",
        )
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        _, _, body = self.request(
            "POST",
            "/api/projects",
            "alpha.example.com",
            cookie,
            {"title": "下载票据测试"},
        )
        project_id = json.loads(body)["id"]
        context = tenant_context.make_context(tenant["id"], "alpha")
        upload_dir = Path(context.upload_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)
        filename = "download-ticket.mp4"
        payload = b"0123456789abcdef"
        (upload_dir / filename).write_bytes(payload)
        node = {
            "id": "download-node",
            "type": "video",
            "title": "段13",
            "metadata": {
                "status": "done",
                "content": f"/uploads/{filename}",
                "params": {
                    "activeVersionId": "video:download-task",
                    "mediaVersions": [{
                        "versionId": "video:download-task",
                        "taskId": "download-task",
                        "url": f"/uploads/{filename}",
                    }],
                    "videoVersions": [{
                        "versionId": "video:download-task",
                        "taskId": "download-task",
                        "url": f"/uploads/{filename}",
                    }],
                    "mediaVersionIndex": 0,
                    "videoVersionIndex": 0,
                },
            },
        }
        status, _, body = self.request(
            "PUT",
            f"/api/projects/{project_id}/nodes",
            "alpha.example.com",
            cookie,
            {"episodeId": "EP001", "nodes": [node]},
        )
        self.assertEqual(status, 200, body.decode("utf-8"))
        status, _, body = self.request(
            "POST",
            f"/api/projects/{project_id}/downloads",
            "alpha.example.com",
            cookie,
            {
                "nodeId": node["id"],
                "versionId": "video:download-task",
                "downloadName": "铁西硬汉_EP07_段13.mp4",
            },
        )
        self.assertEqual(status, 200, body.decode("utf-8"))
        url = json.loads(body)["url"]
        self.assertTrue(url.startswith("/api/downloads/media/"))

        status, headers, body = self.request(
            "GET",
            url,
            "alpha.example.com",
            cookie,
            extra_headers={"Range": "bytes=0-3"},
        )
        self.assertEqual(status, 206)
        self.assertEqual(body, payload[:4])
        self.assertEqual(headers.get("Content-Range"), "bytes 0-3/16")
        disposition = headers.get("Content-Disposition", "")
        self.assertIn("attachment", disposition)
        self.assertIn("filename*=UTF-8''", disposition)
        self.assertIn(
            urllib.parse.quote("铁西硬汉_EP07_段13.mp4", safe=""),
            disposition,
        )
        self.assertEqual(
            self.request("GET", url, "alpha.example.com")[0],
            401,
        )

    def test_media_download_ticket_is_tenant_bound_and_expires(self):
        alpha = tenant_registry.get_tenant_by_slug("alpha")
        beta = tenant_bootstrap.create_tenant("beta", name="Beta")
        with tenant_context.use_tenant(alpha["id"], alpha["slug"]):
            project_id = canvas_store.create_project("Alpha download")
            upload_dir = Path(tenant_context.current_upload_dir())
            upload_dir.mkdir(parents=True, exist_ok=True)
            filename = "alpha-download.mp4"
            (upload_dir / filename).write_bytes(b"alpha-video")
            canvas_store.update_project(
                project_id,
                payload={
                    "nodes": [{
                        "id": "alpha-video",
                        "type": "video",
                        "title": "段13",
                        "metadata": {
                            "status": "done",
                            "content": f"/uploads/{filename}",
                            "params": {
                                "activeVersionId": "video:alpha-download",
                                "mediaVersions": [{
                                    "versionId": "video:alpha-download",
                                    "taskId": "alpha-download",
                                    "url": f"/uploads/{filename}",
                                }],
                                "mediaVersionIndex": 0,
                            },
                        },
                    }],
                    "connections": [],
                    "episodeImports": [],
                },
            )
        with tenant_context.use_tenant(alpha["id"], alpha["slug"]):
            ticket = server.issue_media_download_ticket(
                project_id=project_id,
                node_id="alpha-video",
                version_id="video:alpha-download",
                download_name="alpha.mp4",
            )
        with tenant_context.use_tenant(beta["id"], beta["slug"]):
            with self.assertRaisesRegex(ValueError, "当前租户"):
                server.verify_media_download_ticket(ticket)
        with tenant_context.use_tenant(alpha["id"], alpha["slug"]):
            with patch.object(server.time, "time", return_value=time.time() + 3600):
                with self.assertRaisesRegex(ValueError, "过期"):
                    server.verify_media_download_ticket(ticket)

    def test_media_download_ticket_does_not_fallback_to_another_version(self):
        alpha = tenant_registry.get_tenant_by_slug("alpha")
        invite = tenant_registry.create_invite(alpha["id"], ttl_seconds=3600)
        _, headers, _ = self.request(
            "GET",
            f"/invite/{invite['token']}",
            "alpha.example.com",
        )
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        with tenant_context.use_tenant(alpha["id"], alpha["slug"]):
            project_id = canvas_store.create_project("Strict download version")
            upload_dir = Path(tenant_context.current_upload_dir())
            upload_dir.mkdir(parents=True, exist_ok=True)
            filename = "current-version.mp4"
            (upload_dir / filename).write_bytes(b"current")
            canvas_store.update_project(
                project_id,
                payload={
                    "nodes": [{
                        "id": "versioned-video",
                        "type": "video",
                        "title": "段13",
                        "metadata": {
                            "status": "done",
                            "content": f"/uploads/{filename}",
                            "params": {
                                "activeVersionId": "video:current",
                                "mediaVersions": [{
                                    "versionId": "video:current",
                                    "taskId": "current-task",
                                    "url": f"/uploads/{filename}",
                                }],
                                "mediaVersionIndex": 0,
                            },
                        },
                    }],
                    "connections": [],
                    "episodeImports": [],
                },
            )
        status, _, body = self.request(
            "POST",
            f"/api/projects/{project_id}/downloads",
            "alpha.example.com",
            cookie,
            {
                "nodeId": "versioned-video",
                "versionId": "video:missing",
                "downloadName": "missing.mp4",
            },
        )
        self.assertEqual(status, 404, body.decode("utf-8"))

    def test_media_download_ticket_proxies_tos_with_range_and_filename(self):
        alpha = tenant_registry.get_tenant_by_slug("alpha")
        invite = tenant_registry.create_invite(alpha["id"], ttl_seconds=3600)
        _, headers, _ = self.request(
            "GET",
            f"/invite/{invite['token']}",
            "alpha.example.com",
        )
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        with tenant_context.use_tenant(alpha["id"], alpha["slug"]):
            project_id = canvas_store.create_project("TOS download")
            storage = {
                "status": "stored",
                "provider": "tos",
                "bucket": "media-bucket",
                "key": "drama-canvas/video/tos-only.mp4",
            }
            canvas_store.update_project(
                project_id,
                payload={
                    "nodes": [{
                        "id": "tos-video",
                        "type": "video",
                        "title": "段13",
                        "metadata": {
                            "status": "done",
                            "content": "/uploads/missing-local.mp4",
                            "storage": storage,
                            "params": {
                                "activeVersionId": "video:tos-task",
                                "mediaVersions": [{
                                    "versionId": "video:tos-task",
                                    "taskId": "tos-task",
                                    "url": "/uploads/missing-local.mp4",
                                    "storage": storage,
                                }],
                                "mediaVersionIndex": 0,
                            },
                        },
                    }],
                    "connections": [],
                    "episodeImports": [],
                },
            )
        status, _, body = self.request(
            "POST",
            f"/api/projects/{project_id}/downloads",
            "alpha.example.com",
            cookie,
            {
                "nodeId": "tos-video",
                "versionId": "video:tos-task",
                "downloadName": "铁西硬汉_EP07_段13_TOS.mp4",
            },
        )
        self.assertEqual(status, 200, body.decode("utf-8"))
        ticket_url = json.loads(body)["url"]

        class FakeRemoteResponse:
            status = 206
            headers = {
                "Content-Type": "video/mp4",
                "Content-Length": "4",
                "Content-Range": "bytes 0-3/16",
                "Accept-Ranges": "bytes",
            }

            def __init__(self):
                self._body = b"TOS!"

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size=-1):
                body, self._body = self._body, b""
                return body

        captured = {}

        def fake_urlopen(request, timeout=0):
            captured["url"] = request.full_url
            captured["range"] = request.get_header("Range")
            captured["timeout"] = timeout
            return FakeRemoteResponse()

        with (
            patch.object(
                server.tos_storage,
                "public_presigned_url",
                return_value="https://media-bucket.tos-cn-beijing.volces.com/signed",
            ),
            patch.object(
                server.urllib.request,
                "urlopen",
                side_effect=fake_urlopen,
            ),
        ):
            status, headers, body = self.request(
                "GET",
                ticket_url,
                "alpha.example.com",
                cookie,
                extra_headers={"Range": "bytes=0-3"},
            )
        self.assertEqual(status, 206)
        self.assertEqual(body, b"TOS!")
        self.assertEqual(captured["range"], "bytes=0-3")
        self.assertEqual(captured["timeout"], 60)
        self.assertIn("Content-Range", headers)
        self.assertIn(
            urllib.parse.quote("铁西硬汉_EP07_段13_TOS.mp4", safe=""),
            headers.get("Content-Disposition", ""),
        )

    def test_node_save_allows_shared_asset_edit_without_changing_ownership(self):
        tenant = tenant_registry.get_tenant_by_slug("alpha")
        invite = tenant_registry.create_invite(tenant["id"], ttl_seconds=3600)
        _, headers, _ = self.request(
            "GET",
            f"/invite/{invite['token']}",
            "alpha.example.com",
        )
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        _, _, body = self.request(
            "POST",
            "/api/projects",
            "alpha.example.com",
            cookie,
            {"title": "集归属拒绝测试"},
        )
        pid = json.loads(body)["id"]
        node = {
            "id": "episode-owned-node",
            "type": "image",
            "title": "角色",
            "position": {"x": 0, "y": 0},
            "metadata": {"prompt": "角色"},
        }
        status, _, _ = self.request(
            "PUT",
            f"/api/projects/{pid}/nodes",
            "alpha.example.com",
            cookie,
            {"nodes": [node], "episodeId": "EP001"},
        )
        self.assertEqual(status, 200)

        status, _, body = self.request(
            "PUT",
            f"/api/projects/{pid}/nodes/{node['id']}",
            "alpha.example.com",
            cookie,
            {
                "metadata": {"prompt": "跨集共享更新"},
                "episodeId": "EP002",
            },
        )

        self.assertEqual(status, 200, body.decode("utf-8"))
        with tenant_context.use_tenant(tenant["id"], tenant["slug"]):
            workspace = server.store.get_project_workspace_nodes(pid, "EP001")
        saved = next(
            item for item in workspace["nodes"]
            if item["id"] == node["id"]
        )
        self.assertEqual(saved["metadata"]["prompt"], "跨集共享更新")
        self.assertEqual(saved["metadata"]["episodeId"], "EP001")

    def test_bulk_node_save_updates_shared_assets_without_reassigning_owner(self):
        tenant = tenant_registry.get_tenant_by_slug("alpha")
        invite = tenant_registry.create_invite(tenant["id"], ttl_seconds=3600)
        _, headers, _ = self.request(
            "GET",
            f"/invite/{invite['token']}",
            "alpha.example.com",
        )
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        _, _, body = self.request(
            "POST",
            "/api/projects",
            "alpha.example.com",
            cookie,
            {"title": "批量保存集隔离测试"},
        )
        pid = json.loads(body)["id"]
        ep1_node = {
            "id": "bulk-episode-one",
            "type": "image",
            "title": "第一集图片",
            "metadata": {"prompt": "EP001 原始"},
        }
        ep2_node = {
            "id": "bulk-episode-two",
            "type": "image",
            "title": "第二集图片",
            "metadata": {"prompt": "EP002 原始"},
        }
        for episode_id, node in (
            ("EP001", ep1_node),
            ("EP002", ep2_node),
        ):
            status, _, _ = self.request(
                "PUT",
                f"/api/projects/{pid}/nodes",
                "alpha.example.com",
                cookie,
                {"nodes": [node], "episodeId": episode_id},
            )
            self.assertEqual(status, 200)

        status, _, _ = self.request(
            "PUT",
            f"/api/projects/{pid}/nodes",
            "alpha.example.com",
            cookie,
            {
                "episodeId": "EP002",
                "nodes": [
                    {
                        **ep1_node,
                        "metadata": {"prompt": "EP001 共享更新"},
                    },
                    {
                        **ep2_node,
                        "metadata": {"prompt": "EP002 已更新"},
                    },
                ],
            },
        )
        self.assertEqual(status, 200)

        with tenant_context.use_tenant(tenant["id"], tenant["slug"]):
            ep1_workspace = server.store.get_project_workspace_nodes(
                pid,
                "EP001",
            )
            ep2_workspace = server.store.get_project_workspace_nodes(
                pid,
                "EP002",
            )
        ep1_saved = next(
            item for item in ep1_workspace["nodes"]
            if item["id"] == ep1_node["id"]
        )
        ep2_saved = next(
            item for item in ep2_workspace["nodes"]
            if item["id"] == ep2_node["id"]
        )
        self.assertEqual(ep1_saved["metadata"]["prompt"], "EP001 共享更新")
        self.assertEqual(ep1_saved["metadata"]["episodeId"], "EP001")
        self.assertEqual(ep2_saved["metadata"]["prompt"], "EP002 已更新")

    def test_delete_node_rejects_active_generation(self):
        tenant = tenant_registry.get_tenant_by_slug("alpha")
        invite = tenant_registry.create_invite(tenant["id"], ttl_seconds=3600)
        _, headers, _ = self.request(
            "GET",
            f"/invite/{invite['token']}",
            "alpha.example.com",
        )
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        _, _, body = self.request(
            "POST",
            "/api/projects",
            "alpha.example.com",
            cookie,
            {"title": "生成中删除拦截测试"},
        )
        pid = json.loads(body)["id"]
        node = {
            "id": "generating-delete-node",
            "type": "video",
            "title": "生成中视频",
            "metadata": {
                "prompt": "生成中",
                "status": "generating",
                "episodeId": "EP001",
            },
        }
        status, _, _ = self.request(
            "PUT",
            f"/api/projects/{pid}/nodes",
            "alpha.example.com",
            cookie,
            {"nodes": [node], "episodeId": "EP001"},
        )
        self.assertEqual(status, 200)
        with tenant_context.use_tenant(tenant["id"], tenant["slug"]):
            task = server.store.create_generation_task(
                pid,
                node["id"],
                title=node["title"],
                kind="video",
                episode_id="EP001",
            )
            server.store.update_generation_task(task["id"], status="running")
        status, _, body = self.request(
            "DELETE",
            f"/api/projects/{pid}/nodes/{node['id']}",
            "alpha.example.com",
            cookie,
        )
        self.assertEqual(status, 409, body.decode("utf-8"))
        self.assertEqual(json.loads(body)["code"], "NODE_GENERATION_ACTIVE")
        status, _, _ = self.request(
            "DELETE",
            f"/api/projects/{pid}",
            "alpha.example.com",
            cookie,
        )
        self.assertEqual(status, 409)
        with tenant_context.use_tenant(tenant["id"], tenant["slug"]):
            server.store.update_generation_task(
                task["id"],
                status="failed",
                phase="失败",
                finished_at=server.store._now_iso(),
            )

    def test_manual_connections_persist_without_overwriting_automatic_edges(self):
        tenant = tenant_registry.get_tenant_by_slug("alpha")
        invite = tenant_registry.create_invite(tenant["id"], ttl_seconds=3600)
        _, headers, _ = self.request(
            "GET",
            f"/invite/{invite['token']}",
            "alpha.example.com",
        )
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        _, _, body = self.request(
            "POST",
            "/api/projects",
            "alpha.example.com",
            cookie,
            {"title": "手动连线测试"},
        )
        pid = json.loads(body)["id"]
        nodes = [
            {
                "id": "manual-image",
                "type": "image",
                "title": "角色图",
                "position": {"x": 0, "y": 0},
                "metadata": {"prompt": "角色"},
            },
            {
                "id": "manual-video",
                "type": "video",
                "title": "视频",
                "position": {"x": 500, "y": 0},
                "metadata": {"prompt": "视频"},
            },
            {
                "id": "manual-text",
                "type": "text",
                "title": "补充说明",
                "position": {"x": 500, "y": 300},
                "metadata": {"prompt": "镜头补充说明"},
            },
        ]
        status, _, _ = self.request(
            "PUT",
            f"/api/projects/{pid}/nodes",
            "alpha.example.com",
            cookie,
            {"nodes": nodes, "episodeId": "EP001"},
        )
        self.assertEqual(status, 200)
        with tenant_context.use_tenant(tenant["id"], tenant["slug"]):
            project = server.store.get_project(pid)
            project["payload"]["connections"] = [{
                "id": "e-media-auto",
                "fromNodeId": "manual-image",
                "toNodeId": "manual-video",
                "source": "import",
            }]
            server.store.update_project(pid, payload=project["payload"])
        status, _, body = self.request(
            "GET",
            f"/api/projects/{pid}",
            "alpha.example.com",
            cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            [item["id"] for item in json.loads(body)["payload"]["connections"]],
            ["e-media-auto"],
        )
        status, _, body = self.request(
            "PUT",
            f"/api/projects/{pid}/connections",
            "alpha.example.com",
            cookie,
            {
                "connections": [{
                    "id": "e-user-manual-1",
                    "fromNodeId": "manual-text",
                    "toNodeId": "manual-video",
                }],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["manual"], 1)
        status, _, body = self.request(
            "GET",
            f"/api/projects/{pid}",
            "alpha.example.com",
            cookie,
        )
        self.assertEqual(status, 200)
        connections = json.loads(body)["payload"]["connections"]
        self.assertEqual(len(connections), 2, connections)
        self.assertTrue(any(item["id"] == "e-media-auto" for item in connections))
        manual = next(item for item in connections if item["source"] == "user")
        self.assertEqual(manual["fromNodeId"], "manual-text")
        self.assertEqual(manual["toNodeId"], "manual-video")

        status, _, body = self.request(
            "PUT",
            f"/api/projects/{pid}/connections",
            "alpha.example.com",
            cookie,
            {"connections": []},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["manual"], 0)
        status, _, body = self.request(
            "GET",
            f"/api/projects/{pid}",
            "alpha.example.com",
            cookie,
        )
        self.assertEqual(status, 200)
        connections = json.loads(body)["payload"]["connections"]
        self.assertEqual([item["id"] for item in connections], ["e-media-auto"])

    def test_connection_save_does_not_overwrite_concurrent_node_fields(self):
        tenant = tenant_registry.get_tenant_by_slug("alpha")
        invite = tenant_registry.create_invite(tenant["id"], ttl_seconds=3600)
        _, headers, _ = self.request(
            "GET",
            f"/invite/{invite['token']}",
            "alpha.example.com",
        )
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        _, _, body = self.request(
            "POST",
            "/api/projects",
            "alpha.example.com",
            cookie,
            {"title": "连线并发测试"},
        )
        pid = json.loads(body)["id"]
        nodes = [
            {
                "id": "concurrent-image",
                "type": "image",
                "title": "角色图",
                "position": {"x": 0, "y": 0},
                "metadata": {"prompt": "角色", "status": "idle"},
            },
            {
                "id": "concurrent-video",
                "type": "video",
                "title": "视频",
                "position": {"x": 500, "y": 0},
                "metadata": {
                    "prompt": "旧提示词",
                    "status": "idle",
                    "params": {"refs": []},
                },
            },
        ]
        status, _, _ = self.request(
            "PUT",
            f"/api/projects/{pid}/nodes",
            "alpha.example.com",
            cookie,
            {"nodes": nodes, "episodeId": "EP001"},
        )
        self.assertEqual(status, 200)

        barrier = threading.Barrier(2)
        results = []

        def save_connections():
            barrier.wait()
            results.append(self.request(
                "PUT",
                f"/api/projects/{pid}/connections",
                "alpha.example.com",
                cookie,
                {
                    "connections": [{
                        "id": "e-user-concurrent",
                        "fromNodeId": "concurrent-image",
                        "toNodeId": "concurrent-video",
                    }],
                },
            ))

        def save_prompt():
            barrier.wait()
            results.append(self.request(
                "PUT",
                f"/api/projects/{pid}/nodes/concurrent-video",
                "alpha.example.com",
                cookie,
                {
                    "metadata": {
                        "prompt": "并发后的新提示词",
                        "params": {"refs": [{
                            "nodeId": "concurrent-image",
                            "label": "角色图",
                        }]},
                    },
                    "episodeId": "EP001",
                },
            ))

        threads = [
            threading.Thread(target=save_connections),
            threading.Thread(target=save_prompt),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual([result[0] for result in results], [200, 200])

        status, _, body = self.request(
            "GET",
            f"/api/projects/{pid}",
            "alpha.example.com",
            cookie,
        )
        self.assertEqual(status, 200)
        project = json.loads(body)
        video = next(
            node for node in project["payload"]["nodes"]
            if node["id"] == "concurrent-video"
        )
        self.assertEqual(video["metadata"]["prompt"], "并发后的新提示词")
        self.assertEqual(
            video["metadata"]["params"]["refs"][0]["nodeId"],
            "concurrent-image",
        )
        self.assertTrue(any(
            item["id"] == "e-user-concurrent"
            for item in project["payload"]["connections"]
        ))

    def test_manual_episode_creation_persists_and_owns_new_nodes(self):
        tenant = tenant_registry.get_tenant_by_slug("alpha")
        invite = tenant_registry.create_invite(tenant["id"], ttl_seconds=3600)
        _, headers, _ = self.request(
            "GET",
            f"/invite/{invite['token']}",
            "alpha.example.com",
        )
        cookie = headers.get("Set-Cookie", "").split(";", 1)[0]
        _, _, body = self.request(
            "POST",
            "/api/projects",
            "alpha.example.com",
            cookie,
            {"title": "手动集数测试"},
        )
        pid = json.loads(body)["id"]
        status, _, body = self.request(
            "POST",
            f"/api/projects/{pid}/episodes",
            "alpha.example.com",
            cookie,
            {},
        )
        self.assertEqual(status, 200)
        created = json.loads(body)
        self.assertEqual(created["episode"]["id"], "EP002")
        self.assertEqual(created["episodes"], ["EP001", "EP002"])

        status, _, body = self.request(
            "POST",
            f"/api/projects/{pid}/episodes",
            "alpha.example.com",
            cookie,
            {"episodeId": "2"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["code"], "EPISODE_EXISTS")

        status, _, body = self.request(
            "POST",
            f"/api/projects/{pid}/episodes",
            "alpha.example.com",
            cookie,
            {"episodeId": "3", "title": "第三集 · 新场"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["episode"]["id"], "EP003")

        status, _, _ = self.request(
            "PUT",
            f"/api/projects/{pid}/nodes",
            "alpha.example.com",
            cookie,
            {
                "episodeId": "EP002",
                "nodes": [{
                    "id": "manual-ep-video",
                    "type": "video",
                    "title": "第二集视频",
                    "position": {"x": 100, "y": 100},
                    "metadata": {"prompt": "手动集数中的新视频"},
                }],
            },
        )
        self.assertEqual(status, 200)
        status, _, body = self.request(
            "GET",
            f"/api/projects/{pid}",
            "alpha.example.com",
            cookie,
        )
        self.assertEqual(status, 200)
        groups = json.loads(body)["payload"]["episodeImports"]
        second = next(group for group in groups if group["id"] == "EP002")
        third = next(group for group in groups if group["id"] == "EP003")
        self.assertIn("manual-ep-video", second["nodeIds"])
        self.assertIn("manual-ep-video", second["videoNodeIds"])
        self.assertEqual(third["title"], "第三集 · 新场")
        self.assertEqual(third["nodeIds"], [])

        status, _, body = self.request(
            "DELETE",
            f"/api/projects/{pid}/episodes/EP002",
            "alpha.example.com",
            cookie,
        )
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["code"], "EPISODE_NOT_EMPTY")

        status, _, body = self.request(
            "DELETE",
            f"/api/projects/{pid}/episodes/EP003",
            "alpha.example.com",
            cookie,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["removed"], "EP003")
        self.assertEqual(json.loads(body)["episodes"], ["EP001", "EP002"])

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

