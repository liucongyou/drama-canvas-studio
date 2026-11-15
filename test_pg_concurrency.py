from __future__ import annotations

import os
import json
import statistics
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

import canvas_store
import pg_compat
import tenant_context


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is not configured",
)


def test_postgres_concurrent_project_writes(monkeypatch) -> None:
    monkeypatch.setenv("CANVAS_DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_POSTGRES_URL"])
    path = Path(tempfile.gettempdir()) / f"canvas-pg-concurrency-{uuid.uuid4().hex}" / "canvas.db"
    monkeypatch.setattr(canvas_store, "DB_PATH", str(path))
    canvas_store._SCHEMA_READY_PATHS.clear()
    canvas_store._WAL_READY_PATHS.clear()
    context = tenant_context.TenantContext(
        tenant_id="owner",
        slug="owner",
        data_root=path.parent,
        db_path=path,
        upload_dir=path.parent / "uploads",
        config_path=path.parent / "config.local.json",
        log_dir=path.parent / "logs",
    )

    def write_once(index: int) -> float:
        started = time.perf_counter()
        with tenant_context.use_context(context):
            project_id = canvas_store.create_project(f"并发项目 {index}")
            project = canvas_store.get_project(project_id)
            payload = project["payload"]
            payload["nodes"] = [
                {
                    "id": f"n{index}",
                    "type": "text",
                    "title": f"节点 {index}",
                    "position": {"x": index, "y": index + 1},
                    "metadata": {"prompt": f"并发写入 {index}"},
                }
            ]
            payload["episodeImports"] = [
                {
                    "id": "EP001",
                    "title": f"第{index}集",
                    "nodeIds": [f"n{index}"],
                    "videoNodeIds": [],
                    "connectionIds": [],
                }
            ]
            assert canvas_store.update_project(
                project_id,
                payload=payload,
                if_revision=project["revision"],
            )
            loaded = canvas_store.get_project(project_id)
            assert loaded["payload"]["nodes"][0]["title"] == f"节点 {index}"
        return (time.perf_counter() - started) * 1000

    try:
        write_once(-1)
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(write_once, index) for index in range(36)]
            durations = [future.result(timeout=60) for future in as_completed(futures)]
    finally:
        schema = pg_compat.schema_for_path(path, "canvas")
        connection = pg_compat.connect_path(path, "canvas")
        connection.raw.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        connection.raw.commit()
        connection.close()

    durations.sort()
    p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))]
    print(
        json.dumps(
            {
                "count": len(durations),
                "medianMs": round(statistics.median(durations), 2),
                "p95Ms": round(p95, 2),
                "maxMs": round(max(durations), 2),
            },
            ensure_ascii=False,
        )
    )
    assert len(durations) == 36
    assert p95 < 10_000
    assert statistics.median(durations) < 5_000


def test_postgres_same_project_patches_do_not_lose_updates(monkeypatch) -> None:
    monkeypatch.setenv("CANVAS_DB_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_POSTGRES_URL"])
    path = (
        Path(tempfile.gettempdir())
        / f"canvas-pg-row-lock-{uuid.uuid4().hex}"
        / "canvas.db"
    )
    monkeypatch.setattr(canvas_store, "DB_PATH", str(path))
    canvas_store._SCHEMA_READY_PATHS.clear()
    canvas_store._WAL_READY_PATHS.clear()
    context = tenant_context.TenantContext(
        tenant_id="owner",
        slug="owner",
        data_root=path.parent,
        db_path=path,
        upload_dir=path.parent / "uploads",
        config_path=path.parent / "config.local.json",
        log_dir=path.parent / "logs",
    )
    with tenant_context.use_context(context):
        project_id = canvas_store.create_project(
            "同一项目并发",
            payload={
                "nodes": [
                    {
                        "id": "shared-node",
                        "type": "text",
                        "title": "共享节点",
                        "position": {"x": 0, "y": 0},
                        "metadata": {
                            "episodeId": "EP001",
                            "prompt": "",
                        },
                    }
                ],
                "connections": [],
                "episodeImports": [
                    {
                        "id": "EP001",
                        "title": "EP001",
                        "nodeIds": ["shared-node"],
                        "videoNodeIds": [],
                        "connectionIds": [],
                    }
                ],
            },
        )

    workers = 8
    barrier = threading.Barrier(workers + 1)
    errors: list[BaseException] = []

    def patch(index: int) -> None:
        try:
            with tenant_context.use_context(context):
                barrier.wait()
                canvas_store.update_project_node(
                    project_id,
                    "shared-node",
                    episode_id="EP001",
                    patch_node={"metadata": {f"worker{index}": index}},
                )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=patch, args=(index,))
        for index in range(workers)
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=60)

    try:
        assert not errors
        assert all(not thread.is_alive() for thread in threads)
        with tenant_context.use_context(context):
            node = canvas_store.get_projection_node(project_id, "shared-node")
        assert node is not None
        for index in range(workers):
            assert node["metadata"][f"worker{index}"] == index
    finally:
        schema = pg_compat.schema_for_path(path, "canvas")
        connection = pg_compat.connect_path(path, "canvas")
        connection.raw.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        connection.raw.commit()
        connection.close()
