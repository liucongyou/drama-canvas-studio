from __future__ import annotations

import hashlib
import os
import queue
import re
import threading
from pathlib import Path
from typing import Any, Iterable, Sequence

import psycopg
from psycopg.rows import tuple_row


AUTO_ID_TABLES = {
    "agent": {
        "messages",
        "stages",
        "steps",
        "artifacts",
        "events",
        "tool_calls",
        "conversation_messages",
    },
    "canvas": {"audit_events", "canvas_events"},
}

_POOL_LOCK = threading.Lock()
_POOLS: dict[str, queue.LifoQueue[Any]] = {}
_POOL_IN_USE: dict[str, int] = {}
_POOL_SIZE = max(2, int(os.getenv("CANVAS_PG_POOL_SIZE", "24")))
_POOL_SCHEMA_SIZE = max(2, min(8, _POOL_SIZE))
_SERIAL_ID_CACHE: dict[tuple[str, str], bool] = {}

CANVAS_ROWID_INDEXES = (
    'CREATE INDEX IF NOT EXISTS ix_pg_projection_nodes_order ON canvas_projection_nodes(project_id, rowid)',
    'CREATE INDEX IF NOT EXISTS ix_pg_projection_edges_order ON canvas_projection_edges(project_id, rowid)',
    'CREATE INDEX IF NOT EXISTS ix_pg_projection_episodes_order ON canvas_projection_episodes(project_id, rowid)',
    'CREATE INDEX IF NOT EXISTS ix_pg_projection_notes_order ON canvas_projection_notes(project_id, episode_id, rowid)',
    'CREATE INDEX IF NOT EXISTS ix_pg_snapshots_recent ON canvas_snapshots(canvas_id, created_at DESC, rowid DESC)',
    'CREATE INDEX IF NOT EXISTS ix_pg_tasks_recent ON generation_tasks(project_id, created_at DESC, updated_at DESC, rowid DESC)',
    'CREATE INDEX IF NOT EXISTS ix_pg_audit_recent ON audit_events(created_at DESC, rowid DESC)',
)


class DatabaseError(RuntimeError):
    pass


class OperationalError(DatabaseError):
    pass


class IntegrityError(DatabaseError):
    pass


class PGRow:
    __slots__ = ("_values", "_columns")

    def __init__(self, values: Sequence[Any], columns: Sequence[str]):
        self._values = tuple(values)
        self._columns = tuple(columns)

    def __getitem__(self, key: int | slice | str) -> Any:
        if isinstance(key, str):
            return self._values[self._columns.index(key)]
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def keys(self) -> list[str]:
        return list(self._columns)

    def values(self) -> tuple[Any, ...]:
        return self._values

    def items(self):
        return zip(self._columns, self._values)

    def __repr__(self) -> str:
        return repr(dict(self.items()))


def postgres_enabled() -> bool:
    backend = os.getenv("CANVAS_DB_BACKEND", "").strip().lower()
    if backend in {"sqlite", "sqlite3"}:
        if os.getenv("CANVAS_ALLOW_SQLITE", "").strip() == "1":
            return False
        raise RuntimeError(
            "生产模式禁止 SQLite；仅测试环境可设置 CANVAS_ALLOW_SQLITE=1"
        )
    if backend in {"postgres", "postgresql", "pg"}:
        if not database_url():
            raise RuntimeError(
                "CANVAS_DB_BACKEND=postgres 时必须配置 DATABASE_URL "
                "或 CANVAS_DATABASE_URL"
            )
        return True
    if database_url():
        return True
    if os.getenv("CANVAS_ALLOW_SQLITE", "").strip() == "1":
        return False
    raise RuntimeError(
        "生产模式必须使用 PostgreSQL：缺少 CANVAS_DB_BACKEND=postgres "
        "和 DATABASE_URL；拒绝回退 SQLite"
    )


def database_url() -> str:
    return (
        os.getenv("CANVAS_DATABASE_URL", "").strip()
        or os.getenv("DATABASE_URL", "").strip()
    )


def schema_for_path(path: str | os.PathLike[str], kind: str) -> str:
    raw = Path(path).resolve()
    if kind == "control":
        return _schema_with_test_suffix("control")
    if kind == "agent":
        return _schema_with_test_suffix("agent")
    parent = raw.parent.name.lower()
    if parent and parent not in {"data", "tenants"}:
        candidate = parent
    elif raw.name == "canvas.db":
        candidate = "legacy_root"
    else:
        candidate = raw.stem
    safe = re.sub(r"[^a-z0-9_]+", "_", candidate).strip("_")[:40] or "canvas"
    if kind == "canvas" and safe != "owner":
        identity = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:8]
        base_schema = f"tenant_{safe}_{identity}"
    else:
        base_schema = f"tenant_{safe}" if kind == "canvas" else f"{kind}_{safe}"
    return _schema_with_test_suffix(base_schema)


