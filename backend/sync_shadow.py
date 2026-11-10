"""Idempotently mirror one SQLite tenant into the PostgreSQL shadow schema.

This command never switches traffic or writes the legacy SQLite database.
Schedule it with Task Scheduler, cron, or a Kubernetes CronJob. A PostgreSQL
advisory lock prevents overlapping runs for the same tenant.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text

from backend.db.session import SessionLocal, set_tenant_scope
from backend.services.migration_verify import compare_contents
from backend.services.sqlite_migration import (
    migrate_tenant_database,
    tenant_table_counts,
)


def advisory_lock_key(tenant_id: str) -> int:
    digest = hashlib.blake2b(
        str(tenant_id).encode("utf-8"),
        digest_size=8,
        person=b"canvas-sync",
    ).digest()
    return int.from_bytes(digest, "big", signed=True)


def sync_once(*, sqlite_path: str, tenant_id: str) -> dict[str, Any]:
    source = Path(sqlite_path)
    if not source.is_file():
        raise FileNotFoundError(f"source database not found: {source}")
    tenant_id = str(tenant_id or "").strip()
    if not tenant_id:
        raise ValueError("tenant_id is required")

    lock_key = advisory_lock_key(tenant_id)
    session = SessionLocal()
    locked = False
    try:
        locked = bool(
            session.execute(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": lock_key},
            ).scalar()
        )
        if not locked:
            return {
                "ok": True,
                "skipped": True,
                "reason": "another shadow sync holds the tenant lock",
                "tenantId": tenant_id,
                "lockKey": lock_key,
            }

        set_tenant_scope(session, tenant_id)
        migration = migrate_tenant_database(
            session,
            sqlite_path=str(source),
            tenant_id=tenant_id,
            dry_run=False,
        )
        target_counts = tenant_table_counts(session, tenant_id=tenant_id)
        verification = compare_contents(
            session,
            sqlite_path=str(source),
            tenant_id=tenant_id,
            core_only=False,
        )
        ok = (
            target_counts == migration.tables
            and bool(verification.get("ok"))
        )
        if ok:
            session.commit()
        else:
            session.rollback()
        return {
            "ok": ok,
            "skipped": False,
            "tenantId": tenant_id,
            "source": str(source),
            "lockKey": lock_key,
            "sourceRows": migration.tables,
            "targetRows": target_counts,
            "deletedRows": migration.deleted,
            "contentVerification": verification,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        if locked:
            try:
                session.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": lock_key},
                )
            except Exception:
                pass
        session.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = sync_once(
            sqlite_path=args.sqlite,
            tenant_id=args.tenant,
        )
    except Exception as exc:
        print(json.dumps({
            "ok": False,
            "tenantId": args.tenant,
            "error": str(exc),
        }, ensure_ascii=False))
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            f"SYNC tenant={result['tenantId']} "
            f"ok={result['ok']} skipped={result['skipped']}"
        )
        for table, count in sorted(result.get("sourceRows", {}).items()):
            print(
                f"{table}: source={count} "
                f"target={result.get('targetRows', {}).get(table, '-')}"
            )
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
