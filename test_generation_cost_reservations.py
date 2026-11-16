from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import canvas_store
import tenant_context


class GenerationCostReservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            "CANVAS_DB_BACKEND": "sqlite",
            "CANVAS_ALLOW_SQLITE": "1",
            "CANVAS_MULTI_TENANT": "1",
            "CANVAS_TENANT_DATA_ROOT": str(self.root),
        })
        self.env.start()
        canvas_store._SCHEMA_READY_PATHS.clear()
        self.context = tenant_context.make_context("budget-test", "budget-test")
        tenant_context.ensure_context_dirs(self.context)
        self.scope = tenant_context.use_context(self.context)
        self.scope.__enter__()
        self.project_id = canvas_store.create_project("budget-test")

    def tearDown(self):
        self.scope.__exit__(None, None, None)
        self.env.stop()
        canvas_store._SCHEMA_READY_PATHS.clear()
        self.temp.cleanup()

    def _task(self, node_id, cost, *, status="queued", provider_task_id="", error=""):
        task = canvas_store.create_generation_task(
            self.project_id,
            node_id,
            kind="video",
            estimated_cost=cost,
            cost_currency="CNY",
        )
        canvas_store.update_generation_task(
            task["id"],
            status=status,
            provider_task_id=provider_task_id,
            error=error,
        )
        return task

    def test_cost_snapshot_releases_only_provably_unbilled_tasks(self):
        self._task("succeeded", 2, status="succeeded", provider_task_id="p-1")
        self._task("queued", 1)
        self._task(
            "local-validation",
            4,
            status="failed",
            error="参考素材缺失：角色图尚未生成",
        )
        self._task(
            "cancelled",
            8,
            status="failed",
            error="已取消生成排队",
        )
        self._task(
            "uncertain",
            16,
            status="interrupted",
            error="供应商请求可能已经发出，但未取得回执",
        )
        self._task(
            "provider-failed",
            32,
            status="failed",
            provider_task_id="p-2",
            error="供应商视频生成失败",
        )

        snapshot = canvas_store.generation_cost_total("2000-01-01T00:00:00Z")

        self.assertEqual(snapshot["amount"], 51)
        self.assertEqual(snapshot["reserved"], 17)
        self.assertEqual(snapshot["committed"], 34)
        self.assertEqual(snapshot["releasedTasks"], 2)
        self.assertEqual(snapshot["currencies"], ["CNY"])


if __name__ == "__main__":
    unittest.main()