def _schema_with_test_suffix(value: str) -> str:
    suffix = re.sub(
        r"[^a-z0-9_]+",
        "_",
        os.getenv("CANVAS_PG_SCHEMA_SUFFIX", "").strip().lower(),
    ).strip("_")
    if not suffix:
        return value
    return f"{value}_{suffix}"[:60].rstrip("_")


def _quote_identifier(value: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", value):
        raise ValueError(f"非法 PostgreSQL schema：{value}")
    return f'"{value}"'


def _split_script(script: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_quote = False
    skipping_trigger = False
    for char in script:
        current.append(char)
        if char == "'":
            in_quote = not in_quote
        if in_quote:
            continue
        text = "".join(current)
        upper = text.upper().lstrip()
        if not skipping_trigger and upper.startswith("CREATE TRIGGER"):
            skipping_trigger = True
        if skipping_trigger:
            if re.search(r"\bEND\s*;\s*$", text, re.I):
                current = []
                skipping_trigger = False
            continue
        if char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def _escape_sql_literal_percents(sql: str) -> str:
    """Escape literal percent signs inside SQL strings for psycopg parameters."""
    out: list[str] = []
    in_string = False
    index = 0
    while index < len(sql):
        char = sql[index]
        if char == "'":
            out.append(char)
            if in_string and index + 1 < len(sql) and sql[index + 1] == "'":
                out.append("'")
                index += 2
                continue
            in_string = not in_string
        elif char == "%" and in_string:
            out.append("%%")
        else:
            out.append(char)
        index += 1
    return "".join(out)


def _translate_insert_or_replace(sql: str) -> str:
    match = re.match(
        r"\s*INSERT\s+OR\s+REPLACE\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\)\s*VALUES\s*\((.*)\)\s*$",
        sql,
        re.I | re.S,
    )
    if not match:
        return sql.replace("INSERT OR REPLACE", "INSERT")
    table, columns_raw, values_raw = match.groups()
    columns = [item.strip() for item in columns_raw.split(",") if item.strip()]
    update = ", ".join(
        f"{column}=EXCLUDED.{column}"
        for column in columns
        if column.lower() != "id"
    )
    conflict = f" ON CONFLICT(id) DO UPDATE SET {update}" if update else " ON CONFLICT(id) DO NOTHING"
    return f"INSERT INTO {table} ({columns_raw}) VALUES ({values_raw}){conflict}"


def _translate_json_extract(sql: str) -> str:
    pattern = re.compile(
        r"json_extract\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*'\$\.([^']+)'\s*\)",
        re.I,
    )

    def replace(match: re.Match[str]) -> str:
        column = match.group(1)
        path = ", ".join(
            re.sub(r"[^A-Za-z0-9_]+", "", item)
            for item in match.group(2).split(".")
            if item
        )
        return f"({column}::jsonb #>> '{{{path}}}')"

    return pattern.sub(replace, sql)


def _translate_sql(sql: str) -> str:
    text = str(sql or "").strip()
    if not text:
        return text
    if text.upper().startswith("CREATE TRIGGER"):
        return ""
    if text.upper().startswith("DROP TRIGGER"):
        return ""
    if re.fullmatch(r"PRAGMA\s+(busy_timeout|foreign_keys|journal_mode|synchronous|wal_autocheckpoint).*", text, re.I):
        return ""
    if re.fullmatch(r"PRAGMA\s+wal_checkpoint.*", text, re.I):
        return ""
    if re.fullmatch(r"VACUUM", text, re.I):
        return ""
    text = text.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    text = text.replace("AUTOINCREMENT", "")
    text = re.sub(r"\bREAL\b", "DOUBLE PRECISION", text, flags=re.I)
    text = text.replace(
        "THEN viewport_json",
        "THEN canvas_projection_episodes.viewport_json",
    )
    text = text.replace(
        "THEN layout_json",
        "THEN canvas_projection_episodes.layout_json",
    )
    text = re.sub(r"\s+COLLATE\s+NOCASE", "", text, flags=re.I)
    text = re.sub(r"\bBEGIN\s+IMMEDIATE\b", "BEGIN", text, flags=re.I)
    text = re.sub(r"\bLIMIT\s+-1\s+OFFSET\b", "LIMIT ALL OFFSET", text, flags=re.I)
    text = re.sub(
        r"CASE\s+WHEN\s+\?\s+IS\s+NULL",
        "CASE WHEN ?::text IS NULL",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\bMAX\(\s*(\d+)\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)",
        r"GREATEST(\1, \2)",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"COALESCE\(\s*GROUP_CONCAT\(\s*DISTINCT\s+(.+?)\s*\)\s*,\s*''\s*\)",
        r"COALESCE(STRING_AGG(DISTINCT \1, ','), '')",
        text,
        flags=re.I | re.S,
    )
    text = re.sub(
        r"json_patch\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*,\s*excluded\.([A-Za-z_][A-Za-z0-9_]*)\s*\)",
        r"((\1)::jsonb || (EXCLUDED.\2)::jsonb)::text",
        text,
        flags=re.I,
    )
    text = _translate_json_extract(text)
    text = _translate_insert_or_replace(text)
    text = text.replace("?", "%s")
    return text


class PGCursor:
    def __init__(self, connection: "PGConnection"):
        self.connection = connection
        self._cursor = connection.raw.cursor(row_factory=tuple_row)
        self._synthetic: list[PGRow] = []
        self._synthetic_index = 0
        self._lastrowid = 0

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount or 0)

    @property
    def lastrowid(self) -> int:
        return self._lastrowid

    @property
    def description(self):
        return self._cursor.description

    def execute(self, sql: str, params: Sequence[Any] = ()) -> "PGCursor":
        text = str(sql or "").strip()
        pragma = re.fullmatch(r"PRAGMA\s+table_info\(([A-Za-z_][A-Za-z0-9_]*)\)", text, re.I)
        if pragma:
            rows = self.connection.raw.execute(
                """
                SELECT ordinal_position - 1, column_name, data_type,
                       CASE WHEN is_nullable='NO' THEN 1 ELSE 0 END,
                       column_default, 0
                FROM information_schema.columns
                WHERE table_schema=current_schema() AND table_name=%s
                ORDER BY ordinal_position
                """,
                (pragma.group(1),),
            ).fetchall()
            columns = ["cid", "name", "type", "notnull", "dflt_value", "pk"]
            self._synthetic = [PGRow(row, columns) for row in rows]
            self._synthetic_index = 0
            return self
        translated = _translate_sql(text)
        if params:
            # psycopg treats percent signs in the full SQL string as format
            # markers when parameters are supplied. SQLite LIKE patterns such
            # as '%"status"%failed%' must stay literal in PostgreSQL too.
            translated = _escape_sql_literal_percents(translated)
        if not translated:
            self._synthetic = [PGRow((0, 0, 0), ["busy", "log", "checkpointed"])]
            self._synthetic_index = 0
            return self
        try:
            if os.getenv("PG_DEBUG"):
                print(f"[pg] {translated}", flush=True)
            self._cursor.execute(translated, tuple(params or ()))
            created = re.match(
                r"\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
                translated,
                re.I,
            )
            if (
                created
                and self.connection.kind == "canvas"
                and "BIGSERIAL PRIMARY KEY" not in translated.upper()
            ):
                table = _quote_identifier(created.group(1).lower())
                self.connection.raw.execute(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS rowid BIGSERIAL"
                )
            insert = re.match(
                r"\s*INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)",
                translated,
                re.I,
            )
            needs_lastrowid = bool(
                insert and self._needs_lastrowid(insert.group(1).lower())
            )
            if needs_lastrowid:
                try:
                    self._lastrowid = int(
                        self.connection.raw.execute("SELECT LASTVAL()").fetchone()[0]
                    )
                except psycopg.Error:
                    self._lastrowid = 0
            return self
        except psycopg.IntegrityError as exc:
            if os.getenv("PG_DEBUG"):
                print(f"[pg:error] {exc}", flush=True)
            raise IntegrityError(str(exc)) from exc
        except psycopg.Error as exc:
            if os.getenv("PG_DEBUG"):
                print(f"[pg:error] {exc}", flush=True)
            raise OperationalError(str(exc)) from exc

    def _needs_lastrowid(self, table: str) -> bool:
        tables = AUTO_ID_TABLES.get(self.connection.kind, set())
        if table in tables:
            return True
        key = (self.connection.schema, table)
        with _POOL_LOCK:
            cached = _SERIAL_ID_CACHE.get(key)
        if cached is not None:
            return cached
        row = self.connection.raw.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema=current_schema()
              AND table_name=%s AND column_name='id'
              AND column_default LIKE %s
            LIMIT 1
            """,
            (table, "nextval(%"),
        ).fetchone()
        result = bool(row)
        with _POOL_LOCK:
            _SERIAL_ID_CACHE[key] = result
        return result

    def fetchone(self):
        if self._synthetic:
            if self._synthetic_index >= len(self._synthetic):
                return None
            row = self._synthetic[self._synthetic_index]
            self._synthetic_index += 1
            return row
        row = self._cursor.fetchone()
        if row is None:
            return None
        columns = [item.name for item in self._cursor.description or ()]
        return PGRow(row, columns)

    def fetchall(self):
        if self._synthetic:
            rows = self._synthetic[self._synthetic_index:]
            self._synthetic_index = len(self._synthetic)
            return rows
        rows = self._cursor.fetchall()
        columns = [item.name for item in self._cursor.description or ()]
        return [PGRow(row, columns) for row in rows]

    def close(self) -> None:
        self._cursor.close()

    def __iter__(self):
        return iter(self.fetchall())


class PGConnection:
    def __init__(
        self,
        path: str,
        kind: str,
        schema: str,
        *,
        raw: Any | None = None,
    ):
        if not database_url():
            raise OperationalError("缺少 DATABASE_URL 或 CANVAS_DATABASE_URL")
        self.path = path
        self.kind = kind
        self.schema = schema
        self._pool_key = (
            f"{hashlib.sha256(database_url().encode('utf-8')).hexdigest()[:12]}:"
            f"{kind}:{schema}"
        )
        self._returned_to_pool = False
        self.raw = raw or self._new_raw()
        self.row_factory = None
        self.text_factory = None

    def _new_raw(self):
        raw = psycopg.connect(
            database_url(),
            connect_timeout=10,
            application_name="drama_canvas",
        )
        raw.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote_identifier(self.schema)}")
        raw.execute(f"SET search_path TO {_quote_identifier(self.schema)}")
        raw.execute("SET statement_timeout = '120s'")
        raw.execute("SET lock_timeout = '30s'")
        raw.execute("SET idle_in_transaction_session_timeout = '30s'")
        raw.commit()
        return raw

    @property
    def in_transaction(self) -> bool:
        from psycopg.pq import TransactionStatus

        return self.raw.info.transaction_status != TransactionStatus.IDLE

    def execute(self, sql: str, params: Sequence[Any] = ()) -> PGCursor:
        cursor = PGCursor(self)
        return cursor.execute(sql, params)

    def executescript(self, script: str) -> None:
        for statement in _split_script(script):
            self.execute(statement)

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def close(self) -> None:
        if self._returned_to_pool:
            return
        self._returned_to_pool = True
        try:
            self.raw.rollback()
        except psycopg.Error:
            try:
                self.raw.close()
            except psycopg.Error:
                pass
            self._release_pool_slot()
            return
        self._release_pool_slot()
        with _POOL_LOCK:
            pool = _POOLS.setdefault(self._pool_key, queue.LifoQueue())
            total_idle = sum(item.qsize() for item in _POOLS.values())
            can_return = (
                pool.qsize() < _POOL_SCHEMA_SIZE
                and total_idle < _POOL_SIZE
            )
        if can_return:
            pool.put(self.raw)
            return
        self.raw.close()

    def _release_pool_slot(self) -> None:
        with _POOL_LOCK:
            current = int(_POOL_IN_USE.get(self._pool_key, 0))
            if current <= 1:
                _POOL_IN_USE.pop(self._pool_key, None)
            else:
                _POOL_IN_USE[self._pool_key] = current - 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()


def connect_path(path: str | os.PathLike[str], kind: str = "canvas") -> PGConnection:
    schema = schema_for_path(path, kind)
    pool_key = (
        f"{hashlib.sha256(database_url().encode('utf-8')).hexdigest()[:12]}:"
        f"{kind}:{schema}"
    )
    with _POOL_LOCK:
        pool = _POOLS.setdefault(pool_key, queue.LifoQueue())
        in_use = int(_POOL_IN_USE.get(pool_key, 0))
        _POOL_IN_USE[pool_key] = in_use + 1
    try:
        raw = pool.get_nowait()
        if raw.closed:
            raw = None
    except queue.Empty:
        raw = None
    return PGConnection(str(path), kind, schema, raw=raw)


def ensure_canvas_indexes(connection: PGConnection) -> None:
    for statement in CANVAS_ROWID_INDEXES:
        connection.execute(statement)


def table_names(connection: PGConnection) -> list[str]:
    rows = connection.raw.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema=current_schema() AND table_type='BASE TABLE'
        ORDER BY table_name
        """
    ).fetchall()
    return [str(row[0]) for row in rows]
