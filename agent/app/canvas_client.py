from __future__ import annotations

from typing import Any
import json

import httpx

from app.context import signed_headers, unsigned_headers
from app.config import get_settings


class CanvasError(RuntimeError):
    pass


class CanvasClient:
    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        *,
        tenant_id: str = "",
        account_id: str = "",
        request_id: str = "",
    ):
        settings = get_settings()
        self.base_url = (base_url or settings.canvas_base_url).rstrip("/")
        self.timeout = timeout or settings.request_timeout_seconds
        self.tenant_id = str(tenant_id or settings.canvas_tenant_id or "").strip()
        self.account_id = str(account_id or "").strip()
        self.request_id = str(request_id or "").strip()
        self.internal_secret = str(settings.agent_internal_secret or "").strip()

    def _client(self) -> httpx.Client:
        settings = get_settings()
        headers = {}
        if settings.canvas_cookie:
            headers["Cookie"] = settings.canvas_cookie
        if settings.canvas_token:
            headers["Authorization"] = f"Bearer {settings.canvas_token}"
        return httpx.Client(base_url=self.base_url, timeout=self.timeout, trust_env=False, headers=headers)

    def _internal_headers(
        self,
        method: str,
        path: str,
        body: bytes = b"",
    ) -> dict[str, str]:
        if not self.account_id:
            raise CanvasError("Agent 画布请求缺少账号上下文")
        if not self.internal_secret:
            return unsigned_headers(
                tenant_id=self.tenant_id,
                account_id=self.account_id,
                request_id=self.request_id,
            )
        return signed_headers(
            secret=self.internal_secret,
            method=method,
            path=path,
            tenant_id=self.tenant_id,
            account_id=self.account_id,
            body=body,
            request_id=self.request_id,
        )

    def _use_internal_paths(self) -> bool:
        return bool(self.tenant_id and self.account_id)

    def list_projects(self) -> list[dict[str, Any]]:
        path = "/api/internal/agent/projects"
        with self._client() as client:
            response = client.get(
                path,
                headers=self._internal_headers("GET", path),
            )
            response.raise_for_status()
            data = response.json()
        projects = data.get("projects") if isinstance(data, dict) else data
        return projects if isinstance(projects, list) else []

    def get_project(self, project_id: str) -> dict[str, Any]:
        path = (
            f"/api/internal/agent/projects/{project_id}/project"
            if self._use_internal_paths()
            else f"/api/projects/{project_id}"
        )
        with self._client() as client:
            response = client.get(
                path,
                headers=self._internal_headers("GET", path),
            )
            if response.status_code == 404:
                raise CanvasError("项目不存在")
            response.raise_for_status()
            return response.json()

    def preview_import(self, project_id: str, feed: dict[str, Any]) -> dict[str, Any]:
        path = (
            f"/api/internal/agent/projects/{project_id}/feed/preview"
            if self._use_internal_paths()
            else f"/api/projects/{project_id}/import/preview"
        )
        payload = {
            "feed_json": feed,
            "source": {"name": "agent"},
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        with self._client() as client:
            response = client.post(
                path,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    **self._internal_headers("POST", path, body),
                },
            )
            data = response.json() if response.content else {}
            if response.status_code >= 500:
                raise CanvasError(_issue_text(data, "导入预检失败"))
            # 422 is a structured, repairable validation result.
            if isinstance(data, dict) and not data.get("errors") and isinstance(data.get("issues"), list):
                data["errors"] = data["issues"]
            return data

    def commit_import(self, project_id: str, feed: dict[str, Any], if_revision: int) -> dict[str, Any]:
        path = (
            f"/api/internal/agent/projects/{project_id}/feed/import"
            if self._use_internal_paths()
            else f"/api/projects/{project_id}/import"
        )
        payload = {
            "feed_json": feed,
            "source": {"name": "agent"},
            "if_revision": if_revision,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        with self._client() as client:
            response = client.post(
                path,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    **self._internal_headers("POST", path, body),
                },
            )
            data = response.json() if response.content else {}
            if response.status_code >= 400:
                raise CanvasError(_issue_text(data, "导入失败"))
            return data


def _issue_text(data: Any, fallback: str) -> str:
    if isinstance(data, dict):
        issues = data.get("errors") or data.get("warnings")
        if isinstance(issues, list) and issues:
            return "；".join(str(item.get("message") or item) for item in issues[:5])
        return str(data.get("error") or data.get("detail") or fallback)
    return fallback
