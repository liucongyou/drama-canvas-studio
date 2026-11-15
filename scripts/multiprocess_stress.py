from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _worker_child(args) -> int:
    import canvas_store
    import generation_worker
    import tenant_context
    import server

    call_log = Path(args.call_log)
    delay = max(0.0, float(args.provider_delay))

    def mock_execute(
        task_id,
        tenant_id,
        tenant_slug,
        claim_token,
        task=None,
    ):
        context = tenant_context.make_context(tenant_id, tenant_slug)
        with tenant_context.use_context(context):
            current = canvas_store.get_generation_task(task_id) or {}
            provider_task_id = str(
                current.get("provider_task_id") or ""
            ).strip()
            event = "resume" if provider_task_id else "submit"
            if not provider_task_id:
                canvas_store.update_generation_task(
                    task_id,
                    status="running",
                    queue_state="provider_running",
                    provider_task_id=f"mock-{task_id}",
                    phase="mock provider accepted",
                )
            with call_log.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "event": event,
                            "taskId": task_id,
                            "workerPid": os.getpid(),
                            "at": _now_iso(),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            if delay:
                time.sleep(delay)
            canvas_store.finish_generation_task(
                task_id,
                status="succeeded",
                phase="mock completed",
                delivery_status="ready",
                local_result_url=f"/uploads/mock-{task_id}.mp4",
                provider_result_url=f"https://mock.invalid/{task_id}.mp4",
            )

    server.execute_admitted_generation_task = mock_execute
    worker = generation_worker.GenerationWorker(
        worker_id=args.worker_id,
        concurrency=args.worker_concurrency,
        lease_seconds=args.lease_seconds,
        admission_seconds=max(args.lease_seconds, 30),
        poll_seconds=0.2,
    )

    def stop(_signum, _frame):
        worker.request_stop()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    worker.run_forever()
    return 0


def _create_task(canvas_store, context, index):
    import tenant_context

    with tenant_context.use_context(context):
        project_id = canvas_store.create_project(
            f"multiprocess-{context.slug}-{index}"
        )
        node_id = f"node-{context.slug}-{index}"
        canvas_store.insert_project_nodes(
            project_id,
            [
                {
                    "id": node_id,
                    "type": "video",
                    "title": node_id,
                    "position": {"x": 0, "y": 0},
                    "metadata": {"episodeId": "EP001", "status": "idle"},
                }
            ],
            episode_id="EP001",
        )
        return canvas_store.create_generation_task(
            project_id,
            node_id,
            kind="video",
            episode_id="EP001",
            provider="mock",
            model="mock-video",
        )


def _create_task_batch(canvas_store, contexts, batch_index, tasks_per_tenant):
    tasks = []
    for context in contexts:
        for task_index in range(tasks_per_tenant):
            tasks.append(
                _create_task(
                    canvas_store,
                    context,
                    batch_index * tasks_per_tenant + task_index,
                )
            )
    return tasks


def _prepare_environment(args):
    suffix = "mp_" + uuid.uuid4().hex[:10]
    data_root = tempfile.TemporaryDirectory(prefix="drama-mp-")
    os.environ.update({
        "CANVAS_DB_BACKEND": "postgres",
        "CANVAS_DATABASE_URL": args.url,
        "DATABASE_URL": args.url,
        "CANVAS_MULTI_TENANT": "1",
        "CANVAS_TENANT_DATA_ROOT": data_root.name,
        "CANVAS_PG_SCHEMA_SUFFIX": suffix,
        "CANVAS_SCHEMA_MIGRATIONS_REQUIRED": "1",
        "CANVAS_GENERATION_EXECUTION_MODE": "worker",
    })
    import canvas_store
    import schema_migrations
    import tenant_context
    import tenant_registry

    control = tenant_registry._connect()
    try:
        schema_migrations.apply_migrations(control, "control")
    finally:
        control.close()
    contexts = []
    tasks = []
    owner = tenant_registry.create_tenant(
        "owner",
        name="Owner",
        tenant_id="owner",
    )
    owner_context = tenant_context.make_context(
        owner["id"],
        owner["slug"],
    )
    tenant_context.ensure_context_dirs(owner_context)
    (owner_context.data_root / ".legacy-migrated").write_text(
        "isolated harness owner\n",
        encoding="utf-8",
    )
    with tenant_context.use_context(owner_context):
        connection = canvas_store._connect()
        try:
            schema_migrations.apply_migrations(connection, "canvas")
        finally:
            connection.close()
    contexts.append(owner_context)
    for tenant_index in range(args.tenants):
        slug = f"mp-{tenant_index}-{uuid.uuid4().hex[:6]}"
        tenant = tenant_registry.create_tenant(
            slug,
            tenant_id=slug,
        )
        context = tenant_context.make_context(
            tenant["id"],
            tenant["slug"],
        )
        tenant_context.ensure_context_dirs(context)
        contexts.append(context)
        with tenant_context.use_context(context):
            connection = canvas_store._connect()
            try:
                schema_migrations.apply_migrations(connection, "canvas")
            finally:
                connection.close()
        for task_index in range(args.tasks_per_tenant):
            tasks.append(_create_task(canvas_store, context, task_index))
    return suffix, data_root, contexts, tasks


