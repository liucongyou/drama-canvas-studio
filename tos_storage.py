# -*- coding: utf-8 -*-
"""Private Volcengine TOS persistence for original generated media bytes."""
from __future__ import annotations

import hashlib
import io
import mimetypes
import os
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import provider_catalog
import tenant_context


class TosStorageError(RuntimeError):
    pass


class TosAccountOverdueError(TosStorageError):
    pass


def _tos_error(exc: Exception, action: str) -> TosStorageError:
    header = getattr(exc, "header", None)
    headers = header if hasattr(header, "items") else {}
    normalized = {str(key).lower(): value for key, value in headers.items()}
    ec = str(normalized.get("x-tos-ec") or getattr(exc, "code", "") or "")
    request_id = str(
        normalized.get("x-tos-request-id")
        or normalized.get("x-tos-id-2")
        or getattr(exc, "request_id", "")
        or ""
    )
    status_code = getattr(exc, "status_code", "")
    if ec == "0003-00000024":
        suffix = f"（RequestId: {request_id}）" if request_id else ""
        return TosAccountOverdueError(f"火山账号已欠费，TOS 拒绝访问；请结清欠费后再测试{suffix}")
    detail = str(getattr(exc, "message", "") or exc).strip()
    pieces = [f"{action}失败"]
    if status_code:
        pieces.append(f"HTTP {status_code}")
    if ec:
        pieces.append(f"错误码 {ec}")
    if detail:
        pieces.append(detail)
    suffix = f"（RequestId: {request_id}）" if request_id else ""
    return TosStorageError("：".join(pieces[:2]) + ("：" + "，".join(pieces[2:]) if pieces[2:] else "") + suffix)


def _tos():
    try:
        import tos
    except ImportError as exc:
        raise TosStorageError("缺少 TOS Python SDK，请安装 tos") from exc
    return tos


def _config() -> dict[str, Any]:
    return provider_catalog.storage_config()


def is_ready(config: dict[str, Any] | None = None) -> bool:
    cfg = config or _config()
    return bool(
        cfg.get("enabled")
        and str(cfg.get("provider") or "tos") == "tos"
        and cfg.get("bucket")
        and cfg.get("region") == "cn-beijing"
        and cfg.get("endpoint")
        and cfg.get("internal_endpoint")
        and cfg.get("access_key")
        and cfg.get("secret_key")
    )


def _client(*, internal: bool = False):
    cfg = _config()
    if not is_ready(cfg):
        raise TosStorageError("TOS 尚未启用或配置不完整")
    tos = _tos()
    endpoint = cfg.get("internal_endpoint") if internal else cfg.get("endpoint")
    return tos.TosClientV2(
        cfg["access_key"],
        cfg["secret_key"],
        endpoint,
        cfg.get("region") or "cn-beijing",
    )


def _safe_ext(fallback_ext: str, mime: str = "") -> str:
    ext = os.path.splitext(str(fallback_ext or ""))[1].lower()
    if not ext:
        ext = mimetypes.guess_extension(mime or "") or ".bin"
    if ext == ".jpe":
        ext = ".jpg"
    return ext if len(ext) <= 10 else ".bin"


def _safe_prefix(value: str) -> str:
    prefix = str(value or "").strip().strip("/")
    return f"{prefix}/" if prefix else ""


def _object_key(data: bytes, fallback_ext: str, mime: str, kind: str) -> str:
    cfg = _config()
    digest = hashlib.sha256(data).hexdigest()
    now = datetime.now(timezone.utc)
    return (
        f"{_safe_prefix(cfg.get('prefix') or '')}"
        f"{kind or 'media'}/{now:%Y/%m}/{digest}{_safe_ext(fallback_ext, mime)}"
    )


