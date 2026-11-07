from __future__ import annotations

import concurrent.futures
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import canvas_store
import canvas_feed_contract
import import_feed
import server
import tenant_context


class GenerationMediaStressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root_override = patch.object(
            tenant_context,
            "data_root",
            return_value=Path(self.temp.name),
        )
        self.root_override.start()
        self.addCleanup(self.root_override.stop)
        self.context = tenant_context.make_context(
            "media-stress",
            "media-stress",
        )
        tenant_context.ensure_context_dirs(self.context)

    def _project(self, index, kinds):
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project(
                f"media-stress-{index}"
            )
            nodes = []
            for offset, kind in enumerate(kinds):
                nodes.append({
                    "id": f"{kind}-{index}-{offset}",
                    "type": kind,
                    "title": f"{kind}-{index}-{offset}",
                    "position": {"x": offset * 280, "y": index * 260},
                    "metadata": {
                        "status": "idle",
                        "prompt": f"{kind} stress {index}-{offset}",
                        "params": {},
                    },
                })
            canvas_store.insert_project_nodes(
                project_id,
                nodes,
                episode_id="EP001",
            )
            return project_id, nodes

    def _run_task(self, project_id, node, task_kind, index):
        with tenant_context.use_context(self.context):
            task = canvas_store.create_generation_task(
                project_id,
                node["id"],
                kind=task_kind,
                provider="mock",
                model=f"mock-{task_kind}",
                client_operation_id=f"op-{index}-{node['id']}",
            )
            owner = f"worker-{index}-{node['id']}"
            self.assertTrue(
                canvas_store.claim_generation_task(task["id"], owner, 45)
            )
            finished_at = canvas_store._now_iso()
            committed = canvas_store.commit_generation_terminal(
                project_id,
                node["id"],
                {
                    "status": "done",
                    "content": f"/uploads/{task_kind}-{index}.bin",
                    "lastError": "",
                    "taskId": f"provider-{task_kind}-{index}",
                    "generatedAt": finished_at,
                    "generationFinishedAt": finished_at,
                    "providerTaskStatus": "completed",
                    "providerTaskPhase": "已完成",
                    "providerTaskProgress": 100,
                },
                task_id=task["id"],
                task_updates={
                    "status": "succeeded",
                    "phase": "已完成",
                    "provider_task_id": f"provider-{task_kind}-{index}",
                    "local_result_url": f"/uploads/{task_kind}-{index}.bin",
                    "delivery_status": "ready",
                },
                lease_owner=owner,
            )
            self.assertTrue(committed.get("committed"))
            return task["id"]

    def test_import_asset_reference_pipeline_stress_without_provider_calls(self):
        """Exercise import -> projection -> provider payload with mocked generation."""
        with tenant_context.use_context(self.context):
            project_id = canvas_store.create_project("asset-pipeline-stress")

        feed = {
            "title": "资产链路压力测试 · 第01集",
            "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
            "canvas_contract": {
                "schema": canvas_feed_contract.SCHEMA_VERSION,
                "generator": {
                    "name": "asset-pipeline-stress",
                    "skillVersion": "test",
                    "packageRevision": "test",
                },
            },
            "asset_prompts": [
                {
                    "code": "图1",
                    "semanticCode": "CHR-001",
                    "name": "赵铁柱（角色立绘）",
                    "raw": "男性角色。台词：赵铁柱（低沉）：\"走。\"",
                    "canvas": {
                        "kind": "character",
                        "name": "赵铁柱",
                        "assetId": "character:赵铁柱:default",
                        "seriesId": "character:赵铁柱",
                        "variantKey": "default",
                    },
                },
                {
                    "code": "图2",
                    "semanticCode": "SCN-001",
                    "name": "铁西厂大门口（场景母版）",
                    "raw": "旧工业厂区门口，冷灰蓝。",
                    "canvas": {
                        "kind": "scene",
                        "name": "铁西厂大门口",
                        "assetId": "scene:铁西厂大门口:master",
                        "seriesId": "scene:铁西厂大门口",
                        "variantKey": "master",
                    },
                },
            ],
            "voice_prompts": [{
                "code": "图1",
                "name": "赵铁柱",
                "title": "角色｜赵铁柱（图1）",
                "prompt": (
                    "low, steady, northeastern-accented baritone. "
                    "A male voice, low and steady with a broad northeastern accent. "
                    "A male aged 38-42, speaking northeastern-accented Mandarin, "
                    "short drama dubbing reference clip, "
                    "4.5 seconds clean voice, no background noise."
                ),
                "description": (
                    "A male voice, low and steady with a broad northeastern accent."
                ),
                "label": "low, steady, northeastern-accented baritone",
                "age": "38-42",
                "gender": "male",
                "dialect": "northeastern-accented",
                "format": "audio-prompt-v1",
            }],
            "segments": [
                {
                    "no": index,
                    "name": f"段{index} · 资产链路",
                    "raw": (
                        "@角色｜赵铁柱（图1）站在@场景｜铁西厂大门口（图2）。"
                        "@音频｜赵铁柱（图1）\n"
                        "【音色引用】@音频｜赵铁柱（图1）\n"
                        f'台词：赵铁柱（低沉）："第{index}段。"'
                    ),
                    "shots": [],
                }
                for index in range(1, 7)
            ],
        }

        def import_once():
            with tenant_context.use_context(self.context):
                plan, _commit_result = canvas_feed_contract.prepare_import_locked(
                    feed,
                    project_id,
                )
                if plan.errors:
                    raise AssertionError([
                        canvas_feed_contract.issue_dict(item)
                        for item in plan.errors
                    ])
                workspace = canvas_store.get_project_workspace_nodes(
                    project_id,
                    "EP001",
                )
                nodes = {
                    str(node.get("id")): node
                    for node in workspace["nodes"]
                }
                identity_by_node = {
                    node_id: str(
                        ((node.get("metadata") or {}).get("assetId") or "")
                    )
                    for node_id, node in nodes.items()
                    if node.get("type") in {"image", "audio"}
                }
                videos = [
                    node for node in nodes.values()
                    if node.get("type") == "video"
                ]
                self.assertEqual(len(videos), 6)
                for video in videos:
                    metadata = video.get("metadata") or {}
                    params = metadata.get("params") or {}
                    refs = [
                        *list(params.get("refs") or []),
                        *list(params.get("voiceRefs") or []),
                    ]
                    self.assertTrue(refs)
                    for ref in refs:
                        node_id = str(ref.get("nodeId") or "")
                        self.assertIn(node_id, nodes)
                        expected_id = identity_by_node.get(node_id)
                        self.assertTrue(expected_id)
                        self.assertEqual(ref.get("assetId"), expected_id)
                return workspace

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            workspaces = list(pool.map(lambda _: import_once(), range(12)))

        self.assertEqual(len(workspaces), 12)
        with tenant_context.use_context(self.context):
            project = canvas_store.get_project(project_id)
        nodes = {
            str(node.get("id")): node
            for node in project["payload"]["nodes"]
        }
        videos = [
            node for node in nodes.values()
            if node.get("type") == "video"
        ]
        self.assertEqual(len(videos), 6)
        video = videos[0]
        refs = (video.get("metadata") or {}).get("params", {}).get("refs") or []
        self.assertTrue(refs)
        self.assertTrue(all(ref.get("assetId") for ref in refs))

        materialized_payload = json.loads(json.dumps(project["payload"]))
        materialized_nodes = {
            str(node.get("id")): node
            for node in materialized_payload.get("nodes") or []
        }
        for node in materialized_nodes.values():
            if node.get("type") not in {"image", "audio"}:
                continue
            metadata = node.setdefault("metadata", {})
            metadata["status"] = "done"
            metadata["content"] = (
                f"/uploads/{node['id']}.png"
                if node.get("type") == "image"
                else f"/uploads/{node['id']}.wav"
            )
        video = materialized_nodes[str(video["id"])]
        project = {"id": project_id, "payload": materialized_payload}

        provider = {"id": "mock", "api_key": "test-key"}
        model = {
            "id": "mock-video",
            "remoteModel": "mock-video",
            "defaultParams": {},
            "limits": {},
        }
        captured = {}

        def fake_generate(*args, **kwargs):
            captured["refs"] = args[4]
            captured["voices"] = args[5]
            return {
                "url": "https://provider.example/mock.mp4",
                "taskId": "mock-task",
            }

        handler = object.__new__(server.Handler)
        sent = []
        handler._send = lambda status, payload: sent.append((status, payload)) or payload
        upload_dir = tempfile.TemporaryDirectory()
        self.addCleanup(upload_dir.cleanup)
        for node in materialized_nodes.values():
            if node.get("type") not in {"image", "audio"}:
                continue
            content = str((node.get("metadata") or {}).get("content") or "")
            if content.startswith("/uploads/"):
                Path(upload_dir.name, content.removeprefix("/uploads/")).write_bytes(b"test")
        with (
            tenant_context.use_context(self.context),
            patch.object(server, "_upload_dir", return_value=upload_dir.name),
            patch.object(
                server.provider_catalog,
                "resolve_model",
                return_value=(provider, model, {}),
            ),
            patch.object(server, "ensure_provider_auth"),
            patch.object(
                server.provider_client,
                "generate_video",
                side_effect=fake_generate,
            ),
            patch.object(server, "persist_generated_media", return_value="/uploads/mock.mp4"),
            patch.object(server, "ensure_video_thumbnail", return_value=""),
            patch.object(server, "validate_generated_video", return_value={}),
            patch.object(server, "queue_generated_video_finalize"),
            patch.object(
                server,
                "commit_generation_terminal",
                return_value={"committed": True},
            ),
        ):
            handler._generate_once(project_id, project, video)

        self.assertIn("refs", captured, sent[-1] if sent else "provider not called")
        self.assertTrue(captured["refs"])
        self.assertTrue(captured["voices"])
        self.assertTrue(all(ref.get("assetId") for ref in captured["refs"]))
        self.assertTrue(all(ref.get("assetId") for ref in captured["voices"]))

    def test_multi_user_image_audio_video_state_machine_stress(self):
        kinds = ("image", "audio", "video")
        payloads = [
            self._project(index, kinds)
            for index in range(12)
        ]
        jobs = [
            (project_id, node, node["type"], index)
            for index, (project_id, nodes) in enumerate(payloads)
            for node in nodes
        ]

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            task_ids = list(
                pool.map(
                    lambda item: self._run_task(
                        item[0], item[1], item[2], item[3]
                    ),
                    jobs,
                )
            )

        self.assertEqual(len(task_ids), 36)
        self.assertEqual(len(set(task_ids)), 36)
        with tenant_context.use_context(self.context):
            for project_id, nodes in payloads:
                project = canvas_store.get_project(project_id)
                nodes_by_id = {
                    node["id"]: node
                    for node in project["payload"]["nodes"]
                }
                for node in nodes:
                    saved_node = nodes_by_id[node["id"]]
                    self.assertEqual(saved_node["metadata"]["status"], "done")
                    self.assertTrue(saved_node["metadata"]["content"])

    def test_two_running_video_workers_do_not_block_project_reads_or_writes(self):
        """Two slow provider waits must not serialize the canvas API.

        The production path starts one worker thread per persisted task and the
        provider adapters poll synchronously inside that worker. This regression
        holds both workers at the provider-submit boundary, then verifies that
        independent project reads and another project's terminal write still
        complete well below the simulated provider wait.
        """
        with tenant_context.use_context(self.context):
            first_project, first_nodes = self._project(201, ("video",))
            second_project, second_nodes = self._project(202, ("video",))
            other_project, other_nodes = self._project(203, ("text",))
            first_task = canvas_store.create_generation_task(
                first_project,
                first_nodes[0]["id"],
                kind="video",
                provider="mock",
                model="mock-video",
            )
            second_task = canvas_store.create_generation_task(
                second_project,
                second_nodes[0]["id"],
                kind="video",
                provider="mock",
                model="mock-video",
            )

        started = threading.Barrier(3)
        release = threading.Event()
        entered = []
        entered_lock = threading.Lock()

        def slow_generate(*_args, **_kwargs):
            with entered_lock:
                entered.append(time.monotonic())
            started.wait(timeout=5)
            if not release.wait(timeout=10):
                raise RuntimeError("test did not release provider wait")
            return {
                "url": "https://provider.example/slow.mp4",
                "taskId": "mock-provider-task",
            }

        provider = {"id": "mock", "api_key": "test-key"}
        model = {
            "id": "mock-video",
            "remoteModel": "mock-video",
            "defaultParams": {},
            "limits": {},
        }
        worker_errors = []

        def run_worker(task):
            try:
                server._run_generation_task_worker(
                    task["id"],
                    self.context.tenant_id,
                    self.context.slug,
                )
            except Exception as exc:  # pragma: no cover - asserted below
                worker_errors.append(exc)

        with (
            patch.object(
                server.provider_catalog,
                "resolve_model",
                return_value=(provider, model, {}),
            ),
            patch.object(server, "ensure_provider_auth"),
            patch.object(
                server.provider_client,
                "generate_video",
                side_effect=slow_generate,
            ),
            patch.object(server, "persist_generated_media", return_value="/uploads/slow.mp4"),
            patch.object(server, "ensure_video_thumbnail", return_value=""),
            patch.object(server, "validate_generated_video", return_value={}),
            patch.object(server, "queue_generated_video_finalize"),
        ):
            workers = [
                threading.Thread(target=run_worker, args=(task,), daemon=True)
                for task in (first_task, second_task)
            ]
            for worker in workers:
                worker.start()
            started.wait(timeout=5)

            read_started = time.monotonic()
            with tenant_context.use_context(self.context):
                read_project = canvas_store.get_project(other_project)
            read_elapsed = time.monotonic() - read_started

            write_started = time.monotonic()
            with tenant_context.use_context(self.context):
                write_task = canvas_store.create_generation_task(
                    other_project,
                    other_nodes[0]["id"],
                    kind="text",
                    provider="mock",
                    model="mock-text",
                )
            write_elapsed = time.monotonic() - write_started

            self.assertIsNotNone(read_project)
            self.assertTrue(write_task.get("created_new"))
            self.assertLess(read_elapsed, 1.5)
            self.assertLess(write_elapsed, 1.5)

            release.set()
            for worker in workers:
                worker.join(timeout=10)

        self.assertFalse(worker_errors)
        self.assertEqual(len(entered), 2)

    def test_same_project_two_video_workers_remain_responsive_during_provider_wait(self):
        with tenant_context.use_context(self.context):
            project_id, nodes = self._project(204, ("video", "video", "text"))
            tasks = [
                canvas_store.create_generation_task(
                    project_id,
                    nodes[index]["id"],
                    kind="video",
                    provider="mock",
                    model="mock-video",
                )
                for index in range(2)
            ]

        started = threading.Barrier(3)
        release = threading.Event()

        def slow_generate(*_args, **_kwargs):
            started.wait(timeout=5)
            if not release.wait(timeout=10):
                raise RuntimeError("test did not release provider wait")
            return {
                "url": "https://provider.example/same-project.mp4",
                "taskId": "same-project-provider-task",
            }

        provider = {"id": "mock", "api_key": "test-key"}
        model = {
            "id": "mock-video",
            "remoteModel": "mock-video",
            "defaultParams": {},
            "limits": {},
        }
        worker_errors = []

        def run_worker(task):
            try:
                server._run_generation_task_worker(
                    task["id"],
                    self.context.tenant_id,
                    self.context.slug,
                )
            except Exception as exc:  # pragma: no cover - asserted below
                worker_errors.append(exc)

        with (
            patch.object(
                server.provider_catalog,
                "resolve_model",
                return_value=(provider, model, {}),
            ),
            patch.object(server, "ensure_provider_auth"),
            patch.object(
                server.provider_client,
                "generate_video",
                side_effect=slow_generate,
            ),
            patch.object(server, "persist_generated_media", return_value="/uploads/same-project.mp4"),
            patch.object(server, "ensure_video_thumbnail", return_value=""),
            patch.object(server, "validate_generated_video", return_value={}),
            patch.object(server, "queue_generated_video_finalize"),
        ):
            workers = [
                threading.Thread(target=run_worker, args=(task,), daemon=True)
                for task in tasks
            ]
            for worker in workers:
                worker.start()
            started.wait(timeout=5)

            latency = []
            for _ in range(6):
                started_at = time.monotonic()
                with tenant_context.use_context(self.context):
                    project = canvas_store.get_project(project_id)
                    workspace = canvas_store.get_project_workspace_nodes(
                        project_id,
                        "EP001",
                    )
                latency.append(time.monotonic() - started_at)
                self.assertIsNotNone(project)
                self.assertGreaterEqual(len(workspace["nodes"]), 3)

            self.assertLess(max(latency), 1.5)
            release.set()
            for worker in workers:
                worker.join(timeout=10)

        self.assertFalse(worker_errors)

    def test_same_node_repeated_and_batch_submissions_create_one_active_task(self):
        with tenant_context.use_context(self.context):
            project_id, nodes = self._project(99, ("image", "audio", "video"))

        def submit(item):
            project, node = item
            with tenant_context.use_context(self.context):
                return canvas_store.create_generation_task(
                    project,
                    node["id"],
                    kind=node["type"],
                    provider="mock",
                    model=f"mock-{node['type']}",
                    client_operation_id=f"same-{node['id']}",
                )

        jobs = [
            (project_id, node)
            for node in nodes
            for _ in range(24)
        ]
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(submit, jobs))

        with tenant_context.use_context(self.context):
            tasks = canvas_store.list_generation_tasks(project_id, 100)

        self.assertEqual(len(tasks), 3)
        self.assertEqual(len({task["id"] for task in results}), 3)
        self.assertEqual(sum(
            1 for task in results if task.get("created_new")
        ), 3)
        self.assertEqual(
            {task["node_id"] for task in tasks},
            {node["id"] for node in nodes},
        )

    def test_terminal_task_unlocks_new_unique_task(self):
        with tenant_context.use_context(self.context):
            project_id, nodes = self._project(120, ("image",))
            node = nodes[0]
            first = canvas_store.create_generation_task(
                project_id,
                node["id"],
                kind="image",
            )
            owner = "worker-first"
            self.assertTrue(
                canvas_store.claim_generation_task(first["id"], owner, 45)
            )
            canvas_store.finish_generation_task(
                first["id"],
                status="succeeded",
                phase="已完成",
                local_result_url="/uploads/first-image.bin",
                delivery_status="ready",
                lease_owner=owner,
            )
            second = canvas_store.create_generation_task(
                project_id,
                node["id"],
                kind="image",
            )

        self.assertTrue(second.get("created_new"))
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(second["status"], "queued")


if __name__ == "__main__":
    unittest.main()
