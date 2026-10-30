# -*- coding: utf-8 -*-
"""Public media storage adapters for TOS, Aliyun OSS and Qiniu."""
from __future__ import annotations

import hashlib
import mimetypes
import os
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import provider_catalog
import tenant_context
import tos_storage


class MediaStorageError(RuntimeError):
    pass


def _config() -> dict[str, Any]:
    storage = provider_catalog.storage_config()
    provider = str(
        storage.get("active_provider") or storage.get("provider") or "tos"
    ).strip().lower()
    profiles = storage.get("providers")
    if isinstance(profiles, dict) and isinstance(profiles.get(provider), dict):
        profile = dict(profiles[provider])
        # Keep provider-specific keys only; never let another backend's
        # credentials or endpoint leak through the legacy global fields.
        for key in (
            "access_key",
            "secret_key",
            "bucket",
            "endpoint",
            "internal_endpoint",
            "public_domain",
        ):
            if not profile.get(key):
                profile[key] = storage.get(key)
        return {**storage, **profile, "provider": provider}
    return storage


def test_connection_for(provider: str) -> dict[str, Any]:
    """Test the selected backend using its own stored credential profile."""
    storage = provider_catalog.storage_config()
    provider = str(
        provider
        or storage.get("active_provider")
        or storage.get("provider")
        or "tos"
    ).strip().lower()
    profiles = storage.get("providers")
    if not isinstance(profiles, dict) or not isinstance(profiles.get(provider), dict):
        raise MediaStorageError(f"服务商 {provider} 尚未保存配置")
    cfg = {
        **storage,
        **profiles[provider],
        "enabled": True,
        "provider": provider,
    }
    data = (
        b"drama-canvas storage test "
        + datetime.now(timezone.utc).isoformat().encode()
    )
    try:
        descriptor = persist_bytes_with_config(
            cfg,
            data,
            mime="text/plain; charset=utf-8",
            fallback_ext=".txt",
            kind="test",
            source={"type": "storage-test", "provider": provider},
        )
        url = public_url_with_config(cfg, descriptor)
    except Exception as exc:
        provider_catalog.record_storage_test_failure(str(exc), provider=provider)
        raise
    provider_catalog.record_storage_test_success(provider=provider)
    return {
        "ok": True,
        "provider": provider,
        "message": f"{provider} 上传并访问成功",
        "url": url,
        "key": descriptor.get("key"),
    }


def _provider(config: dict[str, Any] | None = None) -> str:
    cfg = config or _config()
    return str(cfg.get("provider") or "tos").strip().lower()


def _public_prefix(config: dict[str, Any]) -> str:
    return str(config.get("prefix") or "").strip().strip("/")


def _object_key(
    data: bytes,
    *,
    kind: str,
    ext: str,
    config: dict[str, Any],
) -> str:
    digest = hashlib.sha256(data).hexdigest()
    now = datetime.now(timezone.utc)
    prefix = _public_prefix(config)
    parts = [part for part in (prefix, kind or "media") if part]
    return "/".join(parts) + f"/{now:%Y/%m}/{digest}{ext}"


def _read_local(url: str) -> tuple[bytes, str, str]:
    raw = str(url or "").strip()
    if not raw.startswith("/uploads/"):
        raise MediaStorageError("只有 /uploads/ 本地文件可以上传到对象存储")
    name = urllib.parse.unquote(raw[len("/uploads/"):])
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise MediaStorageError("本地素材路径非法")
    path = os.path.join(str(tenant_context.current_upload_dir()), name)
    if not os.path.isfile(path):
        raise MediaStorageError("本地素材不存在")
    with open(path, "rb") as handle:
        data = handle.read()
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    ext = os.path.splitext(name)[1].lower()
    return data, mime, ext


