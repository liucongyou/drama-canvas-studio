from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import canvas_store
import schema_migrations
import tenant_context
import tenant_registry


def _run(database_url: str, *, apply: bool) -> dict:
    os.environ["CANVAS_DB_BACKEND"] = "postgres"
    os.environ["CANVAS_DATABASE_URL"] = database_url
    tenants = tenant_registry.list_tenants(include_disabled=True)
    reports = []
    for tenant in tenants:
        with tenant_context.use_tenant(tenant["id"], tenant["slug"]):
            connection = canvas_store._connect()
            try:
                report = (
                    schema_migrations.apply_migrations(connection, "canvas")
                    if apply
                    else schema_migrations.verify_schema(connection, "canvas")
                )
            finally:
                connection.close()
            report["tenant"] = tenant.get("slug") or tenant.get("id")
            reports.append(report)
    control = tenant_registry._connect()
    try:
        control_report = (
            schema_migrations.apply_migrations(control, "control")
            if apply
            else schema_migrations.verify_schema(control, "control")
        )
    finally:
        control.close()
    return {
        "database": database_url.rsplit("/", 1)[-1],
        "mode": "apply" if apply else "check",
        "tenants": reports,
        "control": control_report,
        "ok": all(item["ok"] for item in reports) and control_report["ok"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--report")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = _run(args.url, apply=args.apply)
    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
