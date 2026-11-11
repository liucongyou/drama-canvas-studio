# -*- coding: utf-8 -*-
import http.client
import json
import os
import threading
import unittest
from unittest.mock import patch

import server
import tenant_registry


class LocalAuthSessionTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"CANVAS_AUTH_MODE": "host"})
        self.env.start()
        self.httpd = server.CanvasHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.env.stop()

    def test_local_host_mode_is_an_authenticated_local_admin(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", "/api/auth/session", headers={"Host": "127.0.0.1"})
        response = connection.getresponse()
        body = json.loads(response.read())
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertTrue(body["authenticated"])
        self.assertEqual(body["username"], "local")
        self.assertEqual(body["role"], "admin")
        self.assertTrue(body["local"])

    def test_local_host_mode_allows_loopback_dev_port_origin(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = b"{}"
        connection.request(
            "PUT",
            "/api/not-a-real-route",
            headers={
                "Host": "127.0.0.1:8890",
                "Origin": "http://127.0.0.1:8891",
                "Content-Type": "application/json",
            },
            body=body,
        )
        response = connection.getresponse()
        response_body = response.read()
        connection.close()
        self.assertNotEqual(response.status, 403)
        self.assertNotIn(b"ORIGIN_REJECTED", response_body)

    def test_local_host_mode_saves_preferences_without_tenant_context(self):
        saved = {
            "theme": "forest",
            "canvasBackgroundMode": "dots",
        }
        with (
            patch.object(
                tenant_registry,
                "get_tenant_by_id",
                return_value=None,
            ),
            patch.object(
                tenant_registry,
                "create_tenant",
                return_value={"id": "legacy"},
            ) as create,
            patch.object(
                tenant_registry,
                "update_tenant_preferences",
                return_value=saved,
            ) as update,
            patch.object(server.store, "record_audit_event"),
        ):
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            connection.request(
                "PUT",
                "/api/preferences",
                headers={
                    "Host": "127.0.0.1:8890",
                    "Origin": "http://127.0.0.1:8891",
                    "Content-Type": "application/json",
                },
                body=json.dumps(saved).encode("utf-8"),
            )
            response = connection.getresponse()
            body = json.loads(response.read())
            connection.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(body["preferences"], saved)
        create.assert_called_once_with(
            "local",
            name="Local",
            tenant_id="legacy",
            legacy_source="local",
        )
        update.assert_called_once_with("legacy", saved)


if __name__ == "__main__":
    unittest.main()
