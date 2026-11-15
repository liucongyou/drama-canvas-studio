from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path

import pg_compat


MIGRATIONS_ROOT = Path(__file__).resolve().parent / "migrations"
_VERSION_RE = re.compile(r"^(?P<version>\d{4})_[a-z0-9_]+\.sql$")


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    sql: str
    checksum: str


def _load_migrations(schema_kind: str, root: Path | None = None) -> list[Migration]:
    directory = (root or MIGRATIONS_ROOT) / schema_kind
    if not directory.exists():
        return []
    migrations = []
    seen = set()
    for path in sorted(directory.glob("*.sql")):
        match = _VERSION_RE.fullmatch(path.name)
        if not match:
            raise MigrationError(f"非法迁移文件名：{path.name}")
        version = match.group("version")
        if version in seen:
            raise MigrationError(f"迁移版本重复：{version}")
        seen.add(version)
        sql = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=version,
                path=path,
                sql=sql,
                checksum=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            )
        )
    return migrations


def required_versions(schema_kind: str, root: Path | None = None) -> list[str]:
    return [item.version for item in _load_migrations(schema_kind, root)]


def _ensure_registry(connection: pg_compat.PGConnection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations(
            version TEXT PRIMARY KEY,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            duration_ms INTEGER NOT NULL
        )
        """
    )


def _advisory_key(connection: pg_compat.PGConnection, schema_kind: str) -> int:
    row = connection.execute("SELECT current_schema()").fetchone()
    schema_name = str(row[0] if row else "")
    digest = hashlib.sha256(
        f"{schema_name}:{schema_kind}:schema_migrations".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def applied_versions(connection: pg_compat.PGConnection) -> dict[str, str]:
    _ensure_registry(connection)
    connection.commit()
    rows = connection.execute(
        "SELECT version,checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def apply_migrations(
    connection: pg_compat.PGConnection,
    schema_kind: str,
    *,
    root: Path | None = None,
) -> dict:
    migrations = _load_migrations(schema_kind, root)
    applied = []
    skipped = []
    try:
        connection.execute("BEGIN")
        connection.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            (_advisory_key(connection, schema_kind),),
        )
        _ensure_registry(connection)
        rows = connection.execute(
            "SELECT version,checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
        existing = {str(row[0]): str(row[1]) for row in rows}
        required = {item.version: item.checksum for item in migrations}
        for version, checksum in existing.items():
            if version not in required:
                raise MigrationError(f"数据库包含未知迁移版本：{version}")
            if required[version] != checksum:
                raise MigrationError(f"迁移校验和不一致：{version}")
        for migration in migrations:
            if migration.version in existing:
                skipped.append(migration.version)
                continue
            started = time.monotonic()
            connection.executescript(migration.sql)
            elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
            connection.execute(
                "INSERT INTO schema_migrations("
                "version,checksum,applied_at,duration_ms"
                ") VALUES(?,?,?,?)",
                (
                    migration.version,
                    migration.checksum,
                    _now_iso(),
                    elapsed_ms,
                ),
            )
            applied.append(migration.version)
        connection.commit()
        return {
            "schemaKind": schema_kind,
            "applied": applied,
            "skipped": skipped,
            "requiredVersion": migrations[-1].version if migrations else "",
            "ok": True,
        }
    except Exception:
        connection.rollback()
        raise


def verify_schema(
    connection: pg_compat.PGConnection,
    schema_kind: str,
    *,
    root: Path | None = None,
) -> dict:
    migrations = _load_migrations(schema_kind, root)
    required = {item.version: item.checksum for item in migrations}
    registry = connection.execute(
        "SELECT to_regclass('schema_migrations')"
    ).fetchone()
    if not registry or not registry[0]:
        connection.rollback()
        return {
            "schemaKind": schema_kind,
            "requiredVersion": migrations[-1].version if migrations else "",
            "appliedVersion": "",
            "missing": sorted(required),
            "unknown": [],
            "mismatched": [],
            "ok": not required,
        }
    try:
        rows = connection.execute(
            "SELECT version,checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    applied = {str(row[0]): str(row[1]) for row in rows}
    missing = sorted(set(required) - set(applied))
    unknown = sorted(set(applied) - set(required))
    mismatched = sorted(
        version
        for version in set(required) & set(applied)
        if required[version] != applied[version]
    )
    return {
        "schemaKind": schema_kind,
        "requiredVersion": migrations[-1].version if migrations else "",
        "appliedVersion": max(applied) if applied else "",
        "missing": missing,
        "unknown": unknown,
        "mismatched": mismatched,
        "ok": not missing and not unknown and not mismatched,
    }


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