def _oss_client(config: dict[str, Any]):
    try:
        import oss2
    except ImportError as exc:
        raise MediaStorageError("缺少阿里云 OSS SDK，请安装 oss2") from exc
    endpoint = str(config.get("endpoint") or "").strip()
    if not endpoint.startswith(("http://", "https://")):
        endpoint = "https://" + endpoint
    auth = oss2.Auth(
        str(config.get("access_key") or ""),
        str(config.get("secret_key") or ""),
    )
    return oss2.Bucket(auth, endpoint, str(config.get("bucket") or ""))


def _qiniu_auth(config: dict[str, Any]):
    try:
        from qiniu import Auth
    except ImportError as exc:
        raise MediaStorageError("缺少七牛云 SDK，请安装 qiniu") from exc
    return Auth(
        str(config.get("access_key") or ""),
        str(config.get("secret_key") or ""),
    )


def _public_origin(config: dict[str, Any]) -> str:
    domain = str(config.get("public_domain") or "").strip().rstrip("/")
    if not domain:
        return ""
    if not domain.startswith(("http://", "https://")):
        domain = "https://" + domain
    return domain


def is_ready(config: dict[str, Any] | None = None) -> bool:
    cfg = config or _config()
    return _profile_ready(cfg)


def _profile_ready(cfg: dict[str, Any]) -> bool:
    provider = _provider(cfg)
    if not cfg.get("enabled"):
        return False
    if provider == "tos":
        return bool(tos_storage.is_ready(cfg))
    if provider == "aliyun_oss":
        return bool(
            cfg.get("bucket")
            and cfg.get("endpoint")
            and cfg.get("access_key")
            and cfg.get("secret_key")
        )
    if provider == "qiniu":
        return bool(
            cfg.get("bucket")
            and cfg.get("public_domain")
            and cfg.get("access_key")
            and cfg.get("secret_key")
        )
    return False


