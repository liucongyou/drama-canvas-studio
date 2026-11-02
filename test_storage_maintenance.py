# -*- coding: utf-8 -*-
"""数据库空间回收（storage_stats / maintain_storage）的回归用例。

守的是：自动 VACUUM 不能在「没什么可回收」时乱锁库，
也不能在「确实有大量空闲页」时不动手。
"""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import canvas_store
import tenant_context


class StorageMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            "CANVAS_MULTI_TENANT": "1",
            "CANVAS_TENANT_DATA_ROOT": str(self.root),
        })
        self.env.start()
        self.context = tenant_context.make_context("storage", "storage")
        tenant_context.ensure_context_dirs(self.context)
        self.token = tenant_context.use_tenant(self.context.tenant_id, self.context.slug)
        self.token.__enter__()
        self.addCleanup(lambda: self.token.__exit__(None, None, None))
        self.addCleanup(self.env.stop)
        self.addCleanup(self.temp.cleanup)

    def _db(self):
        return canvas_store._db_path()

    def _seed_and_bloat(self):
        """写一坨数据再删掉，制造空闲页（模拟整包 payload 反复覆盖写）。"""
        path = self._db()
        con = sqlite3.connect(path, timeout=30)
        try:
            con.execute("CREATE TABLE IF NOT EXISTS bloat(id INTEGER PRIMARY KEY, body TEXT)")
            con.execute(
                "INSERT INTO bloat(body) VALUES (?)",
                ("x" * 200_000,),
            )
            con.executemany(
                "INSERT INTO bloat(body) VALUES (?)",
                [("y" * 200_000,) for _ in range(60)],
            )
            con.commit()
            con.execute("DELETE FROM bloat")
            con.commit()
        finally:
            con.close()

    def test_stats_reports_free_pages(self):
        self._seed_and_bloat()
        stats = canvas_store.storage_stats()
        self.assertTrue(stats["page_count"] > 0)
        self.assertGreater(stats["free_pages"], 0)
        self.assertGreater(stats["free_bytes"], 0)
        self.assertGreater(stats["free_ratio"], 0)

    def test_maintain_reclaims_and_is_idempotent(self):
        self._seed_and_bloat()
        before = canvas_store.storage_stats()
        freed = canvas_store.maintain_storage(min_free_ratio=0.1, min_free_bytes=1024)
        after = canvas_store.storage_stats()
        self.assertGreater(freed, 0)
        self.assertLess(after["file_bytes"], before["file_bytes"])
        # 再跑一次：已经收干净，不应再动（返回 0，不锁库）
        self.assertEqual(
            canvas_store.maintain_storage(min_free_ratio=0.1, min_free_bytes=1024),
            0,
        )

    def test_skips_when_dirty_ratio_is_low(self):
        # 阈值设成 1.1（永远达不到），必须一次都不 VACUUM
        self._seed_and_bloat()
        self.assertEqual(canvas_store.maintain_storage(min_free_ratio=1.1), 0)

    def test_missing_database_is_safe(self):
        with mock.patch.object(canvas_store, "_db_path", return_value=str(self.root / "nope.db")):
            self.assertEqual(canvas_store.maintain_storage(), 0)
            self.assertEqual(canvas_store.storage_stats()["page_count"], 0)

    def test_vacuum_lock_conflict_does_not_raise(self):
        self._seed_and_bloat()
        with mock.patch.object(canvas_store.sqlite3, "connect", side_effect=sqlite3.OperationalError("database is locked")):
            # 连接都建不起来时必须安静返回，不能把后台线程炸掉
            self.assertEqual(canvas_store.maintain_storage(min_free_ratio=0.1, min_free_bytes=1024), 0)

    def test_wal_checkpoint_reports_success_and_keeps_autocheckpoint_off(self):
        project_id = canvas_store.create_project("wal-checkpoint")
        task = canvas_store.create_generation_task(
            project_id,
            "wal-node",
            kind="video",
            episode_id="EP001",
        )
        canvas_store.finish_generation_task(
            task["id"],
            status="succeeded",
            phase="done",
        )
        result = canvas_store.checkpoint_wal(mode="TRUNCATE")
        self.assertTrue(result["ok"], result)
        con = canvas_store._connect()
        try:
            self.assertEqual(
                con.execute("PRAGMA wal_autocheckpoint").fetchone()[0],
                0,
            )
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
