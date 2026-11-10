"""Verify PostgreSQL runtime role privileges and tenant RLS.

This is a read-only verification except for one intentional failing DDL probe.
Run it after provisioning the runtime role and before enabling shadow reads.
"""
from __future__ import annotations

import argparse
import sys

import psycopg


def verify(
    *,
    admin_url: str,
    runtime_url: str,
    tenant_id: str,
    other_tenant_id: str = "runtime-role-isolation-probe",
) -> dict[str, object]:
    with psycopg.connect(admin_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT rolsuper, rolbypassrls, rolinherit "
                "FROM pg_roles WHERE rolname = current_user"
            )
            admin_flags = cursor.fetchone()
            cursor.execute(
                "SELECT rolsuper, rolbypassrls, rolinherit "
                "FROM pg_roles WHERE rolname = %s",
                ("canvas_runtime",),
            )
            runtime_flags = cursor.fetchone()
    if not admin_flags or not runtime_flags:
        raise RuntimeError("runtime role or migration role is missing")
    if any(runtime_flags):
        raise RuntimeError("canvas_runtime must be NOSUPERUSER NOBYPASSRLS NOINHERIT")

    result: dict[str, object] = {
        "runtime_flags": runtime_flags,
    }
    with psycopg.connect(runtime_url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM canvas_projects")
            result["unscoped_rows"] = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT set_config('app.tenant_id', %s, false)",
                (tenant_id,),
            )
            cursor.execute("SELECT count(*) FROM canvas_projects")
            result["tenant_rows"] = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT set_config('app.tenant_id', %s, false)",
                (other_tenant_id,),
            )
            cursor.execute("SELECT count(*) FROM canvas_projects")
            result["other_tenant_rows"] = int(cursor.fetchone()[0])
            try:
                cursor.execute(
                    "CREATE TABLE __runtime_role_ddl_probe(id integer)"
                )
            except psycopg.Error as exc:
                result["ddl_sqlstate"] = getattr(exc, "sqlstate", None)
            else:
                raise RuntimeError("runtime role unexpectedly has DDL privileges")

    if result["unscoped_rows"] != 0:
        raise RuntimeError("RLS did not block an unscoped query")
    if int(result["tenant_rows"]) <= 0:
        raise RuntimeError(f"RLS returned no rows for tenant {tenant_id}")
    if result["other_tenant_rows"] != 0:
        raise RuntimeError("RLS leaked rows across tenants")
    if result["ddl_sqlstate"] != "42501":
        raise RuntimeError("DDL denial did not return SQLSTATE 42501")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-url", required=True)
    parser.add_argument("--runtime-url", required=True)
    parser.add_argument("--tenant", default="owner")
    parser.add_argument(
        "--other-tenant",
        default="runtime-role-isolation-probe",
    )
    args = parser.parse_args()
    try:
        result = verify(
            admin_url=args.admin_url,
            runtime_url=args.runtime_url,
            tenant_id=args.tenant,
            other_tenant_id=args.other_tenant,
        )
    except Exception as exc:
        print(f"RUNTIME_ROLE_VERIFY_FAILED: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
