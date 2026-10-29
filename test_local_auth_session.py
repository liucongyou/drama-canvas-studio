# -*- coding: utf-8 -*-
import http.client
import json
import os
import threading
import unittest
from unittest.mock import patch

import server


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


if __name__ == "__main__":
    unittest.main()
