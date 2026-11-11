from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.canvas_client import CanvasClient


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_project_index",
            "description": "Read project title, revision and bounded counts.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_context",
            "description": "Read a complete bounded project, node and asset context package.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nodeLimit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 300,
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_episode_context",
            "description": "Read one episode summary and node membership.",
            "parameters": {
                "type": "object",
                "properties": {"episodeId": {"type": "string"}},
                "required": ["episodeId"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_nodes",
            "description": "Search bounded project node summaries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "type": {"type": "string"},
                    "episodeId": {"type": "string"},
                    "assetId": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_node_detail",
            "description": "Read one node with bounded prompt, refs and metadata.",
            "parameters": {
                "type": "object",
                "properties": {"nodeId": {"type": "string"}},
                "required": ["nodeId"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_assets",
            "description": "List asset ledger entries by stable assetId.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "kind": {"type": "string"},
                    "seriesId": {"type": "string"},
                    "episodeId": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_asset_detail",
            "description": "Read one stable asset identity and current node.",
            "parameters": {
                "type": "object",
                "properties": {"assetId": {"type": "string"}},
                "required": ["assetId"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_node_dependencies",
            "description": "Read observational upstream and downstream summaries.",
            "parameters": {
                "type": "object",
                "properties": {"nodeId": {"type": "string"}},
                "required": ["nodeId"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_prompt_references",
            "description": "Resolve explicit asset identities without guessing by title.",
            "parameters": {
                "type": "object",
                "properties": {
                    "assetIds": {"type": "array", "items": {"type": "string"}},
                    "references": {"type": "array", "items": {"type": "object"}},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_skill_section",
            "description": "Read a bounded Markdown section from the active fixed skill.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 0},
                    "maxChars": {
                        "type": "integer",
                        "minimum": 1000,
                        "maximum": 16000,
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_feed",
            "description": "Run the existing canvas Feed preview validation.",
            "parameters": {
                "type": "object",
                "properties": {"feed": {"type": "object"}},
                "required": ["feed"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finalize_feed",
            "description": "Finalize a candidate Feed artifact without importing it.",
            "parameters": {
                "type": "object",
                "properties": {"feed": {"type": "object"}},
                "required": ["feed"],
            },
        },
    },
]


class CanvasTools:
    def __init__(
        self,
        project: dict[str, Any],
        canvas_client: CanvasClient | None,
        *,
        max_result_bytes: int = 32 * 1024,
        skill: dict[str, Any] | None = None,
    ):
        self.project = project if isinstance(project, dict) else {}
        self.canvas_client = canvas_client
        self.max_result_bytes = max(1024, int(max_result_bytes))
        self.skill = skill if isinstance(skill, dict) else {}

    def call(
        self,
        name: str,
        arguments: dict[str, Any] | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        args = arguments if isinstance(arguments, dict) else {}
        handlers = {
            "get_project_index": self._project_index,
            "get_project_context": self._project_context,
            "get_episode_context": self._episode_context,
            "search_nodes": self._search_nodes,
            "get_node_detail": self._node_detail,
            "list_assets": self._list_assets,
            "get_asset_detail": self._asset_detail,
            "get_node_dependencies": self._node_dependencies,
            "resolve_prompt_references": self._resolve_references,
            "read_skill_section": self._read_skill_section,
            "validate_feed": self._validate_feed,
            "finalize_feed": self._finalize_feed,
        }
        handler = handlers.get(str(name or ""))
        if not handler:
            raise ValueError(f"未知 Agent 工具：{name}")
        result = handler(args, context)
        return self._bounded(result)

    def _payload(self) -> dict[str, Any]:
        payload = self.project.get("payload")
        return payload if isinstance(payload, dict) else {}

    def _nodes(self) -> list[dict[str, Any]]:
        nodes = self._payload().get("nodes")
        return [item for item in nodes or [] if isinstance(item, dict)]

    def _connections(self) -> list[dict[str, Any]]:
        connections = self._payload().get("connections")
        return [item for item in connections or [] if isinstance(item, dict)]

    def _project_index(self, _args: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
        nodes = self._nodes()
        connections = self._connections()
        by_type: dict[str, int] = {}
        for node in nodes:
            node_type = str(node.get("type") or "unknown")
            by_type[node_type] = by_type.get(node_type, 0) + 1
        return {
            "id": str(self.project.get("id") or self._payload().get("id") or ""),
            "title": str(self.project.get("title") or self._payload().get("title") or ""),
            "revision": self.project.get("revision") or self._payload().get("revision") or 0,
            "nodeCount": len(nodes),
            "connectionCount": len(connections),
            "nodeTypes": by_type,
            "episodes": self._episode_summaries(),
        }

    def full_context(self, *, limit: int = 160) -> dict[str, Any]:
        """Return one bounded, deterministic context package for generation.

        The model should not have to discover the project by issuing dozens of
        node reads. This combines the same read-only data the tools expose.
        """
        nodes = self._nodes()
        node_limit = min(300, max(1, int(limit)))
        summaries = [self._context_node(node) for node in nodes[:node_limit]]
        assets = self._asset_records()[:200]
        payload = {
            "project": self._project_index({}, {}),
            "nodes": summaries,
            "nodeCount": len(nodes),
            "nodesTruncated": len(nodes) > node_limit,
            "assets": assets,
            "assetCount": len(assets),
        }
        # Keep the structure valid and preserve every node/asset summary.
        # If the package is large, trim prompt text first instead of slicing
        # serialized JSON into an unusable preview.
        max_bytes = max(self.max_result_bytes, 32 * 1024)
        for budget in (2400, 1200, 600, 240, 0):
            for item in summaries:
                if "prompt" in item:
                    item["prompt"] = item["prompt"][:budget]
                    item["promptTruncated"] = len(item["prompt"]) >= budget
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if len(encoded) <= max_bytes:
                payload["contextBytes"] = len(encoded)
                payload["contextBudget"] = max_bytes
                return payload
        return {
            "project": payload["project"],
            "nodes": summaries,
            "nodeCount": len(nodes),
            "nodesTruncated": len(nodes) > node_limit,
            "assets": assets,
            "assetCount": len(assets),
            "truncated": True,
            "contextBytes": len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
            "message": "节点与资产摘要完整保留，提示词已按预算压缩。",
        }

    def _context_node(self, node: dict[str, Any]) -> dict[str, Any]:
        summary = self._node_summary(node)
        node_type = str(node.get("type") or "")
        if node_type not in {"image", "audio"}:
            return summary
        metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        prompt = str(metadata.get("prompt") or node.get("prompt") or "")
        params = _safe_mapping(node.get("params"))
        return {
            **summary,
            "prompt": prompt[:2400],
            "promptTruncated": len(prompt) > 2400,
            "refs": _safe_list(params.get("refs")),
            "voiceRefs": _safe_list(params.get("voiceRefs")),
        }

    def _project_context(self, args: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
        return self.full_context(limit=int(args.get("nodeLimit") or 160))

    def _episode_summaries(self) -> list[dict[str, Any]]:
        episodes: dict[str, dict[str, Any]] = {}
        for group in self._payload().get("episodeImports") or []:
            if not isinstance(group, dict):
                continue
            episode_id = str(group.get("id") or "").strip()
            if not episode_id:
                continue
            episodes[episode_id] = {
                "id": episode_id,
                "title": str(group.get("title") or episode_id),
                "nodeIds": [str(item) for item in group.get("nodeIds") or []],
                "source": "EpisodeImports",
            }
        for node in self._nodes():
            metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
            ids = []
            if metadata.get("episodeId"):
                ids.append(str(metadata.get("episodeId")))
            for item in metadata.get("episodeIds") or []:
                if item and str(item) not in ids:
                    ids.append(str(item))
            for episode_id in ids:
                entry = episodes.setdefault(
                    episode_id,
                    {
                        "id": episode_id,
                        "title": episode_id,
                        "nodeIds": [],
                        "source": "NodeMetadata",
                    },
                )
                if str(node.get("id") or "") not in entry["nodeIds"]:
                    entry["nodeIds"].append(str(node.get("id") or ""))
        return list(episodes.values())[:100]

    def _episode_context(self, args: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
        episode_id = str(args.get("episodeId") or "").strip()
        episode = next(
            (item for item in self._episode_summaries() if item["id"] == episode_id),
            None,
        )
        if not episode:
            return {"episodeId": episode_id, "found": False, "nodes": []}
        node_ids = set(episode.get("nodeIds") or [])
        nodes = [
            self._node_summary(node)
            for node in self._nodes()
            if str(node.get("id") or "") in node_ids
        ]
        return {
            "episodeId": episode_id,
            "found": True,
            "title": episode.get("title"),
            "nodeCount": len(nodes),
            "videoCount": sum(1 for item in nodes if item.get("type") == "video"),
            "nodes": nodes[:80],
            "truncated": len(nodes) > 80,
        }

    def _search_nodes(self, args: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip().lower()
        node_type = str(args.get("type") or "").strip()
        episode_id = str(args.get("episodeId") or "").strip()
        asset_id = str(args.get("assetId") or "").strip()
        limit = min(100, max(1, int(args.get("limit") or 50)))
        result = []
        for node in self._nodes():
            if node_type and str(node.get("type") or "") != node_type:
                continue
            metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
            if asset_id and str(metadata.get("assetId") or "") != asset_id:
                continue
            episode_ids = {
                str(metadata.get("episodeId") or ""),
                *[str(item) for item in metadata.get("episodeIds") or []],
            }
            if episode_id and episode_id not in episode_ids:
                continue
            searchable = " ".join(
                [
                    str(node.get("id") or ""),
                    str(node.get("title") or ""),
                    str(node.get("type") or ""),
                    str(metadata.get("prompt") or node.get("prompt") or ""),
                ]
            ).lower()
            if query and query not in searchable:
                continue
            result.append(self._node_summary(node))
        return {
            "total": len(result),
            "nodes": result[:limit],
            "truncated": len(result) > limit,
        }

    def _node_detail(self, args: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
        node_id = str(args.get("nodeId") or "").strip()
        node = next((item for item in self._nodes() if str(item.get("id") or "") == node_id), None)
        if not node:
            return {"found": False, "nodeId": node_id}
        metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        prompt = str(metadata.get("prompt") or node.get("prompt") or "")
        return {
            "found": True,
            "node": {
                **self._node_summary(node),
                "prompt": prompt[:20_000],
                "promptTruncated": len(prompt) > 20_000,
                "params": _safe_mapping(node.get("params")),
                "refs": _safe_list((node.get("params") or {}).get("refs") if isinstance(node.get("params"), dict) else []),
                "voiceRefs": _safe_list((node.get("params") or {}).get("voiceRefs") if isinstance(node.get("params"), dict) else []),
            },
        }

    def _asset_records(self) -> list[dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        for node in self._nodes():
            metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
            asset_id = str(metadata.get("assetId") or "").strip()
            if not asset_id:
                continue
            record = records.setdefault(
                asset_id,
                {
                    "assetId": asset_id,
                    "seriesId": str(metadata.get("seriesId") or ""),
                    "variantKey": str(metadata.get("variantKey") or "default"),
                    "name": str(node.get("title") or asset_id),
                    "kind": _asset_kind(node),
                    "nodeIds": [],
                    "episodeIds": set(),
                    "available": False,
                    "versionCount": 0,
                },
            )
            record["nodeIds"].append(str(node.get("id") or ""))
            episode_id = str(metadata.get("episodeId") or "").strip()
            if episode_id:
                record["episodeIds"].add(episode_id)
            for item in metadata.get("episodeIds") or []:
                if item:
                    record["episodeIds"].add(str(item))
            status = str(metadata.get("status") or "")
            version_history = metadata.get("versionHistory") or metadata.get("versions") or []
            record["available"] = record["available"] or bool(
                metadata.get("mediaUrl")
                or metadata.get("content")
                or metadata.get("localFilename")
                or status in {"done", "ready", "settled", "completed"}
            )
            if isinstance(version_history, list):
                record["versionCount"] = max(record["versionCount"], len(version_history))
        result = []
        for record in records.values():
            record["episodeIds"] = sorted(record["episodeIds"])
            record["nodeId"] = record["nodeIds"][0] if record["nodeIds"] else ""
            result.append(record)
        return result

    def _list_assets(self, args: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip().lower()
        kind = str(args.get("kind") or "").strip()
        series_id = str(args.get("seriesId") or "").strip()
        episode_id = str(args.get("episodeId") or "").strip()
        limit = min(200, max(1, int(args.get("limit") or 100)))
        assets = []
        for asset in self._asset_records():
            if query and query not in (asset["name"] + " " + asset["assetId"]).lower():
                continue
            if kind and asset["kind"] != kind:
                continue
            if series_id and asset["seriesId"] != series_id:
                continue
            if episode_id and episode_id not in asset["episodeIds"]:
                continue
            assets.append(asset)
        return {
            "total": len(assets),
            "assets": assets[:limit],
            "truncated": len(assets) > limit,
        }

    def _asset_detail(self, args: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
        asset_id = str(args.get("assetId") or "").strip()
        asset = next((item for item in self._asset_records() if item["assetId"] == asset_id), None)
        if not asset:
            return {"found": False, "assetId": asset_id}
        node_id = str(asset.get("nodeId") or "")
        detail = self._node_detail({"nodeId": node_id}, {})
        return {"found": True, "asset": asset, "node": detail.get("node") if detail.get("found") else None}

    def _node_dependencies(self, args: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
        node_id = str(args.get("nodeId") or "").strip()
        nodes = {str(item.get("id") or ""): item for item in self._nodes()}
        if node_id not in nodes:
            return {"found": False, "nodeId": node_id, "upstream": [], "downstream": []}
        upstream_ids = []
        downstream_ids = []
        for connection in self._connections():
            source = str(connection.get("fromNodeId") or "")
            target = str(connection.get("toNodeId") or "")
            if target == node_id and source:
                upstream_ids.append(source)
            if source == node_id and target:
                downstream_ids.append(target)
        return {
            "found": True,
            "nodeId": node_id,
            "upstream": [_node_summary(nodes[item]) for item in upstream_ids if item in nodes][:50],
            "downstream": [_node_summary(nodes[item]) for item in downstream_ids if item in nodes][:50],
        }

    def _resolve_references(self, args: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
        assets = {item["assetId"]: item for item in self._asset_records()}
        requested: list[str] = []
        for value in args.get("assetIds") or []:
            if str(value or "").strip():
                requested.append(str(value).strip())
        for item in args.get("references") or []:
            if isinstance(item, dict) and str(item.get("assetId") or "").strip():
                requested.append(str(item["assetId"]).strip())
        resolved = []
        missing = []
        for asset_id in dict.fromkeys(requested):
            asset = assets.get(asset_id)
            if asset:
                resolved.append(asset)
            else:
                missing.append(asset_id)
        return {"references": resolved, "missing": missing, "ambiguous": []}

    def _read_skill_section(
        self,
        args: dict[str, Any],
        _context: dict[str, Any],
    ) -> dict[str, Any]:
        root_value = str(self.skill.get("rootPath") or "").strip()
        if not root_value:
            raise ValueError("当前运行没有固定技能目录")
        root = Path(root_value).resolve()
        relative = str(args.get("path") or "").replace("\\", "/").strip()
        part = Path(relative)
        if (
            not relative
            or part.is_absolute()
            or ".." in part.parts
            or part.suffix.lower() != ".md"
        ):
            raise ValueError("技能章节路径非法")
        path = (root / part).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError("技能章节不存在")
        text = path.read_text(encoding="utf-8", errors="replace")
        offset = max(0, int(args.get("offset") or 0))
        max_chars = min(
            16000,
            max(1000, int(args.get("maxChars") or 8000)),
        )
        content = text[offset:offset + max_chars]
        next_offset = offset + len(content)
        return {
            "path": part.as_posix(),
            "offset": offset,
            "totalChars": len(text),
            "content": content,
            "truncated": next_offset < len(text),
            "nextOffset": next_offset if next_offset < len(text) else None,
        }

    def _validate_feed(self, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        feed = args.get("feed")
        if not isinstance(feed, dict):
            raise ValueError("feed 必须是对象")
        if not self.canvas_client:
            raise ValueError("画布客户端不可用")
        project_id = str(context.get("projectId") or self.project.get("id") or "").strip()
        if not project_id:
            raise ValueError("缺少项目 ID")
        return self.canvas_client.preview_import(project_id, feed)

    def _finalize_feed(self, args: dict[str, Any], _context: dict[str, Any]) -> dict[str, Any]:
        feed = args.get("feed")
        if not isinstance(feed, dict):
            raise ValueError("feed 必须是对象")
        encoded = json.dumps(feed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return {
            "ok": True,
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "bytes": len(encoded),
            "feed": feed,
        }

    def _node_summary(self, node: dict[str, Any]) -> dict[str, Any]:
        metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        return {
            "id": str(node.get("id") or ""),
            "type": str(node.get("type") or ""),
            "title": str(node.get("title") or ""),
            "status": str(metadata.get("status") or node.get("status") or ""),
            "episodeId": str(metadata.get("episodeId") or ""),
            "assetId": str(metadata.get("assetId") or ""),
            "seriesId": str(metadata.get("seriesId") or ""),
            "variantKey": str(metadata.get("variantKey") or ""),
        }

    def _bounded(self, result: dict[str, Any]) -> dict[str, Any]:
        raw = json.dumps(result, ensure_ascii=False).encode("utf-8")
        if len(raw) <= self.max_result_bytes:
            return result
        preview = raw[: self.max_result_bytes].decode("utf-8", errors="ignore")
        return {
            "truncated": True,
            "originalBytes": len(raw),
            "preview": preview,
            "message": "工具结果超过限制，请使用更精确的过滤条件。",
        }


def _asset_kind(node: dict[str, Any]) -> str:
    node_type = str(node.get("type") or "")
    title = str(node.get("title") or "")
    if node_type == "audio":
        return "voice"
    if "角色" in title or "人物" in title:
        return "character"
    if "场景" in title:
        return "scene"
    if "道具" in title:
        return "prop"
    if "群像" in title:
        return "group"
    return node_type or "image"


def _node_summary(node: dict[str, Any]) -> dict[str, Any]:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return {
        "id": str(node.get("id") or ""),
        "type": str(node.get("type") or ""),
        "title": str(node.get("title") or ""),
        "status": str(metadata.get("status") or node.get("status") or ""),
        "episodeId": str(metadata.get("episodeId") or ""),
        "assetId": str(metadata.get("assetId") or ""),
        "seriesId": str(metadata.get("seriesId") or ""),
        "variantKey": str(metadata.get("variantKey") or ""),
    }


def _safe_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    blocked = {"apikey", "api_key", "authorization", "cookie", "token", "secret"}
    return {
        str(key): item
        for key, item in value.items()
        if str(key).lower() not in blocked
    }


def _safe_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    return value[:100]
