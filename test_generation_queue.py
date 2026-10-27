from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

import canvas_store
import server


class GenerationQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = canvas_store.DB_PATH
        self.old_schema_ready = canvas_store._SCHEMA_READY
        canvas_store.DB_PATH = str(Path(self.temp_dir.name) / "queue.db")
        canvas_store._SCHEMA_READY = False
        with server.GENERATION_QUEUE_CONDITION:
            self.old_global_active = server.GENERATION_QUEUE_ACTIVE_GLOBAL
            self.old_provider_active = dict(server.GENERATION_QUEUE_ACTIVE_PROVIDERS)
            self.old_tenant_active = dict(server.GENERATION_QUEUE_ACTIVE_TENANTS)
            server.GENERATION_QUEUE_ACTIVE_GLOBAL = 0
            server.GENERATION_QUEUE_ACTIVE_PROVIDERS.clear()
            server.GENERATION_QUEUE_ACTIVE_TENANTS.clear()

    def tearDown(self):
        with server.GENERATION_QUEUE_CONDITION:
            server.GENERATION_QUEUE_ACTIVE_GLOBAL = self.old_global_active
            server.GENERATION_QUEUE_ACTIVE_PROVIDERS.clear()
            server.GENERATION_QUEUE_ACTIVE_PROVIDERS.update(self.old_provider_active)
            server.GENERATION_QUEUE_ACTIVE_TENANTS.clear()
            server.GENERATION_QUEUE_ACTIVE_TENANTS.update(self.old_tenant_active)
            server.GENERATION_QUEUE_CONDITION.notify_all()
        canvas_store.DB_PATH = self.old_db_path
        canvas_store._SCHEMA_READY = self.old_schema_ready
        self.temp_dir.cleanup()

    def test_attempt_increments_and_active_task_is_reused(self):
        first = canvas_store.create_generation_task(
            "project-1",
            "node-1",
            title="段1",
            kind="image",
        )
        self.assertTrue(first["created_new"])
        self.assertEqual(first["attempt"], 1)

        duplicate = canvas_store.create_generation_task(
            "project-1",
            "node-1",
            title="段1",
            kind="image",
        )
        self.assertFalse(duplicate["created_new"])
        self.assertEqual(duplicate["id"], first["id"])

        canvas_store.update_generation_task(first["id"], status="succeeded")
        second = canvas_store.create_generation_task(
            "project-1",
            "node-1",
            title="段1",
            kind="image",
        )
        self.assertTrue(second["created_new"])
        self.assertEqual(second["attempt"], 2)

    def test_stale_node_edit_is_detected_as_conflict(self):
        current = {
            "id": "node-1",
            "title": "段1",
            "updatedAt": "2026-10-26T12:00:02.000Z",
            "position": {"x": 10, "y": 20},
            "metadata": {
                "prompt": "另一个标签页的新提示词",
                "refs": [],
                "params": {"ratio": "9:16"},
            },
        }
        incoming = {
            "id": "node-1",
            "title": "段1",
            "baseUpdatedAt": "2026-10-26T12:00:01.000Z",
            "position": {"x": 10, "y": 20},
            "metadata": {
                "prompt": "当前标签页的旧提示词",
                "refs": [],
                "params": {"ratio": "9:16"},
            },
        }
        self.assertTrue(server._node_save_conflict(incoming, current))
        incoming["metadata"]["prompt"] = current["metadata"]["prompt"]
        self.assertFalse(server._node_save_conflict(incoming, current))
        incoming["baseUpdatedAt"] = "2026-10-26T12:00:03.000Z"
        incoming["metadata"]["prompt"] = "用户看到新状态后的主动修改"
        self.assertFalse(server._node_save_conflict(incoming, current))

    def test_local_validation_and_provider_failures_are_classified_separately(self):
        self.assertTrue(server.is_local_validation_error("参考素材未就绪 2 项：角色未生成"))
        self.assertTrue(server.is_local_validation_error("音色节点尚未生成"))
        self.assertFalse(server.is_local_validation_error("供应商接口 HTTP429: rate limit"))

    def test_provider_concurrency_slot_is_serialized(self):
        provider = "test-provider"
        first = server.acquire_generation_slot(None, provider)
        acquired = threading.Event()

        def acquire_second():
            slot = server.acquire_generation_slot(None, provider)
            acquired.set()
            server.release_generation_slot(slot)

        thread = threading.Thread(target=acquire_second, daemon=True)
        thread.start()
        time.sleep(0.15)
        self.assertFalse(acquired.is_set())
        server.release_generation_slot(first)
        self.assertTrue(acquired.wait(timeout=2))
        thread.join(timeout=2)

    def test_reconcile_closes_task_when_node_is_done(self):
        pid = canvas_store.create_project("queue-reconcile")
        node = {
            "id": "node-1",
            "type": "video",
            "title": "段1",
            "position": {"x": 0, "y": 0},
            "metadata": {
                "status": "done",
                "content": "/uploads/generated.mp4",
            },
        }
        project = canvas_store.get_project(pid)
        project["payload"]["nodes"] = [node]
        canvas_store.update_project(pid, payload=project["payload"])
        task = canvas_store.create_generation_task(pid, "node-1", kind="video")
        canvas_store.update_generation_task(
            task["id"],
            status="running",
            phase="生成中 10%",
            provider_task_id="provider-task-1",
        )
        con = canvas_store._connect()
        con.execute(
            "UPDATE generation_tasks SET updated_at=? WHERE id=?",
            ("2026-01-01T00:00:00.000Z", task["id"]),
        )
        con.commit()
        con.close()

        reconciled = server.reconcile_generation_task_records()

        self.assertEqual(reconciled, 1)
        updated = canvas_store.get_generation_task(task["id"])
        self.assertEqual(updated["status"], "succeeded")
        self.assertEqual(updated["phase"], "已完成")
        self.assertTrue(updated["finished_at"])

    def test_reconcile_marks_stale_generating_node_failed_after_provider_failure(self):
        pid = canvas_store.create_project("queue-failed")
        node = {
            "id": "node-1",
            "type": "image",
            "title": "角色",
            "position": {"x": 0, "y": 0},
            "metadata": {"status": "generating", "content": ""},
        }
        project = canvas_store.get_project(pid)
        project["payload"]["nodes"] = [node]
        canvas_store.update_project(pid, payload=project["payload"])
        task = canvas_store.create_generation_task(pid, "node-1", kind="image")
        canvas_store.update_generation_task(
            task["id"],
            status="failed",
            phase="生成失败",
            error="供应商接口 HTTP 401：无效的令牌",
            finished_at="2026-01-01T00:00:00.000Z",
        )

        reconciled = server.reconcile_generation_task_records()

        self.assertEqual(reconciled, 1)
        project = canvas_store.get_project(pid)
        meta = project["payload"]["nodes"][0]["metadata"]
        self.assertEqual(meta["status"], "failed")
        self.assertIn("HTTP 401", meta["lastError"])

    def test_active_generation_metadata_cannot_be_overwritten(self):
        meta = {
            "status": "generating",
            "content": "/uploads/current.mp4",
            "providerTaskStatus": "running",
            "prompt": "new prompt",
        }
        server.merge_node_metadata(
            meta,
            {
                "status": "done",
                "content": "/uploads/stale.mp4",
                "providerTaskStatus": "completed",
                "prompt": "edited prompt",
            },
            generation_active=True,
        )

        self.assertEqual(meta["status"], "generating")
        self.assertEqual(meta["content"], "/uploads/current.mp4")
        self.assertEqual(meta["providerTaskStatus"], "running")
        self.assertEqual(meta["prompt"], "edited prompt")


if __name__ == "__main__":
    unittest.main()
