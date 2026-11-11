from __future__ import annotations

import os
import urllib.error
import urllib.request
from typing import Any, Mapping

import tenant_context
from agent.app.context import (
    RequestContext,
    context_from_headers,
    signed_headers,
)

MAX_PROXY_BODY_BYTES = 55 * 1024 * 1024


def _secret() -> str:
    return str(os.environ.get("AGENT_INTERNAL_SECRET") or "").strip()


def _sidecar_url() -> str:
    return str(
        os.environ.get("AGENT_SIDECAR_URL")
        or "http://127.0.0.1:8895"
    ).strip().rstrip("/")


def _is_loopback_sidecar() -> bool:
    parsed = urllib.parse.urlparse(_sidecar_url())
    return (
        parsed.scheme in {"http", "https"}
        and (parsed.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}
    )


def is_agent_browser_path(path: str) -> bool:
    return str(path or "").startswith("/api/agent/")


def is_agent_internal_path(path: str) -> bool:
    return str(path or "").startswith("/api/internal/agent/")

def allow_unsigned_local_agent() -> bool:
    return not _secret() and _is_loopback_sidecar()

def loopback_client(handler: Any) -> bool:
    client = str(getattr(handler, "client_address", ("", 0))[0] or "")
    return client in {"127.0.0.1", "::1", "localhost"}


def sign_context_headers(
    *,
    method: str,
    path: str,
    tenant_id: str,
    account_id: str,
    body: bytes = b"",
    request_id: str = "",
    timestamp: str | None = None,
) -> dict[str, str]:
    return signed_headers(
        secret=_secret(),
        method=method,
        path=path,
        tenant_id=tenant_id,
        account_id=account_id,
        body=body,
        request_id=request_id,
        timestamp=timestamp,
    )


def unsigned_context_headers(
    *,
    tenant_id: str,
    account_id: str,
    request_id: str = "",
) -> dict[str, str]:
    if not str(tenant_id or "").strip() or not str(account_id or "").strip():
        raise ValueError("Agent 请求上下文不完整")
    return {
        "X-Agent-Service": "canvas",
        "X-Agent-Tenant-Id": str(tenant_id).strip(),
        "X-Agent-Account-Id": str(account_id).strip(),
        "X-Agent-Request-Id": str(request_id or ""),
    }


def verify_unsigned_context_headers(headers: Mapping[str, str]) -> RequestContext:
    if str(headers.get("X-Agent-Service") or "").strip() != "canvas":
        raise ValueError("Agent 服务身份无效")
    tenant_id = str(headers.get("X-Agent-Tenant-Id") or "").strip()
    account_id = str(headers.get("X-Agent-Account-Id") or "").strip()
    if not tenant_id or not account_id:
        raise ValueError("Agent 请求上下文不完整")
    return RequestContext(
        tenant_id=tenant_id,
        account_id=account_id,
        request_id=str(headers.get("X-Agent-Request-Id") or "").strip(),
    )


def verify_context_headers(
    headers: Mapping[str, str],
    *,
    method: str,
    path: str,
    body: bytes = b"",
    now: int | None = None,
) -> RequestContext:
    window = int(os.environ.get("AGENT_SIGNATURE_WINDOW_SECONDS") or "60")
    return context_from_headers(
        headers,
        secret=_secret(),
        method=method,
        path=path,
        body=body,
        now=now,
        window_seconds=window,
    )