def persist_bytes(
    data: bytes,
    *,
    mime: str,
    fallback_ext: str,
    kind: str,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not data:
        raise TosStorageError("不能把空文件写入 TOS")
    cfg = _config()
    if not is_ready(cfg):
        raise TosStorageError("TOS 尚未启用或配置不完整")
    tos = _tos()
    object_key = _object_key(data, fallback_ext, mime, kind)
    source = source or {}
    meta = {
        "original-bytes": "1",
        "sha256": hashlib.sha256(data).hexdigest(),
        "source-provider": str(source.get("provider") or ""),
        "source-model": str(source.get("model") or ""),
        "source-task-id": str(source.get("taskId") or ""),
        "source-url": str(source.get("url") or ""),
    }
    try:
        result = _client().put_object(
            cfg["bucket"],
            object_key,
            content=io.BytesIO(data),
            content_length=len(data),
            content_type=mime or "application/octet-stream",
            acl=tos.ACLType.ACL_Private,
            meta=meta,
        )
    except Exception as exc:
        raise _tos_error(exc, "TOS 原始文件上传") from exc
    return {
        "status": "stored",
        "provider": "tos",
        "bucket": cfg["bucket"],
        "key": object_key,
        "region": cfg.get("region") or "cn-beijing",
        "endpoint": cfg.get("endpoint"),
        "internalEndpoint": cfg.get("internal_endpoint"),
        "size": len(data),
        "mime": mime or "application/octet-stream",
        "sha256": meta["sha256"],
        "crc64": str(getattr(result, "hash_crc64_ecma", "") or ""),
        "etag": str(getattr(result, "etag", "") or ""),
        "requestId": str(getattr(result, "request_id", "") or ""),
        "storedAt": datetime.now(timezone.utc).isoformat(),
        "originalBytes": True,
        "source": {
            "provider": meta["source-provider"],
            "model": meta["source-model"],
            "taskId": meta["source-task-id"],
            "generatedAt": str(source.get("generatedAt") or ""),
            "url": meta["source-url"],
        },
    }


def persist_local_url(
    url: str,
    *,
    kind: str,
    fallback_ext: str,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = str(url or "").strip()
    if not raw.startswith("/uploads/"):
        raise TosStorageError("只有本地原始文件可以写入 TOS")
    name = urllib.parse.unquote(raw[len("/uploads/"):])
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise TosStorageError("本地素材路径非法")
    path = os.path.join(str(tenant_context.current_upload_dir()), name)
    if not os.path.isfile(path):
        raise TosStorageError("本地原始文件不存在")
    with open(path, "rb") as fh:
        data = fh.read()
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return persist_bytes(
        data,
        mime=mime,
        fallback_ext=fallback_ext or os.path.splitext(name)[1],
        kind=kind,
        source=source,
    )


def internal_presigned_url(
    storage: dict[str, Any],
    *,
    expires: int | None = None,
) -> str:
    if not isinstance(storage, dict) or storage.get("provider") != "tos":
        raise TosStorageError("素材没有 TOS 原始文件信息")
    bucket = str(storage.get("bucket") or "").strip()
    key = str(storage.get("key") or "").strip()
    if not bucket or not key:
        raise TosStorageError("TOS 对象信息不完整")
    cfg = _config()
    ttl = int(expires or cfg.get("url_ttl_seconds") or 3600)
    tos = _tos()
    try:
        result = _client(internal=True).pre_signed_url(
            tos.enum.HttpMethodType.Http_Method_Get,
            bucket,
            key,
            expires=max(60, min(604800, ttl)),
        )
        return str(getattr(result, "signed_url", "") or "")
    except Exception as exc:
        raise _tos_error(exc, "生成 TOS 内网预签名 URL") from exc


def public_presigned_url(
    storage: dict[str, Any],
    *,
    expires: int | None = None,
) -> str:
    """Return a signed URL on the public endpoint for external providers."""
    if not isinstance(storage, dict) or storage.get("provider") != "tos":
        raise TosStorageError("素材没有 TOS 原始文件信息")
    bucket = str(storage.get("bucket") or "").strip()
    key = str(storage.get("key") or "").strip()
    if not bucket or not key:
        raise TosStorageError("TOS 对象信息不完整")
    cfg = _config()
    ttl = int(expires or cfg.get("url_ttl_seconds") or 3600)
    tos = _tos()
    try:
        result = _client(internal=False).pre_signed_url(
            tos.enum.HttpMethodType.Http_Method_Get,
            bucket,
            key,
            expires=max(60, min(604800, ttl)),
        )
        return str(getattr(result, "signed_url", "") or "")
    except Exception as exc:
        raise _tos_error(exc, "生成 TOS 公网预签名 URL") from exc


def ensure_local_image(
    url: str,
    *,
    storage: dict[str, Any] | None = None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if isinstance(storage, dict) and storage.get("provider") == "tos" and storage.get("key"):
        return storage
    if not is_ready():
        return None
    return persist_local_url(
        url,
        kind="image",
        fallback_ext=".png",
        source=source,
    )


def test_connection() -> dict[str, Any]:
    cfg = _config()
    if not is_ready(cfg):
        raise TosStorageError("TOS 尚未启用或配置不完整")
    try:
        result = _client().head_bucket(cfg["bucket"])
    except Exception as exc:
        raise _tos_error(exc, "TOS 连接测试") from exc
    return {
        "ok": True,
        "message": f"已连接 {cfg['bucket']} / {cfg.get('region')}",
        "requestId": str(getattr(result, "request_id", "") or ""),
    }
