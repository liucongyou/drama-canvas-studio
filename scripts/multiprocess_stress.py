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

    contexts = []
    tasks = []
    try:
        control = tenant_registry._connect()
        try:
            schema_migrations.apply_migrations(control, "control")
        finally:
            control.close()
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
        # The owner tenant exists only to exercise control-path initialization.
        # Load accounting must cover exactly the requested synthetic tenants.
        return suffix, data_root, contexts, contexts[1:], tasks
    except Exception:
        _drop_schemas(args.url, suffix, contexts)
        data_root.cleanup()
        raise


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
        # Six child processes plus the parent share a local PostgreSQL test
        # cluster capped at max_connections=100. Keep each process bounded so a
        # long soak cannot exhaust the server even after repeated worker kills.
        "CANVAS_PG_POOL_MAX": "4",
        "CANVAS_PG_POOL_IDLE_MAX": "2",
        "CANVAS_PG_POOL_WAIT_TIMEOUT_MS": "15000",
    }


def _status_counts(contexts):
    import canvas_store
    import pg_compat
    import tenant_context

    counts = Counter()
    connection = canvas_store._connect()
    try:
        for context in contexts:
            schema = pg_compat.schema_for_path(context.db_path, "canvas")
            if not schema.replace("_", "").isalnum():
                raise ValueError(f"unsafe tenant schema: {schema}")
            rows = connection.execute(
                f'SELECT status,COUNT(*) FROM "{schema}".generation_tasks '
                "GROUP BY status"
            ).fetchall()
            for status, count in rows:
                counts[str(status)] += int(count or 0)
    finally:
        connection.close()
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
    status_counts = report.get("statusCounts") or {}
    task_count = int(report.get("tasks") or -1)
    succeeded = int(status_counts.get("succeeded") or 0)
    interrupted = int(status_counts.get("interrupted") or 0)
    failed = int(status_counts.get("failed") or 0)
    active = sum(
        int(status_counts.get(status) or 0)
        for status in ("queued", "submitting", "running")
    )
    kill_count = int(report.get("killCount") or 0)
    worker_concurrency = max(1, int(report.get("workerConcurrency") or 1))
    allowed_interrupted = kill_count * worker_concurrency if kill_count else 0
    return bool(
        report.get("ok")
        and int(report.get("maxSubmitsPerTask") or 0) <= 1
        and all(
            item.get("ok")
            for item in (report.get("readiness") or {}).values()
        )
        and failed == 0
        and active == 0
        and succeeded + interrupted == task_count
        and interrupted <= allowed_interrupted
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
        pg_compat.schema_for_path(Path("data") / "canvas.db", "canvas"),
    }
    for context in contexts:
        schemas.add(pg_compat.schema_for_path(context.db_path, "canvas"))
    with psycopg.connect(url, autocommit=True) as connection:
        for schema_name in schemas:
            connection.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema_name)
                )
            )


