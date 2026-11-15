from __future__ import annotations

import argparse
import json
import urllib.request

import psycopg
from psycopg import sql


def get_json(url):
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()
    report = {
        "ok": False,
        "expectedCommit": args.expected_commit,
        "checks": {},
    }
    try:
        health = get_json(args.base.rstrip("/") + "/healthz")
        ready = get_json(args.base.rstrip("/") + "/readyz")
        report["checks"]["health"] = bool(
            isinstance(health, dict) and health.get("ok")
        )
        report["checks"]["ready"] = bool(
            isinstance(ready, dict) and ready.get("ok")
        )
        active_tasks = 0
        duplicate_active_tasks = 0
        with psycopg.connect(args.database_url) as connection:
            schemas = [
                row[0]
                for row in connection.execute(
                    "SELECT schema_name "
                    "FROM information_schema.schemata "
                    "WHERE schema_name LIKE 'tenant_%' "
                    "ORDER BY schema_name"
                ).fetchall()
            ]
            for schema in schemas:
                table = connection.execute(
                    "SELECT to_regclass(%s)",
                    (f"{schema}.generation_tasks",),
                ).fetchone()
                if not table or not table[0]:
                    continue
                active_tasks += int(
                    connection.execute(
                        sql.SQL(
                            "SELECT COUNT(*) FROM {}.generation_tasks "
                            "WHERE status IN "
                            "('queued','submitting','running')"
                        ).format(sql.Identifier(schema))
                    ).fetchone()[0]
                    or 0
                )
                duplicate_active_tasks += int(
                    connection.execute(
                        sql.SQL(
                            "SELECT COUNT(*) FROM ("
                            "SELECT project_id,node_id FROM "
                            "{}.generation_tasks "
                            "WHERE status IN "
                            "('queued','submitting','running') "
                            "GROUP BY project_id,node_id "
                            "HAVING COUNT(*) > 1"
                            ") AS duplicates"
                        ).format(sql.Identifier(schema))
                    ).fetchone()[0]
                    or 0
                )
        report["tenantSchemas"] = schemas
        report["checks"]["tenantSchemasPresent"] = bool(schemas)
        report["activeTasks"] = active_tasks
        report["duplicateActiveTasks"] = duplicate_active_tasks
        report["checks"]["noDuplicateActiveTasks"] = (
            duplicate_active_tasks == 0
        )
        commit = str(health.get("releaseCommit") or "").strip()
        ready_commit = str(ready.get("releaseCommit") or "").strip()
        report["deployedCommitReadback"] = commit
        report["checks"]["commitMatches"] = (
            commit == args.expected_commit
            and ready_commit == args.expected_commit
        )
        report["ok"] = all(report["checks"].values())
    except Exception as exc:
        report["error"] = str(exc)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
