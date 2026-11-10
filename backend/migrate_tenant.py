from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.db.session import SessionLocal, transactional_session
from backend.services.sqlite_migration import (
    migrate_tenant_database,
    tenant_table_counts,
)
from backend.services.migration_verify import compare_contents


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate one tenant's SQLite canvas database into the isolated PostgreSQL shadow schema."
    )
    parser.add_argument("--sqlite", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    source = Path(args.sqlite)
    if not source.is_file():
        raise SystemExit(f"source database not found: {source}")
    if args.execute:
        with transactional_session() as session:
            result = migrate_tenant_database(
                session,
                sqlite_path=str(source),
                tenant_id=args.tenant,
                dry_run=False,
            )
            counts = tenant_table_counts(session, tenant_id=args.tenant)
            content = compare_contents(
                session,
                sqlite_path=str(source),
                tenant_id=args.tenant,
                core_only=False,
            )
    else:
        with SessionLocal() as session:
            result = migrate_tenant_database(
                session,
                sqlite_path=str(source),
                tenant_id=args.tenant,
                dry_run=True,
            )
            session.rollback()
            counts = {}
            content = None
    payload = {
        "dryRun": not args.execute,
        "source": result.source,
        "tenantId": result.tenant_id,
        "sourceRows": result.tables,
        "targetRows": counts,
        "deletedRows": result.deleted,
        "missingInTarget": (
            sorted(set(result.tables) - set(counts))
            if args.execute else []
        ),
        "contentVerification": content,
        "ok": (
            not args.execute
            or (
                counts == result.tables
                and bool(content and content.get("ok"))
            )
        ),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            f"{'DRY-RUN' if payload['dryRun'] else 'EXECUTE'} "
            f"tenant={payload['tenantId']} ok={payload['ok']}"
        )
        for table in sorted(result.tables):
            target = counts.get(table, "-")
            pruned = result.deleted.get(table, 0)
            print(
                f"{table}: source={result.tables[table]} "
                f"target={target} pruned={pruned}"
            )
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
