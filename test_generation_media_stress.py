from __future__ import annotations

import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import canvas_store
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
