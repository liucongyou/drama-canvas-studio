from __future__ import annotations

import concurrent.futures
import json
import os
import statistics
import time
import urllib.request


BASE = os.environ.get("CANVAS_BASE", "http://127.0.0.1:8890").rstrip("/")
TOKEN = os.environ.get("CANVAS_TOKEN", "")
PROJECT = os.environ.get("PROJECT_ID", "")

if not TOKEN or not PROJECT:
    raise SystemExit("CANVAS_TOKEN and PROJECT_ID are required")


def get(path: str) -> tuple[int, float, int]:
    started = time.perf_counter()
    request = urllib.request.Request(
        BASE + path,
        headers={
            "Cookie": f"canvas_session={TOKEN}",
            "Origin": "http://127.0.0.1:8891",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        return response.status, (time.perf_counter() - started) * 1000, len(body)


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * ratio))]


def main() -> None:
    paths = [
        f"/api/projects/{PROJECT}/workspace-index",
        f"/api/projects/{PROJECT}/workspace-nodes?episodeId=EP001",
        f"/api/generation-tasks?projectId={PROJECT}&limit=80&episodeId=EP001",
    ]
    for path in paths:
        status, _latency, size = get(path)
        print(path, status, size)
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(get, [paths[index % len(paths)] for index in range(60)]))
    latencies = [item[1] for item in results if item[0] == 200]
    print(json.dumps({
        "requests": len(results),
        "ok": len(latencies),
        "p50_ms": round(statistics.median(latencies), 2),
        "p95_ms": round(percentile(latencies, 0.95), 2),
        "p99_ms": round(percentile(latencies, 0.99), 2),
        "max_ms": round(max(latencies), 2),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
