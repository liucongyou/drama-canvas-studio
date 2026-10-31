# -*- coding: utf-8 -*-
"""Transactional canvas repository boundary.

The first industrial release still uses per-tenant SQLite. This module keeps
payload mutation rules and revision checks out of HTTP handlers while preserving
that storage implementation as an internal detail.
"""
from __future__ import annotations

import copy
import threading
from contextlib import contextmanager

import canvas_store


PROJECT_LOCKS: dict[str, threading.RLock] = {}
PROJECT_LOCKS_GUARD = threading.Lock()


@contextmanager
def project_write_lock(project_id: str):
    key = str(project_id or "").strip()
    if not key:
        raise ValueError("project_id is required for project_write_lock")
    with PROJECT_LOCKS_GUARD:
        lock = PROJECT_LOCKS.setdefault(key, threading.RLock())
    with lock:
        yield


class CanvasRepository:
    def list_projects(self):
        return canvas_store.list_projects()

    def list_project_summaries(self):
        return canvas_store.list_project_summaries()

    def project_activity_summaries(self):
        return canvas_store.project_activity_summaries()

    def get_project(self, project_id):
        return canvas_store.get_project(project_id)

    def get_workspace_index(self, project_id):
        return canvas_store.get_project_workspace_index(project_id)

    def get_workspace_nodes(self, project_id, episode_id=None):
        return canvas_store.get_project_workspace_nodes(project_id, episode_id)

    def get_notes(self, project_id, episode_id):
        if not canvas_store.project_exists(project_id):
            raise KeyError("项目不存在")
        return canvas_store.get_episode_notes(project_id, episode_id)

    def save_notes(
        self,
        project_id,
        episode_id,
        notes,
        *,
        if_revision=None,
    ):
        with project_write_lock(project_id):
            current_revision = canvas_store.get_project_revision(project_id)
            if current_revision is None:
                raise KeyError("项目不存在")
            expected = self._expected_revision(
                {"revision": current_revision},
                if_revision,
            )
            result = canvas_store.replace_episode_notes(
                project_id,
                episode_id,
                notes,
                if_revision=expected,
            )
            if result is None:
                raise KeyError("项目不存在")
            return result

    def create_project(self, title, payload=None):
        return canvas_store.create_project(title, payload)

    def delete_project(self, project_id):
        return canvas_store.delete_project(project_id)

    def commit_payload(
        self,
        project_id,
        payload,
        *,
        title=None,
        if_revision=None,
        snapshot_mode="auto",
    ):
        with project_write_lock(project_id):
            current = self._require_project(project_id)
            expected = self._expected_revision(current, if_revision)
            canvas_store.update_project(
                project_id,
                title=title if title is not None else current["title"],
                payload=payload,
                if_revision=expected,
                snapshot_mode=snapshot_mode,
            )
            return self._require_project(project_id)

    def patch_project_metadata(self, project_id, *, title=None, if_revision=None):
        with project_write_lock(project_id):
            current = self._require_project(project_id)
            expected = self._expected_revision(current, if_revision)
            payload = copy.deepcopy(current["payload"])
            new_title = str(title).strip() if title is not None else current["title"]
            if not new_title:
                raise ValueError("title 不能为空")
            payload["title"] = new_title
            canvas_store.update_project(
                project_id,
                title=new_title,
                payload=payload,
                if_revision=expected,
            )
            return self._require_project(project_id)

    def save_viewport(self, project_id, viewport, *, episode_id=None, if_revision=None):
        if not isinstance(viewport, dict):
            raise ValueError("viewport 必须是对象")
        with project_write_lock(project_id):
            normalized = {
                "x": float(viewport.get("x") or 0),
                "y": float(viewport.get("y") or 0),
                "zoom": float(viewport.get("zoom") or 1),
            }
            ep_id = str(episode_id or "").strip().upper()
            saved = canvas_store.update_project_viewport(
                project_id,
                normalized,
                episode_id=ep_id or None,
                if_revision=if_revision,
            )
            if saved is None:
                raise KeyError("项目不存在")
            return normalized

    def upsert_nodes(self, project_id, nodes, *, episode_id=None, if_revision=None):
        if not isinstance(nodes, list):
            raise ValueError("nodes 必须是数组")
        with project_write_lock(project_id):
            current = self._require_project(project_id)
            expected = self._expected_revision(current, if_revision)
            payload = copy.deepcopy(current["payload"])
            by_id = {
                node.get("id"): node
                for node in payload.setdefault("nodes", [])
                if isinstance(node, dict) and node.get("id")
            }
            created = 0
            for item in nodes:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                node_id = str(item["id"])
                existing = by_id.get(node_id)
                if existing is None:
                    existing = copy.deepcopy(item)
                    payload["nodes"].append(existing)
                    by_id[node_id] = existing
                    created += 1
                else:
                    if "title" in item:
                        existing["title"] = item["title"]
                    if isinstance(item.get("position"), dict):
                        existing["position"] = copy.deepcopy(item["position"])
                    if isinstance(item.get("metadata"), dict):
                        existing["metadata"] = {
                            **(existing.get("metadata") or {}),
                            **copy.deepcopy(item["metadata"]),
                        }
                    if item.get("type") in {"text", "image", "audio", "video"}:
                        existing["type"] = item["type"]
                existing["updatedAt"] = canvas_store._now_iso()
            ep_id = str(episode_id or "").strip().upper()
            if ep_id:
                groups = payload.setdefault("episodeImports", [])
                group = next(
                    (
                        item
                        for item in groups
                        if isinstance(item, dict)
                        and str(item.get("id") or "").upper() == ep_id
                    ),
                    None,
                )
                if group is None:
                    group = {"id": ep_id, "nodeIds": []}
                    groups.append(group)
                ids = group.setdefault("nodeIds", [])
                for item in nodes:
                    node_id = item.get("id") if isinstance(item, dict) else None
                    if node_id and node_id not in ids:
                        ids.append(node_id)
            canvas_store.update_project(
                project_id,
                payload=payload,
                if_revision=expected,
            )
            return {"created": created, "updated": len(nodes) - created}

    def patch_node(
        self,
        project_id,
        node_id,
        patch,
        *,
        episode_id=None,
        if_revision=None,
    ):
        with project_write_lock(project_id):
            if not str(episode_id or "").strip():
                current = self._require_project(project_id)
                node = next(
                    (
                        item
                        for item in current["payload"].get("nodes", [])
                        if item.get("id") == node_id
                    ),
                    None,
                )
                if not node:
                    raise KeyError("节点不存在")
                candidate_episode = (
                    node.get("episodeId")
                    or (node.get("metadata") or {}).get("episodeId")
                )
                row = None
                connection = canvas_store._connect()
                try:
                    row = connection.execute(
                        "SELECT episode_id FROM canvas_projection_nodes "
                        "WHERE project_id=? AND node_id=?",
                        (project_id, node_id),
                    ).fetchone()
                finally:
                    connection.close()
                if (
                    row
                    and str(row[0] or "").strip()
                    and str(row[0]).strip() != canvas_store._UNASSIGNED_EPISODE_ID
                ):
                    candidate_episode = str(row[0]).strip()
                groups = current["payload"].get("episodeImports") or []
                if (
                    not candidate_episode
                    or candidate_episode == canvas_store._UNASSIGNED_EPISODE_ID
                ):
                    matches = [
                        str(group.get("id") or "")
                        for group in groups
                        if isinstance(group, dict)
                        and node_id in (group.get("nodeIds") or [])
                    ]
                    if len(matches) == 1:
                        candidate_episode = matches[0]
                if not candidate_episode and len(groups) == 1:
                    candidate_episode = groups[0].get("id")
                episode_id = candidate_episode
            saved = canvas_store.update_project_node(
                project_id,
                node_id,
                patch_node=patch,
                episode_id=episode_id,
                if_revision=if_revision,
            )
            if saved is None:
                raise KeyError("节点不存在")
            return copy.deepcopy(saved["node"])

    def delete_nodes(self, project_id, node_ids, *, if_revision=None):
        targets = {str(item) for item in node_ids if item}
        if not targets:
            return 0
        with project_write_lock(project_id):
            current = self._require_project(project_id)
            expected = self._expected_revision(current, if_revision)
            payload = copy.deepcopy(current["payload"])
            before = len(payload.get("nodes", []))
            payload["nodes"] = [
                node
                for node in payload.get("nodes", [])
                if node.get("id") not in targets
            ]
            payload["connections"] = [
                edge
                for edge in payload.get("connections", [])
                if edge.get("fromNodeId") not in targets
                and edge.get("toNodeId") not in targets
            ]
            removed = before - len(payload["nodes"])
            if removed:
                canvas_store.update_project(
                    project_id,
                    payload=payload,
                    if_revision=expected,
                )
            return removed

    def patch_edges(self, project_id, edges, *, if_revision=None):
        if not isinstance(edges, list):
            raise ValueError("edges 必须是数组")
        with project_write_lock(project_id):
            current = self._require_project(project_id)
            expected = self._expected_revision(current, if_revision)
            payload = copy.deepcopy(current["payload"])
            existing = {
                edge.get("id"): edge
                for edge in payload.setdefault("connections", [])
                if isinstance(edge, dict) and edge.get("id")
            }
            for edge in edges:
                if not isinstance(edge, dict) or not edge.get("id"):
                    continue
                existing[edge["id"]] = copy.deepcopy(edge)
            payload["connections"] = list(existing.values())
            canvas_store.update_project(
                project_id,
                payload=payload,
                if_revision=expected,
            )
            return copy.deepcopy(payload["connections"])

    def list_versions(self, project_id, limit=50):
        self._require_project(project_id)
        return canvas_store.list_snapshots(project_id, limit)

    def create_version(self, project_id, title=None, *, if_revision=None):
        with project_write_lock(project_id):
            current = self._require_project(project_id)
            self._expected_revision(current, if_revision)
            return canvas_store.create_snapshot(
                project_id,
                title=title,
                kind="manual",
            )

    def get_version(self, project_id, version_id):
        snapshot = canvas_store.get_snapshot(project_id, version_id)
        if snapshot is None:
            raise KeyError("项目版本不存在")
        return snapshot

    def preview_version(self, project_id, version_id):
        snapshot = self.get_version(project_id, version_id)
        current = self._require_project(project_id)
        current_nodes = {
            str(node.get("id")): node
            for node in current["payload"].get("nodes", [])
            if isinstance(node, dict) and node.get("id")
        }
        snapshot_nodes = {
            str(node.get("id")): node
            for node in snapshot["payload"].get("nodes", [])
            if isinstance(node, dict) and node.get("id")
        }
        added = sorted(set(snapshot_nodes) - set(current_nodes))
        removed = sorted(set(current_nodes) - set(snapshot_nodes))
        changed_prompts = 0
        changed_media = 0
        for node_id in set(current_nodes) & set(snapshot_nodes):
            before = snapshot_nodes[node_id].get("metadata") or {}
            after = current_nodes[node_id].get("metadata") or {}
            if str(before.get("prompt") or "") != str(after.get("prompt") or ""):
                changed_prompts += 1
            if str(before.get("content") or "") != str(after.get("content") or ""):
                changed_media += 1
        return {
            **{
                key: snapshot[key]
                for key in (
                    "id",
                    "title",
                    "nodeCount",
                    "connectionCount",
                    "revision",
                    "kind",
                    "createdAt",
                )
            },
            "currentRevision": current["revision"],
            "diff": {
                "nodesAddedByRestore": len(added),
                "nodesRemovedByRestore": len(removed),
                "promptsChanged": changed_prompts,
                "mediaChanged": changed_media,
            },
        }

    def restore_version(self, project_id, version_id, *, if_revision=None):
        with project_write_lock(project_id):
            current = self._require_project(project_id)
            expected = self._expected_revision(current, if_revision)
            snapshot = self.get_version(project_id, version_id)
            # A destructive restore must leave a recoverable copy of the exact
            # state the user had immediately before confirming it.
            canvas_store.create_snapshot(
                project_id,
                title="恢复前自动保护",
                kind="manual",
            )
            payload = copy.deepcopy(snapshot["payload"])
            title = (
                str(payload.get("title") or "").strip()
                or str(current.get("title") or "").strip()
                or "未命名画布"
            )
            canvas_store.update_project(
                project_id,
                title=title,
                payload=payload,
                if_revision=expected,
                snapshot_mode="none",
            )
            restored = self._require_project(project_id)
            return {
                "id": project_id,
                "title": restored["title"],
                "revision": restored["revision"],
                "restoredFrom": version_id,
            }

    @staticmethod
    def _expected_revision(project, if_revision):
        current = int(project.get("revision") or 0)
        if if_revision is not None and current != int(if_revision):
            raise canvas_store.RevisionConflictError(
                f"项目已更新：期望 revision={if_revision}，实际 revision={current}"
            )
        return current

    def _require_project(self, project_id):
        project = self.get_project(project_id)
        if not project:
            raise KeyError("项目不存在")
        return project


repository = CanvasRepository()
