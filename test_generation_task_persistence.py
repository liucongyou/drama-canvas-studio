import unittest
import concurrent.futures
import threading
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
    def test_insert_node_requires_episode_and_claims_exclusive_membership(self):
        project_id = canvas_store.create_project("episode-node-ownership")
        first = {
            "id": "owned-node",
            "type": "image",
            "title": "角色｜测试",
            "position": {"x": 10, "y": 20},
            "metadata": {"status": "idle"},
        }
        canvas_store.insert_project_nodes(
            project_id,
            [first],
            episode_id="EP001",
        )
        project = canvas_store.get_project(project_id)
        node = next(
            item for item in project["payload"]["nodes"]
            if item["id"] == "owned-node"
        )
        self.assertEqual(node["metadata"]["episodeId"], "EP001")
        self.assertIn(
            "owned-node",
            next(
                group for group in project["payload"]["episodeImports"]
                if group["id"] == "EP001"
            )["nodeIds"],
        )
        canvas_store.update_project_nodes(
            project_id,
            [{
                **node,
                "metadata": {**node["metadata"], "episodeId": "EP002"},
            }],
            episode_id="EP002",
        )
        project = canvas_store.get_project(project_id)
        ep1 = next(
            group for group in project["payload"]["episodeImports"]
            if group["id"] == "EP001"
        )
        ep2 = next(
            group for group in project["payload"]["episodeImports"]
            if group["id"] == "EP002"
        )
        self.assertNotIn("owned-node", ep1["nodeIds"])
        self.assertIn("owned-node", ep2["nodeIds"])

    def test_insert_node_without_episode_is_rejected(self):
        project_id = canvas_store.create_project("episode-node-required")
        with self.assertRaisesRegex(ValueError, "所属集"):
            canvas_store.insert_project_nodes(
                project_id,
                [{
                    "id": "orphan-node",
                    "type": "image",
                    "title": "无主节点",
                    "metadata": {},
                }],
            )

    def test_repair_episode_ownership_removes_explicit_cross_episode_membership(self):
        project_id = canvas_store.create_project("episode-node-repair")
        canvas_store.insert_project_nodes(
            project_id,
            [{
                "id": "explicit-node",
                "type": "video",
                "title": "视频｜明确归属",
                "metadata": {"episodeId": "EP001", "status": "idle"},
            }],
            episode_id="EP001",
        )
        project = canvas_store.get_project(project_id)
        project["payload"]["episodeImports"].append({
            "id": "EP002",
            "nodeIds": ["explicit-node"],
            "videoNodeIds": ["explicit-node"],
        })
        canvas_store.update_project(project_id, payload=project["payload"])
        canvas_store.repair_episode_node_ownership(project_id)
        project = canvas_store.get_project(project_id)
        ep1 = next(
            group for group in project["payload"]["episodeImports"]
            if group["id"] == "EP001"
        )
        ep2 = next(
            group for group in project["payload"]["episodeImports"]
            if group["id"] == "EP002"
        )
        self.assertIn("explicit-node", ep1["nodeIds"])
        self.assertNotIn("explicit-node", ep2["nodeIds"])
        self.assertNotIn("explicit-node", ep2["videoNodeIds"])

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

    def test_episode_task_lookup_is_not_truncated_by_newer_other_episode_tasks(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("episode-task-limit")
            old = canvas_store.create_generation_task(
                project_id,
                "episode-a-node",
                kind="video",
                episode_id="EP001",
            )
            canvas_store.finish_generation_task(
                old["id"],
                status="succeeded",
                phase="已完成",
            )
            for index in range(120):
                other = canvas_store.create_generation_task(
                    project_id,
                    f"episode-b-node-{index}",
                    kind="video",
                    episode_id="EP002",
                )
                canvas_store.finish_generation_task(
                    other["id"],
                    status="succeeded",
                    phase="已完成",
                )
            tasks = canvas_store.list_generation_tasks_for_node_ids(
                project_id,
                ["episode-a-node"],
            )

        self.assertEqual([task["id"] for task in tasks], [old["id"]])

    def test_operation_replay_is_scoped_to_project_and_node(self):
        with tenant_context.use_context(self.context):
            first = canvas_store.create_generation_task(
                "project-a",
                "node-a",
                kind="image",
                client_operation_id="operation-a",
            )
            second = canvas_store.create_generation_task(
                "project-b",
                "node-b",
                kind="image",
                client_operation_id="operation-b",
            )
            replay = canvas_store.create_generation_task(
                "project-b",
                "node-b",
                kind="image",
                client_operation_id="operation-b",
            )
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(replay["id"], second["id"])
        self.assertTrue(replay["replayed"])

    def test_submitting_task_can_claim_normal_execution_lease(self):
        with tenant_context.use_context(self.context):
            task = canvas_store.create_generation_task(
                "project-submitting",
                "node-submitting",
                kind="video",
            )
            canvas_store.update_generation_task(
                task["id"],
                status="submitting",
                phase="已获得执行槽位",
            )
            claimed = canvas_store.claim_generation_task(
                task["id"],
                "worker:agnes-video",
                45,
            )
            saved = canvas_store.get_generation_task(task["id"])

        self.assertTrue(claimed)
        self.assertEqual(saved["status"], "running")
        self.assertEqual(saved["lease_owner"], "worker:agnes-video")

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

    def test_failed_row_with_live_provider_task_is_projected_running(self):
        outcome = generation_outcome.task_outcome({
            "status": "failed",
            "phase": "结果待确认",
            "provider_task_id": "task-live-provider",
            "error": (
                "执行租约已过期，但已有供应商任务号；"
                "系统会继续查询原任务，不会自动重新提交。"
            ),
        })

        self.assertEqual(outcome["phase"], "running")
        self.assertEqual(outcome["regenerate"], "blocked")
        self.assertEqual(outcome["reason"], "provider_task_running")
        self.assertTrue(outcome["providerAccepted"])

    def test_journal_reconciliation_revives_failed_task_for_same_provider_task(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("journal-revive")
            task = canvas_store.create_generation_task(
                project_id,
                "node-video",
                kind="video",
                provider="agnes-ai",
                model="agnes-video-2.5-flash",
            )
            canvas_store.update_generation_task(
                task["id"],
                status="failed",
                phase="结果待确认",
                error="old worker failure",
            )
            canvas_store.remember_provider_task(
                task["id"],
                "task-live-provider",
                project_id=project_id,
                node_id="node-video",
                provider="agnes-ai",
                model="agnes-video-2.5-flash",
            )

            recovered = server.reconcile_provider_task_journal()
            saved = canvas_store.get_generation_task(task["id"])

        self.assertGreaterEqual(recovered, 1)
        self.assertEqual(saved["status"], "interrupted")
        self.assertEqual(saved["phase"], "结果待确认")
        self.assertEqual(saved["provider_task_id"], "task-live-provider")

    def test_stale_failed_row_is_reopened_when_node_has_same_live_provider_task(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("failed-live-provider")
            project = canvas_store.get_project(project_id)
            project["payload"]["nodes"] = [{
                "id": "node-video",
                "type": "video",
                "title": "段7",
                "metadata": {
                    "status": "generating",
                    "taskId": "task-live-provider",
                    "providerTaskStatus": "in_progress",
                    "providerTaskPhase": "生成中 10%",
                },
            }]
            canvas_store.update_project(project_id, payload=project["payload"])
            task = canvas_store.create_generation_task(
                project_id,
                "node-video",
                kind="video",
            )
            canvas_store.update_generation_task(
                task["id"],
                status="failed",
                phase="结果待确认",
                provider_task_id="task-live-provider",
                error="old worker failure",
            )

            reconciled = server.reconcile_generation_task_records()
            saved = canvas_store.get_generation_task(task["id"])

        self.assertEqual(reconciled, 1)
        self.assertEqual(saved["status"], "interrupted")
        self.assertEqual(saved["phase"], "结果待确认")
        self.assertEqual(saved["provider_task_id"], "task-live-provider")

    def test_runtime_reconciler_only_projects_state_without_restarting_poller(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("runtime-resume")
            project = canvas_store.get_project(project_id)
            project["payload"]["nodes"] = [{
                "id": "node-video",
                "type": "video",
                "title": "段7",
                "metadata": {
                    "status": "failed",
                    "taskId": "task-live-provider",
                    "providerTaskStatus": "in_progress",
                    "providerTaskPhase": "生成中 10%",
                    "params": {
                        "provider": "agnes-ai",
                        "model": "agnes-video-2.5-flash",
                    },
                },
            }]
            canvas_store.update_project(project_id, payload=project["payload"])
            task = canvas_store.create_generation_task(
                project_id,
                "node-video",
                kind="video",
                provider="agnes-ai",
                model="agnes-video-2.5-flash",
                client_operation_id="runtime-resume-operation",
            )
            canvas_store.update_generation_task(
                task["id"],
                status="failed",
                phase="结果待确认",
                provider_task_id="task-live-provider",
                error="执行租约已过期，但已有供应商任务号",
            )
            provider = {
                "id": "agnes-ai",
                "name": "Agnes AI",
                "base_url": "https://api.example/v1",
                "api_key": "test-key",
            }
            model = {
                "id": "agnes-video-2.5-flash",
                "remoteModel": "agnes-video-2.5-flash",
                "adapter": "agnes_video",
            }
            with (
                patch.object(server, "_active_tenants", return_value=[None]),
                patch.object(server.threading, "Thread") as thread_cls,
            ):
                reconciled = server.reconcile_generation_runtime_once()
            saved = canvas_store.get_generation_task(task["id"])
            node = next(
                item
                for item in canvas_store.get_project(project_id)["payload"]["nodes"]
                if item["id"] == "node-video"
            )

        self.assertEqual(reconciled["resumed"], 0)
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["phase"], "结果待确认")
        self.assertEqual(node["metadata"]["status"], "failed")
        self.assertEqual(node["metadata"]["providerTaskPhase"], "生成中 10%")
        thread_cls.return_value.start.assert_not_called()

    def test_expired_provider_task_starts_one_same_task_query(self):
        expired = [{
            "id": "local-expired",
            "projectId": "project-expired",
            "nodeId": "node-expired",
            "providerTaskId": "provider-expired",
            "tenantId": None,
            "tenantSlug": "local",
            "status": "interrupted",
            "phase": "结果待确认",
        }]
        task = {
            "id": "local-expired",
            "project_id": "project-expired",
            "node_id": "node-expired",
            "provider_task_id": "provider-expired",
            "provider": "agnes-ai",
            "model": "agnes-video-2.5-flash",
            "kind": "video",
            "status": "interrupted",
            "phase": "结果待确认",
            "error": "执行租约已过期",
        }
        project = {
            "id": "project-expired",
            "payload": {
                "nodes": [{
                    "id": "node-expired",
                    "type": "video",
                    "metadata": {
                        "status": "failed",
                        "taskId": "provider-old",
                        "providerTaskStatus": "completed",
                    },
                }],
            },
        }
        provider = {
            "id": "agnes-ai",
            "name": "Agnes AI",
        }
        model = {
            "id": "agnes-video-2.5-flash",
            "adapter": "agnes_video",
        }
        started = []
        with (
            patch.object(server, "_active_tenants", return_value=[None]),
            patch.object(
                server.store,
                "expire_stale_generation_tasks",
                return_value=expired,
            ),
            patch.object(
                server.store,
                "get_generation_task",
                return_value=task,
            ),
            patch.object(
                server.store,
                "get_project",
                return_value=project,
            ),
            patch.object(
                server.provider_catalog,
                "resolve_model",
                return_value=(provider, model, {}),
            ),
            patch.object(server.threading, "Thread") as thread_cls,
            patch.object(
                server,
                "_dispatch_due_provider_tasks",
                return_value=0,
            ),
        ):
            thread_cls.return_value.start.side_effect = lambda: started.append(
                thread_cls.call_args.kwargs["target"]
            )
            result = server.reconcile_generation_runtime_once()

        self.assertEqual(result["resumed"], 1)
        self.assertEqual(len(started), 1)

    def test_periodic_reconcile_loads_only_candidate_tasks(self):
        with (
            patch.object(
                server.store,
                "list_runtime_reconcile_tasks",
                return_value=[],
            ) as candidates,
            patch.object(server.store, "list_projects") as list_projects,
            patch.object(server.store, "list_generation_tasks") as list_tasks,
        ):
            reconciled = server.reconcile_generation_task_records()

        self.assertEqual(reconciled, 0)
        candidates.assert_called_once_with(200)
        list_projects.assert_not_called()
        list_tasks.assert_not_called()

    def test_expired_task_resume_uses_own_tenant_context(self):
        tenants = [
            {"id": "tenant-a", "slug": "tenant-a"},
            {"id": "tenant-b", "slug": "tenant-b"},
        ]
        expired_by_tenant = {
            "tenant-a": [{
                "id": "task-a",
                "projectId": "project-a",
                "nodeId": "node-a",
                "providerTaskId": "provider-a",
                "tenantId": "tenant-a",
                "tenantSlug": "tenant-a",
            }],
            "tenant-b": [{
                "id": "task-b",
                "projectId": "project-b",
                "nodeId": "node-b",
                "providerTaskId": "provider-b",
                "tenantId": "tenant-b",
                "tenantSlug": "tenant-b",
            }],
        }
        tasks = {
            "task-a": {
                "id": "task-a",
                "project_id": "project-a",
                "node_id": "node-a",
                "provider_task_id": "provider-a",
                "provider": "agnes-ai",
                "model": "model-a",
                "kind": "video",
                "status": "interrupted",
                "phase": "结果待确认",
            },
            "task-b": {
                "id": "task-b",
                "project_id": "project-b",
                "node_id": "node-b",
                "provider_task_id": "provider-b",
                "provider": "agnes-ai",
                "model": "model-b",
                "kind": "video",
                "status": "interrupted",
                "phase": "结果待确认",
            },
        }
        projects = {
            "project-a": {
                "id": "project-a",
                "payload": {"nodes": [{
                    "id": "node-a",
                    "type": "video",
                    "metadata": {},
                }]},
            },
            "project-b": {
                "id": "project-b",
                "payload": {"nodes": [{
                    "id": "node-b",
                    "type": "video",
                    "metadata": {},
                }]},
            },
        }
        observed = []

        def expire():
            current = tenant_context.current()
            tenant_id = current.tenant_id if current else "legacy"
            return expired_by_tenant[tenant_id]

        def start_worker():
            # Invoke inline so the captured tenant/project values are verified
            # before the loop advances to the next tenant.
            thread_cls.call_args.kwargs["target"]()

        def fake_run_in_tenant(tenant_id, slug, func, *args, **kwargs):
            observed.append((tenant_id, slug, args[0], args[1]))

        with (
            patch.object(server, "_active_tenants", return_value=tenants),
            patch.object(
                server.store,
                "expire_stale_generation_tasks",
                side_effect=expire,
            ),
            patch.object(
                server,
                "reconcile_pending_generation_deliveries",
                return_value=0,
            ),
            patch.object(
                server,
                "reconcile_generation_task_records",
                return_value=0,
            ),
            patch.object(
                server.store,
                "get_generation_task",
                side_effect=lambda task_id: tasks.get(task_id),
            ),
            patch.object(
                server.store,
                "get_project",
                side_effect=lambda project_id: projects[project_id],
            ),
            patch.object(
                server.provider_catalog,
                "resolve_model",
                return_value=({"id": "agnes-ai"}, {"id": "video"}, {}),
            ),
            patch.object(
                server,
                "_resume_video_task",
                return_value=None,
            ),
            patch.object(
                server,
                "_run_in_tenant",
                side_effect=fake_run_in_tenant,
            ),
            patch.object(
                server.threading,
                "Thread",
            ) as thread_cls,
            patch.object(
                server,
                "_dispatch_due_provider_tasks",
                return_value=0,
            ),
        ):
            thread_cls.return_value.start.side_effect = start_worker
            server.reconcile_generation_runtime_once()

        self.assertEqual(
            observed,
            [
                ("tenant-a", "tenant-a", "project-a", "node-a"),
                ("tenant-b", "tenant-b", "project-b", "node-b"),
            ],
        )

    def test_recovered_video_reuses_local_version_for_same_provider_task(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("recovered-version")
            project = canvas_store.get_project(project_id)
            project["payload"]["nodes"] = [{
                "id": "node-video",
                "type": "video",
                "title": "段4 · 庆功",
                "metadata": {
                    "status": "done",
                    "content": "/uploads/existing.mp4",
                    "taskId": "provider-existing",
                    "params": {
                        "duration": 12,
                        "mediaVersions": [{
                            "url": "/uploads/existing.mp4",
                            "taskId": "provider-existing",
                            "generatedAt": "2026-10-30T15:00:00Z",
                            "kind": "video",
                        }],
                        "mediaVersionIndex": 0,
                        "videoVersions": [{
                            "url": "/uploads/existing.mp4",
                            "taskId": "provider-existing",
                            "generatedAt": "2026-10-30T15:00:00Z",
                            "kind": "video",
                        }],
                        "videoVersionIndex": 0,
                    },
                },
            }]
            canvas_store.update_project(project_id, payload=project["payload"])
            task = canvas_store.create_generation_task(
                project_id,
                "node-video",
                kind="video",
                provider="agnes-ai",
                model="agnes-video-2.5-flash",
                client_operation_id="recovered-version-operation",
            )
            canvas_store.update_generation_task(
                task["id"],
                provider_task_id="provider-existing",
                status="interrupted",
                phase="结果待确认",
            )

            with (
                patch.object(server, "persist_generated_media") as persist,
                patch.object(
                    server,
                    "validate_generated_video",
                    return_value={"status": "valid", "duration": 12},
                ),
            ):
                finalized = server._finalize_resumed_video(
                    project_id,
                    "node-video",
                    {
                        "taskId": "provider-existing",
                        "status": "completed",
                        "url": "https://provider/existing.mp4",
                    },
                    {"id": "agnes-ai", "name": "Agnes AI"},
                    {
                        "id": "agnes-video-2.5-flash",
                        "remoteModel": "agnes-video-2.5-flash",
                        "adapter": "agnes_video",
                    },
                )

            saved_project = canvas_store.get_project(project_id)
            saved_task = canvas_store.get_generation_task(task["id"])
            node = saved_project["payload"]["nodes"][0]
            versions = node["metadata"]["params"]["videoVersions"]

        self.assertTrue(finalized)
        persist.assert_not_called()
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["taskId"], "provider-existing")
        self.assertEqual(node["metadata"]["content"], "/uploads/existing.mp4")
        self.assertEqual(saved_task["status"], "succeeded")
        self.assertEqual(saved_task["local_result_url"], "/uploads/existing.mp4")

    def test_resumed_newapi_video_downloads_protected_artifact_with_key(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("resumed-newapi-auth")
            canvas_store.insert_project_nodes(
                project_id,
                [{
                    "id": "node-video",
                    "type": "video",
                    "title": "段1",
                    "metadata": {
                        "status": "generating",
                        "taskId": "provider-newapi-auth",
                        "params": {
                            "provider": "new-apl",
                            "model": "50-seedance-2.0-fast",
                        },
                    },
                }],
                episode_id="EP001",
            )
            task = canvas_store.create_generation_task(
                project_id,
                "node-video",
                kind="video",
                provider="new-apl",
                model="50-seedance-2.0-fast",
            )
            canvas_store.update_generation_task(
                task["id"],
                status="running",
                phase="正在落盘并回写画布",
                provider_task_id="provider-newapi-auth",
            )

            with (
                patch.object(
                    server,
                    "persist_generated_media",
                    side_effect=server.persist_generated_media,
                ) as persist,
                patch.object(
                    server.provider_client,
                    "save_generated_url",
                    return_value="/uploads/generated-auth.mp4",
                ) as download,
                patch.object(
                    server,
                    "validate_generated_video",
                    return_value={"status": "valid", "duration": 15},
                ),
                patch.object(server, "ensure_video_thumbnail", return_value=""),
            ):
                finalized = server._finalize_resumed_video(
                    project_id,
                    "node-video",
                    {
                        "taskId": "provider-newapi-auth",
                        "status": "completed",
                        "url": (
                            "https://api.canvasproai.com/v1/tasks/"
                            "provider-newapi-auth/artifacts/video-0/content"
                        ),
                    },
                    {
                        "id": "new-apl",
                        "name": "New APl",
                        "api_key": "test-newapi-key",
                    },
                    {
                        "id": "50-seedance-2.0-fast",
                        "remoteModel": "即梦-50-seedance-2.0-fast",
                        "adapter": "canvaspro_video",
                    },
                )
            refreshed = canvas_store.get_project(project_id)
            saved = canvas_store.get_generation_task(task["id"])

        self.assertTrue(finalized)
        self.assertEqual(download.call_args.kwargs["key"], "test-newapi-key")
        self.assertEqual(
            refreshed["payload"]["nodes"][0]["metadata"]["content"],
            "/uploads/generated-auth.mp4",
        )
        self.assertEqual(saved["status"], "succeeded")

    def test_resumer_setup_failure_releases_key_for_next_reconcile(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("runtime-resume-retry")
            project = canvas_store.get_project(project_id)
            project["payload"]["nodes"] = [{
                "id": "node-video",
                "type": "video",
                "title": "段7",
                "metadata": {
                    "status": "failed",
                    "taskId": "task-live-provider",
                    "providerTaskStatus": "in_progress",
                    "providerTaskPhase": "生成中 10%",
                    "params": {
                        "provider": "agnes-ai",
                        "model": "agnes-video-2.5-flash",
                    },
                },
            }]
            canvas_store.update_project(project_id, payload=project["payload"])
            task = canvas_store.create_generation_task(
                project_id,
                "node-video",
                kind="video",
                provider="agnes-ai",
                model="agnes-video-2.5-flash",
                client_operation_id="runtime-resume-retry-operation",
            )
            canvas_store.update_generation_task(
                task["id"],
                status="failed",
                phase="结果待确认",
                provider_task_id="task-live-provider",
                error="执行租约已过期，但已有供应商任务号",
            )
            provider = {
                "id": "agnes-ai",
                "name": "Agnes AI",
                "base_url": "https://api.example/v1",
                "api_key": "test-key",
            }
            model = {
                "id": "agnes-video-2.5-flash",
                "remoteModel": "agnes-video-2.5-flash",
                "adapter": "agnes_video",
            }

            class FailingThread:
                def __init__(self, **_thread_kwargs):
                    pass

                def start(self):
                    raise RuntimeError("thread start failed")

            with (
                patch.object(server, "_active_tenants", return_value=[None]),
                patch.object(
                    server.provider_catalog,
                    "resolve_model",
                    return_value=(provider, model, {}),
                ),
                patch.object(server.threading, "Thread", FailingThread),
            ):
                first = server.start_pending_video_resumers()
                self.assertFalse(server.RESUMED_VIDEO_TASKS)

            class NoopThread:
                def __init__(self, **_thread_kwargs):
                    pass

                def start(self):
                    return None

            with (
                patch.object(server, "_active_tenants", return_value=[None]),
                patch.object(
                    server.provider_catalog,
                    "resolve_model",
                    return_value=(provider, model, {}),
                ),
                patch.object(server.threading, "Thread", NoopThread),
            ):
                second = server.start_pending_video_resumers()

        self.assertEqual(first, 0)
        self.assertEqual(second, 1)
        self.assertTrue(server.RESUMED_VIDEO_TASKS)
        with server.RESUMED_VIDEO_TASKS_LOCK:
            server.RESUMED_VIDEO_TASKS.clear()

    def test_historical_delivery_backfill_uses_only_durable_evidence(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("delivery-backfill")
            completed = canvas_store.create_generation_task(
                project_id,
                "node-completed",
                kind="image",
            )
            canvas_store.update_generation_task(
                completed["id"],
                status="succeeded",
                local_result_url="/uploads/completed.png",
            )
            failed = canvas_store.create_generation_task(
                project_id,
                "node-failed",
                kind="image",
            )
            canvas_store.update_generation_task(
                failed["id"],
                status="failed",
                error="provider failed",
            )
            unknown = canvas_store.create_generation_task(
                project_id,
                "node-unknown",
                kind="image",
            )
            canvas_store.update_generation_task(
                unknown["id"],
                status="succeeded",
            )

            changed = canvas_store.backfill_generation_delivery_status()
            completed_saved = canvas_store.get_generation_task(completed["id"])
            failed_saved = canvas_store.get_generation_task(failed["id"])
            unknown_saved = canvas_store.get_generation_task(unknown["id"])

        self.assertGreaterEqual(changed, 2)
        self.assertEqual(completed_saved["delivery_status"], "ready")
        self.assertEqual(failed_saved["delivery_status"], "failed")
        self.assertEqual(unknown_saved["delivery_status"], "pending")

    def test_late_uncertain_callback_cannot_overwrite_succeeded_node(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("late-uncertain")
            task = canvas_store.create_generation_task(
                project_id,
                "node-image",
                kind="image",
            )
            canvas_store.update_generation_task(
                task["id"],
                status="succeeded",
                phase="已完成",
                provider_task_id="provider-done",
            )
            with patch.object(server, "commit_node_metadata") as commit_meta:
                changed = server._mark_generation_outcome_uncertain(
                    task["id"],
                    project_id,
                    "node-image",
                    RuntimeError("late callback"),
                    "provider-done",
                )
            saved = canvas_store.get_generation_task(task["id"])

        self.assertFalse(changed)
        commit_meta.assert_not_called()
        self.assertEqual(saved["status"], "succeeded")

    def test_persisted_result_recovery_waits_for_live_lease(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("live-recovery-guard")
            project = canvas_store.get_project(project_id)
            project["payload"]["nodes"] = [{
                "id": "node-image",
                "type": "image",
                "title": "角色",
                "metadata": {"status": "generating", "params": {}},
            }]
            canvas_store.update_project(project_id, payload=project["payload"])
            task = canvas_store.create_generation_task(
                project_id,
                "node-image",
                kind="image",
            )
            owner = "worker:live-recovery"
            self.assertTrue(canvas_store.claim_generation_task(task["id"], owner, 45))
            canvas_store.update_generation_task(
                task["id"],
                local_result_url="/uploads/live.png",
            )
            upload_dir = Path(self.context.upload_dir)
            upload_dir.mkdir(parents=True, exist_ok=True)
            (upload_dir / "live.png").write_bytes(b"png")
            with patch.object(server, "commit_node_metadata") as commit_meta:
                recovered = server._recover_persisted_result_to_canvas(
                    project_id,
                    "node-image",
                    canvas_store.get_generation_task(task["id"]),
                )

        self.assertFalse(recovered)
        commit_meta.assert_not_called()

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

    def test_fenced_terminal_write_rejects_lost_lease(self):
        with tenant_context.use_context(self.context):
            task = canvas_store.create_generation_task(
                "project-fenced",
                "node-fenced",
                kind="video",
            )
            owner = "worker:one"
            self.assertTrue(canvas_store.claim_generation_task(task["id"], owner, 45))
            canvas_store.release_generation_task_lease(task["id"], owner)
            result = canvas_store.finish_generation_task(
                task["id"],
                status="succeeded",
                phase="已完成",
                lease_owner=owner,
                delivery_status="ready",
            )
            saved = canvas_store.get_generation_task(task["id"])

        self.assertFalse(result)
        self.assertEqual(saved["status"], "running")

    def test_fenced_success_clears_lease_owner(self):
        with tenant_context.use_context(self.context):
            task = canvas_store.create_generation_task(
                "project-fenced-success",
                "node-fenced-success",
                kind="video",
            )
            owner = "worker:success"
            self.assertTrue(canvas_store.claim_generation_task(task["id"], owner, 45))
            saved = canvas_store.finish_generation_task(
                task["id"],
                status="succeeded",
                phase="已完成",
                lease_owner=owner,
                delivery_status="ready",
            )

        self.assertIsNotNone(saved)
        self.assertEqual(saved["status"], "succeeded")
        self.assertEqual(saved["lease_owner"], "")
        self.assertIsNone(saved["lease_expires_at"])

    def test_resumer_claims_lease_and_heartbeats_same_task(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("resumer-lease")
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
                provider_task_id="provider-resume-1",
            )
            heartbeat_task_ids = []
            original_start = server._start_generation_task_heartbeat

            def start_heartbeat(task_id, owner):
                heartbeat_task_ids.append((task_id, owner))
                return original_start(task_id, owner)

            with (
                patch.object(
                    server.provider_client,
                    "check_video_task",
                    side_effect=provider_client.ProviderHTTPError(503, "temporary"),
                ),
                patch.object(
                    server,
                    "_start_generation_task_heartbeat",
                    side_effect=start_heartbeat,
                ),
                patch.object(server.time, "sleep", side_effect=RuntimeError("stop-loop")),
                patch.object(server, "commit_node_metadata"),
                patch.object(server, "publish_project_event"),
            ):
                with self.assertRaises(RuntimeError):
                    server._resume_video_task(
                        project_id,
                        "node-video",
                        "provider-resume-1",
                        {"id": "new-apl"},
                        {"id": "004-sd2.0"},
                    )
            saved = canvas_store.get_generation_task(task["id"])

        self.assertTrue(heartbeat_task_ids)
        self.assertEqual(heartbeat_task_ids[0][0], task["id"])
        self.assertTrue(heartbeat_task_ids[0][1])
        self.assertEqual(saved["lease_owner"], "")
        self.assertEqual(saved["provider_task_id"], "provider-resume-1")

    def test_recovery_can_take_over_running_task_with_expired_lease(self):
        """A supplier-completed task must not be stranded by a lost local lease.

        Regression: the first worker set status=running with a lease, then the
        process stopped heart-beating. The recovery poller refused to claim the
        still-'running' row, so the paid provider result was never downloaded or
        written back to the canvas.
        """
        with tenant_context.use_context(self.context):
            task = canvas_store.create_generation_task(
                "project-takeover",
                "node-takeover",
                kind="video",
                provider="new-apl",
                model="50-seedance-2.0-fast",
            )
            canvas_store.update_generation_task(
                task["id"],
                provider_task_id="provider-takeover-1",
                status="running",
                phase="结果待确认",
                lease_owner="resume:stale",
                lease_expires_at="2000-01-01T00:00:00.000Z",
            )
            self.assertTrue(
                canvas_store.claim_generation_task_for_recovery(
                    task["id"],
                    "provider-takeover-1",
                    "resume:fresh",
                    45,
                )
            )
            claimed = canvas_store.get_generation_task(task["id"])

        self.assertEqual(claimed["status"], "running")
        self.assertEqual(claimed["lease_owner"], "resume:fresh")
        self.assertEqual(claimed["provider_task_id"], "provider-takeover-1")

    def test_recovery_cannot_steal_a_live_lease(self):
        with tenant_context.use_context(self.context):
            task = canvas_store.create_generation_task(
                "project-live-lease",
                "node-live-lease",
                kind="video",
            )
            canvas_store.update_generation_task(
                task["id"],
                provider_task_id="provider-live-1",
                status="running",
                phase="生成中",
                lease_owner="worker:live",
                lease_expires_at="2999-01-01T00:00:00.000Z",
            )
            claimed = canvas_store.claim_generation_task_for_recovery(
                task["id"],
                "provider-live-1",
                "resume:other",
                45,
            )
            saved = canvas_store.get_generation_task(task["id"])

        self.assertFalse(claimed)
        self.assertEqual(saved["lease_owner"], "worker:live")

    def test_heartbeat_can_restore_a_just_expired_owned_lease(self):
        with tenant_context.use_context(self.context):
            task = canvas_store.create_generation_task(
                "project-heartbeat-recovery",
                "node-heartbeat-recovery",
                kind="video",
            )
            canvas_store.update_generation_task(
                task["id"],
                status="running",
                phase="提交供应商",
                lease_owner="generation:heartbeat-owner",
                lease_expires_at="2000-01-01T00:00:00.000Z",
            )
            renewed = canvas_store.renew_generation_task_lease(
                task["id"],
                "generation:heartbeat-owner",
                45,
            )
            saved = canvas_store.get_generation_task(task["id"])

        self.assertTrue(renewed)
        self.assertGreater(saved["lease_expires_at"], "2026-01-01T00:00:00")
        self.assertEqual(saved["lease_owner"], "generation:heartbeat-owner")

    def test_worker_can_claim_ownerless_premarked_running_task(self):
        with tenant_context.use_context(self.context):
            task = canvas_store.create_generation_task(
                "project-preclaimed",
                "node-preclaimed",
                kind="video",
            )
            canvas_store.update_generation_task(
                task["id"],
                status="running",
                phase="提交供应商",
                lease_owner="",
                lease_expires_at=None,
            )
            claimed = canvas_store.claim_generation_task(
                task["id"],
                "generation:submitter",
                45,
            )
            saved = canvas_store.get_generation_task(task["id"])

        self.assertTrue(claimed)
        self.assertEqual(saved["status"], "running")
        self.assertEqual(saved["lease_owner"], "generation:submitter")
        self.assertGreater(saved["lease_expires_at"], "")

    def test_expiry_reads_journal_before_declaring_missing_provider_task(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("journal-before-expiry")
            canvas_store.insert_project_nodes(
                project_id,
                [{
                    "id": "node-video",
                    "type": "video",
                    "title": "段6",
                    "metadata": {"status": "generating"},
                }],
                episode_id="EP001",
            )
            task = canvas_store.create_generation_task(
                project_id,
                "node-video",
                kind="video",
            )
            canvas_store.update_generation_task(
                task["id"],
                status="running",
                phase="提交供应商",
                lease_owner="generation:delayed-callback",
                lease_expires_at="2000-01-01T00:00:00.000Z",
            )
            canvas_store.remember_provider_task(
                task["id"],
                "provider-delayed-callback",
                project_id=project_id,
                node_id="node-video",
                provider="agnes-ai",
                model="agnes-video-2.5-flash",
            )
            expired = canvas_store.expire_stale_generation_tasks(
                datetime(2026, 11, 1, 11, 12, 16, tzinfo=timezone.utc)
            )
            saved = canvas_store.get_generation_task(task["id"])

        self.assertEqual(len(expired), 1)
        self.assertEqual(saved["status"], "interrupted")
        self.assertEqual(saved["phase"], "结果待确认")
        self.assertEqual(
            saved["provider_task_id"],
            "provider-delayed-callback",
        )
        self.assertIn("已有供应商任务号", saved["error"])

    def test_fresh_queued_worker_gets_launch_grace(self):
        with tenant_context.use_context(self.context):
            task = canvas_store.create_generation_task(
                "project-active-submit",
                "node-active-submit",
                kind="video",
            )
            expired = canvas_store.expire_stale_generation_tasks(
                datetime.now(timezone.utc),
            )
            saved = canvas_store.get_generation_task(task["id"])

        self.assertEqual(expired, [])
        self.assertEqual(saved["status"], "queued")

    def test_rejected_duplicate_does_not_override_completed_node(self):
        node = {
            "id": "node-done",
            "type": "video",
            "title": "段4",
            "metadata": {
                "status": "done",
                "content": "/uploads/done.mp4",
                "lastError": "",
                "taskId": "provider-done",
                "providerTaskStatus": "completed",
                "providerTaskPhase": "已完成",
            },
        }
        rejected_duplicate = {
            "id": "local-rejected",
            "status": "interrupted",
            "phase": "重复提交已忽略",
            "provider_task_id": "",
            "error": "该节点已有任务正在生成，本次重复提交已忽略",
            "created_at": "2026-11-01T11:03:05.676Z",
        }
        projection = server.project_generation_node_state(
            node,
            rejected_duplicate,
        )

        self.assertEqual(projection["metadata"]["status"], "done")
        self.assertEqual(projection["metadata"]["content"], "/uploads/done.mp4")
        self.assertEqual(projection["metadata"]["lastError"], "")

    def test_successful_task_can_resolve_a_previous_missing_provider_failure(self):
        node = {
            "id": "node-seg6",
            "type": "video",
            "title": "段6",
            "metadata": {
                "status": "failed",
                "content": "/uploads/seg6.mp4",
                "lastError": "执行线程已失联且没有供应商任务号",
                "taskId": "provider-seg6-ok",
                "providerTaskStatus": "failed",
                "providerTaskPhase": "执行失联",
            },
        }
        newest_success = {
            "id": "local-seg6-ok",
            "status": "succeeded",
            "phase": "已完成",
            "provider_task_id": "provider-seg6-ok",
            "local_result_url": "/uploads/seg6.mp4",
            "finished_at": "2026-11-01T11:12:30.000Z",
        }
        projection = server.project_generation_node_state(
            node,
            newest_success,
        )

        self.assertEqual(projection["metadata"]["status"], "done")
        self.assertEqual(projection["metadata"]["lastError"], "")
        self.assertEqual(
            projection["metadata"]["providerTaskStatus"],
            "completed",
        )

    def test_stale_worker_cannot_write_progress_after_lease_loss(self):
        with tenant_context.use_context(self.context):
            task = canvas_store.create_generation_task(
                "project-stale-progress",
                "node-stale-progress",
                kind="video",
            )
            first_owner = "worker:first"
            self.assertTrue(canvas_store.claim_generation_task(task["id"], first_owner, 45))
            canvas_store.release_generation_task_lease(task["id"], first_owner)
            canvas_store.update_generation_task(
                task["id"],
                status="failed",
                phase="执行失联",
                error="lease lost",
                finished_at=canvas_store._now_iso(),
            )
            stale_update = canvas_store.update_generation_task(
                task["id"],
                _lease_owner=first_owner,
                status="running",
                phase="stale progress",
                progress=80,
            )
            saved = canvas_store.get_generation_task(task["id"])

        self.assertEqual(stale_update["status"], "failed")
        self.assertEqual(stale_update["phase"], "执行失联")
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["progress"], 0)

    def test_concurrent_same_node_requests_create_one_active_task(self):
        with tenant_context.use_context(self.context):
            results = []

            def create(index):
                with tenant_context.use_context(self.context):
                    return canvas_store.create_generation_task(
                        "project-concurrent-active",
                        "node-concurrent-active",
                        kind="video",
                        client_operation_id=f"same-node-{index}",
                    )

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(create, range(24)))

            tasks = [
                item
                for item in canvas_store.list_generation_tasks(
                    "project-concurrent-active",
                    100,
                )
                if item["status"] in {"queued", "submitting", "running"}
            ]

        self.assertEqual(len(tasks), 1)
        self.assertEqual(
            len({item["id"] for item in results if item.get("created_new")}),
            1,
        )
        self.assertTrue(any(not item.get("created_new") for item in results))

    def test_concurrent_same_operation_creates_one_task(self):
        with tenant_context.use_context(self.context):
            def create(_index):
                with tenant_context.use_context(self.context):
                    return canvas_store.create_generation_task(
                        "project-concurrent-operation",
                        "node-concurrent-operation",
                        kind="image",
                        client_operation_id="shared-operation",
                    )

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(create, range(24)))

            tasks = canvas_store.list_generation_tasks(
                "project-concurrent-operation",
                100,
            )

        self.assertEqual(len(tasks), 1)
        self.assertEqual(len({item["id"] for item in results}), 1)
        self.assertEqual(sum(1 for item in results if item.get("created_new")), 1)

    def test_mixed_episode_task_stress_preserves_isolation_and_single_claim(self):
        projects = 6
        nodes_per_episode = 20
        episodes = ("EP009", "EP010", "EP011")
        created = []

        def setup(project_index):
            with tenant_context.use_context(self.context):
                project_id = canvas_store.create_project(
                    f"mixed-episode-stress-{project_index}"
                )
                for episode_id in episodes:
                    for node_index in range(nodes_per_episode):
                        task = canvas_store.create_generation_task(
                            project_id,
                            f"{episode_id}-node-{node_index}",
                            kind="video",
                            episode_id=episode_id,
                        )
                        created.append((project_id, episode_id, task["id"]))
                return project_id

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            project_ids = list(pool.map(setup, range(projects)))

        self.assertEqual(len(created), projects * len(episodes) * nodes_per_episode)

        def query(index):
            project_id = project_ids[index % projects]
            episode_id = episodes[index % len(episodes)]
            with tenant_context.use_context(self.context):
                return [
                    task
                    for task in canvas_store.list_generation_tasks(
                        project_id,
                        300,
                        episode_id=episode_id,
                    )
                ]

        with concurrent.futures.ThreadPoolExecutor(max_workers=18) as pool:
            batches = list(pool.map(query, range(90)))

        self.assertTrue(batches)
        for batch in batches:
            self.assertTrue(batch)
            self.assertTrue(
                all(task.get("episode_id") in episodes for task in batch)
            )

        claim_targets = created[:60]

        def claim(index_and_item):
            index, (project_id, episode_id, task_id) = index_and_item
            with tenant_context.use_context(self.context):
                return canvas_store.claim_generation_task(
                    task_id,
                    f"stress-worker-{index}",
                    45,
                )

        with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
            outcomes = list(pool.map(claim, enumerate(claim_targets)))

        self.assertEqual(sum(1 for item in outcomes if item), len(claim_targets))
        with tenant_context.use_context(self.context):
            for _project_id, _episode_id, task_id in claim_targets:
                task = canvas_store.get_generation_task(task_id)
                self.assertEqual(task["status"], "running")
                self.assertTrue(task["lease_owner"].startswith("stress-worker-"))

    def test_many_workers_race_for_same_task_only_one_wins(self):
        with tenant_context.use_context(self.context):
            task = canvas_store.create_generation_task(
                "project-claim-race",
                "node-claim-race",
                kind="video",
                episode_id="EP010",
            )

        def claim(index):
            with tenant_context.use_context(self.context):
                return canvas_store.claim_generation_task(
                    task["id"],
                    f"race-worker-{index}",
                    45,
                )

        with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
            outcomes = list(pool.map(claim, range(64)))

        self.assertEqual(sum(1 for item in outcomes if item), 1)
        with tenant_context.use_context(self.context):
            saved = canvas_store.get_generation_task(task["id"])
        self.assertEqual(saved["status"], "running")
        self.assertEqual(saved["lease_owner"], "race-worker-" + str(
            next(index for index, item in enumerate(outcomes) if item)
        ))

    def test_expired_lease_stress_keeps_terminal_state_monotonic(self):
        with tenant_context.use_context(self.context):
            tasks = [
                canvas_store.create_generation_task(
                    "project-expired-lease-stress",
                    f"node-expired-{index}",
                    kind="video",
                    episode_id="EP010",
                )
                for index in range(48)
            ]
            for index, task in enumerate(tasks):
                owner = f"expired-worker-{index}"
                self.assertTrue(
                    canvas_store.claim_generation_task(task["id"], owner, 45)
                )
                canvas_store.release_generation_task_lease(task["id"], owner)
                canvas_store.update_generation_task(
                    task["id"],
                    status="failed",
                    phase="查询失败",
                    error="供应商明确返回任务不存在（HTTP 404）",
                    provider_task_id=f"provider-dead-{index}",
                    delivery_status="failed",
                )

        def stale_write(index_and_task):
            index, task = index_and_task
            with tenant_context.use_context(self.context):
                return canvas_store.update_generation_task(
                    task["id"],
                    _lease_owner=f"expired-worker-{index}",
                    status="running",
                    phase="stale progress",
                    progress=99,
                )

        with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
            results = list(pool.map(stale_write, enumerate(tasks)))

        self.assertEqual(len(results), len(tasks))
        with tenant_context.use_context(self.context):
            saved = [
                canvas_store.get_generation_task(task["id"])
                for task in tasks
            ]
        self.assertTrue(all(task["status"] == "failed" for task in saved))
        self.assertTrue(all(task["phase"] == "查询失败" for task in saved))
        self.assertTrue(
            all(task["provider_task_id"].startswith("provider-dead-") for task in saved)
        )

    def test_startup_reconciliation_makes_no_provider_calls_under_task_load(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("startup-reconcile-stress")
            project = canvas_store.get_project(project_id)
            project["payload"]["nodes"] = []
            for episode_id in ("EP009", "EP010", "EP011"):
                for index in range(30):
                    project["payload"]["nodes"].append({
                        "id": f"{episode_id}-node-{index}",
                        "type": "video",
                        "title": f"{episode_id}-段{index}",
                        "metadata": {
                            "status": "failed",
                            "episodeId": episode_id,
                            "providerTaskStatus": "failed",
                            "providerTaskPhase": "查询失败",
                            "lastError": "供应商明确返回任务不存在（HTTP 404）",
                        },
                    })
            project["payload"]["episodeImports"] = [
                {
                    "id": episode_id,
                    "nodeIds": [
                        f"{episode_id}-node-{index}"
                        for index in range(30)
                    ],
                }
                for episode_id in ("EP009", "EP010", "EP011")
            ]
            canvas_store.update_project(project_id, payload=project["payload"])
            for episode_id in ("EP009", "EP010", "EP011"):
                for index in range(30):
                    task = canvas_store.create_generation_task(
                        project_id,
                        f"{episode_id}-node-{index}",
                        kind="video",
                        episode_id=episode_id,
                    )
                    canvas_store.finish_generation_task(
                        task["id"],
                        status="failed",
                        phase="查询失败",
                        error="供应商明确返回任务不存在（HTTP 404）",
                        provider_task_id=f"provider-dead-{episode_id}-{index}",
                        delivery_status="failed",
                    )

        with (
            patch.object(server, "_active_tenants", return_value=[None]),
            patch.object(server.provider_client, "check_video_task") as check_video,
            patch.object(server.provider_client, "check_image_task") as check_image,
            patch.object(server.threading, "Thread") as thread_cls,
        ):
            result = server.reconcile_generation_runtime_once()

        self.assertEqual(result["resumed"], 0)
        check_video.assert_not_called()
        check_image.assert_not_called()
        thread_cls.return_value.start.assert_not_called()
        with tenant_context.use_context(self.context):
            for episode_id in ("EP009", "EP010", "EP011"):
                tasks = canvas_store.list_generation_tasks(
                    project_id,
                    300,
                    episode_id=episode_id,
                )
                self.assertEqual(len(tasks), 30)
                self.assertTrue(all(task["status"] == "failed" for task in tasks))

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
        started = []
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
        with (
            patch.object(server, "_active_tenants", return_value=[None]),
            patch.object(server.store, "list_projects", return_value=[{"id": "project-1"}]),
            patch.object(server.store, "get_project", return_value=project),
            patch.object(
                server,
                "reconcile_recoverable_provider_tasks",
                return_value=0,
            ),
            patch.object(
                server.provider_catalog,
                "resolve_model",
                return_value=(provider, model, {}),
            ),
            patch.object(server.threading, "Thread") as thread_cls,
        ):
            thread_cls.return_value.start.side_effect = lambda: started.append(
                thread_cls.call_args.kwargs["target"]
            )
            server.start_pending_video_resumers()

        self.assertEqual(len(started), 1)
        self.assertTrue(started)

    def test_interrupted_image_task_without_node_id_is_resumed(self):
        project = {
            "id": "project-image-interrupted",
            "payload": {
                "nodes": [{
                    "id": "node-image-interrupted",
                    "type": "image",
                    "title": "角色图",
                    "metadata": {
                        "status": "generating",
                        "params": {
                            "provider": "new-apl",
                            "model": "65-seedream-5-pro",
                        },
                    },
                }],
            },
        }
        task = {
            "id": "persisted-image-task",
            "project_id": project["id"],
            "node_id": "node-image-interrupted",
            "provider_task_id": "task-image-interrupted",
            "kind": "image",
            "status": "interrupted",
            "error": "执行租约已过期，但已有供应商任务号",
        }
        started = []

        with (
            patch.object(server, "_active_tenants", return_value=[None]),
            patch.object(server.store, "get_generation_task", return_value=task),
            patch.object(server.store, "list_projects", return_value=[{"id": project["id"]}]),
            patch.object(server.store, "get_project", return_value=project),
            patch.object(server.store, "list_generation_tasks", return_value=[task]),
            patch.object(
                server.provider_catalog,
                "resolve_model",
                return_value=(
                    {"id": "new-apl"},
                    {"id": "65-seedream-5-pro", "adapter": "newapi_image"},
                    {},
                ),
            ),
            patch.object(server, "commit_node_metadata") as commit_node,
            patch.object(server.threading, "Thread") as thread_cls,
        ):
            thread_cls.return_value.start.side_effect = lambda: started.append(
                thread_cls.call_args.kwargs["target"]
            )
            server._resume_expired_provider_tasks([task])

        self.assertEqual(len(started), 1)
        commit_node.assert_any_call(
            project["id"],
            "node-image-interrupted",
            {
                "status": "generating",
                "taskId": "task-image-interrupted",
                "providerTaskStatus": "queued",
                "providerTaskPhase": "等待恢复",
                "lastError": "",
                "generationFinishedAt": "",
            },
        )

    def test_interrupted_video_task_without_node_id_is_resumed(self):
        project = {
            "id": "project-video-interrupted",
            "payload": {
                "nodes": [{
                    "id": "node-video-interrupted",
                    "type": "video",
                    "title": "段1",
                    "metadata": {
                        "status": "generating",
                        "params": {
                            "provider": "new-apl",
                            "model": "50-seedance-2.0-fast",
                        },
                    },
                }],
            },
        }
        task = {
            "id": "persisted-video-task",
            "project_id": project["id"],
            "node_id": "node-video-interrupted",
            "provider_task_id": "task-video-interrupted",
            "kind": "video",
            "status": "interrupted",
            "error": "执行租约已过期，但已有供应商任务号",
        }
        started = []

        with (
            patch.object(server, "_active_tenants", return_value=[None]),
            patch.object(server.store, "get_generation_task", return_value=task),
            patch.object(server.store, "list_projects", return_value=[{"id": project["id"]}]),
            patch.object(server.store, "get_project", return_value=project),
            patch.object(server.store, "list_generation_tasks", return_value=[task]),
            patch.object(
                server.provider_catalog,
                "resolve_model",
                return_value=(
                    {"id": "new-apl"},
                    {"id": "50-seedance-2.0-fast", "adapter": "canvaspro_video"},
                    {},
                ),
            ),
            patch.object(server, "commit_node_metadata"),
            patch.object(server.threading, "Thread") as thread_cls,
        ):
            thread_cls.return_value.start.side_effect = lambda: started.append(
                thread_cls.call_args.kwargs["target"]
            )
            server._resume_expired_provider_tasks([task])

        self.assertEqual(len(started), 1)

    def test_task_row_resume_uses_its_persisted_kind_and_id(self):
        project = {
            "id": "project-kind",
            "payload": {
                "nodes": [{
                    "id": "node-kind",
                    "type": "image",
                    "metadata": {"status": "generating"},
                }],
            },
        }
        task = {
            "id": "task-kind",
            "project_id": project["id"],
            "node_id": "node-kind",
            "provider_task_id": "provider-kind",
            "kind": "image",
            "status": "interrupted",
            "provider": "new-apl",
            "model": "65-seedream-5-pro",
        }
        with (
            patch.object(server.store, "get_generation_task", return_value=task),
            patch.object(server.store, "get_project", return_value=project),
            patch.object(
                server.provider_catalog,
                "resolve_model",
                return_value=(
                    {"id": "new-apl"},
                    {"id": "65-seedream-5-pro", "adapter": "newapi_image"},
                    {},
                ),
            ),
            patch.object(server, "commit_node_metadata"),
            patch.object(server, "_resume_image_task") as resume_image,
            patch.object(server.threading, "Thread") as thread_cls,
        ):
            thread_cls.return_value.start.side_effect = lambda: (
                thread_cls.call_args.kwargs["target"]()
            )
            result = server._resume_expired_provider_tasks([task])

        self.assertEqual(result, 1)
        resume_image.assert_called_once()
        args = resume_image.call_args.args
        self.assertEqual(args[0], project["id"])
        self.assertEqual(args[1], "node-kind")
        self.assertEqual(args[2], "provider-kind")

    def test_recoverable_task_rows_attach_provider_id_before_resume(self):
        project = {
            "id": "project-runtime-image",
            "payload": {
                "nodes": [{
                    "id": "node-runtime-image",
                    "type": "image",
                    "title": "角色图",
                    "metadata": {
                        "status": "generating",
                        "params": {
                            "provider": "new-apl",
                            "model": "65-seedream-5-pro",
                        },
                    },
                }],
            },
        }
        task = {
            "id": "runtime-image-task",
            "project_id": project["id"],
            "node_id": "node-runtime-image",
            "provider_task_id": "task-runtime-image",
            "kind": "image",
            "status": "interrupted",
            "error": "执行租约已过期，但已有供应商任务号",
        }
        started = []

        with (
            patch.object(server.store, "list_projects", return_value=[{"id": project["id"]}]),
            patch.object(server.store, "get_project", return_value=project),
            patch.object(server.store, "list_generation_tasks", return_value=[task]),
            patch.object(server, "commit_node_metadata") as commit_node,
        ):
            attached = server.reconcile_recoverable_provider_tasks()

        self.assertEqual(attached, 1)
        commit_node.assert_called_once_with(
            project["id"],
            "node-runtime-image",
            {
                "status": "generating",
                "taskId": "task-runtime-image",
                "providerTaskStatus": "queued",
                "providerTaskPhase": "等待恢复",
                "providerTaskProgress": 0,
                "lastError": "",
                "generationFinishedAt": "",
            },
        )

    def test_recoverable_task_does_not_regress_done_local_media(self):
        project = {
            "id": "project-done-media",
            "payload": {
                "nodes": [{
                    "id": "node-done-media",
                    "type": "image",
                    "title": "已完成图片",
                    "metadata": {
                        "status": "done",
                        "content": "/uploads/done.jpg",
                    },
                }],
            },
        }
        task = {
            "id": "done-media-task",
            "project_id": project["id"],
            "node_id": "node-done-media",
            "provider_task_id": "provider-done-media",
            "kind": "image",
            "status": "interrupted",
        }
        with (
            patch.object(server.store, "list_projects", return_value=[{"id": project["id"]}]),
            patch.object(server.store, "get_project", return_value=project),
            patch.object(server.store, "list_generation_tasks", return_value=[task]),
            patch.object(
                server.provider_client,
                "local_upload_path",
                return_value="done.jpg",
            ),
            patch.object(server, "commit_node_metadata") as commit_node,
            patch.object(
                server,
                "_recover_existing_local_task_result",
                return_value=True,
            ),
            patch.object(server.store, "update_generation_task"),
        ):
            attached = server.reconcile_recoverable_provider_tasks()

        self.assertEqual(attached, 0)
        commit_node.assert_not_called()

    def test_newapi_image_submit_releases_lease_for_single_resumer(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("newapi-image-single-owner")
            canvas_store.insert_project_nodes(
                project_id,
                [{
                    "id": "image-single-owner",
                    "type": "image",
                    "title": "图片｜单一路径",
                    "metadata": {
                        "status": "generating",
                        "taskId": "task-provider-single",
                        "providerTaskStatus": "queued",
                        "params": {"provider": "new-apl", "model": "65-seedream-5-pro"},
                    },
                }],
                episode_id="EP001",
            )
            created_task = canvas_store.create_generation_task(
                project_id,
                "image-single-owner",
                title="图片｜单一路径",
                kind="image",
                episode_id="EP001",
                provider="new-apl",
                model="65-seedream-5-pro",
                client_operation_id="newapi-image-single-owner",
            )
            canvas_store.finish_generation_task(
                created_task["id"],
                status="interrupted",
                phase="供应商任务已提交",
                error="",
                provider_task_id="task-provider-single",
                delivery_status="pending",
            )
            task_id = created_task["id"]
            first = canvas_store.claim_generation_task_for_recovery(
                task_id,
                "task-provider-single",
                "resume:first",
                45,
            )
            second = canvas_store.claim_generation_task_for_recovery(
                task_id,
                "task-provider-single",
                "resume:second",
                45,
            )
            wrong_task = canvas_store.claim_generation_task_for_recovery(
                task_id,
                "different-provider-task",
                "resume:wrong",
                45,
            )
            persisted = canvas_store.get_generation_task(task_id)

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertFalse(wrong_task)
        self.assertEqual(persisted["lease_owner"], "resume:first")

    def test_newapi_image_submission_starts_one_immediate_resumer(self):
        project_id = "project-image-resume"
        node_id = "node-image-resume"
        task_id = "task-provider-image-resume"
        started = []

        with patch.object(server, "_resume_image_task", return_value=None), \
                patch.object(server.threading, "Thread") as thread_cls:
            thread_cls.return_value.start.side_effect = lambda: started.append(
                thread_cls.call_args.kwargs["target"]
            )
            first = server._start_image_resumer(
                project_id,
                node_id,
                task_id,
                {"id": "new-apl"},
                {"id": "65-seedream-5-pro"},
            )
            second = server._start_image_resumer(
                project_id,
                node_id,
                task_id,
                {"id": "new-apl"},
                {"id": "65-seedream-5-pro"},
            )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(len(started), 1)
        server.RESUMED_VIDEO_TASKS.discard((project_id, node_id, task_id))

    def test_immediate_image_resumer_enters_poller_instead_of_self_blocking(self):
        project_id = "project-image-resumer-entry"
        node_id = "node-image-resumer-entry"
        task_id = "task-provider-resumer-entry"
        entered = threading.Event()
        calls = []

        def poll(*args):
            calls.append(args)
            entered.set()

        with patch.object(server, "_resume_image_task", side_effect=poll):
            started = server._start_image_resumer(
                project_id,
                node_id,
                task_id,
                {"id": "new-apl"},
                {"id": "65-seedream-5-pro"},
            )
            self.assertTrue(started)
            self.assertTrue(entered.wait(timeout=3))

        self.assertEqual(calls, [(
            project_id,
            node_id,
            task_id,
            {"id": "new-apl"},
            {"id": "65-seedream-5-pro"},
        )])
        self.assertFalse(server.RESUMED_VIDEO_TASKS)

    def test_runtime_reconciler_rescans_unfinished_tasks_after_prior_expiry(self):
        with (
            patch.object(server, "_collect_for_active_tenants", return_value=[]),
            patch.object(server, "_run_for_active_tenants", return_value=0),
            patch.object(
                server,
                "_resume_expired_provider_tasks",
                return_value=0,
            ),
            patch.object(
                server,
                "_dispatch_due_provider_tasks",
                return_value=0,
            ) as dispatch,
        ):
            first = server.reconcile_generation_runtime_once()
            second = server.reconcile_generation_runtime_once()

        self.assertEqual(dispatch.call_count, 2)
        self.assertEqual(first["resumed"], 0)
        self.assertEqual(second["resumed"], 0)

    def test_image_completion_finds_interrupted_task_by_provider_id(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("image-interrupted-finalize")
            node_id = "node-image-interrupted-finalize"
            provider_task_id = "provider-image-interrupted-finalize"
            canvas_store.insert_project_nodes(
                project_id,
                [{
                    "id": node_id,
                    "type": "image",
                    "title": "场景｜2K",
                    "metadata": {
                        "status": "generating",
                        "taskId": provider_task_id,
                        "params": {
                            "provider": "new-apl",
                            "model": "65-seedream-5-pro",
                            "clarity": "2K",
                        },
                    },
                }],
                episode_id="EP002",
            )
            task = canvas_store.create_generation_task(
                project_id,
                node_id,
                title="场景｜2K",
                kind="image",
                episode_id="EP002",
                provider="new-apl",
                model="65-seedream-5-pro",
            )
            canvas_store.finish_generation_task(
                task["id"],
                status="interrupted",
                phase="供应商任务已提交",
                error="",
                provider_task_id=provider_task_id,
                delivery_status="pending",
            )

            with patch.object(
                server,
                "persist_generated_media",
                return_value="/uploads/image-interrupted-finalize.jpg",
            ):
                finalized = server._finalize_resumed_image(
                    project_id,
                    node_id,
                    {
                        "taskId": provider_task_id,
                        "status": "completed",
                        "url": "https://provider.example/result.jpg",
                    },
                    {"id": "new-apl", "name": "New APl"},
                    {
                        "id": "65-seedream-5-pro",
                        "remoteModel": "65-seedream-5-pro",
                        "adapter": "newapi_image",
                    },
                )

            saved_task = canvas_store.get_generation_task(task["id"])
            saved_node = canvas_store.get_projection_node(project_id, node_id)

        self.assertTrue(finalized)
        self.assertEqual(saved_task["status"], "succeeded")
        self.assertEqual(saved_task["phase"], "已完成")
        self.assertEqual(saved_task["delivery_status"], "ready")
        self.assertEqual(
            saved_task["local_result_url"],
            "/uploads/image-interrupted-finalize.jpg",
        )
        self.assertEqual(saved_node["metadata"]["status"], "done")
        self.assertEqual(
            saved_node["metadata"]["content"],
            "/uploads/image-interrupted-finalize.jpg",
        )

    def test_due_provider_task_scan_is_bounded_and_indexed_by_schedule(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("due-provider-scan")
            due = canvas_store.create_generation_task(
                project_id,
                "node-due",
                kind="image",
            )
            canvas_store.update_generation_task(
                due["id"],
                provider_task_id="provider-due",
                status="interrupted",
                next_poll_at="2026-11-01T00:00:00Z",
            )
            future = canvas_store.create_generation_task(
                project_id,
                "node-future",
                kind="image",
            )
            canvas_store.update_generation_task(
                future["id"],
                provider_task_id="provider-future",
                status="interrupted",
                next_poll_at="2099-11-01T00:00:00Z",
            )
            tasks = canvas_store.list_due_provider_tasks(
                limit=1,
                now="2026-11-01T00:01:00Z",
            )

        self.assertEqual([task["id"] for task in tasks], [due["id"]])

    def test_terminal_provider_task_clears_next_poll(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("terminal-poll-clear")
            task = canvas_store.create_generation_task(
                project_id,
                "node-terminal-poll",
                kind="image",
            )
            canvas_store.update_generation_task(
                task["id"],
                provider_task_id="provider-terminal-poll",
                status="running",
                next_poll_at="2026-11-01T00:00:00Z",
            )
            project = canvas_store.get_project(project_id)
            project["payload"].setdefault("nodes", []).append({
                "id": "node-terminal-poll",
                "type": "image",
                "metadata": {"status": "generating"},
            })
            canvas_store.update_project(project_id, payload=project["payload"])
            committed = canvas_store.commit_generation_terminal(
                project_id,
                "node-terminal-poll",
                {"status": "done", "content": "/uploads/poll.jpg"},
                task_id=task["id"],
                task_updates={
                    "status": "succeeded",
                    "phase": "已完成",
                    "delivery_status": "ready",
                    "next_poll_at": "2026-11-01T00:00:08Z",
                },
            )
            saved = canvas_store.get_generation_task(task["id"])

        self.assertTrue(committed["committed"])
        self.assertEqual(saved["next_poll_at"], "")

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
                thread_cls.call_args.kwargs["target"]
            )
            server.start_pending_video_resumers()

        self.assertEqual(len(started), 1)
        self.assertTrue(started)

    def test_failed_task_without_live_provider_state_is_not_restarted(self):
        project = {
            "id": "project-1",
            "payload": {
                "nodes": [{
                    "id": "node-1",
                    "type": "video",
                    "title": "段4",
                    "metadata": {
                        "status": "failed",
                        "taskId": "task-dead",
                        "providerTaskStatus": "failed",
                        "providerTaskPhase": "查询失败",
                        "params": {
                            "provider": "new-apl",
                            "model": "004-sd2.0-fast933-5-15-720p",
                        },
                    },
                }],
            },
        }
        task = {
            "id": "local-task-dead",
            "project_id": "project-1",
            "node_id": "node-1",
            "provider_task_id": "task-dead",
            "provider": "new-apl",
            "model": "004-sd2.0-fast933-5-15-720p",
            "kind": "video",
            "status": "failed",
            "phase": "查询失败",
            "error": "供应商明确返回任务不存在（HTTP 404）",
        }
        with (
            patch.object(server, "_active_tenants", return_value=[None]),
            patch.object(server.store, "list_projects", return_value=[{"id": "project-1"}]),
            patch.object(server.store, "get_project", return_value=project),
            patch.object(server.store, "list_generation_tasks", return_value=[task]),
            patch.object(server, "commit_node_metadata"),
            patch.object(server.threading, "Thread") as thread_cls,
        ):
            started = server.start_pending_video_resumers()

        self.assertEqual(started, 0)
        thread_cls.return_value.start.assert_not_called()

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

    def test_terminal_task_selector_returns_newest_success_not_old_failure(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("terminal-outcome-order")
            node_id = "node-video"
            failed = canvas_store.create_generation_task(
                project_id,
                node_id,
                kind="video",
                provider="agnes-ai",
                model="agnes-video",
            )
            canvas_store.finish_generation_task(
                failed["id"],
                status="failed",
                phase="生成失败",
                error="queue full",
            )
            succeeded = canvas_store.create_generation_task(
                project_id,
                node_id,
                kind="video",
                provider="agnes-ai",
                model="agnes-video",
            )
            canvas_store.finish_generation_task(
                succeeded["id"],
                status="succeeded",
                phase="已完成",
                provider_task_id="provider-video-ok",
                local_result_url="/uploads/result.mp4",
                provider_result_url="https://example.com/result.mp4",
            )
            selected = server._latest_terminal_generation_task(
                project_id,
                node_id,
            )

        self.assertEqual(selected["id"], succeeded["id"])
        self.assertEqual(selected["status"], "succeeded")

    def test_failed_reconcile_does_not_overwrite_newer_success(self):
        project_id = "project-outcome"
        node_id = "node-video"
        project = {
            "id": project_id,
            "payload": {
                "nodes": [{
                    "id": node_id,
                    "type": "video",
                    "title": "段3",
                    "metadata": {
                        "status": "failed",
                        "content": "/uploads/result.mp4",
                        "lastError": "old queue full",
                        "providerTaskStatus": "failed",
                        "params": {
                            "mediaVersions": [{
                                "url": "/uploads/result.mp4",
                                "taskId": "provider-video-ok",
                            }],
                            "mediaVersionIndex": 0,
                        },
                    },
                }],
            },
        }
        ordered_tasks = [
            {
                "id": "local-ok",
                "node_id": node_id,
                "provider_task_id": "provider-video-ok",
                "status": "succeeded",
                "phase": "已完成",
                "local_result_url": "/uploads/result.mp4",
                "provider_finished_at": "2026-11-01T10:18:33Z",
                "finished_at": "2026-11-01T10:18:34Z",
            },
            {
                "id": "local-old-failure",
                "node_id": node_id,
                "provider_task_id": "",
                "status": "failed",
                "phase": "生成失败",
                "error": "old queue full",
                "finished_at": "2026-11-01T10:06:43Z",
            },
        ]
        with (
            patch.object(
                server.store,
                "list_projects",
                return_value=[{"id": project_id}],
            ),
            patch.object(
                server.store,
                "get_project",
                return_value=project,
            ),
            patch.object(
                server.store,
                "list_generation_tasks",
                return_value=ordered_tasks,
            ),
            patch.object(server, "commit_node_metadata") as commit_meta,
        ):
            repaired = server.reconcile_failed_generation_task_nodes()

        self.assertEqual(repaired, 1)
        updates = commit_meta.call_args.args[2]
        self.assertEqual(updates["status"], "done")
        self.assertEqual(updates["lastError"], "")
        self.assertEqual(updates["taskId"], "provider-video-ok")
        self.assertNotIn("content", updates)

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
                status="interrupted",
                provider_task_id="provider-video-1",
                phase="结果待确认",
            )
            project = canvas_store.get_project(project_id)
            project["payload"].setdefault("nodes", []).append({
                "id": "node-video",
                "type": "video",
                "metadata": {
                    "status": "generating",
                    "taskId": "provider-video-1",
                },
            })
            canvas_store.update_project(project_id, payload=project["payload"])

            with patch.object(
                server,
                "commit_generation_terminal",
                return_value={"committed": True},
            ) as commit_terminal:
                # This test asserts the terminal callback payload. The schedule
                # write is covered separately by the bounded due-task tests.
                with patch.object(server.store, "schedule_provider_task_poll"):
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
        self.assertEqual(
            commit_terminal.call_args.args[2]["providerTaskStatus"],
            "uncertain",
        )
        self.assertEqual(
            commit_terminal.call_args.args[2]["providerTaskPhase"],
            "结果待确认",
        )

    def test_unresolved_no_task_id_is_projected_uncertain(self):
        node = {
            "id": "node-no-provider-task",
            "type": "video",
            "metadata": {
                "status": "generating",
                "providerTaskStatus": "queued",
                "providerTaskPhase": "正在连接供应商",
            },
        }
        task = {
            "id": "local-uncertain",
            "status": "interrupted",
            "phase": "结果待确认",
            "provider_task_id": "",
            "error": "供应商请求可能已经发出，但没有任务号",
            "finished_at": "",
            "updated_at": "2026-11-01T13:01:39.332Z",
        }

        projection = server.project_generation_node_state(node, task)

        self.assertEqual(projection["metadata"]["status"], "error")
        self.assertEqual(
            projection["metadata"]["providerTaskStatus"],
            "uncertain",
        )
        self.assertEqual(
            projection["metadata"]["providerTaskPhase"],
            "结果待确认",
        )
        self.assertEqual(
            projection["metadata"]["generationFinishedAt"],
            "",
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
                status="interrupted",
                provider_task_id="provider-video-404",
                phase="结果待确认",
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
                status="interrupted",
                provider_task_id="provider-video-1",
                phase="结果待确认",
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
            canvas_store.release_generation_task_lease(task["id"], owner)
            canvas_store.update_generation_task(
                task["id"],
                status="interrupted",
                phase="结果待确认",
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

    def test_image_storage_receipt_replaces_current_version_url(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("image-storage-swap")
            canvas_store.insert_project_nodes(
                project_id,
                [{
                    "id": "image-storage-node",
                    "type": "image",
                    "title": "角色｜存储回执",
                    "metadata": {
                        "status": "done",
                        "content": "/uploads/local.png",
                        "storage": {},
                        "params": {
                            "mediaVersions": [{
                                "url": "/uploads/local.png",
                                "taskId": "task-image-storage",
                                "sourceType": "generated",
                                "providerResultUrl": "https://provider.example/tmp.png",
                            }],
                            "mediaVersionIndex": 0,
                        },
                    },
                }],
                episode_id="EP001",
            )
            project = canvas_store.get_project(project_id)
            node = next(
                item for item in project["payload"]["nodes"]
                if item["id"] == "image-storage-node"
            )
            stored = {
                "status": "stored",
                "provider": "qiniu",
                "url": "https://cdn.example/final.png",
            }
            with (
                patch.object(
                    server,
                    "archive_generated_image",
                    return_value=stored,
                ),
                patch.object(
                    server.threading,
                    "Thread",
                ) as thread_cls,
            ):
                server.queue_generated_image_tos_archive(
                    project_id,
                    node["id"],
                    "/uploads/local.png",
                    {"id": "provider"},
                    {"id": "model"},
                    "task-image-storage",
                    "2026-10-31T09:00:00Z",
                )
                target = thread_cls.call_args.kwargs["target"]
                target_args = thread_cls.call_args.kwargs.get("args") or ()
                target(*target_args)
            saved = canvas_store.get_projection_node(
                project_id,
                "image-storage-node",
            )

        metadata = saved["metadata"]
        version = metadata["params"]["mediaVersions"][-1]
        self.assertEqual(metadata["content"], "https://cdn.example/final.png")
        self.assertEqual(version["url"], "https://cdn.example/final.png")
        self.assertEqual(version["storage"], stored)
        self.assertEqual(
            version["providerResultUrl"],
            "https://provider.example/tmp.png",
        )

    def test_video_storage_receipt_replaces_current_version_url(self):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("video-storage-swap")
            canvas_store.insert_project_nodes(
                project_id,
                [{
                    "id": "video-storage-node",
                    "type": "video",
                    "title": "视频｜存储回执",
                    "metadata": {
                        "status": "done",
                        "content": "/uploads/local.mp4",
                        "storage": {},
                        "params": {
                            "mediaVersions": [{
                                "url": "/uploads/local.mp4",
                                "taskId": "task-video-storage",
                                "sourceType": "generated",
                                "providerResultUrl": "https://provider.example/tmp.mp4",
                            }],
                            "videoVersions": [{
                                "url": "/uploads/local.mp4",
                                "taskId": "task-video-storage",
                                "sourceType": "generated",
                                "providerResultUrl": "https://provider.example/tmp.mp4",
                            }],
                            "mediaVersionIndex": 0,
                            "videoVersionIndex": 0,
                            "thumbUrl": "/uploads/local-thumb.jpg",
                        },
                    },
                }],
                episode_id="EP001",
            )
            stored = {
                "status": "stored",
                "provider": "aliyun_oss",
                "url": "https://cdn.example/final.mp4",
            }
            with (
                patch.object(
                    server,
                    "validate_generated_video",
                    return_value={"status": "valid", "duration": 5.0},
                ),
                patch.object(
                    server,
                    "ensure_video_thumbnail",
                    return_value="/uploads/local-thumb.jpg",
                ),
                patch.object(
                    server.provider_catalog,
                    "storage_config",
                    return_value={"enabled": True, "auto_persist_videos": True},
                ),
                patch.object(
                    server.media_storage,
                    "persist_local_url",
                    return_value=stored,
                ),
                patch.object(server.threading, "Thread") as thread_cls,
            ):
                server.queue_generated_video_finalize(
                    project_id,
                    "video-storage-node",
                    "/uploads/local.mp4",
                    {"id": "provider"},
                    {"id": "model"},
                    "task-video-storage",
                    "2026-10-31T09:00:00Z",
                )
                target = thread_cls.call_args.kwargs["target"]
                target_args = thread_cls.call_args.kwargs.get("args") or ()
                target(*target_args)
            saved = canvas_store.get_projection_node(
                project_id,
                "video-storage-node",
            )

        metadata = saved["metadata"]
        media_version = next(
            item for item in metadata["params"]["mediaVersions"]
            if item.get("taskId") == "task-video-storage"
        )
        video_version = next(
            item for item in metadata["params"]["videoVersions"]
            if item.get("taskId") == "task-video-storage"
        )
        self.assertEqual(metadata["content"], "https://cdn.example/final.mp4")
        self.assertEqual(media_version["url"], "https://cdn.example/final.mp4")
        self.assertEqual(video_version["url"], "https://cdn.example/final.mp4")
        self.assertEqual(media_version["storage"], stored)
        self.assertEqual(
            media_version["providerResultUrl"],
            "https://provider.example/tmp.mp4",
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
