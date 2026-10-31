# -*- coding: utf-8 -*-
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import canvas_store
import server
import tenant_context


class ProjectSummaryPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            "CANVAS_MULTI_TENANT": "1",
            "CANVAS_TENANT_DATA_ROOT": str(self.root),
        })
        self.env.start()
        self.context = tenant_context.make_context("summary", "summary")
        tenant_context.ensure_context_dirs(self.context)
        self.scope = tenant_context.use_context(self.context)
        self.scope.__enter__()

    def tearDown(self):
        self.scope.__exit__(None, None, None)
        self.env.stop()
        self.temp.cleanup()

    def test_summary_uses_derived_counts_and_stays_fast_on_large_payloads(self):
        project_id = canvas_store.create_project(
            "large-project",
            {
                "nodes": [
                    {"id": f"node-{index}", "type": "image", "metadata": {}}
                    for index in range(1200)
                ],
                "connections": [{"id": f"edge-{index}"} for index in range(600)],
                "episodeImports": [
                    {"id": f"EP{index:03d}", "nodeIds": []}
                    for index in range(1, 81)
                ],
            },
        )
        started = time.perf_counter()
        summaries = canvas_store.list_project_summaries()
        elapsed = time.perf_counter() - started
        project = next(item for item in summaries if item["id"] == project_id)
        self.assertEqual(project["node_count"], 1200)
        self.assertEqual(project["connection_count"], 600)
        self.assertEqual(project["episode_count"], 80)
        self.assertLess(elapsed, 0.25)

    def test_update_refreshes_derived_counts_in_same_write(self):
        project_id = canvas_store.create_project("counts", {
            "nodes": [{"id": "one"}],
            "connections": [],
            "episodeImports": [{"id": "EP001", "nodeIds": ["one"]}],
        })
        current = canvas_store.get_project(project_id)
        current["payload"]["nodes"].append({"id": "two"})
        current["payload"]["episodeImports"].append({"id": "EP002", "nodeIds": ["two"]})
        canvas_store.update_project(project_id, payload=current["payload"])
        project = next(
            item for item in canvas_store.list_project_summaries()
            if item["id"] == project_id
        )
        self.assertEqual(project["node_count"], 2)
        self.assertEqual(project["episode_count"], 2)

    def test_projects_endpoint_does_not_load_full_payloads(self):
        project_id = canvas_store.create_project("endpoint", {
            "nodes": [{"id": "node-1"}],
            "episodeImports": [{"id": "EP001", "nodeIds": ["node-1"]}],
        })
        handler = object.__new__(server.Handler)
        handler.path = "/api/projects"
        handler.command = "GET"
        handler.headers = {}
        handler._send = mock.Mock()

        with patch.object(
            canvas_store,
            "get_project",
            side_effect=AssertionError("project list must not parse full payloads"),
        ):
            handler._do_get()

        status, body = handler._send.call_args.args
        self.assertEqual(status, 200)
        project = next(
            item for item in body["projects"] if item["id"] == project_id
        )
        self.assertEqual(project["node_count"], 1)
        self.assertEqual(project["episode_count"], 1)

    def test_existing_schema_with_default_zero_counts_is_backfilled_once(self):
        project_id = canvas_store.create_project(
            "nullable-counts",
            {
                "nodes": [{"id": "node-1"}, {"id": "node-2"}],
                "connections": [{"id": "edge-1"}],
                "episodeImports": [{"id": "EP001", "nodeIds": []}],
            },
        )
        connection = canvas_store._connect()
        connection.execute(
            "UPDATE canvas_projects SET node_count=0, connection_count=0,"
            "episode_count=1, failed_count=0, local_blocked_count=0 WHERE id=?",
            (project_id,),
        )
        connection.execute(
            "DELETE FROM canvas_meta WHERE key LIKE 'project_stats_%'"
        )
        connection.commit()
        connection.close()
        canvas_store._SCHEMA_READY = False
        canvas_store._SCHEMA_READY_PATHS.clear()
        project = next(
            item for item in canvas_store.list_project_summaries()
            if item["id"] == project_id
        )
        self.assertEqual(project["node_count"], 2)
        self.assertEqual(project["connection_count"], 1)
        self.assertEqual(project["episode_count"], 1)

    def test_project_stats_migration_records_failed_nodes_without_guessing(self):
        project_id = canvas_store.create_project(
            "failed-counts",
            {
                "nodes": [
                    {
                        "id": "provider-failed",
                        "type": "image",
                        "metadata": {"status": "failed", "lastError": "HTTP 502"},
                    },
                    {
                        "id": "blocked",
                        "type": "image",
                        "metadata": {"status": "failed", "lastError": "参考素材缺失"},
                    },
                ],
                "episodeImports": [{"id": "EP001", "nodeIds": []}],
            },
        )
        project = next(
            item for item in canvas_store.list_project_summaries()
            if item["id"] == project_id
        )
        self.assertEqual(project["failed_count"], 1)
        self.assertEqual(project["local_blocked_count"], 1)


if __name__ == "__main__":
    unittest.main()
