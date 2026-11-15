from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "artifacts" / "backend-test-report.json"
COUNT_RE = re.compile(
    r"(?P<passed>\d+) passed"
    r"(?:, (?P<failed>\d+) failed)?"
    r"(?:, (?P<skipped>\d+) skipped)?"
)


def test_modules() -> list[Path]:
    modules = sorted(ROOT.glob("test_*.py"))
    modules.extend(sorted((ROOT / "tests").glob("test_*.py")))
    return modules


def parse_counts(output: str) -> dict[str, int]:
    match = COUNT_RE.search(output)
    if not match:
        return {"passed": 0, "failed": 0, "skipped": 0}
    return {
        key: int(match.group(key) or 0)
        for key in ("passed", "failed", "skipped")
    }


def run_module(path: Path, timeout: int) -> dict:
    started = time.monotonic()
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    command = [sys.executable, "-m", "pytest", str(path), "-q"]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        output = completed.stdout
        status = "passed" if completed.returncode == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + f"\nTIMEOUT after {timeout}s\n"
        status = "timeout"
    counts = parse_counts(output)
    duration = round(time.monotonic() - started, 3)
    tail = "\n".join(output.strip().splitlines()[-12:])
    print(f"[{status.upper()}] {path}: {duration}s")
    if status != "passed":
        print(tail)
    return {
        "module": path.relative_to(ROOT).as_posix(),
        "status": status,
        "durationSeconds": duration,
        **counts,
        "tail": tail,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    results = [run_module(path, args.timeout) for path in test_modules()]
    summary = {
        "modules": len(results),
        "passedModules": sum(item["status"] == "passed" for item in results),
        "failedModules": sum(item["status"] != "passed" for item in results),
        "passed": sum(item["passed"] for item in results),
        "failed": sum(item["failed"] for item in results),
        "skipped": sum(item["skipped"] for item in results),
        "durationSeconds": round(sum(item["durationSeconds"] for item in results), 3),
        "results": results,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({key: summary[key] for key in (
        "modules",
        "passedModules",
        "failedModules",
        "passed",
        "failed",
        "skipped",
        "durationSeconds",
    )}, ensure_ascii=False))
    return 0 if summary["failedModules"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
