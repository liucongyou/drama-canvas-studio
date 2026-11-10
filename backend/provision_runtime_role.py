"""Provision the non-superuser PostgreSQL runtime role.

Run this with the migration/owner connection, not with the runtime connection:

    python -m backend.provision_runtime_role --database-url "$CANVAS_MIGRATION_DATABASE_URL"

The application must then use a separate URL whose user is the runtime role.
Keeping role provisioning explicit prevents a normal app start from silently
changing database privileges.
"""
from __future__ import annotations

import argparse
import getpass
import os
import re

import psycopg
from psycopg import sql


ROLE_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")
SCHEMA_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")


def _identifier(value: str, pattern: re.Pattern[str], label: str) -> str:
    if not pattern.fullmatch(value):
        raise ValueError(f"{label} contains unsupported characters")
    return value


def provision(
    *,
    database_url: str,
    role: str = "canvas_runtime",
    schema: str = "public",
    password: str | None = None,
) -> None:
    role = _identifier(role, ROLE_RE, "role")
    schema = _identifier(schema, SCHEMA_RE, "schema")
    with psycopg.connect(database_url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s",
                (role,),
            )
            exists = cursor.fetchone() is not None
            if not exists:
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT"
                    ).format(sql.Identifier(role))
                )
            else:
                cursor.execute(
                    sql.SQL(
                        "ALTER ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT"
                    ).format(sql.Identifier(role))
                )
            if password:
                cursor.execute(
                    sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                        sql.Identifier(role),
                        sql.Literal(password),
                    )
                )

            cursor.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(connection.info.dbname),
                    sql.Identifier(role),
                )
            )
            cursor.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(schema),
                    sql.Identifier(role),
                )
            )
            cursor.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE, DELETE "
                    "ON ALL TABLES IN SCHEMA {} TO {}"
                ).format(
                    sql.Identifier(schema),
                    sql.Identifier(role),
                )
            )
            cursor.execute(
                sql.SQL(
                    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}"
                ).format(
                    sql.Identifier(schema),
                    sql.Identifier(role),
                )
            )
            cursor.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA {} "
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
                ).format(
                    sql.Identifier(schema),
                    sql.Identifier(role),
                )
            )
            cursor.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA {} "
                    "GRANT USAGE, SELECT ON SEQUENCES TO {}"
                ).format(
                    sql.Identifier(schema),
                    sql.Identifier(role),
                )
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("CANVAS_MIGRATION_DATABASE_URL", ""),
    )
    parser.add_argument("--role", default="canvas_runtime")
    parser.add_argument("--schema", default="public")
    parser.add_argument(
        "--password-env",
        default="CANVAS_RUNTIME_ROLE_PASSWORD",
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or CANVAS_MIGRATION_DATABASE_URL is required")
    password = os.environ.get(args.password_env) or None
    if password is None and os.environ.get(args.password_env) == "":
        password = getpass.getpass(
            f"Password for {args.role} (leave blank to use peer/trust auth): "
        )
    provision(
        database_url=args.database_url,
        role=args.role,
        schema=args.schema,
        password=password or None,
    )


if __name__ == "__main__":
    main()
