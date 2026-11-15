from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import canvas_store
import tenant_context
import tenant_registry


TITLE = "Industrial Browser Fixture"
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAusB9Y9Z7eQAAAAASUVORK5CYII="
)


def build_payload(count):
    kinds = ("image", "video", "audio")
    nodes = []
    for index in range(count):
        kind = "image" if index < 2 else kinds[index % len(kinds)]
        extension = "png" if index < 2 else "bin"
        first_url = f"/uploads/fixture-{index}-a.{extension}"
        second_url = f"/uploads/fixture-{index}-b.{extension}"
        versions = [
            {
                "versionId": f"{kind}:fixture-{index}-a",
                "url": first_url,
                "sourceType": "generated",
            },
            {
                "versionId": f"{kind}:fixture-{index}-b",
                "url": second_url,
                "sourceType": "generated",
            },
        ]
        nodes.append({
            "id": f"fixture-node-{index}",
            "type": kind,
            "title": f"Fixture {kind} {index}",
            "position": {
                "x": (index % 8) * 360,
                "y": (index // 8) * 300,
            },
            "metadata": {
                "episodeId": "EP001",
                "status": "done",
                "content": second_url,
                "prompt": f"fixture prompt {index}",
                "params": {
                    "mediaVersions": versions,
                    "activeVersionId": versions[-1]["versionId"],
                    "mediaVersionIndex": 1,
                    **(
                        {
                            "videoVersions": [dict(item) for item in versions],
                            "videoVersionIndex": 1,
                        }
                        if kind == "video"
                        else {}
                    ),
                },
            },
        })
    return {
        "nodes": nodes,
        "connections": [],
        "episodeImports": [{
            "id": "EP001",
            "title": "EP001",
            "nodeIds": [node["id"] for node in nodes],
            "videoNodeIds": [
                node["id"] for node in nodes if node["type"] == "video"
            ],
            "connectionIds": [],
        }],
    }


def write_fixture_media(count):
    upload_dir = tenant_context.current_upload_dir()
    upload_dir.mkdir(parents=True, exist_ok=True)
    for index in range(min(2, count)):
        for suffix in ("a", "b"):
            (upload_dir / f"fixture-{index}-{suffix}.png").write_bytes(PNG_1X1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--count", type=int, default=60)
    parser.add_argument("--tenant", default="owner")
    args = parser.parse_args()
    os.environ["CANVAS_DB_BACKEND"] = "postgres"
    os.environ["CANVAS_DATABASE_URL"] = args.url
    os.environ["CANVAS_MULTI_TENANT"] = "1"
    for tenant in tenant_registry.list_tenants(include_disabled=True):
        if tenant["slug"] != args.tenant:
            continue
        with tenant_context.use_tenant(tenant["id"], tenant["slug"]):
            write_fixture_media(args.count)
            payload = build_payload(args.count)
            existing = next(
                (
                    project for project in canvas_store.list_projects()
                    if project.get("title") == TITLE
                ),
                None,
            )
            project_id = (
                existing["id"]
                if existing
                else canvas_store.create_project(
                    TITLE,
                    payload=payload,
                )
            )
            if existing:
                canvas_store.update_project(project_id, payload=payload)
            print(project_id)
            return 0
    raise SystemExit(f"tenant not found: {args.tenant}")


if __name__ == "__main__":
    raise SystemExit(main())