def proxy_agent(handler: Any) -> bool:
    """Proxy one browser request to the local Agent sidecar."""
    session = handler._current_session()
    if not session and os.environ.get("CANVAS_AUTH_MODE", "").strip().lower() == "accounts":
        handler._send(
            401,
            {"error": "请先登录。", "code": "AGENT_AUTH_REQUIRED"},
        )
        return True
    session = session or {}
    current_tenant = tenant_context.current()
    auth_mode = os.environ.get("CANVAS_AUTH_MODE", "host").strip().lower()
    tenant_id = str(
        (current_tenant.tenant_id if current_tenant else "")
        or ("legacy" if auth_mode != "accounts" else "")
        or session.get("tenant_id")
    ).strip()
    account_id = str(session.get("account_id") or "local").strip()
    path = str(handler.path or "")
    method = str(handler.command or "GET").upper()
    try:
        body = _proxy_request_body(handler) if method in {"POST", "PUT", "PATCH"} else b""
    except ValueError as exc:
        handler._send(
            400,
            {"error": str(exc), "code": "AGENT_REQUEST_INVALID"},
        )
        return True
    target = _sidecar_url() + path
    secret = _secret()
    if secret:
        try:
            context_headers = sign_context_headers(
                method=method,
                path=path,
                tenant_id=tenant_id,
                account_id=account_id,
                body=body,
                request_id=str(getattr(handler, "_request_id", "") or ""),
            )
        except ValueError as exc:
            handler._send(
                503,
                {"error": str(exc), "code": "AGENT_GATEWAY_MISCONFIGURED"},
            )
            return True
    elif _is_loopback_sidecar():
        # The sidecar skips HMAC validation when it has no internal secret.
        # Keep that convenience limited to the same-host development topology;
        # any non-loopback sidecar still requires a configured shared secret.
        context_headers = unsigned_context_headers(
            tenant_id=tenant_id,
            account_id=account_id,
            request_id=str(getattr(handler, "_request_id", "") or ""),
        )
    else:
        handler._send(
            503,
            {
                "error": "Agent 内部签名密钥未配置",
                "code": "AGENT_GATEWAY_MISCONFIGURED",
            },
        )
        return True
    headers = {
        **context_headers,
        "Accept": str(handler.headers.get("Accept") or "application/json"),
    }
    if handler.headers.get("Content-Type"):
        headers["Content-Type"] = str(handler.headers.get("Content-Type"))
    if handler.headers.get("Last-Event-ID"):
        headers["Last-Event-ID"] = str(handler.headers.get("Last-Event-ID"))
    request = urllib.request.Request(
        target,
        data=body if method in {"POST", "PUT", "PATCH"} else None,
        method=method,
        headers=headers,
    )
    if "text/event-stream" in headers["Accept"] or path.endswith("/events"):
        return _stream_proxy(handler, request)
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            raw = response.read()
            content_type = response.headers.get(
                "Content-Type",
                "application/json",
            )
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        content_type = exc.headers.get(
            "Content-Type",
            "application/json",
        )
        status = int(exc.code)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        handler._send(
            502,
            {
                "error": f"Agent 服务不可用：{exc}",
                "code": "AGENT_SERVICE_UNAVAILABLE",
            },
        )
        return True
    handler._send(
        status,
        raw=raw,
        content_type=content_type,
        headers={"Cache-Control": "no-store"},
    )
    return True


def _proxy_request_body(handler: Any) -> bytes:
    cached = getattr(handler, "_agent_proxy_body", None)
    if isinstance(cached, bytes):
        return cached
    raw_length = str(handler.headers.get("Content-Length") or "0").strip()
    try:
        length = int(raw_length)
    except ValueError:
        length = -1
    if length < 0 or length > MAX_PROXY_BODY_BYTES:
        raise ValueError("Agent 请求体大小非法")
    body = handler.rfile.read(length) if length else b""
    if len(body) != length:
        raise ValueError("Agent 请求体读取不完整")
    handler._agent_proxy_body = body
    return body


def _stream_proxy(handler: Any, request: urllib.request.Request) -> bool:
    try:
        response = urllib.request.urlopen(request, timeout=300)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        handler._send(
            int(exc.code),
            raw=raw,
            content_type=exc.headers.get("Content-Type", "application/json"),
        )
        return True
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        handler._send(
            502,
            {
                "error": f"Agent 事件流不可用：{exc}",
                "code": "AGENT_SERVICE_UNAVAILABLE",
            },
        )
        return True
    with response:
        handler.send_response(int(response.status))
        for key, value in handler._cors_headers().items():
            handler.send_header(str(key), str(value))
        for key in (
            "Content-Type",
            "Cache-Control",
            "X-Accel-Buffering",
        ):
            value = response.headers.get(key)
            if value:
                handler.send_header(key, value)
        handler.send_header("Connection", "close")
        handler.end_headers()
        while True:
            chunk = response.read(2048)
            if not chunk:
                break
            handler.wfile.write(chunk)
            handler.wfile.flush()
    return True


