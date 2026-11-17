from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _summary(*, ignore_empty: bool = False) -> dict:
    import canvas_store
    import media_delivery
    import tenant_context
    import tenant_registry

    mode = media_delivery.mode()
    tenants = []
    total_nodes = Counter()
    total_versions = Counter()
    readiness_by_tenant = {}
    for tenant in tenant_registry.list_tenants(include_disabled=True):
        context = tenant_context.make_context(
            tenant["id"],
            tenant.get("slug") or tenant["id"],
        )
        node_counts = Counter()
        version_counts = Counter()
        non_durable_count = 0
        non_durable_states = Counter()
        problem_projects = []
        with tenant_context.use_context(context):
            readiness_by_tenant[context.slug] = media_delivery.readiness()
            for project in canvas_store.list_projects():
                project_id = str(project.get("id") or "").strip()
                if not project_id:
                    continue
                stored = canvas_store.get_project(project_id)
                if not stored:
                    continue
                project_nodes = Counter()
                project_versions = Counter()
                project_non_durable = []
                for node in (stored.get("payload") or {}).get("nodes", []):
                    if node.get("type") not in {"image", "audio", "video"}:
                        continue
                    meta = node.get("metadata") or {}
                    state = media_delivery.delivery_state(meta)
                    if not (ignore_empty and state == "empty"):
                        node_counts[state] += 1
                        total_nodes[state] += 1
                        project_nodes[state] += 1
                    if (
                        state != "empty"
                        and not media_delivery.durable(state, mode)
                    ):
                        item = {
                            "projectId": project_id,
                            "nodeId": node.get("id"),
                            "title": node.get("title"),
                            "state": state,
                        }
                        project_non_durable.append(item)
                        non_durable_count += 1
                        non_durable_states[state] += 1
                    params = meta.get("params")
                    params = params if isinstance(params, dict) else {}
                    for key in ("mediaVersions", "videoVersions"):
                        for version in params.get(key) or []:
                            if not isinstance(version, dict):
                                continue
                            version_state = media_delivery.delivery_state(version)
                            if ignore_empty and version_state == "empty":
                                continue
                            version_counts[version_state] += 1
                            total_versions[version_state] += 1
                            project_versions[version_state] += 1
                if project_non_durable:
                    problem_projects.append({
                        "projectId": project_id,
                        "title": str(project.get("title") or ""),
                        "nonDurableCount": len(project_non_durable),
                        "states": dict(Counter(
                            item["state"] for item in project_non_durable
                        )),
                        "samples": project_non_durable[:20],
                    })
        tenants.append({
            "tenant": context.slug,
            "nodes": dict(node_counts),
            "versions": dict(version_counts),
            "deliveryReady": bool(
                (readiness_by_tenant.get(context.slug) or {}).get("ok")
            ),
            "nonDurableCount": non_durable_count,
            "nonDurableStates": dict(non_durable_states),
            "projects": problem_projects,
        })
    return {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "readiness": {
            "ok": all(
                item.get("ok")
                for item in readiness_by_tenant.values()
            ),
            "tenants": readiness_by_tenant,
        },
        "totalNodes": dict(total_nodes),
        "totalVersions": dict(total_versions),
        "tenants": tenants,
        "nonDurableCount": sum(
            int(tenant["nonDurableCount"]) for tenant in tenants
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report")
    parser.add_argument("--require-durable", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--ignore-empty", action="store_true")
    args = parser.parse_args()
    report = _summary(ignore_empty=args.ignore_empty)
    if args.summary_only:
        report.pop("tenants", None)
    report["ok"] = bool(
        report["readiness"].get("ok")
        and (
            not args.require_durable
            or report["nonDurableCount"] == 0
        )
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(text)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
