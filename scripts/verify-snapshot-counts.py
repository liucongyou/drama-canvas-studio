from __future__ import annotations

import argparse
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


def inspect_counts(database_url):
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        schemas = [
            row["schema_name"]
            for row in connection.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name LIKE 'tenant_%' ORDER BY schema_name"
            )
        ]
        result = {"schemas": {}, "totals": {}}
        totals = {
            "projects": 0,
            "nodes": 0,
            "tasks": 0,
            "snapshotRows": 0,
            "snapshotBytes": 0,
        }
        for schema in schemas:
            quoted = '"' + schema.replace('"', '""') + '"'
            item = {}
            for key, query in {
                "projects": f"SELECT COUNT(*) AS value FROM {quoted}.canvas_projects",
                "nodes": f"SELECT COUNT(*) AS value FROM {quoted}.canvas_projection_nodes",
                "tasks": f"SELECT COUNT(*) AS value FROM {quoted}.generation_tasks",
                "snapshotRows": f"SELECT COUNT(*) AS value FROM {quoted}.canvas_snapshots",
                "snapshotBytes": (
                    "SELECT COALESCE(SUM(octet_length(payload_json)),0) AS value "
                    f"FROM {quoted}.canvas_snapshots"
                ),
            }.items():
                try:
                    value = int(connection.execute(query).fetchone()["value"] or 0)
                except psycopg.Error:
                    connection.rollback()
                    value = 0
                item[key] = value
                totals[key] += value
            result["schemas"][schema] = item
        result["totals"] = totals
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--expected")
    parser.add_argument("--output")
    args = parser.parse_args()
    actual = inspect_counts(args.url)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            json.dumps(actual, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.expected:
        expected = json.loads(
            Path(args.expected).read_text(encoding="utf-8")
        )
        if actual != expected:
            print(json.dumps({
                "ok": False,
                "expected": expected,
                "actual": actual,
            }, ensure_ascii=False, indent=2))
            return 1
    print(json.dumps({"ok": True, **actual}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
