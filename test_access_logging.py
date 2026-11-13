# -*- coding: utf-8 -*-
import unittest
from unittest import mock

import canvas_store
import pg_compat
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

    def test_postgres_skips_sqlite_wal_maintenance(self):
        with mock.patch.dict(
            server.os.environ,
            {"CANVAS_DB_BACKEND": "postgres"},
            clear=False,
        ), mock.patch.object(server.threading, "Thread") as thread:
            server.start_wal_maintenance()
            thread.assert_not_called()

    def test_sqlite_wal_maintenance_still_starts(self):
        with mock.patch.dict(
            server.os.environ,
            {"CANVAS_DB_BACKEND": "sqlite"},
            clear=False,
        ), mock.patch.object(server.threading, "Thread") as thread:
            server.start_wal_maintenance()
            thread.assert_called_once()

    def test_write_transaction_does_not_reenter_active_postgres_transaction(self):
        connection = mock.Mock()
        connection.in_transaction = True
        connection.execute.return_value.fetchone.return_value = None
        with mock.patch.object(canvas_store, "_connect", return_value=connection):
            canvas_store._run_write_transaction(lambda con: "ok")
        executed = [call.args[0] for call in connection.execute.call_args_list]
        self.assertNotIn("BEGIN IMMEDIATE", executed)
        connection.commit.assert_called_once()

    def test_postgres_begin_immediate_is_a_noop(self):
        connection = object.__new__(pg_compat.PGConnection)
        connection.raw = mock.Mock()
        connection.kind = "canvas"
        connection.schema = "public"
        cursor = connection.execute("BEGIN IMMEDIATE")
        self.assertIsInstance(cursor, pg_compat.PGCursor)


if __name__ == "__main__":
    unittest.main()
