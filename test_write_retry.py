"""写事务重试：并发下 `readonly` 是瞬态，真只读不该耗满 35 秒。

背景：20 线程并发写同一个 WAL 库时，实测 300 次写里有 2 次抛
"attempt to write a readonly database"（文件存在、目录可写），紧接着重试就成功。
旧逻辑只把 locked/busy 当可重试，这两次会直接变成接口 500，用户看到"保存失败"
但再点一次又能存上。
"""

from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

import canvas_store


def readonly_error() -> sqlite3.OperationalError:
    return sqlite3.OperationalError("attempt to write a readonly database")


def locked_error() -> sqlite3.OperationalError:
    return sqlite3.OperationalError("database is locked")


class FakeConnection:
    def __init__(self, begin_errors=(), commit_error=None):
        self.begin_errors = list(begin_errors)
        self.commit_error = commit_error
        self.in_transaction = False
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def execute(self, statement, *args):
        if statement == "BEGIN IMMEDIATE":
            if self.begin_errors:
                raise self.begin_errors.pop(0)
            self.in_transaction = True
        return None

    def commit(self):
        if self.commit_error is not None:
            raise self.commit_error
        self.in_transaction = False
        self.commits += 1

    def rollback(self):
        self.in_transaction = False
        self.rollbacks += 1

    def close(self):
        self.closed = True


class WriteRetryTests(unittest.TestCase):
    def test_readonly_error_classification(self):
        self.assertTrue(canvas_store._is_readonly_write_error(readonly_error()))
        self.assertTrue(canvas_store._is_retryable_write_error(readonly_error()))
        self.assertTrue(canvas_store._is_retryable_write_error(locked_error()))
        self.assertFalse(canvas_store._is_readonly_write_error(locked_error()))
        self.assertFalse(
            canvas_store._is_retryable_write_error(
                sqlite3.OperationalError("no such table: nope")
            )
        )

    def test_transient_readonly_is_retried_and_succeeds(self):
        connection = FakeConnection(begin_errors=[readonly_error(), readonly_error()])
        calls = []

        def callback(con):
            calls.append(con)
            return "done"

        with (
            mock.patch.object(canvas_store, "_connect", return_value=connection),
            mock.patch.object(canvas_store.time, "sleep"),
        ):
            result = canvas_store._run_write_transaction(callback)

        self.assertEqual(result, "done")
        self.assertEqual(connection.commits, 1)
        self.assertTrue(connection.closed)

    def test_persistent_readonly_gives_up_quickly(self):
        # 真·只读（权限问题）：不该重试到 35 秒的 deadline，几次就放弃。
        attempts = []
        errors = [readonly_error() for _ in range(50)]

        def fake_connect():
            attempts.append(1)
            return FakeConnection(begin_errors=list(errors))

        with (
            mock.patch.object(canvas_store, "_connect", side_effect=fake_connect),
            mock.patch.object(canvas_store.time, "sleep"),
        ):
            with self.assertRaises(sqlite3.OperationalError):
                canvas_store._run_write_transaction(lambda con: "unreachable")

        self.assertLessEqual(len(attempts), canvas_store._MAX_READONLY_RETRIES + 1)
        self.assertGreaterEqual(len(attempts), 2)

    def test_connect_time_readonly_is_retried(self):
        connection = FakeConnection()
        connect = mock.Mock(side_effect=[readonly_error(), connection])

        with (
            mock.patch.object(canvas_store, "_connect", connect),
            mock.patch.object(canvas_store.time, "sleep"),
        ):
            result = canvas_store._run_write_transaction(lambda con: "ok")

        self.assertEqual(result, "ok")
        self.assertEqual(connect.call_count, 2)

    def test_business_error_is_not_retried(self):
        connection = FakeConnection()
        calls = []

        def callback(con):
            calls.append(con)
            raise sqlite3.OperationalError("no such column: oops")

        with (
            mock.patch.object(canvas_store, "_connect", return_value=connection),
            mock.patch.object(canvas_store.time, "sleep"),
        ):
            with self.assertRaises(sqlite3.OperationalError):
                canvas_store._run_write_transaction(callback)

        self.assertEqual(len(calls), 1)
        self.assertEqual(connection.rollbacks, 1)
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