def project_index_from_project(project: dict[str, Any]) -> dict[str, Any]:
    payload = project.get("payload") if isinstance(project.get("payload"), dict) else {}
    nodes = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
    connections = payload.get("connections") if isinstance(payload.get("connections"), list) else []
    episodes = payload.get("episodeImports") if isinstance(payload.get("episodeImports"), list) else []
    node_types: dict[str, int] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or "unknown")
        node_types[node_type] = node_types.get(node_type, 0) + 1
    return {
        "id": str(project.get("id") or payload.get("id") or ""),
        "title": str(project.get("title") or payload.get("title") or ""),
        "revision": int(project.get("revision") or payload.get("revision") or 0),
        "updatedAt": str(project.get("updated_at") or ""),
        "nodeCount": len(nodes),
        "connectionCount": len(connections),
        "episodeCount": len(episodes),
        "nodeTypes": node_types,
    }


def project_summaries_from_store(store: Any) -> list[dict[str, Any]]:
    rows = store.list_project_summaries()
    keys = (
        "id",
        "title",
        "revision",
        "updated_at",
        "node_count",
        "connection_count",
        "episode_count",
    )
    return [
        {key: row.get(key) for key in keys}
        for row in rows
        if isinstance(row, dict)
    ]


def project_nodes_from_project(
    project: dict[str, Any],
    *,
    episode_id: str = "",
    node_type: str = "",
    query: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    payload = project.get("payload") if isinstance(project.get("payload"), dict) else {}
    rows = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
    normalized_query = str(query or "").strip().lower()
    result = []
    for node in rows:
        if not isinstance(node, dict):
            continue
        metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        if node_type and str(node.get("type") or "") != node_type:
            continue
        episode_ids = {
            str(metadata.get("episodeId") or ""),
            *[str(item) for item in metadata.get("episodeIds") or []],
        }
        if episode_id and episode_id not in episode_ids:
            continue
        searchable = (
            str(node.get("id") or "")
            + " "
            + str(node.get("title") or "")
            + " "
            + str(metadata.get("prompt") or node.get("prompt") or "")
        ).lower()
        if normalized_query and normalized_query not in searchable:
            continue
        result.append(
            {
                "id": str(node.get("id") or ""),
                "type": str(node.get("type") or ""),
                "title": str(node.get("title") or ""),
                "status": str(metadata.get("status") or node.get("status") or ""),
                "episodeId": str(metadata.get("episodeId") or ""),
                "assetId": str(metadata.get("assetId") or ""),
                "seriesId": str(metadata.get("seriesId") or ""),
                "variantKey": str(metadata.get("variantKey") or ""),
            }
        )
    capped = max(1, min(500, int(limit)))
    return {
        "total": len(result),
        "nodes": result[:capped],
        "truncated": len(result) > capped,
    }


def project_assets_from_project(project: dict[str, Any], *, limit: int = 300) -> dict[str, Any]:
    payload = project.get("payload") if isinstance(project.get("payload"), dict) else {}
    rows = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
    assets: dict[str, dict[str, Any]] = {}
    for node in rows:
        if not isinstance(node, dict):
            continue
        metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        asset_id = str(metadata.get("assetId") or "").strip()
        if not asset_id:
            continue
        assets.setdefault(
            asset_id,
            {
                "assetId": asset_id,
                "seriesId": str(metadata.get("seriesId") or ""),
                "variantKey": str(metadata.get("variantKey") or "default"),
                "nodeId": str(node.get("id") or ""),
                "title": str(node.get("title") or ""),
                "type": str(node.get("type") or ""),
                "status": str(metadata.get("status") or ""),
                "episodeId": str(metadata.get("episodeId") or ""),
            },
        )
    values = list(assets.values())
    capped = max(1, min(1000, int(limit)))
    return {
        "total": len(values),
        "assets": values[:capped],
        "truncated": len(values) > capped,
    }
