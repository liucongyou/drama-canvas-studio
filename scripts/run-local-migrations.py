from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import nullcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import canvas_store
import tenant_context
import tenant_registry


def _changed_rows(result):
    if isinstance(result, int):
        return result
    if isinstance(result, list):
        return len(result)
    if isinstance(result, dict):
        return int(
            result.get("nodesChanged")
            or result.get("changedRows")
            or result.get("projects")
            or 0
        )
    return 0


def _default_steps(apply, dry_run):
    if dry_run:
        return (
            (
                "repair_media_version_identities",
                lambda: canvas_store.repair_media_version_identities(
                    apply=False
                ),
            ),
        )
    return (
        ("backfill_generation_delivery_status", lambda: canvas_store.backfill_generation_delivery_status()),
        ("backfill_succeeded_delivery_evidence", lambda: canvas_store.backfill_succeeded_delivery_evidence()),
        ("classify_unrecoverable_delivery_tasks", lambda: canvas_store.classify_unrecoverable_delivery_tasks()),
        ("expire_unconfirmed_generation_tasks", lambda: canvas_store.expire_unconfirmed_generation_tasks()),
        ("backfill_video_version_history", lambda: canvas_store.backfill_video_version_history()),
        ("repair_media_version_identities", lambda: canvas_store.repair_media_version_identities(apply=apply)),
    )


def run_local_migrations(database_url, *, dry_run=False, step_steps=None):
    os.environ["CANVAS_DB_BACKEND"] = "postgres"
    os.environ["CANVAS_DATABASE_URL"] = str(database_url)
    steps = step_steps or _default_steps(not dry_run, dry_run)
    tenants = tenant_registry.list_tenants(include_disabled=True)
    report = {
        "database": str(database_url).rsplit("/", 1)[-1],
        "dryRun": bool(dry_run),
        "tenants": [],
        "changedRows": 0,
        "iterations": 0,
        "applied": False,
    }
    for tenant in tenants:
        tenant_result = {
            "tenant": tenant.get("slug") or tenant.get("id"),
            "steps": [],
            "changedRows": 0,
        }
        scope = (
            tenant_context.use_tenant(tenant["id"], tenant["slug"])
            if tenant
            else nullcontext()
        )
        with scope:
            for iteration in range(5):
                iteration_changed = 0
                for name, callback in steps:
                    try:
                        changed = _changed_rows(callback())
                        tenant_result["steps"].append({
                            "name": name,
                            "iteration": iteration + 1,
                            "changedRows": changed,
                            "status": "ok",
                        })
                        iteration_changed += changed
                    except Exception as exc:
                        tenant_result["steps"].append({
                            "name": name,
                            "iteration": iteration + 1,
                            "changedRows": 0,
                            "status": "failed",
                            "error": str(exc),
                        })
                        raise
                tenant_result["changedRows"] += iteration_changed
                if iteration_changed == 0 or dry_run:
                    report["iterations"] = max(
                        report["iterations"],
                        iteration + 1,
                    )
                    break
            else:
                raise RuntimeError("迁移在 5 次收敛后仍在产生变更")
        report["tenants"].append(tenant_result)
        report["changedRows"] += tenant_result["changedRows"]
    report["applied"] = bool(report["changedRows"] and not dry_run)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--report")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    report = run_local_migrations(args.url, dry_run=args.dry_run)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
