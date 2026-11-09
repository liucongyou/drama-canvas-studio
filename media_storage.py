# -*- coding: utf-8 -*-
"""Public media storage adapters for TOS, Aliyun OSS and Qiniu."""
from __future__ import annotations

import hashlib
import mimetypes
import os
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import provider_catalog
import tenant_context
import tos_storage


class MediaStorageError(RuntimeError):
    pass


_HASH_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(1, int(os.environ.get("CANVAS_STORAGE_HASH_WORKERS", "2"))),
    thread_name_prefix="media-hash",
)
_PLAYBACK_URL_TTL_SECONDS = max(
    60,
    min(
        604800,
        int(os.environ.get("CANVAS_PLAYBACK_URL_TTL_SECONDS", "21600")),
    ),
)
_PLAYBACK_URL_CACHE: dict[tuple[str, ...], tuple[float, str]] = {}
_PLAYBACK_URL_CACHE_LOCK = threading.Lock()


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
    provider = str(
        provider
        or provider_catalog.storage_config().get("active_provider")
        or provider_catalog.storage_config().get("provider")
        or "tos"
    ).strip().lower()
    profile_config = provider_catalog.storage_profile_config(provider)
    if not profile_config:
        raise MediaStorageError(f"服务商 {provider} 尚未保存配置")
    cfg = {**profile_config, "enabled": True, "provider": provider}
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


def _local_path(url: str) -> tuple[str, str, str]:
    raw = str(url or "").strip()
    if not raw.startswith("/uploads/"):
        raise MediaStorageError("只有 /uploads/ 本地文件可以上传到对象存储")
    name = urllib.parse.unquote(raw[len("/uploads/"):])
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise MediaStorageError("本地素材路径非法")
    path = os.path.join(str(tenant_context.current_upload_dir()), name)
    if not os.path.isfile(path):
        raise MediaStorageError("本地素材不存在")
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    ext = os.path.splitext(name)[1].lower()
    return path, mime, ext


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


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
    profiles = cfg.get("providers")
    profile = (
        profiles.get(provider)
        if isinstance(profiles, dict)
        and isinstance(profiles.get(provider), dict)
        else None
    )
    if profile:
        cfg = {**cfg, **profile}
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
    cfg = _config()
    provider = _provider(cfg)
    if not _profile_ready(cfg):
        raise MediaStorageError(f"对象存储尚未启用或配置不完整：{provider}")
    path, mime, ext = _local_path(url)
    ext = ext or os.path.splitext(str(fallback_ext or ""))[1].lower() or ".bin"
    size = os.path.getsize(path)
    if size <= 0:
        raise MediaStorageError("不能上传空文件")
    sha_future = _HASH_EXECUTOR.submit(_file_sha256, path)
    sha256 = sha_future.result()
    key = (
        "_".join(
            part
            for part in (
                _public_prefix(cfg).replace("/", "_"),
                kind or "media",
            )
            if part
        )
        + f"_{datetime.now(timezone.utc):%Y%m}_{sha256}{ext}"
    )
    if provider == "tos":
        return tos_storage.upload_local_path_to_key(
            path,
            object_key=key,
            mime=mime,
            source=source,
            display_name=os.path.basename(path),
        )
    if provider == "aliyun_oss":
        try:
            result = _oss_client(cfg).put_object_from_file(
                key,
                path,
                headers={"Content-Type": mime or "application/octet-stream"},
            )
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
            "size": size,
            "mime": mime,
            "sha256": sha256,
            "etag": str(getattr(result, "etag", "") or ""),
            "requestId": str(getattr(result, "request_id", "") or ""),
            "storedAt": datetime.now(timezone.utc).isoformat(),
            "source": source or {},
        }
    if provider == "qiniu":
        try:
            from qiniu import put_file

            token = _qiniu_auth(cfg).upload_token(
                str(cfg.get("bucket") or ""),
                key,
                int(cfg.get("url_ttl_seconds") or 3600),
            )
            ret, info = put_file(
                token,
                key,
                path,
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
            "size": size,
            "mime": mime,
            "sha256": sha256,
            "etag": str((ret or {}).get("hash") or ""),
            "storedAt": datetime.now(timezone.utc).isoformat(),
            "source": source or {},
        }
    raise MediaStorageError(f"不支持的存储类型：{provider}")


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


