from __future__ import annotations

import concurrent.futures
import json
import os
import statistics
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import canvas_store
import tenant_context


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * ratio))]


def main() -> None:
    with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {
        "CANVAS_MULTI_TENANT": "1",
        "CANVAS_TENANT_DATA_ROOT": root,
    }):
        context = tenant_context.make_context("viewport-bench", "viewport-bench")
        tenant_context.ensure_context_dirs(context)
        nodes = [
            {
                "id": f"node-{index}",
                "type": "image",
                "title": f"node-{index}",
                "metadata": {
                    "episodeId": "EP001",
                    "prompt": "x" * 900,
                },
            }
            for index in range(1200)
        ]
        with tenant_context.use_context(context):
            project_id = canvas_store.create_project(
                "viewport-write-bench",
                payload={
                    "nodes": nodes,
                    "connections": [],
                    "episodeImports": [{
                        "id": "EP001",
                        "nodeIds": [item["id"] for item in nodes],
                    }],
                },
            )

        def write(index: int) -> float:
            with tenant_context.use_context(context):
                started = time.perf_counter()
                result = canvas_store.update_project_viewport(
                    project_id,
                    {
                        "x": float(index),
                        "y": float(index * 2),
                        "zoom": 0.5 + (index % 10) / 100,
                    },
                    episode_id="EP001",
                )
                if not result:
                    raise RuntimeError("viewport write failed")
                return (time.perf_counter() - started) * 1000

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            latencies = list(pool.map(write, range(120)))
        with tenant_context.use_context(context):
            workspace = canvas_store.get_project_workspace_index(project_id)
        if not workspace:
            raise RuntimeError("viewport workspace readback returned no project")
        print(json.dumps({
            "requests": len(latencies),
            "concurrency": 12,
            "p50_ms": round(statistics.median(latencies), 2),
            "p95_ms": round(percentile(latencies, 0.95), 2),
            "p99_ms": round(percentile(latencies, 0.99), 2),
            "max_ms": round(max(latencies), 2),
            "viewport": workspace["episodeViewports"].get("EP001"),
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
