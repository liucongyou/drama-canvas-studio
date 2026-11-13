# -*- coding: utf-8 -*-
import unittest
from unittest import mock

import server


class AccessLogPolicyTests(unittest.TestCase):
    def test_errors_mode_logs_only_failures(self):
        with mock.patch.object(server, "CANVAS_ACCESS_LOG_MODE", "errors"):
            self.assertFalse(server.access_log_enabled(200))
            self.assertFalse(server.access_log_enabled(206))
            self.assertFalse(server.access_log_enabled(304))
            self.assertTrue(server.access_log_enabled(400))
            self.assertTrue(server.access_log_enabled(500))

    def test_explicit_all_and_off_modes(self):
        with mock.patch.object(server, "CANVAS_ACCESS_LOG_MODE", "all"):
            self.assertTrue(server.access_log_enabled(200))
        with mock.patch.object(server, "CANVAS_ACCESS_LOG_MODE", "off"):
            self.assertFalse(server.access_log_enabled(500))

    def test_handler_records_status_before_log_message(self):
        handler = object.__new__(server.Handler)
        handler.client_address = ("127.0.0.1", 12345)
        handler._response_status = 0
        handler.log_date_time_string = lambda: "now"
        with mock.patch.object(server, "CANVAS_ACCESS_LOG_MODE", "errors"):
            with mock.patch.object(server.sys, "stderr") as stderr:
                handler.log_message("GET /health 200")
                stderr.write.assert_not_called()
                handler._response_status = 500
                handler.log_message("GET /broken 500")
                stderr.write.assert_called_once()


if __name__ == "__main__":
    unittest.main()
