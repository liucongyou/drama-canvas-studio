from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import canvas_store
import pg_compat
import server
import tenant_context


MAX_CONCURRENCY = 32
REQUEST_TIMEOUT_SECONDS = 20
MAX_DURATION_SECONDS = 600


def _context(prefix):
    root = Path(tempfile.gettempdir()) / f"{prefix}-{uuid.uuid4().hex}"
    return tenant_context.TenantContext(
        tenant_id=prefix,
        slug=prefix,
        data_root=root,
        db_path=root / "canvas.db",
        upload_dir=root / "uploads",
        config_path=root / "config.local.json",
        log_dir=root / "logs",
    )


def _nodes(count, prefix):
    kinds = ("image", "audio", "video")
    return [
        {
            "id": f"{prefix}-node-{index}",
            "type": kinds[index % len(kinds)],
            "title": f"压力节点 {index}",
            "position": {"x": index * 12, "y": index * 8},
            "metadata": {
                "episodeId": "EP001",
                "prompt": f"stress prompt {index}",
                "status": "done",
                "params": {
                    "mediaVersions": [
                        {
                            "versionId": f"{kinds[index % len(kinds)]}:v{index}",
                            "url": f"/uploads/stress-{index}.bin",
                        }
                    ],
                    "activeVersionId": f"{kinds[index % len(kinds)]}:v{index}",
                },
            },
        }
        for index in range(count)
    ]


def _project_payload(count, prefix):
    nodes = _nodes(count, prefix)
    return {
        "nodes": nodes,
        "connections": [],
        "episodeImports": [
            {
                "id": "EP001",
                "title": "EP001",
                "nodeIds": [node["id"] for node in nodes],
                "videoNodeIds": [
                    node["id"] for node in nodes
                    if node["type"] == "video"
                ],
                "connectionIds": [],
            }
        ],
    }


def _p95(values):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]