def _child_env(base_env, suffix, data_root):
    return {
        **base_env,
        "CANVAS_DB_BACKEND": "postgres",
        "CANVAS_DATABASE_URL": base_env["CANVAS_DATABASE_URL"],
        "DATABASE_URL": base_env["DATABASE_URL"],
        "CANVAS_MULTI_TENANT": "1",
        "CANVAS_TENANT_DATA_ROOT": data_root,
        "CANVAS_PG_SCHEMA_SUFFIX": suffix,
        "CANVAS_SCHEMA_MIGRATIONS_REQUIRED": "1",
        "CANVAS_GENERATION_EXECUTION_MODE": "worker",
    }


def _status_counts(contexts):
    import canvas_store
    import tenant_context

    counts = Counter()
    for context in contexts:
        with tenant_context.use_context(context):
            connection = canvas_store._connect()
            try:
                rows = connection.execute(
                    "SELECT status,COUNT(*) FROM generation_tasks "
                    "GROUP BY status"
                ).fetchall()
            finally:
                connection.close()
        for status, count in rows:
            counts[str(status)] += int(count or 0)
    return counts


def _call_counts(path: Path):
    counts = Counter()
    if not path.exists():
        return counts
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        counts[(str(item.get("event") or ""), str(item.get("taskId") or ""))] += 1
    return counts


def _latest_submit_worker_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    worker_pid = None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get("event") == "submit":
            worker_pid = int(item.get("workerPid") or 0) or None
    return worker_pid


def validate_smoke_report(report: dict) -> bool:
    return bool(
        report.get("ok")
        and int(report.get("maxSubmitsPerTask") or 0) <= 1
        and all(
            item.get("ok")
            for item in (report.get("readiness") or {}).values()
        )
        and int((report.get("statusCounts") or {}).get("succeeded") or 0)
        == int(report.get("tasks") or -1)
    )


def _drop_schemas(url, suffix, contexts):
    import pg_compat
    import psycopg
    from psycopg import sql

    stats = pg_compat.pool_stats()
    pg_compat.configure_pool(
        max_connections=stats["max"],
        wait_timeout_ms=stats["waitTimeoutMs"],
        schema_idle_max=stats["schemaIdleMax"],
    )
    schemas = {
        pg_compat.schema_for_path("control.db", "control"),
    }
    for context in contexts:
        schemas.add(pg_compat.schema_for_path(context.db_path, "canvas"))
    with psycopg.connect(url) as connection:
        for schema_name in schemas:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema_name)
                )
            )
