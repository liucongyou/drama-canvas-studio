from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

import canvas_store
import generation_queue
import pg_compat
import schema_migrations
import tenant_context
import tenant_registry


_CACHE_LOCK = threading.Lock()
_MIGRATION_CACHE = {"at": 0.0, "value": None}
_SNAPSHOT_CACHE = {"at": 0.0, "value": None}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"


def _contexts():
    return [
        tenant_context.make_context(tenant["id"], tenant["slug"])
        for tenant in tenant_registry.list_tenants(include_disabled=False)
    ]


def _database_check() -> dict:
    connection = canvas_store._connect()
    try:
        value = connection.execute("SELECT 1").fetchone()[0]
        connection.rollback()
    finally:
        connection.close()
    return {"ok": int(value or 0) == 1}


def _migration_check() -> dict:
    with _CACHE_LOCK:
        cached = _MIGRATION_CACHE["value"]
        if cached is not None and time.monotonic() - _MIGRATION_CACHE["at"] < 5:
            return dict(cached)
    contexts = _contexts()
    schemas = {
        pg_compat.schema_for_path(context.db_path, "canvas"): "canvas"
        for context in contexts
    }
    if not schemas:
        schemas[
            pg_compat.schema_for_path(
                tenant_context.current_db_path(),
                "canvas",
            )
        ] = "canvas"
    control_schema = pg_compat.schema_for_path("control.db", "control")
    schemas[control_schema] = "control"
    control = tenant_registry._connect()
    try:
        check = schema_migrations.verify_schema_set(control, schemas)
    finally:
        control.close()
    failures = [
        {"tenant": schema, **item}
        for schema, item in check.get("schemas", {}).items()
        if not item.get("ok")
    ]
    value = {
        "ok": bool(check.get("ok")) and not failures,
        "failures": failures,
    }
    with _CACHE_LOCK:
        _MIGRATION_CACHE["at"] = time.monotonic()
        _MIGRATION_CACHE["value"] = dict(value)
    return value


def _worker_fleet_check() -> dict:
    workers = generation_queue.worker_heartbeats(active_within_seconds=30)
    return {
        "ok": bool(workers),
        "activeWorkers": len(workers),
        "activeTasks": sum(
            int(worker.get("activeTasks") or 0)
            for worker in workers
        ),
    }


def readiness(
    role: str,
    *,
    release_commit: str,
    execution_mode: str = "inline",
) -> dict:
    checks = {}
    try:
        checks["database"] = _database_check()
    except Exception as exc:
        checks["database"] = {"ok": False, "error": str(exc)}
    try:
        migrations = _migration_check()
        checks["migrations"] = migrations
    except Exception as exc:
        checks["migrations"] = {"ok": False, "error": str(exc)}
    if role == "api" and execution_mode == "worker":
        try:
            checks["workerFleet"] = _worker_fleet_check()
        except Exception as exc:
            checks["workerFleet"] = {"ok": False, "error": str(exc)}
    return {
        "ok": all(bool(item.get("ok")) for item in checks.values()),
        "role": role,
        "releaseCommit": release_commit,
        "executionMode": execution_mode,
        "checkedAt": _now_iso(),
        "checks": checks,
        "databasePool": pg_compat.pool_stats(),
    }


def _tenant_task_metrics() -> list[dict]:
    values = []
    for context in _contexts():
        with tenant_context.use_context(context):
            connection = canvas_store._connect()
            try:
                rows = connection.execute(
                    "SELECT status,COUNT(*) FROM generation_tasks "
                    "GROUP BY status"
                ).fetchall()
                oldest = connection.execute(
                    "SELECT MIN(created_at) FROM generation_tasks "
                    "WHERE status='queued'"
                ).fetchone()[0]
            finally:
                connection.close()
        for status, count in rows:
            values.append({
                "tenant": context.slug,
                "status": str(status or ""),
                "count": int(count or 0),
            })
        if oldest:
            values.append({
                "tenant": context.slug,
                "oldestQueuedAt": str(oldest),
            })
    return values


def metrics_snapshot() -> dict:
    with _CACHE_LOCK:
        cached = _SNAPSHOT_CACHE["value"]
        if cached is not None and time.monotonic() - _SNAPSHOT_CACHE["at"] < 2:
            return dict(cached)
    snapshot = {
        "databasePool": pg_compat.pool_stats(),
        "workerHeartbeats": generation_queue.worker_heartbeats(
            active_within_seconds=60
        ),
        "admissions": generation_queue.admission_metrics(),
        "tenantTasks": _tenant_task_metrics(),
    }
    with _CACHE_LOCK:
        _SNAPSHOT_CACHE["at"] = time.monotonic()
        _SNAPSHOT_CACHE["value"] = snapshot
    return snapshot


def render_prometheus(release_commit: str) -> str:
    snapshot = metrics_snapshot()
    lines = [
        "# HELP canvas_release_info Release metadata.",
        "# TYPE canvas_release_info gauge",
        f'canvas_release_info{{commit="{_escape(release_commit)}"}} 1',
        "# HELP canvas_pg_pool_connections PostgreSQL pool connections.",
        "# TYPE canvas_pg_pool_connections gauge",
    ]
    pool = snapshot["databasePool"]
    for key in ("active", "idle", "physical", "waiting"):
        lines.append(
            f'canvas_pg_pool_connections{{state="{key}"}} '
            f'{int(pool.get(key) or 0)}'
        )
    lines.extend([
        "# HELP canvas_generation_admissions Active distributed admissions.",
        "# TYPE canvas_generation_admissions gauge",
        (
            "canvas_generation_admissions "
            f'{int(snapshot["admissions"].get("activeAdmissions") or 0)}'
        ),
        "# HELP canvas_worker_heartbeats Active worker heartbeats.",
        "# TYPE canvas_worker_heartbeats gauge",
        f'canvas_worker_heartbeats {len(snapshot["workerHeartbeats"])}',
        "# HELP canvas_generation_tasks Generation tasks by tenant and status.",
        "# TYPE canvas_generation_tasks gauge",
    ])
    for item in snapshot["tenantTasks"]:
        if "status" not in item:
            continue
        lines.append(
            'canvas_generation_tasks'
            f'{{tenant="{_escape(item["tenant"])}",'
            f'status="{_escape(item["status"])}"}} '
            f'{int(item["count"])}'
        )
    return "\n".join(lines) + "\n"


def _escape(value) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace('"', '\\"')
    )
