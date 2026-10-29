#!/usr/bin/env python3
"""Repair imported voice references so only actual dialogue speakers remain."""

import argparse
import json
import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import import_feed


def repair_database(path, project_ids=None, dry_run=False):
    db = sqlite3.connect(path)
    try:
        rows = db.execute(
            "select id, title, payload_json, revision from canvas_projects"
        ).fetchall()
        wanted = {str(value) for value in (project_ids or []) if str(value)}
        results = []
        for project_id, title, payload_json, revision in rows:
            if wanted and project_id not in wanted:
                continue
            payload = json.loads(payload_json)
            changed = import_feed.repair_payload_prompt_mentions(payload)
            if changed and not dry_run:
                updated_at = import_feed._now_iso()
                payload["updatedAt"] = updated_at
                db.execute(
                    "update canvas_projects "
                    "set payload_json = ?, revision = ?, updated_at = ? "
                    "where id = ?",
                    (
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                        int(revision or 1) + 1,
                        updated_at,
                        project_id,
                    ),
                )
            results.append({
                "id": project_id,
                "title": title,
                "changed": changed,
                "dry_run": dry_run,
            })
        if not dry_run:
            db.commit()
        return results
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--project-id", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    results = repair_database(args.db, args.project_id, args.dry_run)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
