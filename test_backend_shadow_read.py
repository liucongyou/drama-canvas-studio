import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from backend.api.v1 import canvas as canvas_api
from backend.api.v1.canvas import tenant_from_header
from backend.models.entities import CanvasProject
from backend.services.canvas_repository import CanvasReadRepository
from backend.services.sqlite_migration import _prune_missing_keys, _source_keys
from backend.sync_shadow import advisory_lock_key


class _Result:
    def __init__(self, rows=None, row=None):
        self._rows = rows or []
        self._row = row

    def all(self):
        return self._rows

    def mappings(self):
        return self

    def one_or_none(self):
        return self._row


class _Session:
    def __init__(self, result):
        self.result = result
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append((str(statement), params))
        return self.result

    def close(self):
        pass


class ShadowReadQueryTests(unittest.TestCase):
    def test_tenant_header_rejects_missing_and_path_like_values(self):
        with self.assertRaises(HTTPException):
            tenant_from_header(None)
        with self.assertRaises(HTTPException):
            tenant_from_header("../owner")
        self.assertEqual(tenant_from_header("owner-1"), "owner-1")

    def test_internal_access_token_is_required_in_production(self):
        with patch.object(
            canvas_api,
            "get_settings",
            return_value=SimpleNamespace(
                environment="production",
                internal_api_token="",
            ),
        ):
            with self.assertRaises(HTTPException) as caught:
                canvas_api.require_internal_access(None)
        self.assertEqual(caught.exception.status_code, 503)

        with patch.object(
            canvas_api,
            "get_settings",
            return_value=SimpleNamespace(
                environment="production",
                internal_api_token="secret-token",
            ),
        ):
            with self.assertRaises(HTTPException) as caught:
                canvas_api.require_internal_access("wrong-token")
            self.assertEqual(caught.exception.status_code, 401)
            canvas_api.require_internal_access("secret-token")

    def test_project_summary_does_not_fetch_large_payload_json(self):
        row = SimpleNamespace(
            project_id="project-1",
            title="Project",
            revision=3,
            node_count=4,
            connection_count=2,
            episode_count=1,
            updated_at=None,
        )
        session = _Session(_Result(rows=[row]))
        with patch(
            "backend.services.canvas_repository.set_tenant_scope"
        ):
            result = CanvasReadRepository(lambda: session).list_projects("owner")
        self.assertEqual(result[0].project_id, "project-1")
        sql = session.statements[0][0].lower()
        self.assertNotIn("payload_json", sql)

    def test_workspace_uses_one_aggregate_query_and_skips_node_json(self):
        row = {
            "revision": 9,
            "nodes": [{
                "node_id": "node-1",
                "episode_id": "EP001",
                "node_type": "image",
                "title": "Role",
                "position_x": 12,
                "position_y": 24,
                "metadata_json": {"status": "done"},
                "updated_at": "2026-11-10T00:00:00+00:00",
            }],
            "episode": {
                "viewport_json": {"x": 1, "y": 2},
                "layout_json": {"columns": 2},
            },
            "edges": [{"id": "edge-1"}],
            "event_cursor": 7,
        }
        session = _Session(_Result(row=row))
        with patch(
            "backend.services.canvas_repository.set_tenant_scope"
        ):
            result = CanvasReadRepository(lambda: session).get_workspace(
                "owner",
                "project-1",
                "EP001",
            )
        self.assertEqual(len(session.statements), 1)
        self.assertEqual(result.revision, 9)
        self.assertEqual(result.nodes[0].metadata_json["status"], "done")
        self.assertEqual(result.event_cursor, 7)
        sql = session.statements[0][0].lower()
        self.assertNotIn("node_json", sql)
        self.assertNotIn("payload_json", sql)

    def test_task_list_does_not_fetch_request_json(self):
        row = SimpleNamespace(
            task_id="task-1",
            project_id="project-1",
            node_id="node-1",
            episode_id="EP001",
            status="running",
            phase="Generating",
            provider_task_id="provider-1",
            delivery_status="pending",
            progress=20,
            error="",
            client_operation_id="operation-1",
        )
        session = _Session(_Result(rows=[row]))
        with patch(
            "backend.services.canvas_repository.set_tenant_scope"
        ):
            result = CanvasReadRepository(lambda: session).list_tasks(
                "owner",
                "project-1",
            )
        self.assertEqual(result[0].task_id, "task-1")
        sql = session.statements[0][0].lower()
        self.assertNotIn("request_json", sql)


class ShadowMigrationPruneTests(unittest.TestCase):
    def test_advisory_lock_key_is_stable_and_tenant_specific(self):
        self.assertEqual(
            advisory_lock_key("owner"),
            advisory_lock_key("owner"),
        )
        self.assertNotEqual(
            advisory_lock_key("owner"),
            advisory_lock_key("other"),
        )

    class _Result:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

    class _Session:
        def __init__(self):
            self.statements = []

        def execute(self, statement):
            self.statements.append(statement)
            if statement.is_select:
                return ShadowMigrationPruneTests._Result([
                    ("keep-project",),
                    ("stale-project",),
                ])
            return ShadowMigrationPruneTests._Result([])

    def test_source_keys_ignore_unusable_primary_keys(self):
        keys = _source_keys(
            "canvas_projection_nodes",
            [
                {"project_id": "p1", "node_id": "n1"},
                {"project_id": "p1", "node_id": ""},
                {"project_id": "", "node_id": "n2"},
            ],
        )
        self.assertEqual(keys, {("p1", "n1")})

    def test_prune_deletes_only_target_keys_missing_from_source(self):
        session = self._Session()
        deleted = _prune_missing_keys(
            session,
            tenant_id="owner",
            table="canvas_projects",
            model=CanvasProject,
            source_rows=[{"id": "keep-project"}],
        )
        self.assertEqual(deleted, 1)
        self.assertEqual(len(session.statements), 2)
        self.assertTrue(session.statements[0].is_select)
        self.assertTrue(session.statements[1].is_delete)


if __name__ == "__main__":
    unittest.main()