def persist_bytes(
    data: bytes,
    *,
    mime: str,
    fallback_ext: str,
    kind: str,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return persist_bytes_with_config(
        _config(),
        data,
        mime=mime,
        fallback_ext=fallback_ext,
        kind=kind,
        source=source,
    )


def persist_bytes_with_config(
    cfg: dict[str, Any],
    data: bytes,
    *,
    mime: str,
    fallback_ext: str,
    kind: str,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not data:
        raise MediaStorageError("不能上传空文件")
    provider = _provider(cfg)
    if not _profile_ready(cfg):
        raise MediaStorageError(f"对象存储尚未启用或配置不完整：{provider}")
    ext = os.path.splitext(str(fallback_ext or ""))[1].lower()
    if not ext:
        ext = mimetypes.guess_extension(mime or "") or ".bin"
    key = _object_key(data, kind=kind, ext=ext, config=cfg)
    if provider == "tos":
        return tos_storage.persist_bytes(
            data,
            mime=mime,
            fallback_ext=ext,
            kind=kind,
            source=source,
        )
    if provider == "aliyun_oss":
        try:
            result = _oss_client(cfg).put_object(key, data, headers={
                "Content-Type": mime or "application/octet-stream",
            })
        except Exception as exc:
            detail = str(exc)
            if "SignatureDoesNotMatch" in detail:
                raise MediaStorageError(
                    "阿里云 OSS 鉴权失败：AccessKeySecret 与 AccessKeyId 不匹配。"
                    "请重新复制同一对 AccessKey ID / Secret，确认没有空格、换行或混用了 RAM 子账号与主账号密钥。"
                ) from exc
            if "InvalidAccessKeyId" in detail:
                raise MediaStorageError(
                    "阿里云 OSS 鉴权失败：AccessKeyId 不存在、已删除或填错。"
                ) from exc
            if "NoSuchBucket" in detail:
                raise MediaStorageError(
                    f"阿里云 OSS Bucket 不存在：{cfg.get('bucket') or ''}。"
                    "请核对 Bucket 名和 Endpoint 所属地域。"
                ) from exc
            raise MediaStorageError(f"阿里云 OSS 上传失败：{detail}") from exc
        return {
            "status": "stored",
            "provider": provider,
            "bucket": str(cfg.get("bucket") or ""),
            "key": key,
            "size": len(data),
            "mime": mime,
            "sha256": hashlib.sha256(data).hexdigest(),
            "etag": str(getattr(result, "etag", "") or ""),
            "requestId": str(getattr(result, "request_id", "") or ""),
            "storedAt": datetime.now(timezone.utc).isoformat(),
            "source": source or {},
        }
    if provider == "qiniu":
        try:
            from qiniu import put_data

            token = _qiniu_auth(cfg).upload_token(
                str(cfg.get("bucket") or ""),
                key,
                int(cfg.get("url_ttl_seconds") or 3600),
            )
            ret, info = put_data(
                token,
                key,
                data,
                mime_type=mime or "application/octet-stream",
            )
            if getattr(info, "status_code", 0) != 200:
                raise MediaStorageError(
                    f"七牛云上传失败 HTTP {getattr(info, 'status_code', 0)}："
                    f"{getattr(info, 'error', '')}"
                )
        except MediaStorageError:
            raise
        except Exception as exc:
            raise MediaStorageError(f"七牛云上传失败：{exc}") from exc
        origin = _public_origin(cfg)
        return {
            "status": "stored",
            "provider": provider,
            "bucket": str(cfg.get("bucket") or ""),
            "key": key,
            "url": f"{origin}/{urllib.parse.quote(key)}",
            "size": len(data),
            "mime": mime,
            "sha256": hashlib.sha256(data).hexdigest(),
            "etag": str((ret or {}).get("hash") or ""),
            "storedAt": datetime.now(timezone.utc).isoformat(),
            "source": source or {},
        }
    raise MediaStorageError(f"不支持的存储类型：{provider}")


def persist_local_url(
    url: str,
    *,
    kind: str,
    fallback_ext: str,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data, mime, ext = _read_local(url)
    return persist_bytes(
        data,
        mime=mime,
        fallback_ext=ext or fallback_ext,
        kind=kind,
        source=source,
    )


def public_url(descriptor: dict[str, Any], *, expires: int | None = None) -> str:
    return public_url_with_config(_config(), descriptor, expires=expires)


def public_url_with_config(
    cfg: dict[str, Any],
    descriptor: dict[str, Any],
    *,
    expires: int | None = None,
) -> str:
    provider = str(descriptor.get("provider") or "").strip().lower()
    key = str(descriptor.get("key") or "").strip()
    if provider == "tos":
        return tos_storage.public_presigned_url(descriptor, expires=expires)
    if provider == "aliyun_oss":
        return _oss_client(cfg).sign_url(
            "GET",
            key,
            int(expires or cfg.get("url_ttl_seconds") or 3600),
            slash_safe=True,
        )
    if provider == "qiniu":
        origin = _public_origin(cfg)
        if not origin:
            raise MediaStorageError("七牛云缺少 public_domain")
        return f"{origin}/{urllib.parse.quote(key)}"
    raise MediaStorageError(f"不支持的存储对象：{provider}")


def ensure_local(
    url: str,
    *,
    kind: str,
    fallback_ext: str,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return persist_local_url(
        url,
        kind=kind,
        fallback_ext=fallback_ext,
        source=source,
    )


def test_connection() -> dict[str, Any]:
    data = (b"drama-canvas storage test " + datetime.now(timezone.utc).isoformat().encode())
    try:
        descriptor = persist_bytes(
            data,
            mime="text/plain; charset=utf-8",
            fallback_ext=".txt",
            kind="test",
            source={"type": "storage-test"},
        )
        url = public_url(descriptor)
    except Exception as exc:
        provider_catalog.record_storage_test_failure(str(exc))
        raise
    provider_catalog.record_storage_test_success()
    return {
        "ok": True,
        "message": f"{descriptor.get('provider')} 上传并签名成功",
        "url": url,
        "key": descriptor.get("key"),
    }
