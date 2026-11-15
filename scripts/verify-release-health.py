from __future__ import annotations

import argparse
import json
import urllib.request

import psycopg


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
        metrics = get_json(args.base.rstrip("/") + "/api/metrics")
        report["checks"]["health"] = bool(health)
        report["checks"]["metrics"] = isinstance(metrics, dict)
        report["checks"]["activeTasksStable"] = int(
            metrics.get("activeTasks") or 0
        ) >= 0
        with psycopg.connect(args.database_url) as connection:
            row = connection.execute(
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.schemata "
                "WHERE schema_name LIKE 'tenant_%'"
                ")"
            ).fetchone()
            report["checks"]["tenantSchemasPresent"] = bool(row[0])
        commit = str(metrics.get("releaseCommit") or "").strip()
        report["deployedCommitReadback"] = commit
        report["checks"]["commitMatches"] = (
            commit == args.expected_commit
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