def run(database_url):
    started = time.monotonic()
    os.environ["CANVAS_DB_BACKEND"] = "postgres"
    os.environ["CANVAS_DATABASE_URL"] = database_url
    canvas_store._SCHEMA_READY_PATHS.clear()
    context_a = _context("stress_a")
    context_b = _context("stress_b")
    report = {
        "database": database_url.rsplit("/", 1)[-1],
        "limits": {
            "maxConcurrency": MAX_CONCURRENCY,
            "requestTimeoutSeconds": REQUEST_TIMEOUT_SECONDS,
            "maxDurationSeconds": MAX_DURATION_SECONDS,
        },
        "counts": {},
        "latenciesMs": {},
        "errors": [],
        "checks": {},
    }
    try:
        with tenant_context.use_context(context_a):
            project_a = canvas_store.create_project(
                "Stress A",
                payload=_project_payload(60, "a"),
            )
            project_b = canvas_store.create_project(
                "Stress B",
                payload=_project_payload(60, "b"),
            )
            node_ids = [f"a-node-{index}" for index in range(60)]

            latencies = []

            def patch(index):
                with tenant_context.use_context(context_a):
                    begin = time.perf_counter()
                    canvas_store.update_project_node(
                        project_a,
                        node_ids[index],
                        episode_id="EP001",
                        patch_node={
                            "metadata": {
                                f"stress{index}": f"ok-{index}",
                            }
                        },
                    )
                    return (time.perf_counter() - begin) * 1000

            with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
                latencies.extend(pool.map(patch, range(60)))

            task_ids = [
                canvas_store.create_generation_task(
                    project_a,
                    node_ids[index],
                    kind=("video" if index % 2 else "image"),
                    provider="stress-provider",
                    model="stress-model",
                )["id"]
                for index in range(20)
            ]

            def progress(index):
                with tenant_context.use_context(context_a):
                    canvas_store.update_generation_task(
                        task_ids[index],
                        status="running",
                        phase="生成中",
                        progress=10 + index,
                        provider_task_id=f"provider-{index}",
                    )
                    return 1

            with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
                list(pool.map(progress, range(20)))

            for index in range(10):
                canvas_store.commit_generation_terminal(
                    project_a,
                    node_ids[index],
                    {
                        "status": "done",
                        "content": f"/uploads/stress-result-{index}.bin",
                    },
                    task_id=task_ids[index],
                    task_updates={
                        "status": "succeeded",
                        "phase": "已完成",
                        "progress": 100,
                        "delivery_status": "ready",
                        "local_result_url": f"/uploads/stress-result-{index}.bin",
                    },
                )

            duplicate_node = node_ids[30]
            barrier = threading.Barrier(MAX_CONCURRENCY + 1)
            duplicate_results = []
            duplicate_errors = []

            def duplicate_submit(_index):
                try:
                    with tenant_context.use_context(context_a):
                        barrier.wait()
                        duplicate_results.append(
                            canvas_store.create_generation_task(
                                project_a,
                                duplicate_node,
                                kind="video",
                            )
                        )
                except BaseException as exc:
                    duplicate_errors.append(exc)

            threads = [
                threading.Thread(target=duplicate_submit, args=(index,))
                for index in range(MAX_CONCURRENCY)
            ]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(REQUEST_TIMEOUT_SECONDS)
            all_tasks = canvas_store.list_generation_tasks(
                project_a,
                200,
                "EP001",
            )
            active_duplicate_count = len([
                task for task in all_tasks
                if (
                    str(task.get("node_id") or "") == duplicate_node
                    and str(task.get("status") or "")
                    in {"queued", "submitting", "running"}
                )
            ])

            unrecoverable = canvas_store.create_generation_task(
                project_a,
                node_ids[40],
                kind="video",
            )
            canvas_store.update_generation_task(
                unrecoverable["id"],
                status="succeeded",
                delivery_status="pending",
            )
            classified = canvas_store.classify_unrecoverable_delivery_tasks()

            duplicate_meta = canvas_store.get_projection_node(
                project_a,
                node_ids[50],
            )
            duplicate_meta["metadata"]["params"]["mediaVersions"] = [
                {
                    "versionId": "video:duplicate",
                    "url": "/uploads/duplicate-a.mp4",
                },
                {
                    "versionId": "video:duplicate",
                    "url": "/uploads/duplicate-b.mp4",
                },
            ]
            duplicate_meta["metadata"]["content"] = "/uploads/duplicate-b.mp4"
            canvas_store.update_project_node(
                project_a,
                node_ids[50],
                episode_id="EP001",
                patch_node={"metadata": duplicate_meta["metadata"]},
            )
            repair = canvas_store.repair_media_version_identities(apply=True)

            read_started = time.perf_counter()
            workspace = canvas_store.get_project_workspace_nodes(
                project_a,
                "EP001",
            )
            report["latenciesMs"]["largeCanvasRead"] = round(
                (time.perf_counter() - read_started) * 1000,
                2,
            )

            old_project_limit = server.SSE_PROJECT_LIMIT
            old_tenant_limit = server.SSE_TENANT_LIMIT
            server.SSE_PROJECT_LIMIT = 2
            server.SSE_TENANT_LIMIT = 16
            acquired = []
            rejected = 0
            try:
                for _ in range(6):
                    subscriber, code = server._acquire_event_subscriber(project_a)
                    if subscriber:
                        acquired.append(subscriber)
                    elif code == "SSE_CONNECTION_LIMIT":
                        rejected += 1
            finally:
                for subscriber in acquired:
                    server._release_event_subscriber(project_a, subscriber)
                server.SSE_PROJECT_LIMIT = old_project_limit
                server.SSE_TENANT_LIMIT = old_tenant_limit

            report["latenciesMs"]["projectPatchP95"] = round(_p95(latencies), 2)
            report["latenciesMs"]["projectPatchMedian"] = round(
                statistics.median(latencies),
                2,
            )
            report["counts"] = {
                "projectsTenantA": len(canvas_store.list_projects()),
                "nodesRead": len(workspace["nodes"]),
                "tasksSeen": len(all_tasks),
                "activeDuplicateTasks": active_duplicate_count,
                "duplicateSubmitErrors": [
                    f"{type(exc).__name__}: {exc}"
                    for exc in duplicate_errors
                ],
                "classifiedUnrecoverable": len(classified),
                "duplicateVersionIdsBefore": repair["duplicateIdsBefore"],
                "duplicateVersionIdsAfter": repair["duplicateIdsAfter"],
                "activeVersionMismatchAfter": repair["activeMismatchAfter"],
                "urlsLost": repair["urlsLost"],
                "sseAccepted": len(acquired),
                "sseRejected": rejected,
            }
            report["checks"] = {
                "duplicateSubmitsFinished": all(
                    not thread.is_alive() for thread in threads
                ),
                "duplicateSubmitsNoErrors": not duplicate_errors,
                "oneActiveTaskPerNode": active_duplicate_count <= 1,
                "sameProjectPatchP95Under10000": _p95(latencies) < 10_000,
                "largeCanvasReadUnder1000": report["latenciesMs"]["largeCanvasRead"] < 1000,
                "sseLimitRejected": rejected >= 4,
                "unrecoverableClassified": len(classified) >= 1,
                "versionIdsUnique": repair["duplicateIdsAfter"] == 0,
                "activeVersionResolved": repair["activeMismatchAfter"] == 0,
                "noUrlsLost": repair["urlsLost"] == 0,
            }

        with tenant_context.use_context(context_b):
            foreign_projects = canvas_store.list_projects()
            report["checks"]["tenantIsolation"] = all(
                project.get("id") not in {project_a, project_b}
                for project in foreign_projects
            )
            report["counts"]["projectsTenantB"] = len(foreign_projects)
    finally:
        for context in (context_a, context_b):
            schema = pg_compat.schema_for_path(context.db_path, "canvas")
            connection = pg_compat.connect_path(context.db_path, "canvas")
            try:
                connection.raw.execute(
                    f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'
                )
                connection.raw.commit()
            finally:
                connection.close()
    report["durationSeconds"] = round(time.monotonic() - started, 3)
    report["passed"] = all(report["checks"].values()) and (
        report["durationSeconds"] < MAX_DURATION_SECONDS
    )
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--report")
    args = parser.parse_args()
    report = run(args.url)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
