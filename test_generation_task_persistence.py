import unittest
import json
import tempfile
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import canvas_store
import provider_client
import server
import tenant_context


class GenerationTaskPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root_override = patch.dict(
            "os.environ",
            {
                "CANVAS_MULTI_TENANT": "1",
                "CANVAS_TENANT_DATA_ROOT": self.temp.name,
            },
        )
        self.root_override.start()
        self.context = tenant_context.make_context("persistence", "persistence")
        tenant_context.ensure_context_dirs(self.context)

    def tearDown(self):
        self.root_override.stop()
        self.temp.cleanup()

    def test_all_async_task_callbacks_require_successful_persistence(self):
        callback = mock.Mock(side_effect=RuntimeError("database is locked"))
        payload = {"taskId": "task-123", "status": "queued"}

        with self.assertRaises(provider_client.ProviderOutcomeUncertain) as caught:
            provider_client._emit_task_created(callback, payload)

        self.assertEqual(caught.exception.response["taskId"], "task-123")
        self.assertIn("database is locked", str(caught.exception))

    def test_task_creation_without_task_id_is_uncertain(self):
        callback = mock.Mock()

        with self.assertRaises(provider_client.ProviderOutcomeUncertain):
            provider_client._emit_task_created(callback, {"taskId": ""})

        callback.assert_not_called()

    def test_provider_task_journal_survives_database_failure(self):
        with tenant_context.use_context(self.context):
            saved = canvas_store.remember_provider_task(
                "local-task-1",
                "task_REiQtcNHsrOzoN21Fy3TxblDD9I3AG3h",
                project_id="project-1",
                node_id="node-1",
                provider="new-apl",
                model="65-seedream-5-pro",
            )
            journal_entries = canvas_store.list_provider_task_journal()

        self.assertEqual(
            saved["providerTaskId"],
            "task_REiQtcNHsrOzoN21Fy3TxblDD9I3AG3h",
        )
        self.assertEqual(
            journal_entries[0]["providerTaskId"],
            "task_REiQtcNHsrOzoN21Fy3TxblDD9I3AG3h",
        )
        journal_path = (
            Path(self.temp.name)
            / "tenants"
            / "persistence"
            / "generation-journal"
            / "task_REiQtcNHsrOzoN21Fy3TxblDD9I3AG3h.json"
        )
        self.assertTrue(journal_path.is_file())
        self.assertEqual(
            json.loads(journal_path.read_text(encoding="utf-8"))["nodeId"],
            "node-1",
        )

    def test_pending_image_tasks_are_resumed_for_newapi(self):
        project = {
            "id": "project-1",
            "payload": {
                "nodes": [{
                    "id": "node-1",
                    "type": "image",
                    "title": "角色图",
                    "metadata": {
                        "status": "generating",
                        "taskId": "task-image-1",
                        "providerTaskStatus": "queued",
                        "params": {
                            "provider": "new-apl",
                            "model": "65-seedream-5-pro",
                        },
                    },
                }],
            },
        }
        provider = {"id": "new-apl", "name": "New APl"}
        model = {"id": "65-seedream-5-pro", "adapter": "newapi_image"}
        started = []

        with (
            patch.object(server, "_active_tenants", return_value=[None]),
            patch.object(server.store, "list_projects", return_value=[{"id": "project-1"}]),
            patch.object(server.store, "get_project", return_value=project),
            patch.object(
                server.provider_catalog,
                "resolve_model",
                return_value=(provider, model, {}),
            ),
            patch.object(server.threading, "Thread") as thread_cls,
        ):
            thread_cls.return_value.start.side_effect = lambda: started.append(
                thread_cls.call_args.kwargs["args"]
            )
            server.start_pending_video_resumers()

        self.assertEqual(len(started), 1)
        self.assertIs(started[0][2], server._resume_image_task)
        self.assertEqual(started[0][3:6], ("project-1", "node-1", "task-image-1"))

    def test_journal_restores_uncertain_image_node_to_resumable_state(self):
        project = {
            "id": "project-1",
            "payload": {
                "nodes": [{
                    "id": "node-1",
                    "type": "image",
                    "title": "角色图",
                    "metadata": {
                        "status": "error",
                        "taskId": "",
                        "providerTaskStatus": "uncertain",
                        "params": {
                            "provider": "new-apl",
                            "model": "65-seedream-5-pro",
                        },
                    },
                }],
            },
        }
        entry = {
            "taskId": "local-task-1",
            "providerTaskId": "task-image-1",
            "projectId": "project-1",
            "nodeId": "node-1",
        }
        with (
            patch.object(
                server.store,
                "list_provider_task_journal",
                return_value=[entry],
            ),
            patch.object(
                server.store,
                "get_generation_task",
                return_value={
                    "id": "local-task-1",
                    "provider_task_id": "",
                },
            ),
            patch.object(server.store, "get_project", return_value=project),
            patch.object(server.store, "update_generation_task") as update_task,
            patch.object(server, "commit_node_metadata") as commit_meta,
        ):
            recovered = server.reconcile_provider_task_journal()

        self.assertGreaterEqual(recovered, 2)
        update_task.assert_called_with(
            "local-task-1",
            provider_task_id="task-image-1",
            status="running",
            phase="等待恢复",
            error="",
            finished_at="",
        )
        self.assertEqual(
            commit_meta.call_args.args[2]["taskId"],
            "task-image-1",
        )
        self.assertEqual(
            commit_meta.call_args.args[2]["status"],
            "generating",
        )


if __name__ == "__main__":
    unittest.main()
