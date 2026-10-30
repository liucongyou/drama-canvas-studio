# -*- coding: utf-8 -*-
"""Optional ImgBB image mirroring for generated images."""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import network_security
import provider_catalog
import tenant_context


UPLOAD_URL = "https://api.imgbb.com/1/upload"
MAX_IMAGE_BYTES = 32 * 1024 * 1024


def _config() -> dict[str, Any]:
    storage = provider_catalog.storage_config()
    config = storage.get("imgbb")
    result = dict(config) if isinstance(config, dict) else {}
    result["_enabled"] = storage.get("enabled", False)
    result["_provider"] = storage.get("provider", "tos")
    return result


def is_ready() -> bool:
    config = _config()
    return bool(
        config.get("_enabled")
        and config.get("_provider") == "imgbb"
        and str(config.get("api_key") or "").strip()
        and config.get("auto_persist_images", True) is not False
    )


def upload_bytes(data: bytes, *, mime: str = "image/png", name: str = "", source=None):
    config = _config()
    api_key = str(config.get("api_key") or "").strip()
    if not api_key:
        raise RuntimeError("未配置 ImgBB API Key")
    if not data:
        raise RuntimeError("ImgBB 上传内容为空")
    if len(data) > MAX_IMAGE_BYTES:
        raise RuntimeError("图片超过 ImgBB 32 MB 上限")
    query = urllib.parse.urlencode({"key": api_key})
    body = urllib.parse.urlencode({
        "image": base64.b64encode(data).decode("ascii"),
        "name": str(name or "drama-canvas")[:128],
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{UPLOAD_URL}?{query}",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "drama-canvas-studio/1.0",
        },
    )
    try:
        with network_security.safe_urlopen(request, timeout=90) as response:
            raw = response.read().decode("utf-8", errors="replace").strip()
            try:
                payload = json.loads(raw or "{}")
            except json.JSONDecodeError:
                # Some ImgBB gateways prepend/append diagnostics around the
                # JSON object. Keep the first complete object instead of
                # treating a valid upload as a failed mirror.
                decoder = json.JSONDecoder()
                payload = {}
                for index, char in enumerate(raw):
                    if char != "{":
                        continue
                    try:
                        candidate, _ = decoder.raw_decode(raw[index:])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(candidate, dict):
                        payload = candidate
                        break
                if not payload:
                    raise
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        try:
            parsed = json.loads(detail)
            detail = str(
                (parsed.get("error") or {}).get("message")
                or parsed.get("status_txt")
                or detail
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        raise RuntimeError(f"ImgBB 上传失败 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ImgBB 连接失败：{exc.reason}") from exc
    data_block = payload.get("data") if isinstance(payload, dict) else None
    if not payload.get("success") or not isinstance(data_block, dict):
        raise RuntimeError(f"ImgBB 上传失败：{payload.get('status_txt') or '返回格式异常'}")
    url = str(data_block.get("url") or data_block.get("display_url") or "").strip()
    if not url:
        raise RuntimeError("ImgBB 上传成功但未返回图片 URL")
    return {
        "provider": "imgbb",
        "status": "stored",
        "id": str(data_block.get("id") or ""),
        "url": url,
        "displayUrl": str(data_block.get("display_url") or url),
        "deleteUrl": str(data_block.get("delete_url") or ""),
        "thumbUrl": str((data_block.get("thumb") or {}).get("url") or ""),
        "mime": mime,
        "size": len(data),
        "uploadedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": source or {},
    }


def mirror_local_image(url: str) -> dict[str, Any]:
    """Upload one local image and return its public ImgBB URL."""
    raw = str(url or "").strip()
    if not raw.startswith("/uploads/"):
        raise RuntimeError("只有 /uploads/ 本地图片可以镜像到 ImgBB")
    name = urllib.parse.unquote(raw[len("/uploads/"):])
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise RuntimeError("本地图片路径非法")
    path = os.path.join(str(tenant_context.current_upload_dir()), name)
    if not os.path.isfile(path):
        raise RuntimeError("本地图片不存在")
    with open(path, "rb") as handle:
        data = handle.read()
    mime = mimetypes.guess_type(name)[0] or "image/png"
    return upload_bytes(data, mime=mime, name=name, source={"localUrl": raw})


def test_connection():
    """Upload a 1x1 transparent PNG as an explicit user-triggered test."""
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    storage = upload_bytes(png, mime="image/png", name="drama-canvas-connection-test")
    return {
        "ok": True,
        "message": "ImgBB 上传成功",
        "url": storage.get("url"),
    }
