from __future__ import annotations

import re
from pathlib import Path

import pg_compat


def test_literal_percent_patterns_are_escaped_for_psycopg_parameters() -> None:
    sql = (
        "SELECT COUNT(*) FROM nodes WHERE project_id=? "
        "AND metadata_json LIKE '%\"status\"%failed%' "
        "AND metadata_json NOT LIKE '%参考素材缺失%'"
    )
    translated = pg_compat._translate_sql(sql)
    escaped = pg_compat._escape_sql_literal_percents(translated)

    assert "project_id=%s" in escaped
    assert "LIKE '%%\"status\"%%failed%%'" in escaped
    assert "LIKE '%%参考素材缺失%%'" in escaped
    assert "%s" in escaped.split("LIKE", 1)[0]


def test_remaining_sqlite_constructs_translate_without_dialect_assumptions() -> None:
    scalar_max = pg_compat._translate_sql(
        "UPDATE canvas_projects SET episode_count=MAX(1,episode_count) WHERE id=?"
    )
    assert "GREATEST(1, episode_count)" in scalar_max

    aggregate = pg_compat._translate_sql(
        "SELECT COALESCE(GROUP_CONCAT(DISTINCT NULLIF(cost_currency,'')),'') "
        "FROM generation_tasks WHERE created_at>=?"
    )
    assert "STRING_AGG(DISTINCT NULLIF(cost_currency,''), ',')" in aggregate

    json_path = pg_compat._translate_sql(
        "SELECT json_extract(details_json, '$.auditContext.requestId') WHERE id=?"
    )
    assert "details_json::jsonb #>> '{auditContext, requestId}'" in json_path

    replaceable = pg_compat._translate_sql(
        "INSERT OR REPLACE INTO canvas_snapshots(id,value) VALUES(?,?)"
    )
    assert replaceable.startswith("INSERT INTO canvas_snapshots")
    assert "ON CONFLICT(id) DO UPDATE SET value=EXCLUDED.value" in replaceable

    limited = pg_compat._translate_sql(
        "SELECT id FROM tasks ORDER BY rowid DESC LIMIT -1 OFFSET ?"
    )
    assert "LIMIT ALL OFFSET %s" in limited


def test_runtime_sql_contains_no_sqlite_scalar_max_or_min_placeholders() -> None:
    source = Path(__file__).with_name("canvas_store.py").read_text(encoding="utf-8")
    assert not re.search(r"\b(?:MAX|MIN)\([^)]*\?", source, re.I)