def _playback_cache_key(storage: dict[str, Any]) -> tuple[str, ...]:
    provider = str(storage.get("provider") or "").strip().lower()
    profile = provider_catalog.storage_profile_config(provider) or _config()
    credential_fingerprint = hashlib.sha256(
        str(profile.get("access_key") or "").encode("utf-8")
    ).hexdigest()[:16]
    return (
        tenant_context.current_tenant_id(),
        provider,
        str(storage.get("bucket") or ""),
        str(storage.get("key") or ""),
        str(profile.get("endpoint") or ""),
        credential_fingerprint,
        str(_PLAYBACK_URL_TTL_SECONDS),
    )


def _cached_public_url(storage: dict[str, Any]) -> str:
    """Mint a stable playback URL for this process without persisting it."""
    key = _playback_cache_key(storage)
    now = time.monotonic()
    with _PLAYBACK_URL_CACHE_LOCK:
        cached = _PLAYBACK_URL_CACHE.get(key)
        if cached and cached[0] > now:
            return cached[1]
        if cached:
            _PLAYBACK_URL_CACHE.pop(key, None)
        # Pre-signing is local computation. Holding this lock guarantees two
        # concurrent canvas reads cannot observe different URLs and reload the
        # same video twice.
        url = public_url(storage, expires=_PLAYBACK_URL_TTL_SECONDS)
        if not url:
            return ""
        # Renew well before the object-store signature expires so a canvas that
        # stays open does not repeatedly reload its videos.
        cache_seconds = max(
            30,
            _PLAYBACK_URL_TTL_SECONDS
            - min(300, _PLAYBACK_URL_TTL_SECONDS // 10),
        )
        if len(_PLAYBACK_URL_CACHE) >= 2048:
            expired = [
                cache_key
                for cache_key, (expires_at, _) in _PLAYBACK_URL_CACHE.items()
                if expires_at <= now
            ]
            for cache_key in expired:
                _PLAYBACK_URL_CACHE.pop(cache_key, None)
            if len(_PLAYBACK_URL_CACHE) >= 2048:
                oldest = min(
                    _PLAYBACK_URL_CACHE,
                    key=lambda item: _PLAYBACK_URL_CACHE[item][0],
                )
                _PLAYBACK_URL_CACHE.pop(oldest, None)
        _PLAYBACK_URL_CACHE[key] = (now + cache_seconds, url)
    return url


def clear_playback_url_cache() -> None:
    with _PLAYBACK_URL_CACHE_LOCK:
        _PLAYBACK_URL_CACHE.clear()


def _stored_descriptor(value: Any) -> dict[str, Any] | None:
    if (
        isinstance(value, dict)
        and value.get("status") == "stored"
        and value.get("provider")
        and value.get("key")
    ):
        return value
    return None


def _same_remote_object(left: str, right: str) -> bool:
    """Match the same provider object without confusing equal basenames."""
    try:
        left_url = urllib.parse.urlsplit(str(left or "").strip())
        right_url = urllib.parse.urlsplit(str(right or "").strip())
    except ValueError:
        return False
    left_host = str(left_url.hostname or "").strip().lower()
    right_host = str(right_url.hostname or "").strip().lower()
    left_path = urllib.parse.unquote(left_url.path or "").strip("/")
    right_path = urllib.parse.unquote(right_url.path or "").strip("/")
    return bool(
        left_host
        and left_host == right_host
        and left_path
        and left_path == right_path
    )


def _signed_url_expired(url: str) -> bool:
    """Return True only when a TOS signature carries a confirmed past expiry."""
    try:
        query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(str(url or "").strip()).query,
            keep_blank_values=True,
        )
    except ValueError:
        return False
    raw_expiry = next(
        (
            values[-1]
            for key, values in query.items()
            if str(key or "").strip().lower() == "x-tos-expires" and values
        ),
        "",
    )
    try:
        expiry = int(float(str(raw_expiry).strip()))
    except (TypeError, ValueError):
        return False
    # TOS uses Unix time. Be conservative for malformed legacy values: only a
    # parsed timestamp in the past is a confirmed expiration.
    return 0 < expiry < int(time.time())


def _descriptor_for_version(
    version: dict[str, Any],
    *,
    active_storage: dict[str, Any] | None,
    active_content: str,
) -> dict[str, Any] | None:
    """Recover a durable descriptor for legacy versions that stored only a signed URL.

    Older rows persisted a short-lived TOS URL in ``mediaVersions[].url`` and did
    not copy ``storage`` onto the version. The active node content still usually
    points at the same durable object/key. This narrowly recovers that case; it
    never guesses a key from an arbitrary remote URL.
    """
    descriptor = _stored_descriptor(version.get("storage"))
    if descriptor:
        return descriptor
    if not active_storage:
        return None
    version_url = str(version.get("url") or "").strip()
    if not version_url:
        return None
    if version_url == active_content:
        return active_storage
    if _same_remote_object(active_content, version_url):
        return active_storage
    return None


