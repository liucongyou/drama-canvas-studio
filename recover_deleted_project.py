#!/usr/bin/env python3
"""Recover a deleted canvas payload from unvacuumed SQLite pages.

This script is read-only for the database. It scans raw SQLite bytes for JSON
objects containing the requested project id, validates balanced JSON, and
writes every distinct candidate plus the best candidate to a recovery folder.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


OBJECT_START = re.compile(rb'\{\s*"activeChatId"\s*:')


def balanced_json_end(data: bytes, start: int) -> int | None:
    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(data)):
        byte = data[index]
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:  # backslash
                escaped = True
            elif byte == 0x22:  # double quote
                in_string = False
            continue

        if byte == 0x22:
            in_string = True
        elif byte == 0x7B:  # {
            depth += 1
        elif byte == 0x7D:  # }
            depth -= 1
            if depth == 0:
                return index + 1
            if depth < 0:
                return None

    return None


def id_values(payload: dict[str, Any]) -> set[str]:
    return {
        str(payload.get(key))
        for key in ("id", "projectId", "workspaceProjectId")
        if payload.get(key) is not None
    }


def count_connections(payload: dict[str, Any]) -> int:
    connections = payload.get("connections")
    return len(connections) if isinstance(connections, list) else 0


def scan_candidates(data: bytes, project_id: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_payload_hashes: set[str] = set()
    pid_bytes = project_id.encode("utf-8")

    for match in OBJECT_START.finditer(data):
        start = match.start()
        end = balanced_json_end(data, start)
        if end is None:
            continue

        raw = data[start:end]
        if pid_bytes not in raw:
            continue

        try:
            text = raw.decode("utf-8")
            payload = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue

        if not isinstance(payload, dict) or project_id not in id_values(payload):
            continue

        raw_hash = hashlib.sha256(raw).hexdigest()
        if raw_hash in seen_payload_hashes:
            continue
        seen_payload_hashes.add(raw_hash)

        candidates.append(
            {
                "offset": start,
                "end_offset": end,
                "byte_length": len(raw),
                "sha256": raw_hash,
                "updated_at": payload.get("updatedAt"),
                "created_at": payload.get("createdAt"),
                "revision": payload.get("revision"),
                "title": payload.get("title"),
                "node_count": len(payload.get("nodes") or []),
                "connection_count": count_connections(payload),
                "payload": payload,
            }
        )

    candidates.sort(
        key=lambda item: (
            str(item.get("updated_at") or ""),
            int(item.get("revision") or 0),
            int(item.get("offset") or 0),
        ),
        reverse=True,
    )
    return candidates


def validate_payload(payload: dict[str, Any], project_id: str) -> list[str]:
    errors: list[str] = []
    if project_id not in id_values(payload):
        errors.append("project id fields do not contain the target id")

    nodes = payload.get("nodes")
    connections = payload.get("connections")
    if not isinstance(nodes, list):
        errors.append("nodes is not a list")
        nodes = []
    if not isinstance(connections, list):
        errors.append("connections is not a list")
        connections = []

    node_ids = {str(node.get("id")) for node in nodes if isinstance(node, dict) and node.get("id")}
    for index, connection in enumerate(connections):
        if not isinstance(connection, dict):
            errors.append(f"connection {index} is not an object")
            continue
        source = connection.get("source") or connection.get("from")
        target = connection.get("target") or connection.get("to")
        if source and str(source) not in node_ids:
            errors.append(f"connection {index} source is missing: {source}")
        if target and str(target) not in node_ids:
            errors.append(f"connection {index} target is missing: {target}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    data = args.db.read_bytes()
    candidates = scan_candidates(data, args.project_id)
    if not candidates:
        print(json.dumps({"ok": False, "error": "no valid payload candidates found"}, ensure_ascii=False))
        return 1

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    for index, candidate in enumerate(candidates, start=1):
        payload_path = output_dir / f"candidate-{index:02d}.json"
        payload_path.write_text(
            json.dumps(candidate["payload"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        candidate["file"] = payload_path.name
        del candidate["payload"]

    best_path = output_dir / "candidate-01.json"
    best_payload = json.loads(best_path.read_text(encoding="utf-8"))
    validation_errors = validate_payload(best_payload, args.project_id)

    # Keep the conventional payload.json for the restore step.
    (output_dir / "payload.json").write_text(
        json.dumps(best_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report = {
        "ok": True,
        "database": str(args.db.resolve()),
        "database_bytes": len(data),
        "database_sha256": hashlib.sha256(data).hexdigest(),
        "project_id": args.project_id,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "chosen": candidates[0],
        "validation_errors": validation_errors,
    }
    (output_dir / "scan-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if candidate_is_usable(candidates[0], validation_errors) else 2


def candidate_is_usable(candidate: dict[str, Any], validation_errors: list[str]) -> bool:
    return (
        candidate["node_count"] == 22
        and candidate["connection_count"] == 61
        and not validation_errors
    )


if __name__ == "__main__":
    raise SystemExit(main())
