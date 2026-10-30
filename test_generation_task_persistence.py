import unittest
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import canvas_store
import generation_outcome
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

    def test_client_operation_id_replays_existing_task_without_new_insert(self):
        with tenant_context.use_context(self.context):
            first = canvas_store.create_generation_task(
                "project-operation",
                "node-operation",
                kind="video",
                client_operation_id="operation-1",
                request_snapshot={"prompt": "first snapshot"},
            )
            replay = canvas_store.create_generation_task(
                "project-operation",
                "node-operation",
                kind="video",
                client_operation_id="operation-1",
                request_snapshot={"prompt": "changed snapshot"},
            )

        self.assertTrue(first["created_new"])
        self.assertFalse(replay["created_new"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["id"], replay["id"])
        self.assertIn("first snapshot", replay["request_json"])

    def test_failed_operation_can_create_new_task_with_new_operation_id(self):
        with tenant_context.use_context(self.context):
            original = canvas_store.create_generation_task(
                "project-retry",
                "node-retry",
                kind="image",
                client_operation_id="operation-failed",
                request_snapshot={"model": "old-model"},
            )
            canvas_store.update_generation_task(
                original["id"],
                status="failed",
                phase="生成失败",
                error="old model rejected",
                finished_at=canvas_store._now_iso(),
            )
            replacement = canvas_store.create_generation_task(
                "project-retry",
                "node-retry",
                kind="image",
                client_operation_id="operation-new-model",
                request_snapshot={"model": "new-model"},
            )

        self.assertTrue(replacement["created_new"])
        self.assertNotEqual(replacement["id"], original["id"])
        self.assertIn("new-model", replacement["request_json"])

    def test_success_with_pending_delivery_is_not_reported_ready(self):
        outcome = generation_outcome.task_outcome({
            "status": "succeeded",
            "phase": "已完成",
            "delivery_status": "materializing",
            "provider_task_id": "provider-delivery-1",
            "adapter": "newapi_video",
        })

        self.assertEqual(outcome["phase"], "fetching")
        self.assertEqual(outcome["regenerate"], "blocked")
        self.assertEqual(outcome["reason"], "result_fetching")

    def test_success_with_failed_delivery_is_not_reported_ready(self):
        outcome = generation_outcome.task_outcome({
            "status": "succeeded",
            "phase": "已完成",
            "delivery_status": "failed",
            "provider_task_id": "provider-delivery-2",
            "adapter": "newapi_video",
        })

        self.assertEqual(outcome["phase"], "failed")
        self.assertEqual(outcome["regenerate"], "confirm")
        self.assertEqual(outcome["reason"], "result_delivery_failed")

    def test_expired_lease_without_provider_task_releases_node(self):
        with tenant_context.use_context(self.context):
            task = canvas_store.create_generation_task(
                "project-expired",
                "node-expired",
                kind="video",
            )
            owner = "observer:one"
            claimed = canvas_store.claim_generation_task(task["id"], owner, 45)
            expired_at = datetime.now(timezone.utc) + timedelta(seconds=60)
            expired_rows = canvas_store.expire_stale_generation_tasks(expired_at)
            saved = canvas_store.get_generation_task(task["id"])
            replacement = canvas_store.create_generation_task(
                "project-expired",
                "node-expired",
                kind="video",
            )

        self.assertTrue(claimed)
        self.assertEqual([item["id"] for item in expired_rows], [task["id"]])
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["phase"], "执行失联")
        self.assertEqual(saved["lease_owner"], "")
        self.assertTrue(replacement["created_new"])
        self.assertNotEqual(replacement["id"], task["id"])

    def test_expired_lease_with_provider_task_stays_recoverable(self):
        with tenant_context.use_context(self.context):
            task = canvas_store.create_generation_task(
                "project-recoverable",
                "node-recoverable",
                kind="video",
            )
            owner = "observer:two"
            self.assertTrue(canvas_store.claim_generation_task(task["id"], owner, 45))
            canvas_store.update_generation_task(
                task["id"],
                provider_task_id="provider-task-still-live",
            )
            canvas_store.expire_stale_generation_tasks(
                datetime.now(timezone.utc) + timedelta(seconds=60)
            )
            saved = canvas_store.get_generation_task(task["id"])

        self.assertEqual(saved["status"], "interrupted")
        self.assertEqual(saved["phase"], "结果待确认")
        self.assertEqual(saved["provider_task_id"], "provider-task-still-live")

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
        self.assertEqual(saved["delivery_status"], "ready")
        self.assertEqual(
            refreshed["payload"]["nodes"][0]["metadata"]["content"],
            "/uploads/result.mp4",
        )

    def test_pending_delivery_reconciles_without_provider_resubmit(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("pending-delivery")
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
                status="interrupted",
                provider_task_id="provider-delivery-pending",
                local_result_url="/uploads/pending.mp4",
                delivery_status="pending",
            )
            upload_dir = Path(self.context.upload_dir)
            upload_dir.mkdir(parents=True, exist_ok=True)
            (upload_dir / "pending.mp4").write_bytes(b"pending-video")

            with (
                patch.object(server.provider_client, "generate_video") as submit,
                patch.object(server.provider_client, "check_video_task") as query,
                patch.object(
                    server,
                    "validate_generated_video",
                    return_value={"status": "valid"},
                ),
            ):
                recovered = server.reconcile_pending_generation_deliveries()

            saved = canvas_store.get_generation_task(task["id"])
            refreshed = canvas_store.get_project(project_id)
            versions = refreshed["payload"]["nodes"][0]["metadata"]["params"]["mediaVersions"]

        submit.assert_not_called()
        query.assert_not_called()
        self.assertEqual(recovered, 1)
        self.assertEqual(saved["status"], "succeeded")
        self.assertEqual(saved["delivery_status"], "ready")
        self.assertEqual(
            [item["url"] for item in versions],
            ["/uploads/pending.mp4"],
        )

    def test_failure_chain_queries_same_task_and_recovers_after_restart(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("failure-chain")
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
                client_operation_id="failure-chain-operation",
            )
            owner = "worker:failure-chain"
            self.assertTrue(canvas_store.claim_generation_task(task["id"], owner, 45))
            canvas_store.update_generation_task(
                task["id"],
                provider_task_id="provider-same-task",
            )

            query_calls = []
            query_results = [
                provider_client.ProviderHTTPError(503, "temporary"),
                {
                    "taskId": "provider-same-task",
                    "status": "completed",
                    "url": "https://provider.example/final.mp4",
                    "rawStatus": "completed",
                },
            ]

            def query_step(*_args, **_kwargs):
                query_calls.append("query")
                result = query_results.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result

            upload_dir = Path(self.context.upload_dir)
            upload_dir.mkdir(parents=True, exist_ok=True)
            (upload_dir / "final.mp4").write_bytes(b"final-video")

            with (
                patch.object(
                    server.provider_client,
                    "check_video_task",
                    side_effect=query_step,
                ),
                patch.object(
                    server,
                    "persist_generated_media",
                    return_value="/uploads/final.mp4",
                ),
                patch.object(
                    server,
                    "validate_generated_video",
                    return_value={"status": "valid", "duration": 12},
                ),
                patch.object(server.time, "sleep"),
            ):
                server._resume_video_task(
                    project_id,
                    "node-video",
                    "provider-same-task",
                    {"id": "new-apl"},
                    {"id": "004-sd2.0"},
                )

            saved = canvas_store.get_generation_task(task["id"])
            refreshed = canvas_store.get_project(project_id)
            # Simulate a restart after the file was persisted but before the
            # node projection was acknowledged. Reconstruct the exact durable
            # fault state instead of rewinding the already-successful projection.
            raw = sqlite3.connect(str(self.context.db_path))
            raw.execute(
                "UPDATE generation_tasks SET "
                "status='interrupted',phase='结果待确认',delivery_status='pending',"
                "local_result_url='/uploads/final.mp4' "
                "WHERE id=?",
                (task["id"],),
            )
            payload = json.loads(
                raw.execute(
                    "SELECT payload_json FROM canvas_projects WHERE id=?",
                    (project_id,),
                ).fetchone()[0]
            )
            for node in payload["nodes"]:
                if node["id"] == "node-video":
                    node["metadata"].update({
                        "status": "error",
                        "content": "",
                        "lastError": "结果待确认",
                        "generationFinishedAt": "",
                    })
                    node["metadata"].setdefault("params", {}).pop("mediaVersions", None)
                    node["metadata"].setdefault("params", {}).pop("mediaVersionIndex", None)
            raw.execute(
                "UPDATE canvas_projects SET payload_json=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), project_id),
            )
            raw.commit()
            raw.close()
            with (
                patch.object(server.provider_client, "generate_video") as submit,
                patch.object(server.provider_client, "check_video_task") as query,
            ):
                recovered = server.reconcile_pending_generation_deliveries()
            recovered_task = canvas_store.get_generation_task(task["id"])
            recovered_project = canvas_store.get_project(project_id)

        submit.assert_not_called()
        query.assert_not_called()
        self.assertEqual(query_calls, ["query", "query"])
        self.assertEqual(saved["status"], "succeeded")
        self.assertEqual(
            refreshed["payload"]["nodes"][0]["metadata"]["content"],
            "/uploads/final.mp4",
        )
        self.assertEqual(recovered, 1)
        self.assertEqual(recovered_task["delivery_status"], "ready")
        self.assertEqual(
            recovered_project["payload"]["nodes"][0]["metadata"]["content"],
            "/uploads/final.mp4",
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
