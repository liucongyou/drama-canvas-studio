from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Mapping

SERVICE_HEADER = "X-Agent-Service"
TIMESTAMP_HEADER = "X-Agent-Timestamp"
SIGNATURE_HEADER = "X-Agent-Signature"
TENANT_HEADER = "X-Agent-Tenant-Id"
ACCOUNT_HEADER = "X-Agent-Account-Id"
REQUEST_HEADER = "X-Agent-Request-Id"


@dataclass(frozen=True)
class RequestContext:
    tenant_id: str
    account_id: str
    request_id: str = ""


def _body_hash(body: bytes) -> str:
    return hashlib.sha256(body or b"").hexdigest()


def _digest(
    secret: str,
    method: str,
    path: str,
    timestamp: str,
    tenant_id: str,
    account_id: str,
    body: bytes,
) -> str:
    message = "\n".join(
        (
            str(method or "").upper(),
            str(path or ""),
            str(timestamp or ""),
            str(tenant_id or ""),
            str(account_id or ""),
            _body_hash(body),
        )
    )
    return hmac.new(
        str(secret or "").encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def sign_request(
    secret: str,
    method: str,
    path: str,
    timestamp: str,
    tenant_id: str,
    account_id: str,
    body: bytes,
) -> str:
    if not str(secret or ""):
        raise ValueError("Agent 内部签名密钥未配置")
    return _digest(
        secret,
        method,
        path,
        timestamp,
        tenant_id,
        account_id,
        body,
    )


def verify_request(
    secret: str,
    method: str,
    path: str,
    timestamp: str,
    tenant_id: str,
    account_id: str,
    body: bytes,
    signature: str,
    *,
    now: int | None = None,
    window_seconds: int = 60,
) -> None:
    if not str(secret or ""):
        raise ValueError("Agent 内部签名密钥未配置")
    if not str(tenant_id or "") or not str(account_id or ""):
        raise ValueError("Agent 请求上下文不完整")
    try:
        signed_at = int(str(timestamp or ""))
    except ValueError as exc:
        raise ValueError("Agent 请求时间戳非法") from exc
    current = int(time.time() if now is None else now)
    if abs(current - signed_at) > max(0, int(window_seconds)):
        raise ValueError("Agent 请求已过期")
    expected = _digest(
        secret,
        method,
        path,
        str(signed_at),
        tenant_id,
        account_id,
        body,
    )
    if not hmac.compare_digest(expected, str(signature or "")):
        raise ValueError("Agent 请求签名无效")


def context_from_headers(
    headers: Mapping[str, str],
    *,
    secret: str,
    method: str,
    path: str,
    body: bytes = b"",
    now: int | None = None,
    window_seconds: int = 60,
) -> RequestContext:
    service = str(headers.get(SERVICE_HEADER) or "").strip()
    timestamp = str(headers.get(TIMESTAMP_HEADER) or "").strip()
    signature = str(headers.get(SIGNATURE_HEADER) or "").strip()
    tenant_id = str(headers.get(TENANT_HEADER) or "").strip()
    account_id = str(headers.get(ACCOUNT_HEADER) or "").strip()
    request_id = str(headers.get(REQUEST_HEADER) or "").strip()
    if service != "canvas":
        raise ValueError("Agent 服务身份无效")
    verify_request(
        secret,
        method,
        path,
        timestamp,
        tenant_id,
        account_id,
        body,
        signature,
        now=now,
        window_seconds=window_seconds,
    )
    return RequestContext(
        tenant_id=tenant_id,
        account_id=account_id,
        request_id=request_id,
    )


def signed_headers(
    *,
    secret: str,
    method: str,
    path: str,
    tenant_id: str,
    account_id: str,
    body: bytes = b"",
    request_id: str = "",
    timestamp: str | None = None,
) -> dict[str, str]:
    stamp = str(int(time.time()) if timestamp is None else timestamp)
    signature = sign_request(
        secret,
        method,
        path,
        stamp,
        tenant_id,
        account_id,
        body,
    )
    return {
        SERVICE_HEADER: "canvas",
        TIMESTAMP_HEADER: stamp,
        SIGNATURE_HEADER: signature,
        TENANT_HEADER: tenant_id,
        ACCOUNT_HEADER: account_id,
        REQUEST_HEADER: request_id,
    }
