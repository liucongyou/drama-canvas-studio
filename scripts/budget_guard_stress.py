from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _child(args) -> int:
    import canvas_store
    import generation_budget_guard
    import tenant_context

    context = tenant_context.make_context(args.tenant_id, args.tenant_slug)
    accepted = False
    amount = 0.0
    with tenant_context.use_context(context):
        with generation_budget_guard.acquire(timeout_seconds=20):
            snapshot = canvas_store.generation_cost_total(
                "2000-01-01T00:00:00Z"
            )
            if snapshot["amount"] + args.unit_cost <= args.limit + 1e-9:
                canvas_store.create_generation_task(
                    args.project_id,
                    f"budget-node-{args.worker_index}",
                    title=f"budget worker {args.worker_index}",
                    kind="video",
                    provider="mock",
                    model="mock-video",
                    estimated_cost=args.unit_cost,
                    cost_currency="CNY",
                )
                accepted = True
                amount = snapshot["amount"] + args.unit_cost
    print(json.dumps({
        "worker": args.worker_index,
        "accepted": accepted,
        "amount": amount,
    }, ensure_ascii=False))
    return 0


def _drop_schemas(url, schema_names):
    import psycopg
    from psycopg import sql

    with psycopg.connect(url, autocommit=True) as connection:
        for schema_name in schema_names:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema_name)
                )
            )


def run(args) -> dict:
    suffix = "budget_stress_" + uuid.uuid4().hex[:10]
    data_root = tempfile.TemporaryDirectory(prefix="budget-guard-")
    os.environ.update({
        "CANVAS_DB_BACKEND": "postgres",
        "CANVAS_DATABASE_URL": args.url,
        "DATABASE_URL": args.url,
        "CANVAS_MULTI_TENANT": "1",
        "CANVAS_TENANT_DATA_ROOT": data_root.name,
        "CANVAS_PG_SCHEMA_SUFFIX": suffix,
        "CANVAS_SCHEMA_MIGRATIONS_REQUIRED": "1",
    })
    import canvas_store
    import pg_compat
    import schema_migrations
    import tenant_context
    import tenant_registry

    control = tenant_registry._connect()
    try:
        schema_migrations.apply_migrations(control, "control")
    finally:
        control.close()
    context = tenant_context.make_context("budget-load", "budget-load")
    tenant_context.ensure_context_dirs(context)
    with tenant_context.use_context(context):
        connection = canvas_store._connect()
        try:
            schema_migrations.apply_migrations(connection, "canvas")
        finally:
            connection.close()
        project_id = canvas_store.create_project("budget guard pressure")

    schema_names = {
        pg_compat.schema_for_path(context.db_path, "canvas"),
        pg_compat.schema_for_path(context.data_root / "control.db", "control"),
    }
    started = time.monotonic()
    processes = []
    try:
        for index in range(args.processes):
            processes.append(subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    str(Path(__file__).resolve()),
                    "--role",
                    "child",
                    "--url",
                    args.url,
                    "--project-id",
                    project_id,
                    "--tenant-id",
                    context.tenant_id,
                    "--tenant-slug",
                    context.slug,
                    "--worker-index",
                    str(index),
                    "--limit",
                    str(args.limit),
                    "--unit-cost",
                    str(args.unit_cost),
                ],
                cwd=ROOT,
                env=os.environ.copy(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            ))
        results = []
        errors = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=args.timeout_seconds)
            if process.returncode != 0:
                errors.append(stderr.strip())
                continue
            for line in reversed(stdout.strip().splitlines()):
                try:
                    results.append(json.loads(line))
                    break
                except ValueError:
                    continue
        with tenant_context.use_context(context):
            snapshot = canvas_store.generation_cost_total(
                "2000-01-01T00:00:00Z"
            )
            tasks = canvas_store.list_generation_tasks(project_id, 500)
        accepted = sum(bool(item.get("accepted")) for item in results)
        expected = min(args.processes, int(args.limit / args.unit_cost))
        report = {
            "ok": (
                not errors
                and accepted == expected
                and len(tasks) == expected
                and abs(snapshot["amount"] - accepted * args.unit_cost) < 1e-9
            ),
            "processes": args.processes,
            "limit": args.limit,
            "unitCost": args.unit_cost,
            "expectedAccepted": expected,
            "accepted": accepted,
            "taskCount": len(tasks),
            "amount": snapshot["amount"],
            "reserved": snapshot["reserved"],
            "committed": snapshot["committed"],
            "errors": errors,
            "durationSeconds": round(time.monotonic() - started, 3),
        }
        if args.report:
            path = Path(args.report)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return report
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        _drop_schemas(args.url, schema_names)
        data_root.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("parent", "child"), default="parent")
    parser.add_argument("--url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--processes", type=int, default=24)
    parser.add_argument("--limit", type=float, default=8)
    parser.add_argument("--unit-cost", type=float, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--report")
    parser.add_argument("--project-id", default="")
    parser.add_argument("--tenant-id", default="")
    parser.add_argument("--tenant-slug", default="")
    parser.add_argument("--worker-index", type=int, default=0)
    args = parser.parse_args()
    if args.role == "child":
        return _child(args)
    if not args.url:
        parser.error("--url or DATABASE_URL is required")
    report = run(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