def run_smoke(args) -> dict:
    suffix, data_root, contexts, initial_tasks = _prepare_environment(args)
    base_env = os.environ.copy()
    child_env = _child_env(base_env, suffix, data_root.name)
    output = Path(args.output) if args.output else (
        ROOT / "artifacts" / f"multiprocess-{uuid.uuid4().hex[:8]}"
    )
    output.mkdir(parents=True, exist_ok=True)
    call_log = output / "provider-calls.jsonl"
    processes = []
    worker_processes = []
    api_ports = [_free_port(), _free_port()]
    try:
        for index, port in enumerate(api_ports):
            env = {
                **child_env,
                "CANVAS_PORT": str(port),
                "CANVAS_API_HOST": "127.0.0.1",
                "CANVAS_SAFE_LOCAL_READONLY": "1",
                "CANVAS_SKIP_STARTUP_BACKFILLS": "1",
            }
            log = (output / f"api-{index + 1}.log").open("w", encoding="utf-8")
            process = subprocess.Popen(
                [sys.executable, "-u", "server.py"],
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            processes.append((process, log))
        api_start_deadline = time.monotonic() + min(
            args.timeout_seconds,
            60,
        )
        while time.monotonic() < api_start_deadline:
            health = [
                _read_json(f"http://127.0.0.1:{port}/healthz")
                for port in api_ports
            ]
            if all(item.get("ok") for item in health):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("API 进程未在超时时间内完成启动")
        for index in range(args.workers):
            worker_processes.append(
                _spawn_worker(
                    args,
                    child_env,
                    call_log,
                    output,
                    f"worker-{index + 1}",
                )
            )
        started = time.monotonic()
        deadline = started + args.timeout_seconds
        run_deadline = (
            started + args.duration_seconds
            if args.duration_seconds > 0
            else deadline
        )
        killed = False
        replacement = None
        expected = args.tenants * args.tasks_per_tenant
        batch_index = 0
        while time.monotonic() < deadline:
            counts = _status_counts(contexts)
            call_counts = _call_counts(call_log)
            submits = sum(
                count
                for (event, _task), count in call_counts.items()
                if event == "submit"
            )
            if not killed and submits >= 1:
                submit_pid = _latest_submit_worker_pid(call_log)
                victim_index = next(
                    (
                        index
                        for index, process in enumerate(worker_processes)
                        if process.pid == submit_pid
                    ),
                    0,
                )
                victim = worker_processes.pop(victim_index)
                _kill_process(victim)
                killed = True
                replacement = _spawn_worker(
                    args,
                    child_env,
                    call_log,
                    output,
                    "worker-replacement",
                )
                worker_processes.append(replacement)
            terminal = (
                counts["succeeded"]
                + counts["failed"]
                + counts["interrupted"]
            )
            if (
                args.duration_seconds > 0
                and time.monotonic() < run_deadline
                and terminal >= expected
            ):
                import canvas_store

                batch_index += 1
                _create_task_batch(
                    canvas_store,
                    contexts,
                    batch_index,
                    args.tasks_per_tenant,
                )
                expected += args.tenants * args.tasks_per_tenant
            if args.duration_seconds > 0:
                if time.monotonic() >= run_deadline and terminal >= expected:
                    break
            elif (
                killed
                and counts["succeeded"] >= expected
            ):
                break
            time.sleep(0.25)
        readiness = {}
        for index, port in enumerate(api_ports):
            readiness[f"api{index + 1}"] = _read_json(
                f"http://127.0.0.1:{port}/readyz"
            )
        counts = _status_counts(contexts)
        call_counts = _call_counts(call_log)
        submit_by_task = {
            task_id: count
            for (event, task_id), count in call_counts.items()
            if event == "submit"
        }
        max_submits = max(submit_by_task.values(), default=0)
        ok = (
            counts["succeeded"] == expected
            and all(item.get("ok") for item in readiness.values())
            and max_submits <= 1
        )
        report = {
            "ok": ok,
            "tenants": args.tenants,
            "tasks": expected,
            "batches": batch_index + 1,
            "durationTargetSeconds": args.duration_seconds,
            "workers": args.workers,
            "apis": len(api_ports),
            "killedWorker": killed,
            "replacementStarted": replacement is not None,
            "statusCounts": dict(counts),
            "readiness": readiness,
            "submitCalls": sum(submit_by_task.values()),
            "resumeCalls": sum(
                count
                for (event, _task), count in call_counts.items()
                if event == "resume"
            ),
            "maxSubmitsPerTask": max_submits,
            "durationSeconds": round(time.monotonic() - started, 3),
            "artifactDirectory": str(output),
        }
        report["ok"] = validate_smoke_report(report)
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return report
    finally:
        for process in worker_processes:
            _stop_process(process)
        for process, log in processes:
            _stop_process(process)
            log.close()
        _drop_schemas(args.url, suffix, contexts)
        data_root.cleanup()


def _spawn_worker(args, env, call_log, output, worker_id):
    log = (output / f"{worker_id}.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            str(Path(__file__).resolve()),
            "--role",
            "worker-child",
            "--worker-id",
            worker_id,
            "--worker-concurrency",
            str(args.worker_concurrency),
            "--lease-seconds",
            str(args.lease_seconds),
            "--provider-delay",
            str(args.provider_delay),
            "--call-log",
            str(call_log),
        ],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    process._codex_log = log
    return process


def _stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    log = getattr(process, "_codex_log", None)
    if log:
        log.close()


def _kill_process(process):
    if process.poll() is None:
        process.kill()
        process.wait(timeout=5)
    log = getattr(process, "_codex_log", None)
    if log:
        log.close()


def _read_json(url):
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--role",
        choices=("parent", "worker-child"),
        default="parent",
    )
    parser.add_argument("--url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--tenants", type=int, default=4)
    parser.add_argument("--tasks-per-tenant", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--worker-concurrency", type=int, default=2)
    parser.add_argument("--lease-seconds", type=int, default=15)
    parser.add_argument("--provider-delay", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--duration-seconds", type=float, default=0.0)
    parser.add_argument("--output")
    parser.add_argument("--worker-id", default="")
    parser.add_argument("--call-log", default="")
    args = parser.parse_args()
    if args.role == "worker-child":
        return _worker_child(args)
    if not args.url:
        parser.error("--url or DATABASE_URL is required")
    report = run_smoke(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
