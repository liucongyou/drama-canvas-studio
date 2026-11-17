from __future__ import annotations

import json
from unittest.mock import patch

import observability
import operational_metrics


def test_api_worker_mode_requires_an_active_worker():
    with (
        patch.object(
            operational_metrics,
            "_database_check",
            return_value={"ok": True},
        ),
        patch.object(
            operational_metrics,
            "_migration_check",
            return_value={"ok": True, "failures": []},
        ),
        patch.object(
            operational_metrics,
            "_worker_fleet_check",
            return_value={"ok": False, "activeWorkers": 0},
        ),
    ):
        report = operational_metrics.readiness(
            "api",
            release_commit="abc",
            execution_mode="worker",
        )

    assert report["ok"] is False
    assert report["checks"]["workerFleet"]["activeWorkers"] == 0


def test_worker_role_does_not_depend_on_worker_fleet_heartbeat():
    with (
        patch.object(
            operational_metrics,
            "_database_check",
            return_value={"ok": True},
        ),
        patch.object(
            operational_metrics,
            "_migration_check",
            return_value={"ok": True, "failures": []},
        ),
    ):
        report = operational_metrics.readiness(
            "worker",
            release_commit="abc",
            execution_mode="worker",
        )

    assert report["ok"] is True
    assert "workerFleet" not in report["checks"]


def test_readiness_fails_closed_when_object_media_plane_is_not_verified():
    with (
        patch.object(
            operational_metrics,
            "_database_check",
            return_value={"ok": True},
        ),
        patch.object(
            operational_metrics,
            "_migration_check",
            return_value={"ok": True, "failures": []},
        ),
        patch.object(
            operational_metrics.media_delivery,
            "readiness",
            return_value={
                "ok": False,
                "mode": "object",
                "required": True,
                "object": {
                    "ready": True,
                    "verified": False,
                },
            },
        ),
    ):
        report = operational_metrics.readiness(
            "worker",
            release_commit="abc",
            execution_mode="worker",
        )

    assert report["ok"] is False
    assert report["checks"]["mediaDelivery"]["mode"] == "object"


def test_prometheus_metrics_include_capacity_and_queue_states():
    snapshot = {
        "databasePool": {
            "active": 2,
            "idle": 3,
            "physical": 5,
            "waiting": 0,
        },
        "workerHeartbeats": [{"workerId": "worker-a"}],
        "admissions": {"activeAdmissions": 2},
        "tenantTasks": [
            {"tenant": "owner", "status": "queued", "count": 4},
            {"tenant": "owner", "oldestQueuedAt": "2026-01-01T00:00:00Z"},
        ],
    }
    with patch.object(
        operational_metrics,
        "metrics_snapshot",
        return_value=snapshot,
    ):
        text = operational_metrics.render_prometheus("abc")

    assert 'canvas_release_info{commit="abc"} 1' in text
    assert 'canvas_pg_pool_connections{state="active"} 2' in text
    assert "canvas_generation_admissions 2" in text
    assert "canvas_worker_heartbeats 1" in text
    assert (
        'canvas_generation_tasks{tenant="owner",status="queued"} 4'
        in text
    )


def test_structured_logs_redact_secrets_and_signed_queries(capsys):
    observability.log_event(
        "generation-worker",
        "worker.started",
        workerId="worker-a",
        token="secret-token",
        nested={"password": "secret-password"},
        url=(
            "https://example.tos.com/video.mp4?"
            "X-Tos-Signature=secret&X-Tos-Date=2026"
        ),
    )

    output = capsys.readouterr().out.strip()
    payload = json.loads(output)
    assert payload["workerId"] == "worker-a"
    assert payload["token"] == "***"
    assert payload["nested"]["password"] == "***"
    assert payload["url"] == "https://example.tos.com/video.mp4"
    assert "secret-token" not in output
    assert "X-Tos-Signature" not in output
