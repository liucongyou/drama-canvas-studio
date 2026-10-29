# -*- coding: utf-8 -*-
import concurrent.futures
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import canvas_store
import server
import tenant_context


class CanvasStoreConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            "CANVAS_MULTI_TENANT": "1",
            "CANVAS_TENANT_DATA_ROOT": str(self.root),
        })
        self.env.start()
        self.context = tenant_context.make_context("concurrency", "concurrency")
        tenant_context.ensure_context_dirs(self.context)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_sqlite_uses_wal_and_short_busy_timeout(self):
        with tenant_context.use_context(self.context):
            connection = canvas_store._connect()
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
            foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            connection.close()
        self.assertEqual(str(journal_mode).lower(), "wal")
        self.assertEqual(int(busy_timeout), 30000)
        self.assertEqual(int(foreign_keys), 1)

    def test_project_locks_are_independent_and_same_project_is_exclusive(self):
        first = server.project_write_lock("project-a")
        second = server.project_write_lock("project-b")
        first.__enter__()
        try:
            second.__enter__()
            second.__exit__(None, None, None)
        finally:
            first.__exit__(None, None, None)

        active = 0
        maximum = 0
        guard = threading.Lock()

        def worker():
            nonlocal active, maximum
            with server.project_write_lock("project-shared"):
                with guard:
                    active += 1
                    maximum = max(maximum, active)
                time.sleep(0.02)
                with guard:
                    active -= 1

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda _index: worker(), range(12)))

        self.assertEqual(maximum, 1)

    def test_concurrent_writes_to_different_projects_succeed(self):
        with tenant_context.use_context(self.context):
            project_ids = [
                canvas_store.create_project(f"concurrent-{index}")
                for index in range(12)
            ]

        def update(project_id):
            with tenant_context.use_context(self.context):
                with server.project_write_lock(project_id):
                    project = canvas_store.get_project(project_id)
                    project["payload"]["title"] = f"updated-{project_id}"
                    canvas_store.update_project(
                        project_id,
                        title=project["payload"]["title"],
                        payload=project["payload"],
                    )
                    return canvas_store.get_project(project_id)["revision"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            revisions = list(pool.map(update, project_ids))

        self.assertEqual(revisions, [2] * len(project_ids))


if __name__ == "__main__":
    unittest.main()
