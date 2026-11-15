from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "scripts" / "multiprocess_stress.py"
SPEC = importlib.util.spec_from_file_location("multiprocess_stress", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_provider_call_counts_detect_duplicate_submission():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "calls.jsonl"
        path.write_text(
            "\n".join([
                json.dumps({"event": "submit", "taskId": "task-a"}),
                json.dumps({"event": "submit", "taskId": "task-a"}),
                json.dumps({"event": "resume", "taskId": "task-a"}),
            ])
            + "\n",
            encoding="utf-8",
        )
        counts = MODULE._call_counts(path)

    assert counts[("submit", "task-a")] == 2
    assert counts[("resume", "task-a")] == 1


def test_smoke_report_requires_all_tasks_and_ready_apis():
    valid = {
        "ok": True,
        "tasks": 4,
        "maxSubmitsPerTask": 1,
        "statusCounts": {"succeeded": 4},
        "readiness": {
            "api1": {"ok": True},
            "api2": {"ok": True},
        },
    }
    assert MODULE.validate_smoke_report(valid) is True

    duplicate = {**valid, "maxSubmitsPerTask": 2}
    assert MODULE.validate_smoke_report(duplicate) is False

    not_ready = {
        **valid,
        "readiness": {
            "api1": {"ok": True},
            "api2": {"ok": False},
        },
    }
    assert MODULE.validate_smoke_report(not_ready) is False
