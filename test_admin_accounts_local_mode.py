"""本机单用户模式下的 `/api/admin/accounts`。

背景：前端顶栏每 30 秒拉一次这个接口，只为读 `onlineCount`。但服务端原来先走
`_admin_session()`，本机模式没有账号表 → 直接 403 ADMIN_REQUIRED。结果是
控制台每 30 秒稳定刷一条红色 403，而用户根本没有任何账号可管。

这里锁住两点：
① 本机模式必须回 200 + 一个「零账号 / 一人在线」的快照，不能 403；
② 账号模式（accounts）仍然要求管理员，未登录必须 403 —— 别把鉴权一起放松了。
"""

from __future__ import annotations

import http.client
import json
import os
import threading
import unittest
from unittest import mock

import server


class AdminAccountsLocalModeTests(unittest.TestCase):
    def setUp(self):
        self.httpd = server.CanvasHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def _get(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", "/api/admin/accounts")
            response = conn.getresponse()
            body = response.read()
            return response.status, json.loads(body.decode("utf-8") or "{}")
        finally:
            conn.close()

    def test_local_mode_returns_snapshot_instead_of_403(self):
        with mock.patch.dict(os.environ, {"CANVAS_AUTH_MODE": "host"}):
            status, payload = self._get()
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload.get("accounts"), [])
        self.assertEqual(payload.get("onlineCount"), 1)
        self.assertTrue(payload.get("local"))
        usernames = [item.get("username") for item in payload.get("onlineUsers") or []]
        self.assertEqual(usernames, ["local"])

    def test_accounts_mode_without_session_still_refuses(self):
        # accounts 模式要求显式配置对外 host（否则启动期就 500），这里补齐。
        # 未登录时全局会话校验会先拦下（401 ACCOUNT_AUTH_REQUIRED），
        # 已登录但非管理员才轮到 403 ADMIN_REQUIRED —— 两条都算拒绝，
        # 关键是**不能**像本机模式那样直接吐快照。
        with mock.patch.dict(os.environ, {
            "CANVAS_AUTH_MODE": "accounts",
            "CANVAS_API_HOST": "127.0.0.1",
        }):
            status, payload = self._get()
        self.assertIn(status, (401, 403), payload)
        self.assertIn(
            payload.get("code"),
            {"ACCOUNT_AUTH_REQUIRED", "ADMIN_REQUIRED"},
            payload,
        )
        self.assertNotIn("accounts", payload)


if __name__ == "__main__":
    unittest.main()
