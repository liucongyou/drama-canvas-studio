# -*- coding: utf-8 -*-
import concurrent.futures
import io
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
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

    def test_connections_save_drops_stale_and_reverse_duplicate_edges(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project(
                "connections-contract",
                payload={
                    "nodes": [{"id": "a"}, {"id": "b"}],
                    "connections": [{
                        "id": "edge-auto",
                        "fromNodeId": "a",
                        "toNodeId": "b",
                        "source": "import",
                    }],
                },
            )
            handler = object.__new__(server.Handler)
            handler.path = f"/api/projects/{project_id}/connections"
            handler.send_response = mock.Mock()
            handler.send_header = mock.Mock()
            handler.end_headers = mock.Mock()
            handler.wfile = mock.Mock()
            handler._cors_headers = mock.Mock(return_value={})
            handler.command = "PUT"
            body = json.dumps({
                "connections": [
                    {"id": "stale", "fromNodeId": "a", "toNodeId": "missing"},
                    {
                        "id": "reverse-duplicate",
                        "fromNodeId": "b",
                        "toNodeId": "a",
                    },
                ],
            }).encode("utf-8")
            handler.headers = {"Content-Length": str(len(body))}
            handler.rfile = io.BytesIO(body)

            handler._do_put_locked()

            saved = canvas_store.get_project(project_id)

        response = json.loads(handler.wfile.write.call_args.args[0])
        self.assertEqual(response["droppedInvalid"], 1)
        self.assertEqual(response["total"], 1)
        self.assertEqual(
            saved["payload"]["connections"],
            [{
                "id": "edge-auto",
                "fromNodeId": "a",
                "toNodeId": "b",
                "source": "import",
            }],
        )

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

    def test_generation_task_write_retries_transient_database_lock(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("retry-lock")
            task = canvas_store.create_generation_task(
                project_id,
                "node-retry",
                kind="image",
            )
            original_connect = canvas_store._connect
            calls = {"count": 0}

            def flaky_connect():
                connection = original_connect()
                if calls["count"] == 0:
                    calls["count"] += 1
                    connection.close()
                    raise sqlite3.OperationalError("database is locked")
                return connection

            with patch.object(canvas_store, "_connect", side_effect=flaky_connect), \
                    patch.object(canvas_store.time, "sleep"):
                updated = canvas_store.update_generation_task(
                    task["id"],
                    provider_task_id="task-after-retry",
                    status="running",
                )

        self.assertGreaterEqual(calls["count"], 1)
        self.assertEqual(updated["provider_task_id"], "task-after-retry")
        self.assertEqual(updated["status"], "running")

    def test_generation_cost_reservations_are_summed_for_budget_checks(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("budget")
            canvas_store.create_generation_task(
                project_id,
                "node-image",
                kind="image",
                estimated_cost=0.3,
                cost_currency="CNY",
            )
            canvas_store.create_generation_task(
                project_id,
                "node-video",
                kind="video",
                estimated_cost=6.93,
                cost_currency="CNY",
            )
            total = canvas_store.generation_cost_total("2000-01-01T00:00:00+00:00")
        self.assertAlmostEqual(total["amount"], 7.23, places=6)
        self.assertEqual(total["currency"], "CNY")
        self.assertEqual(total["currencies"], ["CNY"])

    def test_many_generation_task_writers_do_not_lose_state(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("generation-write-stress")
            tasks = [
                canvas_store.create_generation_task(
                    project_id,
                    f"node-{index}",
                    kind="video",
                )
                for index in range(40)
            ]

        def update(index_and_task):
            index, task = index_and_task
            with tenant_context.use_context(self.context):
                for step in range(5):
                    canvas_store.update_generation_task(
                        task["id"],
                        phase=f"progress-{step}",
                        progress=step * 10,
                        provider_task_id=f"provider-{index}",
                    )
                return canvas_store.get_generation_task(task["id"])

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            saved = list(pool.map(update, enumerate(tasks)))

        self.assertEqual(len(saved), 40)
        self.assertTrue(all(item["provider_task_id"] for item in saved))
        self.assertTrue(all(item["progress"] == 40 for item in saved))

    def test_reads_and_writes_under_parallel_load(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("read-write-stress")
            tasks = [
                canvas_store.create_generation_task(
                    project_id,
                    f"rw-node-{index}",
                    kind="image",
                )
                for index in range(20)
            ]

        def worker(index):
            with tenant_context.use_context(self.context):
                task = tasks[index % len(tasks)]
                if index % 3 == 0:
                    canvas_store.list_generation_tasks(project_id, 300)
                    return "read"
                canvas_store.update_generation_task(
                    task["id"],
                    progress=index % 100,
                )
                return "write"

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            outcomes = list(pool.map(worker, range(120)))

        self.assertEqual(outcomes.count("read"), 40)
        self.assertEqual(outcomes.count("write"), 80)


if __name__ == "__main__":
    unittest.main()
