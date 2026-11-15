from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    duration_seconds = max(60, int(args.hours * 3600))
    timeout = duration_seconds + 120
    command = [
        sys.executable,
        "-u",
        str(ROOT / "scripts" / "multiprocess_stress.py"),
        "--url",
        args.url,
        "--tenants",
        "100",
        "--tasks-per-tenant",
        "1",
        "--workers",
        "4",
        "--worker-concurrency",
        "8",
        "--provider-delay",
        "5",
        "--timeout-seconds",
        str(timeout),
        "--duration-seconds",
        str(duration_seconds),
        "--kill-interval-seconds",
        "3600",
        "--output",
        args.output,
    ]
    return subprocess.call(command, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