def run_smoke(args) -> dict:
    suffix, data_root, contexts, load_contexts, initial_tasks = (
        _prepare_environment(args)
    )
    base_env = os.environ.copy()
    child_env = _child_env(base_env, suffix, data_root.name)
    output = Path(args.output) if args.output else (
        ROOT / "artifacts" / f"multiprocess-{uuid.uuid4().hex[:8]}"
    )
    output.mkdir(parents=True, exist_ok=True)
    call_log = output / "provider-calls.jsonl"
    connection_log = output / "connection-samples.jsonl"
    connection_threshold = max(
        1,
        int(args.connection_warning_threshold),
    )
    connection_sample_seconds = max(
        1.0,
        float(args.connection_sample_seconds),
    )
    peak_connections = 0
    connection_errors = 0
    last_connection_sample = 0.0
    last_connection_snapshot = {}
    processes = []
    worker_processes = []
    api_ports = [_free_port(), _free_port()]
    monitor_connection = None

    def sample_connections(force: bool = False) -> dict:
        nonlocal last_connection_sample
        nonlocal last_connection_snapshot
        nonlocal peak_connections
        nonlocal connection_errors
        now = time.monotonic()
        if (
            not force
            and now - last_connection_sample < connection_sample_seconds
        ):
            return last_connection_snapshot
        last_connection_sample = now
        try:
            snapshot = _connection_snapshot(monitor_connection)
            peak_connections = max(
                peak_connections,
                int(snapshot.get("total") or 0),
            )
            last_connection_snapshot = snapshot
            _append_connection_sample(connection_log, snapshot)
        except Exception as exc:
            connection_errors += 1
            last_connection_snapshot = {
                "at": _now_iso(),
                "error": str(exc),
            }
            _append_connection_sample(
                connection_log,
                last_connection_snapshot,
            )
        return last_connection_snapshot

    try:
        monitor_connection = _open_connection_monitor(args.url)
        sample_connections(force=True)
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
        api_start_deadline = time.monotonic() + args.timeout_seconds
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
        kill_count = 0
        replacement = None
        next_kill_at = (
            started + args.kill_interval_seconds
            if args.kill_interval_seconds > 0
            else started
        )
        last_kill_submit_count = -1
        expected = args.tenants * args.tasks_per_tenant
        batch_index = 0
        while time.monotonic() < deadline:
            sample_connections()
            counts = _status_counts(load_contexts)
            call_counts = _call_counts(call_log)
            submits = sum(
                count
                for (event, _task), count in call_counts.items()
                if event == "submit"
            )
            kill_due = (
                kill_count == 0
                or (
                    args.kill_interval_seconds > 0
                    and time.monotonic() >= next_kill_at
                )
            )
            submit_pid = (
                _latest_submit_worker_pid(call_log)
                if kill_due
                else None
            )
            submission_owner_available = any(
                process.pid == submit_pid
                for process in worker_processes
            )
            if (
                kill_due
                and submission_owner_available
                and submits >= 1
                and submits > last_kill_submit_count
            ):
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
                kill_count += 1
                last_kill_submit_count = submits
                replacement = _spawn_worker(
                    args,
                    child_env,
                    call_log,
                    output,
                    "worker-replacement",
                )
                worker_processes.append(replacement)
                next_kill_at = (
                    time.monotonic() + args.kill_interval_seconds
                    if args.kill_interval_seconds > 0
                    else float("inf")
                )
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
                    load_contexts,
                    batch_index,
                    args.tasks_per_tenant,
                )
                expected += args.tenants * args.tasks_per_tenant
            if args.duration_seconds > 0:
                if time.monotonic() >= run_deadline and terminal >= expected:
                    break
            elif (
                kill_count > 0
                and terminal >= expected
            ):
                break
            # One hundred tenant schemas are sampled every two seconds. Polling
            # faster creates enough metadata load on a 2-vCPU host to starve the
            # Workers that the soak is supposed to measure.
            time.sleep(2.0)
        sample_connections(force=True)
        readiness = {}
        for index, port in enumerate(api_ports):
            readiness[f"api{index + 1}"] = _read_json(
                f"http://127.0.0.1:{port}/readyz"
            )
        counts = _status_counts(load_contexts)
        call_counts = _call_counts(call_log)
        submit_by_task = {
            task_id: count
            for (event, task_id), count in call_counts.items()
            if event == "submit"
        }
        max_submits = max(submit_by_task.values(), default=0)
        active_count = sum(
            int(counts.get(status) or 0)
            for status in ("queued", "submitting", "running")
        )
        ok = (
            counts["failed"] == 0
            and active_count == 0
            and counts["succeeded"] + counts["interrupted"] == expected
            and counts["interrupted"] <= (
                kill_count * max(1, args.worker_concurrency)
            )
            and all(item.get("ok") for item in readiness.values())
            and max_submits <= 1
            and connection_errors == 0
            and peak_connections < connection_threshold
        )
        report = {
            "ok": ok,
            "tenants": args.tenants,
            "tasks": expected,
            "batches": batch_index + 1,
            "durationTargetSeconds": args.duration_seconds,
            "workers": args.workers,
            "workerConcurrency": args.worker_concurrency,
            "apis": len(api_ports),
            "killedWorker": kill_count > 0,
            "killCount": kill_count,
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
            "connections": {
                "threshold": connection_threshold,
                "peak": peak_connections,
                "sampleSeconds": connection_sample_seconds,
                "errors": connection_errors,
                "samples": str(connection_log),
                "last": last_connection_snapshot,
            },
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
        if monitor_connection is not None:
            monitor_connection.close()
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
    import urllib.error

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
            payload = json.loads(body)
            if isinstance(payload, dict):
                payload.setdefault("httpStatus", exc.code)
                return payload
        except Exception:
            pass
        return {"ok": False, "httpStatus": exc.code, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _open_connection_monitor(url):
    import psycopg

    return psycopg.connect(
        url,
        autocommit=True,
        connect_timeout=10,
        application_name="drama_canvas_soak_monitor",
    )


def _connection_snapshot(connection):
    row = connection.execute(
        """
        SELECT
          current_setting('max_connections')::int,
          COUNT(*),
          COUNT(*) FILTER (WHERE state = 'active'),
          COUNT(*) FILTER (WHERE state = 'idle'),
          COUNT(*) FILTER (WHERE state = 'idle in transaction')
        FROM pg_stat_activity
        """
    ).fetchone()
    states = connection.execute(
        """
        SELECT COALESCE(state, 'unknown'), COUNT(*)
        FROM pg_stat_activity
        GROUP BY 1
        ORDER BY 1
        """
    ).fetchall()
    applications = connection.execute(
        """
        SELECT COALESCE(application_name, ''), COUNT(*)
        FROM pg_stat_activity
        GROUP BY 1
        ORDER BY 1
        """
    ).fetchall()
    return {
        "at": _now_iso(),
        "maxConnections": int(row[0] or 0),
        "total": int(row[1] or 0),
        "active": int(row[2] or 0),
        "idle": int(row[3] or 0),
        "idleInTransaction": int(row[4] or 0),
        "states": {str(key): int(value) for key, value in states},
        "applications": {
            str(key or "unknown"): int(value)
            for key, value in applications
        },
    }


def _append_connection_sample(path: Path, snapshot: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, ensure_ascii=False) + "\n")


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
    parser.add_argument("--kill-interval-seconds", type=float, default=0.0)
    parser.add_argument(
        "--connection-warning-threshold",
        type=int,
        default=75,
    )
    parser.add_argument("--connection-sample-seconds", type=float, default=5.0)
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
