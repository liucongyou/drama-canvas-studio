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
        events = canvas_store.list_audit_events(10)["events"]
        event = next(item for item in events if item["id"] == event_id)
        self.assertEqual(event["details"]["api_key"], "***")
        self.assertEqual(event["details"]["nested"]["password"], "***")
        self.assertEqual(event["details"]["nested"]["model"], "seedance-2.0")
        self.assertNotIn("secret-value", repr(event))

    def test_audit_queries_are_paginated_and_filtered(self):
        first = canvas_store.record_audit_event(
            "generation.submit",
            actor="owner",
            target_type="generation_task",
            target_id="task-1",
            details={
                "projectId": "project-a",
                "operationId": "op-1",
                "entry": "single",
            },
        )
        canvas_store.record_audit_event(
            "project.update",
            actor="owner",
            target_type="project",
            target_id="project-a",
        )
        canvas_store.record_audit_event(
            "generation.submit",
            actor="owner",
            target_type="generation_task",
            target_id="task-2",
            details={"projectId": "project-b"},
        )
        page = canvas_store.list_audit_events(
            1,
            project_id="project-a",
            action="generation",
        )
        self.assertEqual(len(page["events"]), 1)
        self.assertEqual(page["events"][0]["id"], first)
        self.assertEqual(page["events"][0]["details"]["operationId"], "op-1")
        self.assertFalse(page["hasMore"])
        self.assertEqual(page["tenantId"], "audit")

    def test_request_id_correlates_every_event_written_during_one_request(self):
        with canvas_store.use_audit_request(
            request_id="req-correlation",
            method="POST",
            path="/api/projects/p/nodes/n/generate",
            ip="127.0.0.1",
            user_agent="AuditTest/1.0",
        ):
            canvas_store.record_audit_event(
                "generation.submit",
                actor="owner",
                target_type="generation_task",
                target_id="task-request",
            )
            canvas_store.record_audit_event(
                "generation.complete",
                actor="owner",
                target_type="generation_task",
                target_id="task-request",
            )
        page = canvas_store.list_audit_events(
            20,
            request_id="req-correlation",
        )
        self.assertEqual(len(page["events"]), 2)
        for event in page["events"]:
            self.assertEqual(event["details"]["auditContext"]["requestId"], "req-correlation")
            self.assertEqual(
                event["details"]["auditContext"]["requestPath"],
                "/api/projects/p/nodes/n/generate",
            )

    def test_audit_rotation_keeps_bounded_history(self):
        for index in range(12):
            canvas_store.record_audit_event(
                "probe",
                actor="owner",
                details={"index": index},
            )
        deleted = canvas_store.rotate_audit_events(max_rows=5, ttl_days=3650)
        self.assertEqual(deleted, 7)
        page = canvas_store.list_audit_events(50)
        self.assertEqual(len(page["events"]), 5)

    def test_audit_is_read_only_for_the_current_tenant(self):
        with tenant_context.use_tenant("other", "other"):
            canvas_store.record_audit_event(
                "other.action",
                actor="other",
                details={"projectId": "other-project"},
            )
        self.assertEqual(
            canvas_store.list_audit_events(50)["events"],
            [],
        )
        with tenant_context.use_tenant("other", "other"):
            other_events = canvas_store.list_audit_events(50)["events"]
        self.assertEqual([event["action"] for event in other_events], ["other.action"])

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
