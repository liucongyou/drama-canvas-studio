# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import canvas_store
import tenant_context


class AuditMetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            "CANVAS_MULTI_TENANT": "1",
            "CANVAS_TENANT_DATA_ROOT": str(self.root),
        })
        self.env.start()
        self.context = tenant_context.make_context("audit", "audit")
        tenant_context.ensure_context_dirs(self.context)
        self.scope = tenant_context.use_context(self.context)
        self.scope.__enter__()

    def tearDown(self):
        self.scope.__exit__(None, None, None)
        self.env.stop()
        self.temp.cleanup()

    def test_audit_events_redact_secrets_and_remain_listable(self):
        event_id = canvas_store.record_audit_event(
            "provider.update",
            actor="owner",
            target_type="provider",
            target_id="ark",
            details={
                "api_key": "secret-value",
                "nested": {
                    "password": "another-secret",
                    "model": "seedance-2.0",
                },
            },
        )
        events = canvas_store.list_audit_events(10)
        event = next(item for item in events if item["id"] == event_id)
        self.assertEqual(event["details"]["api_key"], "***")
        self.assertEqual(event["details"]["nested"]["password"], "***")
        self.assertEqual(event["details"]["nested"]["model"], "seedance-2.0")
        self.assertNotIn("secret-value", repr(event))

    def test_runtime_metrics_cover_projects_tasks_and_storage(self):
        project_id = canvas_store.create_project("metrics")
        canvas_store.create_generation_task(
            project_id,
            "node-a",
            kind="video",
            estimated_cost=1.5,
            cost_currency="CNY",
        )
        metrics = canvas_store.runtime_metrics()
        self.assertEqual(metrics["projects"], 1)
        self.assertGreaterEqual(metrics["snapshots"], 1)
        self.assertEqual(metrics["tasks"]["queued"], 1)
        self.assertEqual(metrics["activeTasks"], 1)
        self.assertGreater(metrics["databaseBytes"], 0)


if __name__ == "__main__":
    unittest.main()
