from __future__ import annotations

import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

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
            self.old_model_active = dict(server.GENERATION_QUEUE_ACTIVE_MODELS)
            self.old_provider_cooldown = dict(server.GENERATION_QUEUE_PROVIDER_COOLDOWN_UNTIL)
            server.GENERATION_QUEUE_ACTIVE_GLOBAL = 0
            server.GENERATION_QUEUE_ACTIVE_PROVIDERS.clear()
            server.GENERATION_QUEUE_ACTIVE_TENANTS.clear()
            server.GENERATION_QUEUE_ACTIVE_MODELS.clear()
            server.GENERATION_QUEUE_PROVIDER_COOLDOWN_UNTIL.clear()

    def tearDown(self):
        with server.GENERATION_QUEUE_CONDITION:
            server.GENERATION_QUEUE_ACTIVE_GLOBAL = self.old_global_active
            server.GENERATION_QUEUE_ACTIVE_PROVIDERS.clear()
            server.GENERATION_QUEUE_ACTIVE_PROVIDERS.update(self.old_provider_active)
            server.GENERATION_QUEUE_ACTIVE_TENANTS.clear()
            server.GENERATION_QUEUE_ACTIVE_TENANTS.update(self.old_tenant_active)
            server.GENERATION_QUEUE_ACTIVE_MODELS.clear()
            server.GENERATION_QUEUE_ACTIVE_MODELS.update(self.old_model_active)
            server.GENERATION_QUEUE_PROVIDER_COOLDOWN_UNTIL.clear()
            server.GENERATION_QUEUE_PROVIDER_COOLDOWN_UNTIL.update(self.old_provider_cooldown)
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

    def test_failed_node_accepts_new_model_operation_but_replays_same_operation(self):
        first = canvas_store.create_generation_task(
            "project-model-switch",
            "node-model-switch",
            kind="image",
            client_operation_id="old-model-operation",
            request_snapshot={"model": "old-model"},
        )
        canvas_store.finish_generation_task(
            first["id"],
            status="failed",
            phase="生成失败",
            error="old model failed",
            delivery_status="failed",
        )
        replay = canvas_store.create_generation_task(
            "project-model-switch",
            "node-model-switch",
            kind="image",
            client_operation_id="old-model-operation",
            request_snapshot={"model": "old-model"},
        )
        replacement = canvas_store.create_generation_task(
            "project-model-switch",
            "node-model-switch",
            kind="image",
            client_operation_id="new-model-operation",
            request_snapshot={"model": "new-model"},
        )

        self.assertFalse(replay["created_new"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["id"], first["id"])
        self.assertTrue(replacement["created_new"])
        self.assertNotEqual(replacement["id"], first["id"])

    def test_succeeded_node_accepts_new_model_with_new_operation(self):
        first = canvas_store.create_generation_task(
            "project-model-switch-success",
            "node-model-switch-success",
            kind="video",
            provider="agnes-ai",
            model="agnes-video-2.5-flash",
            client_operation_id="agnes-video-operation",
            request_snapshot={
                "provider": "agnes-ai",
                "model": "agnes-video-2.5-flash",
            },
        )
        canvas_store.finish_generation_task(
            first["id"],
            status="succeeded",
            phase="已完成",
            delivery_status="ready",
        )
        replacement = canvas_store.create_generation_task(
            "project-model-switch-success",
            "node-model-switch-success",
            kind="video",
            provider="ark",
            model="doubao-seedance-2-5",
            client_operation_id="ark-video-operation",
            request_snapshot={
                "provider": "ark",
                "model": "doubao-seedance-2-5",
            },
        )

        self.assertTrue(replacement["created_new"])
        self.assertNotEqual(replacement["id"], first["id"])
        self.assertEqual(replacement["provider"], "ark")
        self.assertEqual(replacement["model"], "doubao-seedance-2-5")
        self.assertIn("doubao-seedance-2-5", replacement["request_json"])

    def test_unresolved_old_model_blocks_new_model_submission(self):
        first = canvas_store.create_generation_task(
            "project-model-switch-pending",
            "node-model-switch-pending",
            kind="video",
            provider="agnes-ai",
            model="agnes-video-2.5-flash",
            client_operation_id="agnes-pending-operation",
        )
        canvas_store.update_generation_task(
            first["id"],
            status="running",
            phase="结果待确认",
            provider_task_id="provider-pending-task",
        )
        blocked = canvas_store.create_generation_task(
            "project-model-switch-pending",
            "node-model-switch-pending",
            kind="video",
            provider="ark",
            model="doubao-seedance-2-5",
            client_operation_id="ark-blocked-operation",
        )

        self.assertFalse(blocked["created_new"])
        self.assertEqual(blocked["id"], first["id"])
        self.assertEqual(blocked["provider"], "agnes-ai")
        self.assertEqual(blocked["provider_task_id"], "provider-pending-task")

    def test_provider_phase_hides_non_progress_values(self):
        self.assertEqual(server._provider_task_phase("running", 0), "生成中")
        self.assertEqual(server._provider_task_phase("running", 10), "生成中 10%")
        self.assertEqual(server._provider_task_phase("running", 58), "生成中 58%")
        self.assertEqual(server._provider_task_phase("running", 100), "生成中")
        self.assertEqual(server._provider_task_phase("running", None), "生成中")

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

    def test_generation_input_uses_the_explicit_ui_snapshot(self):
        node = {
            "id": "node-1",
            "type": "image",
            "metadata": {
                "prompt": "旧提示词",
                "params": {"provider": "ark", "model": "old-model", "ratio": "1:1"},
            },
        }
        meta = server.apply_generation_input(node, {
            "prompt": "角色定妆，正面半身像",
            "params": {"provider": "agnes-ai", "model": "agnes-image-2.5-flash", "ratio": "16:9"},
        })
        self.assertEqual(meta["prompt"], "角色定妆，正面半身像")
        self.assertEqual(meta["params"], {
            "provider": "agnes-ai",
            "model": "agnes-image-2.5-flash",
            "ratio": "16:9",
        })

    def test_generation_input_cannot_overwrite_generation_owned_versions(self):
        node = {
            "id": "node-1",
            "type": "image",
            "metadata": {
                "prompt": "提示词",
                "params": {
                    "model": "current-model",
                    "mediaVersions": [{"url": "/uploads/current.png"}],
                    "mediaVersionIndex": 0,
                },
            },
        }
        meta = server.apply_generation_input(node, {
            "params": {
                "model": "new-model",
                "mediaVersions": [{"url": "/uploads/stale.png"}],
                "mediaVersionIndex": 9,
            },
        })
        self.assertEqual(meta["params"]["model"], "new-model")
        self.assertEqual(meta["params"]["mediaVersions"], [{"url": "/uploads/current.png"}])
        self.assertEqual(meta["params"]["mediaVersionIndex"], 0)

    def test_client_disconnect_never_rewrites_a_successful_generation_outcome(self):
        self.assertTrue(server.preserve_generation_outcome("task-1", BrokenPipeError(32, "Broken pipe")))
        with patch.object(server.store, "get_generation_task", return_value={"status": "succeeded"}):
            self.assertTrue(server.preserve_generation_outcome("task-1", RuntimeError("response write failed")))
        with patch.object(server.store, "get_generation_task", return_value={"status": "failed"}):
            self.assertFalse(server.preserve_generation_outcome("task-1", RuntimeError("provider failed")))

    def test_media_version_is_not_duplicated_by_provider_ready_intermediate(self):
        meta = {
            "content": "/uploads/generated.png",
            "params": {
                "mediaVersions": [
                    {"url": "/uploads/generated.png", "kind": "image"},
                ],
                "mediaVersionIndex": 0,
            },
        }
        versions = server._record_media_version(
            meta,
            "image",
            "/uploads/generated.png",
            "provider-task-1",
            "2026-10-30T00:00:00.000Z",
        )
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["url"], "/uploads/generated.png")

    def test_regeneration_keeps_previous_videos_as_history(self):
        meta = {
            "content": "/uploads/video-1.mp4",
            "taskId": "task-1",
            "generatedAt": "2026-10-30T00:00:00Z",
            "provider": "agnes-ai",
            "model": "agnes-video-2.5-flash",
            "params": {},
        }

        server._record_media_version(
            meta,
            "video",
            "/uploads/video-2.mp4",
            "task-2",
            "2026-10-30T00:01:00Z",
        )
        meta["content"] = "/uploads/video-2.mp4"
        server._record_media_version(
            meta,
            "video",
            "/uploads/video-3.mp4",
            "task-3",
            "2026-10-30T00:02:00Z",
        )

        versions = meta["params"]["videoVersions"]
        self.assertEqual(
            [item["url"] for item in versions],
            [
                "/uploads/video-1.mp4",
                "/uploads/video-2.mp4",
                "/uploads/video-3.mp4",
            ],
        )
        self.assertEqual(meta["params"]["mediaVersionIndex"], 2)
        self.assertEqual(meta["params"]["videoVersionIndex"], 2)
        self.assertEqual(meta["params"]["mediaVersions"], versions)

    def test_duplicate_provider_progress_does_not_refresh_change_time(self):
        meta = {
            "providerTaskProgress": 10,
            "providerTaskProgressChangedAt": "2026-10-30T00:00:00.000Z",
        }
        with patch.object(
            server.store,
            "_now_iso",
            return_value="2026-10-30T00:10:00.000Z",
        ):
            server._with_progress_changed_at(meta, "running", 10)
        self.assertEqual(
            meta["providerTaskProgressChangedAt"],
            "2026-10-30T00:00:00.000Z",
        )

        with patch.object(
            server.store,
            "_now_iso",
            return_value="2026-10-30T00:11:00.000Z",
        ):
            server._with_progress_changed_at(meta, "running", 20)
        self.assertEqual(
            meta["providerTaskProgressChangedAt"],
            "2026-10-30T00:11:00.000Z",
        )

    def test_provider_terminal_failure_is_never_reopened(self):
        task = canvas_store.create_generation_task(
            "project-terminal",
            "node-terminal",
            kind="video",
        )
        canvas_store.finish_generation_task(
            task["id"],
            status="failed",
            phase="查询失败",
            error=(
                "供应商明确返回任务不存在（HTTP 404）。"
                "系统已停止查询该任务。"
            ),
            provider_task_id="provider-task-dead",
            delivery_status="failed",
        )

        reopened = canvas_store.reopen_generation_task_for_provider(
            task["id"],
            "provider-task-dead",
            message="供应商任务仍在执行",
        )

        self.assertIsNone(reopened)
        self.assertEqual(
            canvas_store.get_generation_task(task["id"])["status"],
            "failed",
        )

    def test_journal_does_not_reopen_provider_terminal_failure(self):
        project_id = "project-journal-dead"
        node_id = "node-journal-dead"
        task = canvas_store.create_generation_task(
            project_id,
            node_id,
            kind="video",
        )
        canvas_store.finish_generation_task(
            task["id"],
            status="failed",
            phase="查询失败",
            error="供应商明确返回任务不存在（HTTP 404）",
            provider_task_id="provider-task-journal-dead",
            delivery_status="failed",
        )
        project = {
            "id": project_id,
            "payload": {
                "nodes": [{
                    "id": node_id,
                    "type": "video",
                    "metadata": {
                        "status": "generating",
                        "taskId": "provider-task-journal-dead",
                        "providerTaskStatus": "running",
                    },
                }],
                "episodeImports": [],
            },
        }
        entry = {
            "taskId": task["id"],
            "providerTaskId": "provider-task-journal-dead",
            "projectId": project_id,
            "nodeId": node_id,
        }
        update = unittest.mock.Mock()
        commit = unittest.mock.Mock()
        with patch.object(
            server.store,
            "list_provider_task_journal",
            return_value=[entry],
        ), patch.object(
            server.store,
            "get_generation_task",
            return_value=canvas_store.get_generation_task(task["id"]),
        ), patch.object(
            server.store,
            "get_project",
            return_value=project,
        ), patch.object(
            server.store,
            "update_generation_task",
            update,
        ), patch.object(
            server,
            "commit_node_metadata",
            commit,
        ):
            recovered = server.reconcile_provider_task_journal()

        self.assertEqual(recovered, 0)
        update.assert_not_called()
        commit.assert_not_called()
        self.assertEqual(
            canvas_store.get_generation_task(task["id"])["status"],
            "failed",
        )

    def test_task_filter_keeps_only_selected_episode_nodes(self):
        project = {
            "id": "project-episode-scope",
            "payload": {
                "nodes": [],
                "episodeImports": [
                    {"id": "EP009", "nodeIds": ["node-old"]},
                    {"id": "EP010", "nodeIds": ["node-current"]},
                ],
            },
        }
        tasks = [
            {"id": "task-old", "node_id": "node-old"},
            {"id": "task-current", "node_id": "node-current"},
        ]
        with patch.object(server.store, "get_project", return_value=project):
            filtered = server._filter_generation_tasks_for_episode(
                "project-episode-scope",
                tasks,
                "EP010",
            )

        self.assertEqual([task["id"] for task in filtered], ["task-current"])

    def test_generation_task_rows_are_queried_by_episode_id(self):
        current = canvas_store.create_generation_task(
            "project-task-episode",
            "node-current",
            kind="video",
            episode_id="EP010",
        )
        old = canvas_store.create_generation_task(
            "project-task-episode",
            "node-old",
            kind="video",
            episode_id="EP009",
        )

        tasks = canvas_store.list_generation_tasks(
            "project-task-episode",
            80,
            episode_id="EP010",
        )

        self.assertEqual([task["id"] for task in tasks], [current["id"]])
        self.assertNotIn(old["id"], [task["id"] for task in tasks])

    def test_generation_worker_does_not_need_http_request_thread(self):
        task = canvas_store.create_generation_task(
            "project-worker",
            "node-worker",
            kind="video",
            episode_id="EP010",
        )
        project = {
            "id": "project-worker",
            "payload": {
                "nodes": [{
                    "id": "node-worker",
                    "type": "video",
                    "metadata": {"params": {}},
                }],
                "episodeImports": [],
            },
        }
        finished = []
        with patch.object(
            server.store,
            "get_generation_task",
            side_effect=[
                task,
                {**task, "status": "failed", "phase": "测试终态"},
            ],
        ), patch.object(
            server.store,
            "get_project",
            return_value=project,
        ), patch.object(
            server,
            "acquire_generation_slot",
            return_value={"tenant": "legacy"},
        ), patch.object(
            server.store,
            "claim_generation_task",
            return_value=True,
        ), patch.object(
            server,
            "_start_generation_task_heartbeat",
            return_value=None,
        ), patch.object(
            server,
            "commit_node_metadata",
        ), patch.object(
            server,
            "_finish_generation_task_lease",
        ), patch.object(
            server,
            "release_generation_slot",
        ), patch.object(
            server.Handler,
            "_generate",
            side_effect=lambda *args, **kwargs: finished.append((args, kwargs)),
        ):
            server._run_generation_task_worker(
                task["id"],
                None,
                "local",
            )

        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0][1]["task_id"], task["id"])

    def test_image_download_failure_does_not_create_fake_version(self):
        node = {
            "id": "image-same-result",
            "type": "image",
            "title": "角色｜赵铁柱（图1）",
            "metadata": {
                "prompt": "test image",
                "status": "done",
                "content": "/uploads/generated.png",
                "params": {
                    "provider": "agnes-ai",
                    "model": "agnes-image",
                    "mediaVersions": [
                        {
                            "url": "/uploads/generated.png",
                            "kind": "image",
                            "provider": "agnes-ai",
                            "model": "agnes-image",
                        },
                    ],
                    "mediaVersionIndex": 0,
                },
            },
        }
        project = {"id": "project-same-result", "payload": {"nodes": [node]}}
        provider = {
            "id": "agnes-ai",
            "api_key": "test-key",
            "base_url": "https://api.agnes-ai.cn/v1",
        }
        model = {
            "id": "agnes-image",
            "remoteModel": "agnes-image-2.5-flash",
            "defaultParams": {},
            "limits": {},
        }
        sent = []
        task_updates = []
        handler = object.__new__(server.Handler)
        handler._send = lambda status, payload: sent.append((status, payload)) or payload
        canvas_store.create_generation_task(
            project["id"],
            node["id"],
            kind="video",
        )
        queued_task = canvas_store.list_generation_tasks(project["id"], 10)[0]
        lease_owner = "test-video-download"
        self.assertTrue(
            canvas_store.claim_generation_task(queued_task["id"], lease_owner, 45)
        )
        with (
            patch.object(server.provider_catalog, "resolve_model", return_value=(provider, model, {})),
            patch.object(
                server.provider_catalog,
                "test_provider",
                return_value={"ok": True, "message": "连接成功"},
            ),
            patch.object(
                server.provider_client,
                "generate_image",
                return_value={"url": "https://provider/same.png"},
            ),
            patch.object(
                server,
                "persist_generated_media",
                side_effect=RuntimeError("download failed"),
            ),
            patch.object(
                server.store,
                "finish_generation_task",
                side_effect=lambda task_id, **fields: task_updates.append(fields),
            ),
            patch.object(
                server,
                "commit_node_metadata",
                side_effect=lambda project_id, node_id, updates: node["metadata"].update(updates),
            ),
        ):
            handler._generate(
                project["id"],
                project,
                node,
                task_id="local-same-result",
            )
        status, payload = sent[-1]
        self.assertEqual(status, 202)
        self.assertEqual(payload["code"], "PROVIDER_OUTCOME_UNCERTAIN")
        self.assertIn("download failed", payload["error"])
        self.assertEqual(
            len(node["metadata"]["params"]["mediaVersions"]),
            1,
        )
        self.assertTrue(
            any(update.get("phase") == "结果待确认" for update in task_updates)
        )

    def test_uncertain_provider_outcome_is_not_reported_as_plain_failure(self):
        node = {
            "id": "image-uncertain",
            "type": "image",
            "title": "角色｜待确认（图1）",
            "metadata": {
                "prompt": "test image",
                "status": "generating",
                "params": {"provider": "agnes-ai", "model": "agnes-image"},
            },
        }
        project = {"id": "project-uncertain", "payload": {"nodes": [node]}}
        provider = {
            "id": "agnes-ai",
            "api_key": "test-key",
            "base_url": "https://api.agnes-ai.cn/v1",
        }
        model = {
            "id": "agnes-image",
            "remoteModel": "agnes-image-2.5-flash",
            "defaultParams": {},
            "limits": {},
        }
        sent = []
        updates = []
        handler = object.__new__(server.Handler)
        handler._send = lambda status, payload: sent.append((status, payload)) or payload
        with (
            patch.object(server.provider_catalog, "resolve_model", return_value=(provider, model, {})),
            patch.object(
                server.provider_catalog,
                "test_provider",
                return_value={"ok": True, "message": "连接成功"},
            ),
            patch.object(
                server.provider_client,
                "generate_image",
                side_effect=server.provider_client.ProviderOutcomeUncertain(
                    "供应商请求已发出，但读取结果超时",
                ),
            ),
            patch.object(
                server.store,
                "finish_generation_task",
                side_effect=lambda task_id, **fields: updates.append(fields),
            ),
            patch.object(server, "commit_node_metadata"),
        ):
            handler._generate(
                project["id"],
                project,
                node,
                task_id="local-uncertain",
            )
        status, payload = sent[-1]
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "pending")
        self.assertEqual(payload["code"], "PROVIDER_OUTCOME_UNCERTAIN")
        self.assertTrue(any(update.get("phase") == "结果待确认" for update in updates))

    def test_runtime_does_not_reap_active_task_without_provider_id(self):
        project_id = "project-ghost"
        node_id = "node-ghost"
        node = {
            "id": node_id,
            "type": "video",
            "title": "段1 · 告别",
            "metadata": {
                "status": "generating",
                "provider": "agnes-ai",
                "model": "agnes-video-2.5-flash",
                "providerTaskStatus": "submitting",
                "providerTaskPhase": "提交供应商",
            },
        }
        stale = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
        task = {
            "id": "task-ghost",
            "project_id": project_id,
            "node_id": node_id,
            "status": "running",
            "phase": "提交供应商",
            "kind": "video",
            "provider_task_id": "",
            "created_at": stale,
            "started_at": stale,
            "updated_at": stale,
        }
        update_task = unittest.mock.Mock()
        commit = unittest.mock.Mock()
        with patch.object(server.store, "list_projects", return_value=[{"id": project_id}]), \
                patch.object(server.store, "get_project", return_value={"id": project_id, "payload": {"nodes": [node]}}), \
                patch.object(server.store, "list_generation_tasks", return_value=[task]), \
                patch.object(server.store, "update_generation_task", update_task), \
                patch.object(server, "commit_node_metadata", commit):
            reconciled = server.reconcile_generation_task_records()
        self.assertEqual(reconciled, 0)
        update_task.assert_not_called()
        commit.assert_not_called()

    def test_sync_image_without_provider_task_id_is_not_marked_submit_timeout(self):
        """Agnes image is synchronous and has no provider task id by design."""
        project_id = "project-image-sync"
        node_id = "node-image-sync"
        node = {
            "id": node_id,
            "type": "image",
            "title": "角色｜钱万金（图2）",
            "metadata": {
                "status": "generating",
                "provider": "agnes-ai",
                "model": "agnes-image-2.5-flash",
                "providerTaskStatus": "submitting",
                "providerTaskPhase": "提交供应商",
            },
        }
        stale = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
        task = {
            "id": "task-image-sync",
            "project_id": project_id,
            "node_id": node_id,
            "kind": "image",
            "status": "running",
            "phase": "提交供应商",
            "provider_task_id": "",
            "created_at": stale,
            "started_at": stale,
            "updated_at": stale,
        }
        update_task = unittest.mock.Mock()
        commit = unittest.mock.Mock()
        with patch.object(server.store, "list_projects", return_value=[{"id": project_id}]), \
                patch.object(server.store, "get_project", return_value={"id": project_id, "payload": {"nodes": [node]}}), \
                patch.object(server.store, "list_generation_tasks", return_value=[task]), \
                patch.object(server.store, "update_generation_task", update_task), \
                patch.object(server, "commit_node_metadata", commit):
            reconciled = server.reconcile_generation_task_records()
        self.assertEqual(reconciled, 0)
        update_task.assert_not_called()
        commit.assert_not_called()

    def test_generation_preflight_blocks_invalid_provider_key(self):
        server._PROVIDER_AUTH_CACHE.clear()
        provider = {
            "id": "agnes-ai",
            "base_url": "https://api.agnes-ai.cn/v1",
            "api_key": "invalid-test-key",
        }
        with patch.object(
            server.provider_catalog,
            "test_provider",
            return_value={
                "ok": False,
                "authFailed": True,
                "message": "鉴权失败（HTTP 401）：无效的令牌",
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "鉴权失败"):
                server.ensure_provider_auth(provider)
            # The failed result is cached, so batch retries fail immediately
            # without probing the provider again.
            with self.assertRaisesRegex(RuntimeError, "鉴权失败"):
                server.ensure_provider_auth(provider)

    def test_generation_preflight_blocks_missing_provider_key(self):
        server._PROVIDER_AUTH_CACHE.clear()
        with self.assertRaisesRegex(RuntimeError, "尚未配置 API Key"):
            server.ensure_provider_auth({"id": "agnes-ai", "base_url": "https://api.agnes-ai.cn/v1"})

    def test_generation_preflight_cache_is_scoped_to_key(self):
        server._PROVIDER_AUTH_CACHE.clear()
        calls = []

        def test_provider(provider_id):
            calls.append(provider_id)
            return {"ok": True, "message": "连接成功"}

        with patch.object(server, "tenant_context") as tenant_context_mock, \
                patch.object(server.provider_catalog, "provider_key", return_value="key-old"), \
                patch.object(server.provider_catalog, "test_provider", side_effect=test_provider):
            tenant_context_mock.current_tenant_id.return_value = "tenant-1"
            server.ensure_provider_auth({"id": "agnes-ai", "base_url": "https://api.agnes-ai.cn/v1"})
        with patch.object(server, "tenant_context") as tenant_context_mock, \
                patch.object(server.provider_catalog, "provider_key", return_value="key-new"), \
                patch.object(server.provider_catalog, "test_provider", side_effect=test_provider):
            tenant_context_mock.current_tenant_id.return_value = "tenant-1"
            server.ensure_provider_auth({"id": "agnes-ai", "base_url": "https://api.agnes-ai.cn/v1"})
        self.assertEqual(calls, ["agnes-ai", "agnes-ai"])

    def test_local_validation_and_provider_failures_are_classified_separately(self):
        self.assertTrue(server.is_local_validation_error("参考素材未就绪 2 项：角色未生成"))
        self.assertTrue(server.is_local_validation_error("音色节点尚未生成"))
        self.assertFalse(server.is_local_validation_error("供应商接口 HTTP429: rate limit"))

    def test_provider_concurrency_limit_is_enforced_when_configured(self):
        provider = "test-provider"
        with patch.object(server, "GENERATION_QUEUE_PROVIDER_LIMIT", 1):
            first = server.acquire_generation_slot(None, provider, "image-model", "image")
            acquired = threading.Event()

            def acquire_second():
                slot = server.acquire_generation_slot(None, provider, "image-model", "image")
                acquired.set()
                server.release_generation_slot(slot)

            thread = threading.Thread(target=acquire_second, daemon=True)
            thread.start()
            time.sleep(0.15)
            self.assertFalse(acquired.is_set())
            server.release_generation_slot(first)
            self.assertTrue(acquired.wait(timeout=2))
            thread.join(timeout=2)

    def test_multiple_image_tasks_can_use_independent_slots(self):
        first = server.acquire_generation_slot(None, "ark", "image-model", "image")
        second = server.acquire_generation_slot(None, "ark", "image-model", "image")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertGreaterEqual(server.GENERATION_QUEUE_ACTIVE_MODELS.get(first["model"], 0), 2)
        server.release_generation_slot(first)
        server.release_generation_slot(second)

    def test_different_providers_do_not_block_each_other(self):
        first = server.acquire_generation_slot(None, "ark", "image-model", "image")
        second = server.acquire_generation_slot(None, "agnes-ai", "image-model", "image")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        server.release_generation_slot(first)
        server.release_generation_slot(second)

    def test_ten_video_tasks_can_run_together_by_default(self):
        slots = [
            server.acquire_generation_slot(None, "ark", "video-model", "video")
            for _ in range(10)
        ]
        self.assertEqual(len(slots), 10)
        self.assertEqual(
            server.GENERATION_QUEUE_ACTIVE_MODELS.get(slots[0]["model"]),
            10,
        )
        for slot in slots:
            server.release_generation_slot(slot)

    def test_model_concurrency_limit_blocks_only_after_its_own_capacity(self):
        with patch.object(server, "generation_model_concurrency", return_value=2):
            first = server.acquire_generation_slot(None, "ark", "image-model", "image")
            second = server.acquire_generation_slot(None, "ark", "image-model", "image")
            acquired = threading.Event()

            def acquire_third():
                slot = server.acquire_generation_slot(None, "ark", "image-model", "image")
                acquired.set()
                server.release_generation_slot(slot)

            thread = threading.Thread(target=acquire_third, daemon=True)
            thread.start()
            time.sleep(0.15)
            self.assertFalse(acquired.is_set())
            server.release_generation_slot(first)
            self.assertTrue(acquired.wait(timeout=2))
            server.release_generation_slot(second)
            thread.join(timeout=2)

    def test_model_json_concurrency_override_is_used(self):
        catalog = {
            "models": [{
                "id": "video-model",
                "remoteModel": "video-model-remote",
                "limits": {"maxConcurrency": 3},
            }],
        }
        with patch.object(server.provider_catalog, "public_catalog", return_value=catalog):
            self.assertEqual(server.generation_model_concurrency("video-model", "video"), 3)
            self.assertEqual(server.generation_model_concurrency("video-model-remote", "video"), 3)

    def test_provider_rate_limit_cools_only_that_provider_lane(self):
        tenant_key = server.tenant_context.current_tenant_id()
        provider_key = f"{tenant_key}\x1fark"
        with server.GENERATION_QUEUE_CONDITION:
            server.GENERATION_QUEUE_PROVIDER_COOLDOWN_UNTIL[provider_key] = time.monotonic() + 0.3
        acquired = threading.Event()

        def acquire_after_cooldown():
            slot = server.acquire_generation_slot(None, "ark", "video-model", "video")
            acquired.set()
            server.release_generation_slot(slot)

        thread = threading.Thread(target=acquire_after_cooldown, daemon=True)
        thread.start()
        time.sleep(0.1)
        self.assertFalse(acquired.is_set())
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

    def test_ghost_active_task_is_closed_before_retry(self):
        pid = canvas_store.create_project("queue-ghost")
        node = {
            "id": "node-1",
            "type": "video",
            "title": "段1",
            "position": {"x": 0, "y": 0},
            "metadata": {
                "status": "failed",
                "lastError": "后端重启，任务未取得供应商任务 ID",
                "providerTaskPhase": "服务中断",
            },
        }
        project = canvas_store.get_project(pid)
        project["payload"]["nodes"] = [node]
        canvas_store.update_project(pid, payload=project["payload"])
        task = canvas_store.create_generation_task(pid, "node-1", kind="video")
        canvas_store.update_generation_task(task["id"], status="running", phase="排队中")

        repaired = server.reconcile_active_generation_task_for_node(pid, "node-1", node)

        self.assertEqual(repaired["status"], "failed")
        retry = canvas_store.create_generation_task(pid, "node-1", kind="video")
        self.assertTrue(retry["created_new"])

    def test_active_task_adopts_provider_id_from_node_metadata(self):
        pid = canvas_store.create_project("queue-adopt-provider-id")
        node = {
            "id": "node-1",
            "type": "video",
            "title": "段1",
            "position": {"x": 0, "y": 0},
            "metadata": {
                "status": "generating",
                "taskId": "provider-task-42",
                "providerTaskStatus": "in_progress",
            },
        }
        project = canvas_store.get_project(pid)
        project["payload"]["nodes"] = [node]
        canvas_store.update_project(pid, payload=project["payload"])
        task = canvas_store.create_generation_task(pid, "node-1", kind="video")
        canvas_store.update_generation_task(task["id"], status="running", phase="提交供应商")

        repaired = server.reconcile_active_generation_task_for_node(pid, "node-1", node)

        self.assertEqual(repaired["provider_task_id"], "provider-task-42")
        self.assertEqual(repaired["status"], "running")

    def test_stale_failed_node_does_not_close_live_provider_task(self):
        pid = canvas_store.create_project("queue-stale-node-live-provider")
        node = {
            "id": "node-1",
            "type": "video",
            "title": "段7",
            "position": {"x": 0, "y": 0},
            "metadata": {
                "status": "failed",
                "lastError": "旧页面错误",
                "taskId": "provider-still-live",
                "providerTaskStatus": "in_progress",
            },
        }
        project = canvas_store.get_project(pid)
        project["payload"]["nodes"] = [node]
        canvas_store.update_project(pid, payload=project["payload"])
        task = canvas_store.create_generation_task(pid, "node-1", kind="video")
        canvas_store.update_generation_task(
            task["id"],
            status="running",
            phase="生成中",
            provider_task_id="provider-still-live",
        )

        current = server.reconcile_active_generation_task_for_node(pid, "node-1", node)

        self.assertEqual(current["status"], "running")
        self.assertEqual(current["provider_task_id"], "provider-still-live")
        self.assertEqual(
            canvas_store.get_generation_task(task["id"])["status"],
            "running",
        )

    def test_unresolved_provider_task_blocks_new_submission(self):
        pid = canvas_store.create_project("queue-unresolved-provider")
        task = canvas_store.create_generation_task(
            pid,
            "node-1",
            kind="video",
        )
        canvas_store.update_generation_task(
            task["id"],
            status="interrupted",
            phase="结果待确认",
            provider_task_id="provider-unresolved",
        )

        unresolved = server._unresolved_provider_task_for_node(pid, "node-1")

        self.assertIsNotNone(unresolved)
        self.assertEqual(unresolved["id"], task["id"])
        self.assertEqual(unresolved["provider_task_id"], "provider-unresolved")

    def test_unresolved_task_without_provider_id_does_not_block_new_submission(self):
        pid = canvas_store.create_project("queue-unresolved-no-provider")
        task = canvas_store.create_generation_task(
            pid,
            "node-1",
            kind="video",
        )
        canvas_store.update_generation_task(
            task["id"],
            status="interrupted",
            phase="本地中断",
        )

        unresolved = server._unresolved_provider_task_for_node(pid, "node-1")

        self.assertIsNone(unresolved)

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

    def test_reconcile_keeps_active_provider_task_running_when_node_is_stale_failed(self):
        pid = canvas_store.create_project("queue-active-provider-task")
        node = {
            "id": "node-1",
            "type": "video",
            "title": "段7",
            "position": {"x": 0, "y": 0},
            "metadata": {
                "status": "failed",
                "lastError": "旧的失败信息",
                "taskId": "provider-task-live",
                "providerTaskStatus": "in_progress",
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
            provider_task_id="provider-task-live",
        )

        server.reconcile_generation_task_records()

        current = canvas_store.get_generation_task(task["id"])
        self.assertEqual(current["status"], "running")
        self.assertEqual(current["provider_task_id"], "provider-task-live")

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

    def test_stale_media_save_preserves_generated_versions_without_explicit_selection(self):
        meta = {
            "status": "done",
            "content": "https://i.ibb.co/second.png",
            "params": {
                "ratio": "16:9",
                "mediaVersions": [
                    {"url": "/uploads/first.png"},
                    {"url": "https://i.ibb.co/second.png"},
                ],
                "mediaVersionIndex": 1,
            },
        }
        server.merge_node_metadata(meta, {
            "prompt": "更新后的提示词",
            "content": "/uploads/first.png",
            "params": {
                "ratio": "16:9",
                "mediaVersions": [{"url": "/uploads/first.png"}],
                "mediaVersionIndex": 0,
            },
        })

        self.assertEqual(meta["prompt"], "更新后的提示词")
        self.assertEqual(len(meta["params"]["mediaVersions"]), 2)
        self.assertEqual(meta["content"], "https://i.ibb.co/second.png")
        self.assertEqual(meta["params"]["mediaVersionIndex"], 1)

        server.merge_node_metadata(meta, {
            "params": {
                "mediaVersions": [{"url": "/uploads/stale.png"}],
                "mediaVersionIndex": 1,
            },
        })
        self.assertEqual(len(meta["params"]["mediaVersions"]), 2)
        self.assertEqual(meta["content"], "https://i.ibb.co/second.png")
        self.assertEqual(meta["params"]["mediaVersionIndex"], 1)

    def test_image_generation_returns_local_preview_before_remote_mirror(self):
        node = {
            "id": "image-1",
            "type": "image",
            "title": "角色｜测试（图1）",
            "metadata": {
                "prompt": "test image",
                "status": "generating",
                "params": {"provider": "ark", "model": "seedream"},
            },
        }
        project = {"payload": {"nodes": [node]}}
        provider = {"id": "ark", "api_key": "test-key", "base_url": "https://ark.example/api/v3"}
        model = {
            "id": "seedream",
            "remoteModel": "doubao-seedream",
            "defaultParams": {},
            "limits": {},
        }
        sent = []
        committed = []
        handler = object.__new__(server.Handler)
        handler._send = lambda status, payload: sent.append((status, payload)) or payload

        with (
            patch.object(server.provider_catalog, "resolve_model", return_value=(provider, model, {})),
            patch.object(
                server.provider_catalog,
                "test_provider",
                return_value={
                    "ok": False,
                    "probeMissing": True,
                    "message": "接口可达但无 /models 探针",
                },
            ),
            patch.object(server.provider_client, "generate_image", return_value={"url": "https://provider/result.png"}),
            patch.object(server, "persist_generated_media", return_value="/uploads/generated.png"),
            patch.object(server, "archive_generated_image", return_value=None),
            patch.object(server, "queue_generated_image_tos_archive"),
            patch.object(server, "commit_node_metadata", side_effect=lambda *args: committed.append(args)),
        ):
            handler._generate_once("project-1", project, node)

        self.assertEqual(sent[-1][1]["url"], "/uploads/generated.png")
        self.assertEqual(node["metadata"]["content"], "/uploads/generated.png")
        self.assertEqual(committed[-1][2]["content"], "/uploads/generated.png")

    def test_image_tos_archive_runs_after_local_success(self):
        node = {
            "id": "image-tos",
            "type": "image",
            "title": "角色｜甲（图1）",
            "updatedAt": "2026-10-30T00:00:00.000Z",
            "metadata": {
                "prompt": "test image",
                "status": "generating",
                "params": {"provider": "ark", "model": "seedream"},
            },
        }
        project = {"id": "project-image-tos", "payload": {"nodes": [node]}}
        provider = {
            "id": "ark",
            "name": "Ark",
            "api_key": "test-key",
            "base_url": "https://ark.example/api/v3",
        }
        model = {
            "id": "seedream",
            "remoteModel": "doubao-seedream",
            "defaultParams": {},
            "limits": {},
        }
        archived = []
        sent = []
        handler = object.__new__(server.Handler)
        handler._send = lambda status, payload: sent.append((status, payload)) or payload
        with (
            patch.object(server.provider_catalog, "resolve_model", return_value=(provider, model, {})),
            patch.object(server.provider_catalog, "test_provider", return_value={"ok": True}),
            patch.object(
                server.provider_client,
                "generate_image",
                return_value={"url": "https://provider/result.png"},
            ),
            patch.object(server, "persist_generated_media", return_value="/uploads/generated.png"),
            patch.object(
                server,
                "queue_generated_image_tos_archive",
                side_effect=lambda *args: archived.append(args),
            ),
            patch.object(server, "commit_node_metadata"),
        ):
            handler._generate_once(project["id"], project, node)

        self.assertEqual(sent[-1][0], 200)
        self.assertEqual(sent[-1][1]["status"], "done")
        self.assertEqual(archived[0][2], "/uploads/generated.png")

    def test_provider_success_with_download_failure_is_pending_not_failed(self):
        node = {
            "id": "image-cache-failure",
            "type": "image",
            "title": "角色｜缓存失败（图1）",
            "updatedAt": "2026-10-30T00:00:00.000Z",
            "metadata": {
                "prompt": "test image",
                "status": "generating",
                "params": {"provider": "agnes-ai", "model": "agnes-image"},
            },
        }
        project = {
            "id": "project-cache-failure",
            "payload": {"nodes": [node]},
        }
        provider = {
            "id": "agnes-ai",
            "api_key": "test-key",
            "base_url": "https://api.agnes-ai.cn/v1",
        }
        model = {
            "id": "agnes-image",
            "remoteModel": "agnes-image-2.5-flash",
            "defaultParams": {},
            "limits": {},
        }
        sent = []
        task_updates = []
        handler = object.__new__(server.Handler)
        handler._send = lambda status, payload: sent.append((status, payload)) or payload

        with (
            patch.object(server.provider_catalog, "resolve_model", return_value=(provider, model, {})),
            patch.object(
                server.provider_catalog,
                "test_provider",
                return_value={"ok": True, "message": "连接成功"},
            ),
            patch.object(
                server.provider_client,
                "generate_image",
                return_value={
                    "url": "https://provider/result.png",
                    "taskId": "provider-task-1",
                },
            ),
            patch.object(
                server,
                "persist_generated_media",
                side_effect=RuntimeError("cache read failed"),
            ),
            patch.object(
                server.store,
                "finish_generation_task",
                side_effect=lambda task_id, **fields: task_updates.append(fields),
            ),
            patch.object(server, "commit_node_metadata"),
        ):
            handler._generate(
                project["id"],
                project,
                node,
                task_id="local-task-1",
            )

        status, payload = sent[-1]
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "pending")
        self.assertEqual(payload["code"], "PROVIDER_OUTCOME_UNCERTAIN")
        self.assertTrue(
            any(update.get("phase") == "结果待确认" for update in task_updates)
        )

    def test_video_provider_success_is_written_after_local_download(self):
        node = {
            "id": "video-ready",
            "type": "video",
            "title": "段1",
            "metadata": {
                "prompt": "test video",
                "status": "generating",
                "params": {"provider": "agnes-ai", "model": "agnes-video"},
            },
        }
        project = {
            "id": "project-video-ready",
            "payload": {"nodes": [node]},
        }
        provider = {
            "id": "agnes-ai",
            "name": "Agnes",
            "api_key": "test-key",
            "base_url": "https://api.agnes-ai.cn/v1",
        }
        model = {
            "id": "agnes-video",
            "remoteModel": "agnes-video-2.5-flash",
            "defaultParams": {},
            "limits": {},
        }
        sent = []
        queued = []
        handler = object.__new__(server.Handler)
        handler._send = lambda status, payload: sent.append((status, payload)) or payload
        canvas_store.create_generation_task(
            project["id"],
            node["id"],
            kind="video",
        )
        queued_task = canvas_store.list_generation_tasks(project["id"], 10)[0]
        lease_owner = "test-video-download"
        self.assertTrue(
            canvas_store.claim_generation_task(queued_task["id"], lease_owner, 45)
        )
        with (
            patch.object(server.provider_catalog, "resolve_model", return_value=(provider, model, {})),
            patch.object(
                server.provider_catalog,
                "test_provider",
                return_value={"ok": True},
            ),
            patch.object(
                server.provider_client,
                "generate_video",
                return_value={
                    "url": "https://provider.example/video.mp4",
                    "taskId": "provider-video-1",
                },
            ),
            patch.object(
                server,
                "persist_generated_media",
                return_value="/uploads/video.mp4",
            ),
            patch.object(
                server,
                "queue_generated_video_finalize",
                side_effect=lambda *args: queued.append(args),
            ),
            patch.object(server, "commit_node_metadata"),
        ):
            handler._generate_once(
                project["id"],
                project,
                node,
                task_id=queued_task["id"],
                lease_owner=lease_owner,
            )
        status, payload = sent[-1]
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["url"], "/uploads/video.mp4")
        self.assertEqual(node["metadata"]["status"], "done")
        self.assertEqual(node["metadata"]["content"], "/uploads/video.mp4")
        self.assertEqual(queued[0][2], "/uploads/video.mp4")
        canvas_store.finish_generation_task(
            queued_task["id"],
            status="succeeded",
            phase="已完成",
            lease_owner=lease_owner,
        )

    def test_video_finalize_stores_tos_original_bytes(self):
        run = []
        commit = []
        storage = {
            "status": "stored",
            "provider": "tos",
            "bucket": "drama-originals",
            "key": "drama-canvas/video/result.mp4",
            "originalBytes": True,
        }
        with (
            patch.object(server, "_run_in_tenant", side_effect=lambda _tenant, _slug, fn: fn()),
            patch.object(server, "persist_generated_media", return_value="/uploads/video.mp4"),
            patch.object(server, "validate_generated_video", return_value={"duration": 5}),
            patch.object(
                server.provider_catalog,
                "storage_config",
                return_value={
                    "enabled": True,
                    "provider": "tos",
                    "auto_persist_videos": True,
                },
            ),
            patch.object(server.media_storage, "persist_local_url", return_value=storage) as persist,
            patch.object(server, "commit_node_metadata", side_effect=lambda *args: commit.append(args)),
            patch.object(
                server.threading,
                "Thread",
                side_effect=lambda **kw: type(
                    "T", (), {"start": lambda self: kw["target"](*kw.get("args", ()))}
                )(),
            ),
        ):
            server.queue_generated_video_finalize(
                "project-1",
                "node-1",
                "https://provider.example/video.mp4",
                {"id": "agnes-ai"},
                {"id": "video-model", "remoteModel": "video-remote"},
                "task-1",
                "2026-10-30T00:00:00Z",
            )

        persist.assert_called_once()
        self.assertEqual(persist.call_args.kwargs["kind"], "video")
        updates = commit[-1][2]
        self.assertEqual(updates["content"], "/uploads/video.mp4")
        self.assertEqual(updates["storage"], storage)
        self.assertFalse(updates["finalizePending"])

    def test_video_finalize_tos_failure_keeps_video_successful(self):
        commit = []
        with (
            patch.object(server, "_run_in_tenant", side_effect=lambda _tenant, _slug, fn: fn()),
            patch.object(server, "persist_generated_media", return_value="/uploads/video.mp4"),
            patch.object(server, "validate_generated_video", return_value={"duration": 5}),
            patch.object(
                server.provider_catalog,
                "storage_config",
                return_value={
                    "enabled": True,
                    "provider": "tos",
                    "auto_persist_videos": True,
                },
            ),
            patch.object(
                server.media_storage,
                "persist_local_url",
                side_effect=RuntimeError("TOS 欠费"),
            ),
            patch.object(server, "commit_node_metadata", side_effect=lambda *args: commit.append(args)),
            patch.object(
                server.threading,
                "Thread",
                side_effect=lambda **kw: type(
                    "T", (), {"start": lambda self: kw["target"](*kw.get("args", ()))}
                )(),
            ),
        ):
            server.queue_generated_video_finalize(
                "project-1",
                "node-1",
                "https://provider.example/video.mp4",
                {"id": "agnes-ai"},
                {"id": "video-model", "remoteModel": "video-remote"},
                "task-1",
                "2026-10-30T00:00:00Z",
            )

        updates = commit[-1][2]
        self.assertEqual(updates["content"], "/uploads/video.mp4")
        self.assertEqual(updates["storageError"], "TOS 欠费")
        self.assertFalse(updates["finalizePending"])

    def test_video_finalize_skips_tos_when_video_switch_is_off(self):
        with (
            patch.object(server, "_run_in_tenant", side_effect=lambda _tenant, _slug, fn: fn()),
            patch.object(server, "persist_generated_media", return_value="/uploads/video.mp4"),
            patch.object(server, "validate_generated_video", return_value={"duration": 5}),
            patch.object(
                server.provider_catalog,
                "storage_config",
                return_value={
                    "enabled": True,
                    "provider": "tos",
                    "auto_persist_videos": False,
                },
            ),
            patch.object(server.media_storage, "persist_local_url") as persist,
            patch.object(server, "commit_node_metadata"),
            patch.object(
                server.threading,
                "Thread",
                side_effect=lambda **kw: type(
                    "T", (), {"start": lambda self: kw["target"](*kw.get("args", ()))}
                )(),
            ),
        ):
            server.queue_generated_video_finalize(
                "project-1",
                "node-1",
                "https://provider.example/video.mp4",
                {"id": "agnes-ai"},
                {"id": "video-model", "remoteModel": "video-remote"},
                "task-1",
                "2026-10-30T00:00:00Z",
            )

        persist.assert_not_called()


if __name__ == "__main__":
    unittest.main()
