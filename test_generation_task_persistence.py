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
        server.RESUMED_VIDEO_TASKS.clear()

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

    def test_failed_node_with_persisted_provider_task_is_resumed(self):
        project = {
            "id": "project-1",
            "payload": {
                "nodes": [{
                    "id": "node-1",
                    "type": "video",
                    "title": "段3",
                    "metadata": {
                        "status": "failed",
                        "params": {
                            "provider": "new-apl",
                            "model": "004-sd2.0-fast933-5-15-720p",
                        },
                    },
                }],
            },
        }
        task = {
            "id": "local-task-1",
            "project_id": "project-1",
            "node_id": "node-1",
            "provider_task_id": "task-provider-1",
            "provider": "new-apl",
            "model": "004-sd2.0-fast933-5-15-720p",
            "kind": "video",
            "status": "running",
        }
        provider = {"id": "new-apl", "name": "New APl"}
        model = {
            "id": "004-sd2.0-fast933-5-15-720p",
            "adapter": "canvaspro_video",
        }
        started = []
        with (
            patch.object(server, "_active_tenants", return_value=[None]),
            patch.object(server.store, "list_projects", return_value=[{"id": "project-1"}]),
            patch.object(server.store, "get_project", return_value=project),
            patch.object(server.store, "list_generation_tasks", return_value=[task]),
            patch.object(
                server.provider_catalog,
                "resolve_model",
                return_value=(provider, model, {}),
            ),
            patch.object(server, "commit_node_metadata"),
            patch.object(server.threading, "Thread") as thread_cls,
        ):
            thread_cls.return_value.start.side_effect = lambda: started.append(
                thread_cls.call_args.kwargs["args"]
            )
            server.start_pending_video_resumers()

        self.assertEqual(len(started), 1)
        self.assertIs(started[0][2], server._resume_video_task)
        self.assertEqual(
            started[0][3:6],
            ("project-1", "node-1", "task-provider-1"),
        )

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

    def test_terminal_generation_task_cannot_regress(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("terminal-task")
            task = canvas_store.create_generation_task(
                project_id,
                "node-video",
                kind="video",
                provider="new-apl",
                model="seedance",
            )
            succeeded = canvas_store.update_generation_task(
                task["id"],
                status="succeeded",
                phase="已完成",
                provider_task_id="provider-video-1",
                finished_at="2026-10-30T00:01:00+00:00",
                error="",
            )
            failed = canvas_store.update_generation_task(
                task["id"],
                status="failed",
                phase="生成失败",
                error="late stale writer",
                finished_at="2026-10-30T00:02:00+00:00",
            )

        self.assertEqual(succeeded["status"], "succeeded")
        self.assertEqual(failed["status"], "succeeded")
        self.assertEqual(failed["phase"], "已完成")
        self.assertEqual(failed["error"], "")
        self.assertEqual(failed["provider_task_id"], "provider-video-1")

    def test_failed_generation_task_accepts_success_but_not_running_regression(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("failed-task")
            task = canvas_store.create_generation_task(
                project_id,
                "node-image",
                kind="image",
            )
            failed = canvas_store.update_generation_task(
                task["id"],
                status="failed",
                phase="生成失败",
                error="provider failed",
                finished_at="2026-10-30T00:01:00+00:00",
            )
            stale_running = canvas_store.update_generation_task(
                task["id"],
                status="running",
                phase="生成中",
                error="",
            )
            recovered = canvas_store.update_generation_task(
                task["id"],
                status="succeeded",
                phase="已完成",
                error="",
                finished_at="2026-10-30T00:02:00+00:00",
            )

        self.assertEqual(failed["status"], "failed")
        self.assertEqual(stale_running["status"], "failed")
        self.assertEqual(stale_running["error"], "provider failed")
        self.assertEqual(recovered["status"], "succeeded")
        self.assertEqual(recovered["error"], "")

    def test_submitted_task_exception_stays_uncertain_and_resumable(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("uncertain-video")
            task = canvas_store.create_generation_task(
                project_id,
                "node-video",
                kind="video",
                provider="new-apl",
                model="004-sd2.0",
            )
            canvas_store.update_generation_task(
                task["id"],
                status="running",
                provider_task_id="provider-video-1",
                phase="生成中",
            )

            with patch.object(server, "commit_node_metadata") as commit_meta:
                server._mark_generation_outcome_uncertain(
                    task["id"],
                    project_id,
                    "node-video",
                    RuntimeError("download interrupted"),
                )

            saved = canvas_store.get_generation_task(task["id"])

        self.assertEqual(saved["status"], "interrupted")
        self.assertEqual(saved["phase"], "结果待确认")
        self.assertEqual(saved["provider_task_id"], "provider-video-1")
        self.assertEqual(commit_meta.call_args.args[2]["providerTaskStatus"], "uncertain")
        self.assertEqual(
            commit_meta.call_args.args[2]["providerTaskPhase"],
            "结果待确认",
        )

    def test_reused_active_task_refreshes_after_status_changed(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("fresh-active-task")
            task = canvas_store.create_generation_task(
                project_id,
                "node-video",
                kind="video",
            )
            original_get = canvas_store.get_generation_task
            finished = {"done": False}

            def finish_before_reuse(task_id):
                if not finished["done"]:
                    finished["done"] = True
                    with patch.object(
                        canvas_store,
                        "get_generation_task",
                        side_effect=original_get,
                    ):
                        canvas_store.update_generation_task(
                            task_id,
                            status="succeeded",
                            phase="已完成",
                            progress=100,
                            error="",
                            finished_at="2026-10-30T00:01:00+00:00",
                        )
                return original_get(task_id)

            with patch.object(
                canvas_store,
                "get_generation_task",
                side_effect=finish_before_reuse,
            ):
                reused = canvas_store.create_generation_task(
                    project_id,
                    "node-video",
                    kind="video",
                )

        self.assertFalse(reused["created_new"])
        self.assertEqual(reused["status"], "succeeded")
        self.assertEqual(reused["phase"], "已完成")

    def test_provider_query_failure_keeps_task_recoverable_without_fake_progress(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("query-retry")
            task = canvas_store.create_generation_task(
                project_id,
                "node-video",
                kind="video",
                provider="new-apl",
                model="004-sd2.0",
            )
            canvas_store.update_generation_task(
                task["id"],
                status="running",
                provider_task_id="provider-video-1",
                phase="生成中",
            )

            project = {
                "id": project_id,
                "payload": {
                    "nodes": [{
                        "id": "node-video",
                        "type": "video",
                        "metadata": {"status": "error"},
                    }],
                },
            }
            with (
                patch.object(server.store, "get_project", return_value=project),
                patch.object(
                    server.provider_client,
                    "check_video_task",
                    side_effect=RuntimeError("temporary query failure"),
                ),
                patch.object(
                    server,
                    "commit_node_metadata",
                ) as commit_meta,
                patch.object(server, "publish_project_event"),
                patch.object(server.time, "sleep", side_effect=RuntimeError("stop-loop")),
            ):
                with self.assertRaises(RuntimeError):
                    server._resume_video_task(
                        project_id,
                        "node-video",
                        "provider-video-1",
                        {"id": "new-apl"},
                        {"id": "004-sd2.0"},
                    )

            saved = canvas_store.get_generation_task(task["id"])

        self.assertEqual(saved["status"], "running")
        self.assertEqual(saved["phase"], "查询重试中")
        self.assertEqual(saved["provider_task_id"], "provider-video-1")
        self.assertEqual(commit_meta.call_args.args[2]["status"], "generating")
        self.assertEqual(
            commit_meta.call_args.args[2]["providerTaskStatus"],
            "retrying",
        )

    def test_provider_query_404_stops_without_resubmit(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("query-not-found")
            task = canvas_store.create_generation_task(
                project_id,
                "node-video",
                kind="video",
                provider="agnes-ai",
                model="agnes-video",
            )
            canvas_store.update_generation_task(
                task["id"],
                status="running",
                provider_task_id="provider-video-404",
                phase="生成中",
            )
            project = {
                "id": project_id,
                "payload": {
                    "nodes": [{
                        "id": "node-video",
                        "type": "video",
                        "metadata": {"status": "generating"},
                    }],
                },
            }
            with (
                patch.object(server.store, "get_project", return_value=project),
                patch.object(
                    server.provider_client,
                    "check_video_task",
                    side_effect=provider_client.ProviderHTTPError(
                        404,
                        "task not found",
                    ),
                ),
                patch.object(server.provider_client, "generate_video") as submit,
                patch.object(server, "commit_node_metadata"),
                patch.object(server, "publish_project_event"),
                patch.object(server.time, "sleep") as sleep,
            ):
                server._resume_video_task(
                    project_id,
                    "node-video",
                    "provider-video-404",
                    {"id": "agnes-ai"},
                    {"id": "agnes-video"},
                )

            saved = canvas_store.get_generation_task(task["id"])

        submit.assert_not_called()
        sleep.assert_not_called()
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["phase"], "查询失败")
        self.assertIn("任务不存在", saved["error"])
        self.assertEqual(saved["provider_task_id"], "provider-video-404")

    def test_unknown_provider_status_is_not_treated_as_completed(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("unknown-provider-status")
            task = canvas_store.create_generation_task(
                project_id,
                "node-video",
                kind="video",
                provider="new-apl",
                model="004-sd2.0",
            )
            canvas_store.update_generation_task(
                task["id"],
                status="running",
                provider_task_id="provider-video-1",
                phase="生成中",
            )
            project = {
                "id": project_id,
                "payload": {
                    "nodes": [{
                        "id": "node-video",
                        "type": "video",
                        "metadata": {"status": "generating"},
                    }],
                },
            }
            with (
                patch.object(server.store, "get_project", return_value=project),
                patch.object(
                    server.provider_client,
                    "check_video_task",
                    return_value={
                        "taskId": "provider-video-1",
                        "status": "running",
                        "rawStatus": "unknown",
                        "progress": None,
                    },
                ),
                patch.object(server, "commit_node_metadata"),
                patch.object(server, "publish_project_event"),
                patch.object(server, "_finalize_resumed_video") as finalize,
                patch.object(server.time, "sleep", side_effect=RuntimeError("stop-loop")),
            ):
                with self.assertRaises(RuntimeError):
                    server._resume_video_task(
                        project_id,
                        "node-video",
                        "provider-video-1",
                        {"id": "new-apl"},
                        {"id": "004-sd2.0"},
                    )

            saved = canvas_store.get_generation_task(task["id"])

        finalize.assert_not_called()
        self.assertEqual(saved["status"], "running")
        self.assertEqual(saved["provider_task_id"], "provider-video-1")

    def test_downloaded_result_is_reused_without_redownload_or_resubmit(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("persisted-result")
            project = canvas_store.get_project(project_id)
            project["payload"]["nodes"] = [{
                "id": "node-video",
                "type": "video",
                "title": "段1",
                "metadata": {"status": "error", "params": {}},
            }]
            canvas_store.update_project(project_id, payload=project["payload"])
            task = canvas_store.create_generation_task(
                project_id,
                "node-video",
                kind="video",
                provider="new-apl",
                model="004-sd2.0",
            )
            canvas_store.update_generation_task(
                task["id"],
                status="running",
                provider_task_id="provider-video-1",
                local_result_url="/uploads/result.mp4",
                provider_result_url="https://provider.example/result.mp4",
            )
            upload_dir = Path(self.context.upload_dir)
            upload_dir.mkdir(parents=True, exist_ok=True)
            (upload_dir / "result.mp4").write_bytes(b"local-video")

            with (
                patch.object(server.provider_client, "check_video_task") as query,
                patch.object(server.provider_client, "generate_video") as submit,
                patch.object(server, "persist_generated_media") as download,
                patch.object(server, "validate_generated_video", return_value={"status": "valid"}),
            ):
                server._resume_video_task(
                    project_id,
                    "node-video",
                    "provider-video-1",
                    {"id": "new-apl"},
                    {"id": "004-sd2.0"},
                )

            saved = canvas_store.get_generation_task(task["id"])
            refreshed = canvas_store.get_project(project_id)

        query.assert_not_called()
        submit.assert_not_called()
        download.assert_not_called()
        self.assertEqual(saved["status"], "succeeded")
        self.assertEqual(
            refreshed["payload"]["nodes"][0]["metadata"]["content"],
            "/uploads/result.mp4",
        )

    def test_missing_persisted_result_stops_without_resubmit(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("missing-persisted-result")
            project = canvas_store.get_project(project_id)
            project["payload"]["nodes"] = [{
                "id": "node-video",
                "type": "video",
                "title": "段1",
                "metadata": {"status": "generating", "params": {}},
            }]
            canvas_store.update_project(project_id, payload=project["payload"])
            task = canvas_store.create_generation_task(
                project_id,
                "node-video",
                kind="video",
                provider="new-apl",
                model="004-sd2.0",
            )
            canvas_store.update_generation_task(
                task["id"],
                status="running",
                provider_task_id="provider-video-missing",
                local_result_url="/uploads/missing.mp4",
                provider_result_url="https://provider.example/missing.mp4",
            )

            with (
                patch.object(server.provider_client, "check_video_task") as query,
                patch.object(server.provider_client, "generate_video") as submit,
                patch.object(server.provider_client, "save_generated_url") as download,
            ):
                server._resume_video_task(
                    project_id,
                    "node-video",
                    "provider-video-missing",
                    {"id": "new-apl"},
                    {"id": "004-sd2.0"},
                )

            saved = canvas_store.get_generation_task(task["id"])
            refreshed = canvas_store.get_project(project_id)

        query.assert_not_called()
        submit.assert_not_called()
        download.assert_not_called()
        self.assertEqual(saved["status"], "interrupted")
        self.assertEqual(saved["phase"], "本地文件缺失")
        self.assertEqual(
            refreshed["payload"]["nodes"][0]["metadata"]["status"],
            "failed",
        )

    def test_downloaded_image_is_persisted_before_canvas_write(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("persisted-image")
            task = canvas_store.create_generation_task(
                project_id,
                "node-image",
                kind="image",
                provider="new-apl",
                model="65-seedream-5-pro",
            )
            canvas_store.update_generation_task(
                task["id"],
                status="running",
                provider_task_id="provider-image-1",
            )
            saved = canvas_store.update_generation_task(
                task["id"],
                provider_result_url="https://provider.example/result.png",
                local_result_url="/uploads/result.png",
                provider_finished_at="2026-10-30T00:01:00+00:00",
            )

        self.assertEqual(saved["local_result_url"], "/uploads/result.png")
        self.assertEqual(
            saved["provider_result_url"],
            "https://provider.example/result.png",
        )


if __name__ == "__main__":
    unittest.main()
