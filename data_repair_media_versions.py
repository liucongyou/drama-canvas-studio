from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import canvas_store


def _repair_snapshot_node(node, report):
    if not isinstance(node, dict):
        return
    node_type = str(node.get("type") or "")
    if node_type not in {"image", "audio", "video"}:
        return
    metadata = node.get("metadata")
    if not isinstance(metadata, dict):
        report["scanned"] += 1
        return
    repair = canvas_store._repair_media_version_metadata(
        metadata,
        node_type,
    )
    report["scanned"] += 1
    if not repair or not repair.get("changed"):
        return
    report["nodesChanged"] += 1
    report["duplicateIdsBefore"] += int(repair["duplicateIdsBefore"])
    report["duplicateIdsAfter"] += int(repair["duplicateIdsAfter"])
    report["missingIdsBefore"] += int(repair["missingIdsBefore"])
    report["activeMismatchBefore"] += int(repair["activeMismatchBefore"])
    report["activeMismatchAfter"] += int(repair["activeMismatchAfter"])
    report["unresolved"] += int(repair["unresolved"])
    report["urlsLost"] += int(repair["urlsLost"])
    report["nodes"].append({
        "nodeId": str(node.get("id") or ""),
        "nodeType": node_type,
        "title": str(node.get("title") or ""),
        "activeVersionId": repair["activeVersionId"],
        "unresolved": bool(repair["unresolved"]),
    })


def _walk_snapshot(value, report):
    if isinstance(value, dict):
        if (
            value.get("type") in {"image", "audio", "video"}
            and isinstance(value.get("metadata"), dict)
        ):
            _repair_snapshot_node(value, report)
            return
        for item in value.values():
            _walk_snapshot(item, report)
    elif isinstance(value, list):
        for item in value:
            _walk_snapshot(item, report)


def repair_snapshot(snapshot):
    repaired = copy.deepcopy(snapshot)
    report = {
        "scanned": 0,
        "nodesChanged": 0,
        "duplicateIdsBefore": 0,
        "duplicateIdsAfter": 0,
        "missingIdsBefore": 0,
        "activeMismatchBefore": 0,
        "activeMismatchAfter": 0,
        "unresolved": 0,
        "urlsLost": 0,
        "nodes": [],
    }
    _walk_snapshot(repaired, report)
    return repaired, report


def main():
    parser = argparse.ArgumentParser(
        description="Repair duplicate and mismatched media version identities.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--input")
    parser.add_argument("--output")
    args = parser.parse_args()
    apply = bool(args.apply)

    if args.input:
        source = json.loads(Path(args.input).read_text(encoding="utf-8"))
        repaired, report = repair_snapshot(source)
        report["apply"] = apply
        report["mode"] = "snapshot"
        if args.output:
            Path(args.output).write_text(
                json.dumps(repaired, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        payload = report
    else:
        payload = canvas_store.repair_media_version_identities(apply=apply)
        payload["mode"] = "database"

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
