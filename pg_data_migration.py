from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

import pg_compat


def _normalize(value: Any) -> Any:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        return {"$bytes": hashlib.sha256(value).hexdigest()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _canonical_row(row: tuple[Any, ...], columns: list[str]) -> str:
    return json.dumps(
        dict(zip(columns, [_normalize(value) for value in row])),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _ordered_tables(source: sqlite3.Connection) -> list[tuple[str, str]]:
    table_rows = [
        (str(row[0]), str(row[1]))
        for row in source.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND sql IS NOT NULL ORDER BY name"
        )
    ]
    by_name = dict(table_rows)
    deps: dict[str, set[str]] = {}
    for name, sql in table_rows:
        deps[name] = {
            match
            for match in (
                item.group(1)
                for item in re.finditer(
                    r"REFERENCES\s+[\"`\[]?([A-Za-z_][A-Za-z0-9_]*)",
                    sql,
                    re.I,
                )
            )
            if match in by_name and match != name
        }
    ordered: list[str] = []
    remaining = set(by_name)
    while remaining:
        ready = sorted(
            name for name in remaining
            if not (deps.get(name, set()) & remaining)
        )
        if not ready:
            ready = sorted(remaining)
        ordered.extend(ready)
        remaining.difference_update(ready)
    return [(name, by_name[name]) for name in ordered]


def _columns(source: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [
        {
            "name": str(row[1]),
            "type": str(row[2] or ""),
            "notnull": bool(row[3]),
            "default": row[4],
            "pk": int(row[5] or 0),
        }
        for row in source.execute(f'PRAGMA table_info("{table}")')
    ]


def _digest_source(
    source: sqlite3.Connection,
    table: str,
    columns: list[str],
) -> tuple[int, str]:
    order = ",".join(f'"{column}"' for column in columns)
    try:
        rows = source.execute(
            f'SELECT {order} FROM "{table}" ORDER BY rowid'
        ).fetchall()
    except sqlite3.DatabaseError:
        rows = source.execute(f'SELECT {order} FROM "{table}"').fetchall()
    digest = hashlib.sha256()
    for row in sorted(
        rows,
        key=lambda item: _canonical_row(tuple(item), columns),
    ):
        digest.update(_canonical_row(tuple(row), columns).encode("utf-8"))
        digest.update(b"\n")
    return len(rows), digest.hexdigest()


def _digest_target(
    target: pg_compat.PGConnection,
    table: str,
    columns: list[str],
) -> tuple[int, str]:
    order = ",".join(f'"{column}"' for column in columns)
    rows = target.raw.execute(
        f'SELECT {order} FROM "{table}"'
    ).fetchall()
    digest = hashlib.sha256()
    for row in sorted(
        rows,
        key=lambda item: _canonical_row(tuple(item), columns),
    ):
        digest.update(_canonical_row(tuple(row), columns).encode("utf-8"))
        digest.update(b"\n")
    return len(rows), digest.hexdigest()


def _schema_sql(source: sqlite3.Connection) -> tuple[str, str]:
    tables = [sql for _name, sql in _ordered_tables(source)]
    indexes = [
        str(row[0])
        for row in source.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND sql IS NOT NULL ORDER BY name"
        )
    ]
    return ";\n".join(tables) + ";", ";\n".join(indexes) + (";" if indexes else "")


def _reset_sequences(target: pg_compat.PGConnection, tables: list[str]) -> None:
    for table in tables:
        columns = target.raw.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name=%s
              AND column_default LIKE %s
            """,
            (table, "nextval(%"),
        ).fetchall()
        for (column,) in columns:
            target.raw.execute(
                "SELECT setval("
                "pg_get_serial_sequence(%s, %s), "
                f'COALESCE((SELECT MAX("{column}") FROM "{table}"), 1), '
                f'EXISTS (SELECT 1 FROM "{table}"))',
                (table, column),
            )


def migrate(
    sqlite_path: Path,
    *,
    kind: str,
    schema: str,
    database_url: str,
    recreate: bool = False,
) -> dict[str, Any]:
    if not sqlite_path.is_file():
        raise FileNotFoundError(sqlite_path)
    source = sqlite3.connect(
        f"file:{sqlite_path.as_posix()}?mode=ro",
        uri=True,
    )
    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        if recreate:
            with pg_compat.PGConnection(
                str(sqlite_path),
                kind,
                schema,
            ) as connection:
                connection.raw.execute(
                    f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'
                )
        target = pg_compat.PGConnection(str(sqlite_path), kind, schema)
        if schema != target.schema:
            target.raw.close()
            target = pg_compat.PGConnection(str(sqlite_path), kind, schema)
        table_sql, index_sql = _schema_sql(source)
        if table_sql.strip():
            target.executescript(table_sql)
        if index_sql.strip():
            target.executescript(index_sql)
        target.commit()

        tables = [name for name, _sql in _ordered_tables(source)]
        report: dict[str, Any] = {
            "source": str(sqlite_path),
            "schema": schema,
            "kind": kind,
            "tables": [],
        }
        for table in tables:
            columns = _columns(source, table)
            column_names = [str(item["name"]) for item in columns]
            if not column_names:
                continue
            pk_columns = [
                item["name"]
                for item in sorted(columns, key=lambda item: item["pk"])
                if item["pk"] > 0
            ]
            select_columns = ",".join(f'"{name}"' for name in column_names)
            try:
                rows = source.execute(
                    f'SELECT {select_columns} FROM "{table}" ORDER BY rowid'
                ).fetchall()
            except sqlite3.DatabaseError:
                rows = source.execute(
                    f'SELECT {select_columns} FROM "{table}"'
                ).fetchall()
            placeholders = ",".join("%s" for _ in column_names)
            insert_columns = ",".join(f'"{name}"' for name in column_names)
            sql = f'INSERT INTO "{table}" ({insert_columns}) VALUES ({placeholders})'
            if pk_columns:
                conflict = ",".join(f'"{name}"' for name in pk_columns)
                updates = ",".join(
                    f'"{name}"=EXCLUDED."{name}"'
                    for name in column_names
                    if name not in pk_columns
                )
                sql += (
                    f" ON CONFLICT ({conflict}) DO UPDATE SET {updates}"
                    if updates
                    else f" ON CONFLICT ({conflict}) DO NOTHING"
                )
            for row in rows:
                target.execute(sql, tuple(row))
            target.commit()
            source_rows, source_hash = _digest_source(
                source,
                table,
                column_names,
            )
            target_rows, target_hash = _digest_target(
                target,
                table,
                column_names,
            )
            report["tables"].append(
                {
                    "table": table,
                    "rows": source_rows,
                    "sourceSha256": source_hash,
                    "targetRows": target_rows,
                    "targetSha256": target_hash,
                    "ok": source_rows == target_rows
                    and source_hash == target_hash,
                }
            )
        _reset_sequences(target, tables)
        target.commit()
        target.close()
        report["ok"] = all(item["ok"] for item in report["tables"])
        report["totalRows"] = sum(item["rows"] for item in report["tables"])
        return report
    finally:
        source.close()
        if old_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old_url


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate one SQLite runtime database into a PostgreSQL schema."
    )
    parser.add_argument("--sqlite", required=True)
    parser.add_argument("--kind", choices=["canvas", "control", "agent"], required=True)
    parser.add_argument("--schema", default="")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--recreate", action="store_true")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    path = Path(args.sqlite).resolve()
    schema = (
        args.schema.strip()
        or pg_compat.schema_for_path(path, args.kind)
    )
    report = migrate(
        path,
        kind=args.kind,
        schema=schema,
        database_url=args.database_url,
        recreate=args.recreate,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
