from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any


def fetch(url: str, cookie: str) -> tuple[int, float, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        headers={
            "Cookie": cookie,
            "Accept": "application/json",
            "User-Agent": "recipe-catalog-stress/1.0",
        },
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
        return response.status, (time.perf_counter() - started) * 1000, payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8890")
    parser.add_argument("--cookie", required=True)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--workers", type=int, default=20)
    args = parser.parse_args()
    query = urllib.parse.urlencode({"query": "镜头 群像 动作", "limit": 8})
    urls = [
        f"{args.base.rstrip('/')}/api/agent/recipes?{query}"
        for _ in range(max(1, args.requests))
    ]
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        results = list(executor.map(lambda url: fetch(url, args.cookie), urls))
    latencies = [item[1] for item in results]
    statuses: dict[int, int] = {}
    for status, _latency, _payload in results:
        statuses[status] = statuses.get(status, 0) + 1
    sorted_latencies = sorted(latencies)
    report = {
        "requests": len(results),
        "workers": args.workers,
        "statuses": statuses,
        "p50Ms": round(statistics.median(latencies), 2),
        "p95Ms": round(sorted_latencies[max(0, int(len(sorted_latencies) * 0.95) - 1)], 2),
        "maxMs": round(max(latencies), 2),
        "allCatalog295": all(item[2].get("catalog", {}).get("recipeCount") == 295 for item in results),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if statuses == {200: len(results)} and report["allCatalog295"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
