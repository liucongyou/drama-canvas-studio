from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

import server


class BackgroundGenerationTests(unittest.TestCase):
    """The HTTP request must only enqueue; the paid call lives in a worker thread."""

    def test_start_generation_background_invokes_worker_without_blocking_caller(self):
        started = threading.Event()
        release = threading.Event()
        seen = {}

        def fake_worker(project_id, node_id, task_id, tenant_id, tenant_slug):
            seen.update({
                "project_id": project_id,
                "node_id": node_id,
                "task_id": task_id,
                "tenant_id": tenant_id,
                "tenant_slug": tenant_slug,
            })
            started.set()
            release.wait(timeout=2)

        with patch.object(server, "_run_generation_background", side_effect=fake_worker):
            thread = server._start_generation_background(
                "p1", "n1", "t1", "tenant-1", "tenant-one",
            )
            self.assertTrue(started.wait(timeout=2))
            self.assertEqual(seen["task_id"], "t1")
            self.assertEqual(seen["tenant_id"], "tenant-1")
            release.set()
            thread.join(timeout=2)

    def test_worker_marks_task_failed_when_project_disappears(self):
        calls = []

        with patch.object(
            server,
            "tenant_context",
        ) as tenant_context, patch.object(
            server.store,
            "get_project",
            return_value=None,
        ), patch.object(
            server.store,
            "update_generation_task",
            side_effect=lambda task_id, **fields: calls.append((task_id, fields)),
        ):
            tenant_context.use_tenant.return_value.__enter__.return_value = None
            tenant_context.use_tenant.return_value.__exit__.return_value = False
            server._run_generation_background("p1", "n1", "t1", "tenant-1", "tenant-one")

        self.assertTrue(calls)
        self.assertEqual(calls[0][0], "t1")
        self.assertEqual(calls[0][1]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
