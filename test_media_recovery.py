from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import canvas_store
import server
import tenant_context


class MissingMediaRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            "CANVAS_MULTI_TENANT": "1",
            "CANVAS_TENANT_DATA_ROOT": self.temp.name,
        })
        self.env.start()
        self.context = tenant_context.make_context("recovery", "recovery")
        tenant_context.ensure_context_dirs(self.context)
        self.token = tenant_context.use_tenant(
            self.context.tenant_id,
            self.context.slug,
        )
        self.token.__enter__()
        self.addCleanup(self.token.__exit__, None, None, None)
        self.addCleanup(self.env.stop)
        self.addCleanup(self.temp.cleanup)

    def _seed(self, *, provider_url: str = "https://example.test/result.png"):
        project_id = canvas_store.create_project("media-recovery")
        node = {
            "id": "image-recovery",
            "type": "image",
            "title": "待恢复图片",
            "metadata": {
                "status": "done",
                "content": "/uploads/already-missing.png",
            },
        }
        canvas_store.insert_project_nodes(
            project_id,
            [node],
            episode_id="EP001",
        )
        task = canvas_store.create_generation_task(
            project_id,
            node["id"],
            kind="image",
            episode_id="EP001",
        )
        canvas_store.finish_generation_task(
            task["id"],
            status="succeeded",
            phase="已完成",
            provider_result_url=provider_url,
            local_result_url="/uploads/already-missing.png",
            delivery_status="ready",
        )
        return project_id, node["id"], task["id"]

    def test_recovers_the_same_success_result_without_new_generation(self):
        project_id, node_id, task_id = self._seed()
        with mock.patch.object(
            server,
            "persist_generated_media",
            return_value="/uploads/recovered.png",
        ) as persist, mock.patch.object(
            server,
            "provider_catalog",
        ) as catalog:
            catalog.resolve_model.side_effect = RuntimeError("model disabled")
            Path(self.context.upload_dir, "recovered.png").write_bytes(b"png")
            result = server.recover_missing_generated_media(
                project_id,
                node_id,
            )

        self.assertTrue(result["restored"], result)
        persist.assert_called_once_with(
            "https://example.test/result.png",
            ".png",
            provider={},
            model={},
        )
        task = canvas_store.get_generation_task(task_id)
        self.assertEqual(task["status"], "succeeded")
        project = canvas_store.get_project(project_id)
        node = next(
            item for item in project["payload"]["nodes"]
            if item["id"] == node_id
        )
        self.assertEqual(node["metadata"]["content"], "/uploads/recovered.png")
        self.assertEqual(
            len(canvas_store.list_generation_tasks(project_id)),
            1,
        )

    def test_returns_a_clear_reason_when_provider_url_has_expired(self):
        project_id, node_id, _task_id = self._seed()
        with mock.patch.object(
            server,
            "persist_generated_media",
            side_effect=RuntimeError("下载生成结果失败 HTTP 403"),
        ):
            result = server.recover_missing_generated_media(
                project_id,
                node_id,
            )
        self.assertFalse(result["restored"])
        self.assertIn("403", result["reason"])
        self.assertEqual(
            len(canvas_store.list_generation_tasks(project_id)),
            1,
        )


if __name__ == "__main__":
    unittest.main()