def project_node_media_url(node: dict[str, Any]) -> str:
    """Project durable object-store receipts into response-only playback URLs.

    The database keeps stable bucket/key receipts. The API response uses a
    short-lived cached signature so polling does not change the video URL and
    trigger a reload on every reconciliation.
    """
    if not isinstance(node, dict):
        return ""
    meta = node.get("metadata")
    if not isinstance(meta, dict):
        return ""
    durable_content = str(meta.get("content") or "")
    storage = _stored_descriptor(meta.get("storage"))

    def projection(descriptor, fallback=""):
        descriptor = _stored_descriptor(descriptor)
        if not descriptor:
            return fallback, ""
        try:
            return (_cached_public_url(descriptor) or fallback), ""
        except Exception as exc:
            return fallback, str(exc)

    # Project versions first. Matching the current version against the original
    # stable URL must happen before the node's content is replaced by a fresh
    # signature, otherwise every read can make the active index look stale.
    params = meta.get("params")
    if isinstance(params, dict):
        active_id = str(params.get("activeVersionId") or "").strip()
        for list_name in ("mediaVersions", "videoVersions"):
            versions = params.get(list_name)
            if not isinstance(versions, list):
                continue
            for version in versions:
                if not isinstance(version, dict):
                    continue
                version_storage = _descriptor_for_version(
                    version,
                    active_storage=storage,
                    active_content=durable_content,
                )
                raw_version_url = str(version.get("url") or "")
                version_url, version_error = projection(
                    version_storage,
                    raw_version_url,
                )
                if version_url:
                    version["url"] = version_url
                # A storage receipt is metadata, not proof that this read can
                # produce a usable URL. Only a successful durable projection is
                # authoritative; otherwise a bad credential or missing object
                # would still render as an available historical version. A
                # local-only legacy version is authoritative only when its file
                # still exists in this tenant.
                version_available = bool(
                    version_storage and version_url and not version_error
                )
                if not version_available and raw_version_url.startswith("/uploads/"):
                    # Older rows can point at a local file while retaining a
                    # durable TOS receipt. The local file may have been cleaned
                    # up, but the object is still the authoritative copy.
                    try:
                        _local_path(raw_version_url)
                        version_available = True
                    except MediaStorageError:
                        version_available = bool(
                            version_storage and version_url and not version_error
                        )
                elif raw_version_url.startswith(("http://", "https://", "data:")):
                    # A freshly signed URL proves the durable object was readable
                    # by the signer. Legacy unsigned URLs without a descriptor are
                    # still usable candidates but cannot be called confirmed.
                    version_available = version_available or bool(
                        version_url and version_url != raw_version_url
                    )
                if version_available:
                    version["mediaAvailability"] = "available"
                elif raw_version_url.startswith(("http://", "https://")):
                    # A signed provider URL without a durable storage receipt
                    # cannot be recovered after expiry. Do not let the UI switch
                    # the active card to a known-dead history entry.
                    version["mediaAvailability"] = (
                        "missing"
                        if _signed_url_expired(raw_version_url)
                        else "unknown"
                    )
                else:
                    version["mediaAvailability"] = "missing"
                if active_id and str(version.get("versionId") or "").strip() == active_id:
                    version["active"] = True

    projected_content, content_error = projection(storage, durable_content)
    if projected_content:
        meta["content"] = projected_content
    current_content = str(meta.get("content") or projected_content)
    if current_content.startswith("/uploads/"):
        try:
            _local_path(current_content)
            meta["mediaAvailability"] = "available"
            meta.pop("storageError", None)
        except MediaStorageError:
            meta["mediaAvailability"] = "missing"
    elif storage and projected_content and not content_error:
        meta["mediaAvailability"] = "available"
        meta.pop("storageError", None)
    elif current_content.startswith("data:"):
        meta["mediaAvailability"] = "available"
        meta.pop("storageError", None)
    elif current_content.startswith(("http://", "https://")):
        meta["mediaAvailability"] = "missing" if content_error else "unknown"
    elif current_content:
        meta["mediaAvailability"] = "missing"
    else:
        meta["mediaAvailability"] = ""
    if content_error:
        meta["storageError"] = content_error
    return current_content


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
