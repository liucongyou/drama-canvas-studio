from __future__ import annotations

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
