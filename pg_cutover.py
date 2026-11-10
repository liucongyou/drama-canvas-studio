from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import psycopg
import pg_compat
from pg_data_migration import migrate


def _tenant_slugs(control_db: Path) -> list[tuple[str, str]]:
    with sqlite3.connect(
        f"file:{control_db.as_posix()}?mode=ro",
        uri=True,
    ) as connection:
        return [
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT id,slug FROM tenants ORDER BY created_at,id"
            )
        ]


def run_cutover(
    *,
    app_root: Path,
    database_url: str,
    recreate: bool,
    include_legacy_root: bool,
) -> dict[str, Any]:
    data_root = app_root / "data"
    control_db = data_root / "control.db"
    agent_db = app_root / "agent" / "data" / "agent.db"
    if not control_db.is_file():
        raise FileNotFoundError(control_db)

    reports: list[dict[str, Any]] = []
    reports.append(
        migrate(
            control_db,
            kind="control",
            schema="control",
            database_url=database_url,
            recreate=recreate,
        )
    )
    for tenant_id, slug in _tenant_slugs(control_db):
        source = data_root / "tenants" / tenant_id / "canvas.db"
        if not source.is_file():
            reports.append(
                {
                    "tenantId": tenant_id,
                    "slug": slug,
                    "source": str(source),
                    "ok": False,
                    "error": "missing tenant SQLite database",
                }
            )
            continue
        reports.append(
            {
                "tenantId": tenant_id,
                "slug": slug,
                **migrate(
                    source,
                    kind="canvas",
                    schema=pg_compat.schema_for_path(source, "canvas"),
                    database_url=database_url,
                    recreate=recreate,
                ),
            }
        )
    if agent_db.is_file():
        reports.append(
            migrate(
                agent_db,
                kind="agent",
                schema="agent",
                database_url=database_url,
                recreate=recreate,
            )
        )
    if include_legacy_root:
        legacy = data_root / "canvas.db"
        if legacy.is_file():
            reports.append(
                migrate(
                    legacy,
                    kind="canvas",
                    schema="archive_legacy_root",
                    database_url=database_url,
                    recreate=recreate,
                )
            )

    with psycopg.connect(database_url) as connection:
        schema_rows = connection.execute(
            """
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name='control' OR schema_name='agent'
               OR schema_name LIKE 'tenant_%'
               OR schema_name='archive_legacy_root'
            ORDER BY schema_name
            """
        ).fetchall()
    return {
        "ok": all(item.get("ok") for item in reports),
        "database": database_url.split("@")[-1],
        "runtimeBackend": "postgres",
        "schemas": [str(row[0]) for row in schema_rows],
        "reports": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate every runtime SQLite database into PostgreSQL."
    )
    parser.add_argument("--app-root", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--recreate", action="store_true")
    parser.add_argument("--include-legacy-root", action="store_true")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    report = run_cutover(
        app_root=Path(args.app_root).resolve(),
        database_url=args.database_url,
        recreate=args.recreate,
        include_legacy_root=args.include_legacy_root,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
