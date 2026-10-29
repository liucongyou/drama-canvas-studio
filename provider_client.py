# -*- coding: utf-8 -*-
"""Protocol adapters for model-provider generation.

The canvas stores a provider/model ID, not a hard-coded client implementation.
This module resolves the selected catalog entry and translates node parameters
to the provider protocol.
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from datetime import datetime, timezone
from typing import Any, Callable

import network_security
import provider_catalog
import tenant_context
import tos_storage


BASE = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE, "data", "uploads")
MAX_INLINE_BYTES = 24 * 1024 * 1024
TaskEventCallback = Callable[[dict[str, Any]], None]


def poll_delay(elapsed_seconds: float) -> float:
    """任务轮询间隔（自适应）。

    用户感知的"服务商已完成但卡片还没变"，下限就是一次轮询间隔：轮询越稀，
    发现完成就越晚。因此前 60 秒加密到 1.5s（覆盖绝大多数图片和一成短视频），
    之后逐步放缓，避免长任务把供应商接口打成 QPS 攻击。
    """
    elapsed = max(0.0, float(elapsed_seconds or 0.0))
    if elapsed < 60:
        return 1.5
    if elapsed < 180:
        return 3.0
    return 6.0


def _upload_dir() -> str:
    context = tenant_context.current()
    return str(context.upload_dir) if context is not None else UPLOAD_DIR


def _emit_task_event(callback: TaskEventCallback | None, payload: dict[str, Any]) -> None:
    if callback is None:
        return
    try:
        callback(payload)
    except Exception:
        # Persistence/logging failures must never abort an already-paid remote task.
        pass


class ProviderHTTPError(RuntimeError):
    def __init__(self, status_code: int, detail: str, headers: Any = None):
        super().__init__(f"供应商接口 HTTP {status_code}：{detail}")
        self.status_code = status_code
        self.detail = detail
        self.headers = headers


class ProviderOutcomeUncertain(RuntimeError):
    """The paid request may have been accepted, but no usable response arrived."""


_GUIDANCE_WORDS = {
    "轻度": "\nlight detail, keep loose and natural",
    "中等": "\nbalanced detail, standard quality",
    "高": "\nhigh detail, refined textures, sharp focus",
    "极高": "\nultra-high detail, intricate textures, crisp edges, rich material fidelity",
    "最高": "\nextreme detail, photorealistic fidelity, maximum sharpness, perfect material accuracy",
}

_SEEDREAM_MIN_PIXELS = 921_600
_SEEDREAM_SAFE_SIZES = {
    "1:1": "1536x1536",
    "16:9": "1536x864",
    "9:16": "864x1536",
    "4:3": "1536x1152",
    "3:4": "1152x1536",
    "21:9": "1536x658",
}
SUBMIT_TIMEOUT_SECONDS = 45
SYNC_MEDIA_TIMEOUT_SECONDS = 900
SUBMIT_RETRY_WINDOW_SECONDS = 180
SUBMIT_RETRY_MAX_DELAY_SECONDS = 20


def _safe_seedream_size(size: Any, ratio: Any) -> str:
    raw = str(size or "").strip()
    match = re.match(r"^(\d+)\s*[x×]\s*(\d+)$", raw, re.IGNORECASE)
    if match:
        width, height = int(match.group(1)), int(match.group(2))
        if width > 0 and height > 0 and width * height >= _SEEDREAM_MIN_PIXELS:
            return f"{width}x{height}"
    return _SEEDREAM_SAFE_SIZES.get(str(ratio or "1:1"), "1536x1536")


def _join_url(base: str, path: str) -> str:
    return f"{str(base or '').rstrip('/')}/{str(path or '').lstrip('/')}"


def _request_json(
    method: str,
    url: str,
    key: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: int = 120,
) -> dict[str, Any]:
    if method.upper() == "POST" and body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        with tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".json",
            delete=False,
        ) as handle:
            handle.write(data)
            payload_path = handle.name
        headers_path = payload_path + ".headers"
        response_path = payload_path + ".response"
        try:
            curl = shutil.which("curl")
            if curl:
                result = subprocess.run(
                    [
                        curl,
                        "--silent",
                        "--show-error",
                        "--max-time", str(max(1, int(timeout))),
                        "--connect-timeout", "15",
                        "--dump-header", headers_path,
                        "--output", response_path,
                        "--header", f"Authorization: Bearer {key}",
                        "--header", "Content-Type: application/json",
                        "--header", "Accept: application/json",
                        "--data-binary", f"@{payload_path}",
                        "--write-out", "%{http_code}",
                        url,
                    ],
                    capture_output=True,
                    timeout=max(5, int(timeout) + 5),
                    check=False,
                )
                status_text = (
                    result.stdout.decode("utf-8", errors="replace")
                    if isinstance(result.stdout, bytes)
                    else str(result.stdout or "")
                ).strip()
                try:
                    status = int(status_text)
                except ValueError:
                    status = 0
                if result.returncode != 0 or status == 0:
                    detail = result.stderr.decode("utf-8", errors="replace") if isinstance(result.stderr, bytes) else str(result.stderr)
                    raise ProviderOutcomeUncertain(
                        f"供应商请求已发出，但未收到可确认结果：{detail or 'curl request failed'}"
                    )
                body_bytes = b""
                if os.path.isfile(response_path):
                    with open(response_path, "rb") as response_file:
                        body_bytes = response_file.read()
                if status >= 400:
                    raise ProviderHTTPError(
                        status,
                        body_bytes.decode("utf-8", errors="replace")[:1200],
                        None,
                    )
                return json.loads(body_bytes.decode("utf-8", errors="replace")) if body_bytes.strip() else {}
        finally:
            try:
                os.unlink(payload_path)
            except OSError:
                pass
            try:
                os.unlink(headers_path)
            except OSError:
                pass
            try:
                os.unlink(response_path)
            except OSError:
                pass
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with network_security.safe_urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise ProviderHTTPError(exc.code, detail, exc.headers) from exc
    except (TimeoutError, OSError) as exc:
        if method.upper() == "POST":
            raise ProviderOutcomeUncertain(
                f"供应商请求已发出，但读取结果超时：{exc}"
            ) from exc
        raise RuntimeError(f"供应商接口读取失败：{exc}") from exc
    except urllib.error.URLError as exc:
        if method.upper() == "POST":
            raise ProviderOutcomeUncertain(
                f"供应商请求未取得可确认结果：{exc.reason}"
            ) from exc
        raise RuntimeError(f"供应商接口连接失败：{exc.reason}") from exc


def _request_multipart(
    method: str,
    url: str,
    key: str,
    fields: list[tuple[str, Any]],
    *,
    timeout: int = 120,
) -> dict[str, Any]:
    boundary = f"----drama-canvas-{secrets.token_hex(12)}"
    chunks: list[bytes] = []
    for name, value in fields:
        if isinstance(value, tuple):
            filename, mime, content = value
            chunks.extend([
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {mime}\r\n\r\n".encode("utf-8"),
                content,
                b"\r\n",
            ])
            continue
        chunks.extend([
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
            str(value).encode("utf-8"),
            b"\r\n",
        ])
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    request = urllib.request.Request(
        url,
        data=b"".join(chunks),
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
    )
    try:
        with network_security.safe_urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1200]
        raise ProviderHTTPError(exc.code, detail, exc.headers) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"供应商接口连接失败：{exc.reason}") from exc


def _submit_with_retry(
    attempt: Callable[[], dict[str, Any]],
    *,
    on_task_update: TaskEventCallback | None = None,
    retry_window: int = SUBMIT_RETRY_WINDOW_SECONDS,
) -> dict[str, Any]:
    """Shared retry policy for every task-creating provider submission.

    Explicit rate-limit / upstream-unavailable responses are retried with
    bounded exponential backoff. Timeouts and connection drops are ambiguous
    paid states and are never re-POSTed, so they surface immediately instead of
    risking a duplicate charge.
    """
    deadline = time.time() + max(0, int(retry_window))
    delay = 3
    while True:
        try:
            return attempt()
        except ProviderHTTPError as exc:
            retryable = exc.status_code in {429, 500, 502, 503, 504}
            if not retryable or time.time() >= deadline:
                raise
            wait_seconds = min(SUBMIT_RETRY_MAX_DELAY_SECONDS, delay)
            _emit_task_event(on_task_update, {
                "taskId": "",
                "status": "queued",
                "phase": "供应商繁忙，系统自动重试",
                "progress": 15,
                "retryInSeconds": wait_seconds,
                "retryable": True,
                "httpStatus": exc.status_code,
            })
            time.sleep(wait_seconds)
            delay = min(SUBMIT_RETRY_MAX_DELAY_SECONDS, delay * 2)
        except RuntimeError:
            raise


def _submit_json(
    method: str,
    url: str,
    key: str,
    body: dict[str, Any],
    *,
    timeout: int = SUBMIT_TIMEOUT_SECONDS,
    on_task_update: TaskEventCallback | None = None,
    retry_window: int = SUBMIT_RETRY_WINDOW_SECONDS,
) -> dict[str, Any]:
    return _submit_with_retry(
        lambda: _request_json(method, url, key, body, timeout=timeout),
        on_task_update=on_task_update,
        retry_window=retry_window,
    )


def _submit_multipart(
    method: str,
    url: str,
    key: str,
    fields: list[tuple[str, Any]],
    *,
    timeout: int = SUBMIT_TIMEOUT_SECONDS,
    on_task_update: TaskEventCallback | None = None,
    retry_window: int = SUBMIT_RETRY_WINDOW_SECONDS,
) -> dict[str, Any]:
    return _submit_with_retry(
        lambda: _request_multipart(method, url, key, fields, timeout=timeout),
        on_task_update=on_task_update,
        retry_window=retry_window,
    )


MEDIA_DOWNLOAD_TIMEOUT_SECONDS = 300


def _download_bytes(url: str, timeout: int = MEDIA_DOWNLOAD_TIMEOUT_SECONDS) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "drama-canvas-studio/1.0"})
    try:
        with network_security.safe_urlopen(request, timeout=timeout) as response:
            data = response.read()
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
            return data, content_type
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"下载生成结果失败 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"下载生成结果失败：{exc.reason}") from exc


def _download_bytes_with_key(
    url: str,
    key: str,
    timeout: int = MEDIA_DOWNLOAD_TIMEOUT_SECONDS,
) -> tuple[bytes, str]:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "User-Agent": "drama-canvas-studio/1.0",
        },
    )
    try:
        with network_security.safe_urlopen(request, timeout=timeout) as response:
            data = response.read()
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip()
            return data, content_type
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"下载生成结果失败 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"下载生成结果失败：{exc.reason}") from exc


def _extension_for_mime(mime: str, fallback: str) -> str:
    return mimetypes.guess_extension(mime or "") or fallback


def save_generated_bytes(data: bytes, mime: str, fallback_ext: str) -> str:
    if not data:
        raise RuntimeError("供应商返回了空文件")
    upload_dir = _upload_dir()
    os.makedirs(upload_dir, exist_ok=True)
    ext = _extension_for_mime(mime, fallback_ext)
    if ext == ".jpe":
        ext = ".jpg"
    name = f"generated-{int(time.time())}-{secrets.token_hex(5)}{ext}"
    with open(os.path.join(upload_dir, name), "wb") as fh:
        fh.write(data)
    return f"/uploads/{name}"


def save_generated_url(url: str, fallback_ext: str = ".bin") -> str:
    if not str(url or "").strip():
        return ""
    if str(url).startswith("/uploads/"):
        return str(url)
    data, mime = _download_bytes(str(url))
    return save_generated_bytes(data, mime, fallback_ext)


def _trim_wav_bytes(data: bytes, max_seconds: float) -> bytes:
    if max_seconds <= 0:
        return data
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            frame_rate = source.getframerate()
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            frame_count = source.getnframes()
            max_frames = max(1, int(frame_rate * max_seconds))
            if frame_count <= max_frames:
                return data
            frames = source.readframes(max_frames)
            out = io.BytesIO()
            with wave.open(out, "wb") as target:
                target.setnchannels(channels)
                target.setsampwidth(sample_width)
                target.setframerate(frame_rate)
                target.writeframes(frames)
            return out.getvalue()
    except (wave.Error, EOFError):
        return data


def _ffmpeg_executable() -> str:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg

        bundled = str(imageio_ffmpeg.get_ffmpeg_exe() or "").strip()
        if bundled and os.path.isfile(bundled) and os.access(bundled, os.X_OK):
            return bundled
    except Exception:
        pass
    return ""


def _trim_audio_bytes(data: bytes, fallback_ext: str, max_seconds: float) -> bytes:
    if not data or max_seconds <= 0:
        return data
    suffix = (fallback_ext or "").lower()
    if suffix == ".wav":
        trimmed = _trim_wav_bytes(data, max_seconds)
        if trimmed != data:
            return trimmed

    ffmpeg = _ffmpeg_executable()
    if not ffmpeg:
        raise RuntimeError("裁剪非 WAV 音频需要 ffmpeg，但当前系统未找到 ffmpeg")
    codec = {
        ".mp3": ["-c:a", "libmp3lame", "-b:a", "128k"],
        ".m4a": ["-c:a", "aac", "-b:a", "128k"],
        ".aac": ["-c:a", "aac", "-b:a", "128k"],
    }.get(suffix, ["-c:a", "copy"])
    with tempfile.TemporaryDirectory(prefix="audio-trim-") as tmp:
        source = os.path.join(tmp, f"input{suffix or '.bin'}")
        target = os.path.join(tmp, f"output{suffix or '.bin'}")
        with open(source, "wb") as fh:
            fh.write(data)
        command = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", source, "-t", f"{max_seconds:.3f}", "-vn",
            "-map_metadata", "-1", *codec, target,
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if completed.returncode != 0 or not os.path.isfile(target):
            detail = completed.stderr.decode("utf-8", errors="replace")[:400]
            raise RuntimeError(f"音频裁剪失败：{detail or 'ffmpeg 未生成输出文件'}")
        with open(target, "rb") as fh:
            return fh.read()


def local_upload_path(url: str) -> str | None:
    raw = str(url or "").strip()
    if not raw.startswith("/uploads/"):
        return None
    name = urllib.parse.unquote(raw[len("/uploads/"):])
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise RuntimeError("本地素材路径非法")
    path = os.path.join(_upload_dir(), name)
    if not os.path.isfile(path):
        raise RuntimeError(f"本地素材不存在：{name}")
    return path


def _asset_url_for_provider(url: str) -> str:
    """Provider APIs cannot reach this machine's localhost.

    For local uploads we send a data URL. Remote URLs stay untouched.
    """
    raw = str(url or "").strip()
    if not raw:
        raise RuntimeError("引用素材缺少 URL")
    if raw.startswith(("http://", "https://", "data:")):
        return raw
    local = local_upload_path(raw)
    if not local:
        raise RuntimeError(f"不支持的素材地址：{raw[:80]}")
    size = os.path.getsize(local)
    if size > MAX_INLINE_BYTES:
        raise RuntimeError("本地参考素材超过 24MB，无法安全内联给供应商")
    mime = mimetypes.guess_type(local)[0] or "application/octet-stream"
    with open(local, "rb") as fh:
        encoded = base64.b64encode(fh.read()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _ark_reference_url(ref: dict[str, Any]) -> str:
    raw = str(ref.get("url") or "").strip()
    if not raw:
        raise RuntimeError("引用素材缺少 URL")
    storage = ref.get("storage")
    kind = str(ref.get("kind") or "image")
    source_model = str(ref.get("sourceModel") or "").lower()
    storage_cfg = provider_catalog.storage_config()
    source_generated_at = str(
        ref.get("generatedAt")
        or (
            storage.get("source", {}).get("generatedAt")
            if isinstance(storage, dict) and isinstance(storage.get("source"), dict)
            else ""
        )
        or ""
    )
    if source_model.startswith("doubao-seedream-5-0-") and source_generated_at:
        try:
            generated = datetime.fromisoformat(source_generated_at.replace("Z", "+00:00"))
            if generated.tzinfo is None:
                generated = generated.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - generated).total_seconds() > 30 * 86400:
                raise RuntimeError(
                    "Seedream 5.0 原始人像产物的方舟信任窗口只有生成后 30 天；"
                    "TOS 能长期保存文件，但不能延长这 30 天信任期，请重新生成角色图"
                )
        except RuntimeError:
            raise
        except (TypeError, ValueError):
            pass
    if isinstance(storage, dict) and storage.get("provider") == "tos" and storage.get("key"):
        return tos_storage.internal_presigned_url(storage)
    if kind == "image" and raw.startswith("/uploads/") and tos_storage.is_ready():
        descriptor = tos_storage.ensure_local_image(
            raw,
            source={
                "provider": ref.get("sourceProvider"),
                "model": ref.get("sourceModel"),
                "taskId": ref.get("taskId"),
                "generatedAt": ref.get("generatedAt"),
                "url": raw,
            },
        )
        if descriptor:
            return tos_storage.internal_presigned_url(descriptor)
    if (
        kind == "image"
        and source_model.startswith("doubao-seedream-5-0-")
        and storage_cfg.get("require_for_ark_portrait", True)
    ):
        raise RuntimeError(
            "Seedream 5.0 角色图进入 Seedance 前必须先把原始产物转存到 TOS；"
            "请先在设置中启用对象存储 TOS 并完成连接测试"
        )
    return _asset_url_for_provider(raw)


def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _safe_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _set_api_path(target: dict[str, Any], path: str, value: Any) -> None:
    parts = [part for part in str(path or "").split(".") if part]
    if not parts:
        return
    current = target
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _apply_declared_params(body: dict[str, Any], model: dict[str, Any], params: dict[str, Any]) -> None:
    """Copy custom catalog fields into the supplier payload.

    A model schema entry can declare `"api": "field"` or `"api": "input.field"`
    to make a custom field fully wired without changing node UI code.
    """
    for key, spec in (model.get("params") or {}).items():
        if not isinstance(spec, dict) or not spec.get("api"):
            continue
        value = params.get(key)
        if value in (None, ""):
            continue
        _set_api_path(body, str(spec["api"]), value)


def _reference_prompt(prompt: str, refs: list[dict[str, Any]]) -> str:
    if not refs:
        return prompt
    lines = []
    for index, ref in enumerate(refs, 1):
        kind = {"image": "参考图", "video": "参考视频", "audio": "参考音频", "text": "参考文本"}.get(
            ref.get("kind", ""), "参考素材"
        )
        label = str(ref.get("label") or f"素材{index}").strip()
        lines.append(f"{kind} {index}：{label}")
    return f"{prompt}\n\n[参考素材顺序]\n" + "\n".join(lines)


def _text_refs(refs: list[dict[str, Any]]) -> str:
    parts = []
    for ref in refs:
        if ref.get("kind") != "text":
            continue
        text = str(ref.get("text") or "").strip()
        if text:
            parts.append(f"【{ref.get('label') or '参考文本'}】\n{text}")
    return "\n\n".join(parts)


def _parse_image_result(response: dict[str, Any], provider: dict[str, Any]) -> dict[str, Any]:
    items = response.get("data") or response.get("images") or []
    if isinstance(items, dict):
        items = [items]
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("url"):
            return {"url": str(item["url"]), "raw": response}
        encoded = item.get("b64_json") or item.get("base64")
        if encoded:
            try:
                data = base64.b64decode(str(encoded))
            except (binascii.Error, ValueError) as exc:
                raise RuntimeError("供应商返回的图片 Base64 无法解码") from exc
            return {"url": save_generated_bytes(data, "image/png", ".png"), "raw": response}
    output = response.get("output") if isinstance(response.get("output"), dict) else {}
    for key in ("url", "image_url"):
        if output.get(key):
            return {"url": str(output[key]), "raw": response}
    raise RuntimeError(f"{provider.get('name') or '供应商'}出图无返回：{json.dumps(response, ensure_ascii=False)[:400]}")


def _newapi_api_origin(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(str(base_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("New API 接口地址不是有效的 HTTP 地址")
    return f"{parsed.scheme}://{parsed.netloc}"


def _newapi_image_route(remote_model: str) -> tuple[str, str, str, str]:
    """Return submit path, poll path, task-id field, and result parser."""
    name = str(remote_model or "").strip()
    lowered = name.lower()
    if lowered.startswith("s-gpt-image"):
        return "/hc/v1/images", "/hc/v1/images/tasks/", "task_id", "data"
    if name.startswith("即梦-"):
        return "/aistars/v1/images", "/aistars/v1/tasks/", "id", "artifacts"
    if name.startswith("T香蕉"):
        return "/kr/gemini/v1/images", "/kr/gemini/v1/images/tasks/", "task_id", "data"
    if lowered == "gpt-image-2":
        return "/kr/v1/images", "/kr/v1/images/tasks/", "task_id", "data"
    raise RuntimeError(
        f"New API 图片线路未实现：{name or '未填写远程模型 ID'}"
    )


def _newapi_image_result(
    task: dict[str, Any],
    *,
    source: str,
    provider: dict[str, Any],
    key: str,
) -> dict[str, Any]:
    if source == "artifacts":
        artifacts = task.get("artifacts") or []
        if isinstance(artifacts, dict):
            artifacts = [artifacts]
        for item in artifacts:
            if isinstance(item, dict) and item.get("content_url"):
                return {
                    "url": str(item["content_url"]),
                    "raw": task,
                    "authUrl": True,
                }
        raise RuntimeError(
            f"New API 图片任务完成但没有 artifacts 地址："
            f"{json.dumps(task, ensure_ascii=False)[:500]}"
        )
    try:
        return _parse_image_result(task, provider)
    except RuntimeError as exc:
        raise RuntimeError(
            f"New API 图片任务完成但没有图片地址："
            f"{json.dumps(task, ensure_ascii=False)[:500]}"
        ) from exc


def _newapi_image_reference_fields(
    route: str,
    prompt: str,
    image_urls: list[str],
    *,
    size: str,
    quality: str,
    image_size: str,
) -> list[tuple[str, Any]]:
    fields: list[tuple[str, Any]] = [
        ("prompt", prompt),
        ("n", "1"),
    ]
    if route == "/hc/v1/images":
        if size:
            fields.append(("size", size))
        if quality:
            fields.append(("quality", quality))
        return fields
    if route == "/kr/v1/images":
        if size:
            fields.append(("size", size))
        if image_size:
            fields.append(("image_size", image_size))
        if quality:
            fields.append(("quality", quality))
        return fields
    raise RuntimeError(f"New API 图片编辑线路未实现：{route}")


def _newapi_protected_content_url(raw_url: str, base: str) -> bool:
    parsed = urllib.parse.urlsplit(str(raw_url or "").strip())
    origin = urllib.parse.urlsplit(str(base or "").strip())
    return (
        parsed.scheme in {"http", "https"}
        and parsed.netloc == origin.netloc
        and parsed.path.startswith("/v1/tasks/")
    )


def _newapi_public_image_urls(image_urls: list[str], base: str) -> list[str]:
    public: list[str] = []
    for raw in image_urls:
        url = str(raw or "").strip()
        if not url.startswith(("http://", "https://")):
            return []
        if _newapi_protected_content_url(url, base):
            return []
        public.append(url)
    return public


def _newapi_image_file(
    raw_url: str,
    *,
    base: str,
    key: str,
) -> tuple[str, str, bytes]:
    raw = str(raw_url or "").strip()
    if raw.startswith("data:"):
        header, _, encoded = raw.partition(",")
        mime = header[5:].split(";", 1)[0].strip().lower() or "image/png"
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError("New API 参考图 Base64 无法解码") from exc
    else:
        url = raw if not raw.startswith("/") else base + raw
        if raw.startswith("/v1/tasks/") or _newapi_protected_content_url(raw, base):
            payload, mime = _download_bytes_with_key(url, key)
        else:
            payload, mime = _download_bytes(url)
    mime = str(mime or "image/png").split(";", 1)[0].strip().lower()
    if mime not in {"image/png", "image/jpeg", "image/webp"}:
        raise RuntimeError(f"New API 参考图格式不支持：{mime}")
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[mime]
    return f"reference{ext}", mime, payload


def _newapi_image_files(
    image_urls: list[str],
    *,
    base: str,
    key: str,
) -> list[tuple[str, str, bytes]]:
    return [
        _newapi_image_file(str(url), base=base, key=key)
        for url in image_urls
    ]


def _newapi_inline_image_parts(
    image_urls: list[str],
    *,
    base: str,
    key: str,
) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for raw in image_urls:
        mime = "image/png"
        if raw.startswith("data:"):
            header, _, encoded = raw.partition(",")
            declared = header[5:].split(";", 1)[0].strip().lower()
            if declared in {"image/png", "image/jpeg", "image/webp"}:
                mime = declared
            try:
                base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise RuntimeError("New API 参考图 Base64 无法解码") from exc
            data = encoded
        else:
            url = raw if not raw.startswith("/") else base + raw
            if raw.startswith("/v1/tasks/") or _newapi_protected_content_url(raw, base):
                payload, declared = _download_bytes_with_key(url, key)
            else:
                payload, declared = _download_bytes(url)
            if declared in {"image/png", "image/jpeg", "image/webp"}:
                mime = declared
            data = base64.b64encode(payload).decode("ascii")
        parts.append({"inlineData": {"mimeType": mime, "data": data}})
    return parts


def _newapi_image(
    provider: dict[str, Any],
    model: dict[str, Any],
    prompt: str,
    params: dict[str, Any],
    refs: list[dict[str, Any]],
    *,
    on_task_created: TaskEventCallback | None = None,
) -> dict[str, Any]:
    image_refs = [ref for ref in refs if ref.get("kind") == "image" and ref.get("url")]
    text_refs = [ref for ref in refs if ref.get("kind") == "text"]
    text = _reference_prompt(prompt, image_refs)
    extra_text = _text_refs(text_refs)
    if extra_text:
        text += "\n\n" + extra_text
    text = text.strip()
    if not text:
        raise RuntimeError("提示词为空，不能提交生成")

    remote_model = str(model.get("remoteModel") or "").strip()
    route, poll_prefix, task_field, source = _newapi_image_route(remote_model)
    base = _newapi_api_origin(provider["base_url"])
    key = provider_catalog.provider_key(provider)

    size = str(params.get("size") or "").strip()
    quality = str(params.get("quality") or "").strip()
    image_size = str(params.get("clarity") or params.get("imageSize") or "").strip().upper()
    if image_size not in {"1K", "2K", "4K"}:
        image_size = ""
    ratio = str(params.get("ratio") or "").strip()

    image_urls = [_asset_url_for_provider(str(ref["url"])) for ref in image_refs]
    if route == "/kr/v1/images":
        if len(text) > 4000:
            raise RuntimeError("gpt-image-2 提示词最多 4000 字符")
        body: dict[str, Any] = {
            "model": remote_model,
            "prompt": text,
            "n": 1,
        }
        if ratio:
            body["size"] = ratio
            if image_size:
                body["image_size"] = image_size
        elif size:
            body["size"] = size
        if quality:
            body["quality"] = quality
        _apply_declared_params(body, model, params)
        if image_urls:
            fields = [
                ("model", remote_model),
                *_newapi_image_reference_fields(
                    route, text, image_urls,
                    size=ratio or size,
                    quality=quality,
                    image_size=image_size,
                ),
            ]
            public_urls = _newapi_public_image_urls(image_urls, base)
            if public_urls:
                for url in public_urls:
                    fields.append(("image_url[]", url))
            else:
                for filename, mime, payload in _newapi_image_files(
                    image_urls, base=base, key=key,
                ):
                    fields.append(("image[]", (filename, mime, payload)))
            response = _request_multipart(
                "POST", f"{base}{route}/edits", key, fields,
                timeout=360,
            )
        else:
            response = _request_json(
                "POST", f"{base}{route}/generations", key, body, timeout=360,
            )
    elif route == "/kr/gemini/v1/images":
        parts: list[dict[str, Any]] = [{"text": text}]
        parts.extend(_newapi_inline_image_parts(image_urls, base=base, key=key))
        aspect_ratio = ratio or "1:1"
        resolution = image_size or "2K"
        body = {
            "model": remote_model,
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "imageConfig": {
                    "aspectRatio": aspect_ratio,
                    "imageSize": resolution,
                },
            },
        }
        _apply_declared_params(body, model, params)
        response = _request_json(
            "POST", f"{base}{route}/generations", key, body, timeout=360,
        )
    elif route == "/hc/v1/images":
        if len(text) > 8000:
            raise RuntimeError("HC 图片提示词最多 8000 字符")
        if image_urls:
            public_urls = _newapi_public_image_urls(image_urls, base)
            if public_urls:
                body = {
                    "model": remote_model,
                    "prompt": text,
                    "image": public_urls[0] if len(public_urls) == 1 else public_urls,
                    "n": 1,
                }
                if size:
                    body["size"] = size
                if quality:
                    body["quality"] = quality
                _apply_declared_params(body, model, params)
                response = _request_json(
                    "POST", f"{base}{route}/edits", key, body, timeout=360,
                )
            else:
                fields = [
                    ("model", remote_model),
                    ("prompt", text),
                    ("n", "1"),
                ]
                if size:
                    fields.append(("size", size))
                if quality:
                    fields.append(("quality", quality))
                for filename, mime, payload in _newapi_image_files(
                    image_urls, base=base, key=key,
                ):
                    fields.append(("image", (filename, mime, payload)))
                response = _request_multipart(
                    "POST", f"{base}{route}/edits/upload", key, fields,
                    timeout=360,
                )
        else:
            body = {
                "model": remote_model,
                "prompt": text,
                "n": 1,
            }
            if size:
                body["size"] = size
            if quality:
                body["quality"] = quality
            _apply_declared_params(body, model, params)
            response = _request_json(
                "POST", f"{base}{route}/generations", key, body, timeout=360,
            )
    elif route == "/aistars/v1/images":
        if len(text) > 5000:
            raise RuntimeError("即梦图片提示词最多 5000 字符")
        resolution = str(params.get("clarity") or params.get("resolution") or "").strip().upper()
        if resolution not in {"1K", "2K", "4K"}:
            resolution = "2K"
        aspect_ratio = ratio or "1:1"
        body = {
            "model": remote_model,
            "prompt": text,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "n": 1,
        }
        if image_urls:
            if any(not str(url).startswith("http") for url in image_urls):
                raise RuntimeError(
                    "即梦图片参考图必须是公网 HTTP(S) 地址，"
                    "不能使用本地文件或 Base64"
                )
            body["images"] = image_urls
            action = "edits"
        else:
            action = "generations"
        _apply_declared_params(body, model, params)
        response = _request_json(
            "POST", f"{base}{route}/{action}", key, body, timeout=360,
        )
    else:
        raise RuntimeError(f"New API 图片线路未实现：{route}")

    task_id = str(response.get(task_field) or response.get("id") or "").strip()
    if not task_id:
        raise RuntimeError(
            f"New API 图片任务未返回 {task_field}："
            f"{json.dumps(response, ensure_ascii=False)[:500]}"
        )
    _emit_task_event(on_task_created, {
        "taskId": task_id,
        "status": "queued",
        "progress": 0,
        "createdAt": time.time(),
    })
    poll_url = f"{base}{poll_prefix}{urllib.parse.quote(task_id, safe='')}"
    deadline = time.time() + 900
    poll_started = time.time()
    while time.time() < deadline:
        status_raw = str(response.get("status") or "").lower()
        if status_raw in {"succeeded", "completed", "success", "done"}:
            result = _newapi_image_result(
                response, source=source, provider=provider, key=key,
            )
            result["taskId"] = task_id
            result["raw"] = response
            if result.pop("authUrl", False):
                result["url"] = _persist_auth_url(
                    str(result["url"]), base, key, ".png",
                )
            return result
        if status_raw in {"failed", "error", "cancelled", "canceled"}:
            error = response.get("error")
            detail = error.get("message") if isinstance(error, dict) else error
            raise RuntimeError(
                f"New API 图片生成失败：{detail or json.dumps(response, ensure_ascii=False)[:600]}"
            )
        time.sleep(poll_delay(time.time() - poll_started))
        response = _request_json("GET", poll_url, key, timeout=30)
    raise RuntimeError(f"New API 图片任务超时（900s，任务 {task_id}）")


def _persist_auth_url(url: str, base: str, key: str, fallback_ext: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    if raw.startswith("/"):
        raw = base + raw
    data, mime = _download_bytes_with_key(raw, key)
    return save_generated_bytes(data, mime, fallback_ext)


def _ark_image(
    provider: dict[str, Any],
    model: dict[str, Any],
    prompt: str,
    params: dict[str, Any],
    refs: list[dict[str, Any]],
) -> dict[str, Any]:
    media_refs = [ref for ref in refs if ref.get("url")]
    semantic_prompt = _reference_prompt(prompt, media_refs)
    extra_text = _text_refs(refs)
    if extra_text:
        semantic_prompt += "\n\n" + extra_text
    guidance = str(params.get("guidance") or "").strip()
    if guidance:
        semantic_prompt += _GUIDANCE_WORDS.get(guidance, "")

    body: dict[str, Any] = {
        "model": model["remoteModel"],
        "prompt": semantic_prompt,
        "size": _safe_seedream_size(params.get("size"), params.get("ratio")),
        "response_format": "url",
        "watermark": bool(params.get("watermark", False)),
    }
    images = [_asset_url_for_provider(str(ref["url"])) for ref in media_refs if ref.get("kind") == "image"]
    if len(images) == 1:
        body["image"] = images[0]
    elif len(images) > 1:
        body["image"] = images
    if params.get("sequentialImageGeneration"):
        body["sequential_image_generation"] = params["sequentialImageGeneration"]
    if params.get("sequentialMaxImages"):
        body["sequential_image_generation_options"] = {
            "max_images": _safe_int(params["sequentialMaxImages"], 1, 1, 15)
        }
    _apply_declared_params(body, model, params)
    response = _request_json(
        "POST",
        _join_url(provider["base_url"], "/images/generations"),
        provider_catalog.provider_key(provider),
        body,
        timeout=SYNC_MEDIA_TIMEOUT_SECONDS,
    )
    return _parse_image_result(response, provider)


def _openai_image(
    provider: dict[str, Any],
    model: dict[str, Any],
    prompt: str,
    params: dict[str, Any],
    refs: list[dict[str, Any]],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model["remoteModel"],
        "prompt": _reference_prompt(prompt, refs),
        "size": params.get("size") or "1024x1024",
        "response_format": "url",
    }
    if params.get("quality"):
        body["quality"] = params["quality"]
    if params.get("style"):
        body["style"] = params["style"]
    _apply_declared_params(body, model, params)
    response = _request_json(
        "POST",
        _join_url(provider["base_url"], "/images/generations"),
        provider_catalog.provider_key(provider),
        body,
        timeout=SYNC_MEDIA_TIMEOUT_SECONDS,
    )
    return _parse_image_result(response, provider)


def _agnes_image_prompt(prompt: str, image_refs: list[dict[str, Any]], text_refs: list[dict[str, Any]]) -> str:
    text = prompt
    if image_refs:
        mapping = [
            f"<Picture {index}>：{str(ref.get('label') or f'参考图 {index}').strip()}"
            for index, ref in enumerate(image_refs, 1)
        ]
        text += "\n\n[参考图映射]\n" + "\n".join(mapping)
    extra_text = _text_refs(text_refs)
    if extra_text:
        text += "\n\n" + extra_text
    return text


def _agnes_image(
    provider: dict[str, Any],
    model: dict[str, Any],
    prompt: str,
    params: dict[str, Any],
    refs: list[dict[str, Any]],
) -> dict[str, Any]:
    image_refs = [ref for ref in refs if ref.get("kind") == "image" and ref.get("url")]
    text_refs = [ref for ref in refs if ref.get("kind") == "text"]
    images = [_asset_url_for_provider(str(ref["url"])) for ref in image_refs]
    response_format = str(params.get("responseFormat") or "url").strip().lower()
    if response_format not in {"url", "b64_json"}:
        response_format = "url"

    tier = str(params.get("clarity") or "").strip().upper()
    if tier not in {"1K", "2K", "3K", "4K"}:
        candidate = str(params.get("size") or "").strip().upper()
        tier = candidate if candidate in {"1K", "2K", "3K", "4K"} else "2K"
    ratios = {"1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9"}
    ratio = str(params.get("ratio") or "16:9").strip()
    if ratio not in ratios:
        ratio = "16:9"

    extra_body: dict[str, Any] = {"response_format": response_format}
    if images:
        extra_body["image"] = images
    body: dict[str, Any] = {
        "model": model["remoteModel"],
        "prompt": _agnes_image_prompt(prompt, image_refs, text_refs),
        "size": tier,
        "ratio": ratio,
        "extra_body": extra_body,
    }
    if response_format == "b64_json":
        body["return_base64"] = True
    _apply_declared_params(body, model, params)
    response = _request_json(
        "POST",
        _join_url(provider["base_url"], "/images/generations"),
        provider_catalog.provider_key(provider),
        body,
        timeout=SYNC_MEDIA_TIMEOUT_SECONDS,
    )
    return _parse_image_result(response, provider)


def _agnes_video_prompt(
    prompt: str,
    image_refs: list[dict[str, Any]],
    audio_refs: list[dict[str, Any]],
    text_refs: list[dict[str, Any]],
) -> str:
    lines = []
    lines.extend(
        f"<Picture {index}>：{str(ref.get('label') or f'参考图 {index}').strip()}"
        for index, ref in enumerate(image_refs, 1)
    )
    lines.extend(
        f"<Audio {index}>：{str(ref.get('label') or f'参考音频 {index}').strip()}"
        for index, ref in enumerate(audio_refs, 1)
    )
    text = prompt
    if lines:
        text += "\n\n[参考素材映射]\n" + "\n".join(lines)
    extra_text = _text_refs(text_refs)
    if extra_text:
        text += "\n\n" + extra_text
    return text


def _agnes_api_origin(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(str(base_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("Agnes 接口地址不是有效的 HTTP 地址")
    return f"{parsed.scheme}://{parsed.netloc}"


def _agnes_video(
    provider: dict[str, Any],
    model: dict[str, Any],
    prompt: str,
    params: dict[str, Any],
    refs: list[dict[str, Any]],
    voices: list[dict[str, Any]],
    *,
    on_task_created: TaskEventCallback | None = None,
    on_task_update: TaskEventCallback | None = None,
) -> dict[str, Any]:
    image_refs = [ref for ref in refs if ref.get("kind") == "image" and ref.get("url")]
    video_refs = [ref for ref in refs if ref.get("kind") == "video" and ref.get("url")]
    text_refs = [ref for ref in refs if ref.get("kind") == "text"]
    audio_refs = [ref for ref in voices if ref.get("url")]
    if video_refs:
        raise RuntimeError("Agnes Video 2.5 Flash 不支持参考视频，请移除视频引用")
    if len(image_refs) > 5:
        raise RuntimeError(f"Agnes Video 2.5 Flash 最多接受 5 张参考图，实际 {len(image_refs)} 张")
    if len(audio_refs) > 3:
        raise RuntimeError(f"Agnes Video 2.5 Flash 最多接受 3 段参考音频，实际 {len(audio_refs)} 段")

    images = [_asset_url_for_provider(str(ref["url"])) for ref in image_refs]
    audios = [_asset_url_for_provider(str(ref["url"])) for ref in audio_refs]
    first_frame = str(params.get("firstFrame") or params.get("first_frame") or "").strip()
    last_frame = str(params.get("lastFrame") or params.get("last_frame") or "").strip()
    mode = str(params.get("mode") or "auto").strip().lower()
    if mode == "auto":
        if first_frame or last_frame:
            mode = "keyframe"
        elif images or audios:
            mode = "reference"
        else:
            mode = "text"
    if mode not in {"text", "reference", "keyframe"}:
        raise RuntimeError("Agnes 视频模式只支持 auto / text / reference / keyframe")

    ratios = {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}
    ratio = str(params.get("ratio") or "16:9").strip()
    if ratio not in ratios:
        ratio = "16:9"
    duration = _safe_int(params.get("duration"), 5, 4, 12)
    body: dict[str, Any] = {
        "model": model["remoteModel"],
        "prompt": _agnes_video_prompt(prompt, image_refs, audio_refs, text_refs),
        "seconds": str(duration),
        "mode": mode,
        "size": "720P",
        "aspect_ratio": ratio,
        "n": 1,
    }

    if mode == "text":
        if images or audios:
            raise RuntimeError("Agnes 视频 text 模式不接受图片或音频引用；请改用 reference/auto")
    elif mode == "keyframe":
        if not first_frame and images:
            first_frame = images[0]
        if not last_frame and len(images) > 1:
            last_frame = images[1]
        if not first_frame and not last_frame:
            raise RuntimeError("Agnes 视频 keyframe 模式至少需要首帧或尾帧图片")
        if audios:
            raise RuntimeError("Agnes 视频 keyframe 模式不支持参考音频；请改用 reference/auto")
        if first_frame:
            body["first_frame"] = _asset_url_for_provider(first_frame)
        if last_frame:
            body["last_frame"] = _asset_url_for_provider(last_frame)
    else:
        if not images and not audios:
            raise RuntimeError("Agnes 视频 reference 模式至少需要 1 张参考图或 1 段参考音频")
        if images:
            body["images"] = images
        if audios:
            body["audios"] = audios

    if params.get("seed") not in (None, ""):
        body["seed"] = _safe_int(params.get("seed"), 0, 0, 2**31 - 1)
    _apply_declared_params(body, model, params)
    key = provider_catalog.provider_key(provider)
    response = _submit_json(
        "POST",
        _join_url(provider["base_url"], "/videos"),
        key,
        body,
        timeout=SUBMIT_TIMEOUT_SECONDS,
        on_task_update=on_task_update,
    )
    video_id = str(response.get("video_id") or _task_id(response)).strip()
    if not video_id:
        raise RuntimeError(f"Agnes 视频任务未返回 video_id：{json.dumps(response, ensure_ascii=False)[:400]}")
    poll_url = (
        f"{_agnes_api_origin(provider['base_url'])}/agnesapi?"
        + urllib.parse.urlencode({"video_id": video_id, "model_name": model["remoteModel"]})
    )
    _emit_task_event(on_task_created, {
        "taskId": video_id,
        "status": "queued",
        "progress": 0,
        "createdAt": time.time(),
    })
    deadline = time.time() + 900
    poll_started = time.time()
    retry_streak = 0
    last_poll_error = ""
    while time.time() < deadline:
        try:
            task = _request_json("GET", poll_url, key, timeout=30)
        except ProviderHTTPError as exc:
            if exc.status_code not in {429, 500, 502, 503, 504}:
                raise
            retry_streak += 1
            last_poll_error = str(exc)
            retry_after = 0
            try:
                retry_after = int((exc.headers or {}).get("Retry-After", "0"))
            except (TypeError, ValueError):
                retry_after = 0
            base_delay = poll_delay(time.time() - poll_started)
            wait_seconds = min(30, max(base_delay, retry_after or base_delay * (2 ** min(retry_streak - 1, 3))))
            if time.time() + wait_seconds >= deadline:
                break
            time.sleep(wait_seconds)
            continue
        retry_streak = 0
        status = str(task.get("status") or "").lower()
        _emit_task_event(on_task_update, {
            "taskId": video_id,
            "status": status or "running",
            "internalStatus": str(task.get("internal_status") or ""),
            "progress": task.get("progress"),
            "startedAt": task.get("started_at"),
        })
        if status in {"completed", "succeeded", "success", "done"}:
            url = _video_url_from_task(task)
            if not url:
                raise RuntimeError(f"Agnes 视频任务完成但没有视频地址：{json.dumps(task, ensure_ascii=False)[:500]}")
            return {"url": url, "raw": task, "taskId": video_id, "videoId": video_id}
        if status in {"failed", "error", "cancelled", "canceled"}:
            error = task.get("error")
            detail = error.get("message") if isinstance(error, dict) else error
            raise RuntimeError(f"Agnes 视频生成失败：{detail or json.dumps(task, ensure_ascii=False)[:600]}")
        time.sleep(poll_delay(time.time() - poll_started))
    suffix = f"，最后错误：{last_poll_error}" if last_poll_error else ""
    raise RuntimeError(f"Agnes 视频任务超时（900s，video_id {video_id}）{suffix}")


def _task_id(response: dict[str, Any]) -> str:
    for key in ("id", "task_id", "request_id"):
        if response.get(key):
            return str(response[key])
    task = response.get("task") if isinstance(response.get("task"), dict) else {}
    if task.get("id"):
        return str(task["id"])
    raise RuntimeError(f"视频任务未返回任务 ID：{json.dumps(response, ensure_ascii=False)[:400]}")


def _video_url_from_task(task: dict[str, Any]) -> str:
    if task.get("url"):
        return str(task["url"])
    if task.get("video_url"):
        return str(task["video_url"])
    content = task.get("content") if isinstance(task.get("content"), dict) else {}
    for key in ("video_url", "url"):
        if content.get(key):
            return str(content[key])
    output = task.get("output") if isinstance(task.get("output"), dict) else {}
    for key in ("video_url", "url"):
        if output.get(key):
            return str(output[key])
    urls = output.get("video_urls") or output.get("urls") or []
    if isinstance(urls, list):
        for item in urls:
            if isinstance(item, str):
                return item
            if isinstance(item, dict) and item.get("url"):
                return str(item["url"])
    return ""


def _newapi_video(
    provider: dict[str, Any],
    model: dict[str, Any],
    prompt: str,
    params: dict[str, Any],
    refs: list[dict[str, Any]],
    voices: list[dict[str, Any]],
    *,
    on_task_created: TaskEventCallback | None = None,
    on_task_update: TaskEventCallback | None = None,
) -> dict[str, Any]:
    """OpenAI-compatible video relay: POST /v1/videos, GET /v1/videos/{id}."""
    text = _reference_prompt(prompt, refs + voices)
    text_refs = _text_refs(refs)
    if text_refs:
        text += "\n\n" + text_refs
    images = [
        _asset_url_for_provider(str(ref["url"]))
        for ref in refs
        if ref.get("kind") == "image" and ref.get("url")
    ]
    seconds = _safe_int(params.get("duration") or params.get("seconds"), 5, 1, 30)
    resolution = str(params.get("resolution") or "720P").upper().replace("P", "p")
    metadata = {
        "resolution": resolution,
        "generate_audio": bool(params.get("generateAudio", True)),
        "watermark": bool(params.get("watermark", False)),
    }
    body: dict[str, Any] = {
        "model": model["remoteModel"],
        "prompt": text,
        "seconds": seconds,
        "size": resolution,
        "metadata": metadata,
    }
    if images:
        body["images"] = images
        body["image"] = images[0] if len(images) == 1 else images
        body["input_reference"] = images[0]
    _apply_declared_params(body, model, params)
    key = provider_catalog.provider_key(provider)
    response = _submit_json(
        "POST",
        _join_url(provider["base_url"], "/videos"),
        key,
        body,
        on_task_update=on_task_update,
    )
    task_id = _task_id(response)
    _emit_task_event(on_task_created, {
        "taskId": task_id,
        "status": "queued",
        "progress": response.get("progress") or 0,
        "createdAt": time.time(),
    })
    poll_url = _join_url(
        provider["base_url"],
        "/videos/" + urllib.parse.quote(task_id, safe=""),
    )
    deadline = time.time() + 1800
    poll_started = time.time()
    while time.time() < deadline:
        task = _request_json("GET", poll_url, key, timeout=30)
        status = str(task.get("status") or "").lower()
        _emit_task_event(on_task_update, {
            "taskId": task_id,
            "status": status or "running",
            "progress": task.get("progress"),
        })
        if status in {"completed", "succeeded", "success", "done"}:
            url = _video_url_from_task(task)
            if not url:
                raise RuntimeError(f"视频任务完成但没有视频地址：{json.dumps(task, ensure_ascii=False)[:500]}")
            return {"url": url, "raw": task, "taskId": task_id}
        if status in {"failed", "error", "cancelled", "canceled"}:
            error = task.get("error")
            detail = error.get("message") if isinstance(error, dict) else error
            raise RuntimeError(f"视频生成失败：{detail or json.dumps(task, ensure_ascii=False)[:600]}")
        time.sleep(poll_delay(time.time() - poll_started))
    raise RuntimeError(f"视频任务超时（1800s，任务 {task_id}）")


def _ark_video(
    provider: dict[str, Any],
    model: dict[str, Any],
    prompt: str,
    params: dict[str, Any],
    refs: list[dict[str, Any]],
    voices: list[dict[str, Any]],
    *,
    on_task_created: TaskEventCallback | None = None,
    on_task_update: TaskEventCallback | None = None,
) -> dict[str, Any]:
    text = _reference_prompt(prompt, refs + voices)
    text_refs = _text_refs(refs)
    if text_refs:
        text += "\n\n" + text_refs
    guidance = str(params.get("guidance") or "").strip()
    if guidance:
        text += _GUIDANCE_WORDS.get(guidance, "")
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for ref in refs:
        if not ref.get("url"):
            continue
        url = _ark_reference_url(ref)
        kind = str(ref.get("kind") or "image")
        if kind == "video":
            content.append({"type": "video_url", "video_url": {"url": url}, "role": "reference_video"})
        elif kind == "image":
            content.append({"type": "image_url", "image_url": {"url": url}, "role": "reference_image"})
    for voice in voices:
        if not voice.get("url"):
            continue
        url = _ark_reference_url(voice)
        content.append({"type": "audio_url", "audio_url": {"url": url}, "role": "reference_audio"})

    duration_default = 5
    duration_spec = (model.get("params") or {}).get("duration") or {}
    duration = _safe_int(
        params.get("duration"),
        _safe_int(duration_spec.get("default"), duration_default, 4, 30),
        _safe_int(duration_spec.get("min"), 4, 1, 60),
        _safe_int(duration_spec.get("max"), 30, 1, 60),
    )
    resolution_spec = (model.get("params") or {}).get("resolution") or {}
    resolutions = resolution_spec.get("options") or ["480P", "720P", "1080P"]
    resolution = str(params.get("resolution") or resolutions[0]).upper()
    if resolution not in {str(item).upper() for item in resolutions}:
        resolution = str(resolutions[0]).upper()
    ratio_spec = (model.get("params") or {}).get("ratio") or {}
    ratios = ratio_spec.get("options") or ["9:16"]
    ratio = str(params.get("ratio") or "9:16")
    if ratio not in ratios:
        ratio = str(ratios[0])

    body: dict[str, Any] = {
        "model": model["remoteModel"],
        "content": content,
        "duration": duration,
        "ratio": ratio,
        "resolution": resolution.lower(),
        "generate_audio": bool(params.get("generateAudio", True)),
        "watermark": bool(params.get("watermark", False)),
    }
    if params.get("seed") not in (None, ""):
        body["seed"] = _safe_int(params.get("seed"), -1, -1, 2**31 - 1)
    _apply_declared_params(body, model, params)
    response = _submit_json(
        "POST",
        _join_url(provider["base_url"], "/contents/generations/tasks"),
        provider_catalog.provider_key(provider),
        body,
        on_task_update=on_task_update,
    )
    task_id = _task_id(response)
    _emit_task_event(on_task_created, {
        "taskId": task_id,
        "status": "queued",
        "progress": 0,
        "createdAt": time.time(),
    })
    poll_url = _join_url(
        provider["base_url"],
        "/contents/generations/tasks/" + urllib.parse.quote(task_id, safe=""),
    )
    key = provider_catalog.provider_key(provider)
    deadline = time.time() + 900
    poll_started = time.time()
    while time.time() < deadline:
        task = _request_json("GET", poll_url, key, timeout=30)
        status = str(task.get("status") or "").lower()
        _emit_task_event(on_task_update, {
            "taskId": task_id,
            "status": status or "running",
            "progress": task.get("progress"),
        })
        if status in {"succeeded", "success", "completed", "done"}:
            url = _video_url_from_task(task)
            if not url:
                raise RuntimeError(f"视频任务完成但没有视频地址：{json.dumps(task, ensure_ascii=False)[:500]}")
            return {"url": url, "raw": task, "taskId": task_id}
        if status in {"failed", "error", "cancelled", "canceled"}:
            raise RuntimeError(f"视频生成失败：{json.dumps(task, ensure_ascii=False)[:600]}")
        time.sleep(poll_delay(time.time() - poll_started))
    raise RuntimeError(f"视频任务超时（900s，任务 {task_id}）")


def _openai_chat(
    provider: dict[str, Any],
    model: dict[str, Any],
    prompt: str,
    params: dict[str, Any],
    refs: list[dict[str, Any]],
) -> dict[str, Any]:
    messages = []
    system = str(params.get("system") or "").strip()
    refs_text = _text_refs(refs)
    if system or refs_text:
        system_text = "\n\n".join(item for item in (system, refs_text) if item)
        messages.append({"role": "system", "content": system_text})
    messages.append({"role": "user", "content": prompt})
    body: dict[str, Any] = {
        "model": model["remoteModel"],
        "messages": messages,
        "max_tokens": _safe_int(params.get("maxTokens"), 3000, 64, 65536),
        "temperature": _safe_float(params.get("temperature"), 0.7, 0.0, 2.0),
    }
    _apply_declared_params(body, model, params)
    response = _request_json(
        "POST",
        _join_url(provider["base_url"], "/chat/completions"),
        provider_catalog.provider_key(provider),
        body,
    )
    choices = response.get("choices") or []
    if not choices:
        raise RuntimeError(f"文本模型无返回：{json.dumps(response, ensure_ascii=False)[:400]}")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    text = str(content or message.get("reasoning_content") or "").strip()
    if not text:
        raise RuntimeError("文本模型返回空内容")
    return {"text": text, "raw": response}


def _audio_payload_result(
    response: dict[str, Any],
    *,
    fallback_mime: str,
    fallback_ext: str,
    max_seconds: float | None = None,
) -> str:
    output = response.get("output") if isinstance(response.get("output"), dict) else {}
    for container in (output.get("audio"), output.get("preview_audio"), response.get("audio")):
        if not isinstance(container, dict):
            continue
        if container.get("data"):
            try:
                data = base64.b64decode(str(container["data"]))
            except (binascii.Error, ValueError) as exc:
                raise RuntimeError("百炼返回的音频 Base64 无法解码") from exc
            if max_seconds:
                data = _trim_audio_bytes(data, fallback_ext, max_seconds)
            return save_generated_bytes(data, fallback_mime, fallback_ext)
        if container.get("url"):
            local = save_generated_url(str(container["url"]), fallback_ext)
            if max_seconds:
                path = local_upload_path(local)
                if path:
                    try:
                        actual_ext = os.path.splitext(path)[1].lower() or fallback_ext
                        with open(path, "rb") as fh:
                            trimmed = _trim_audio_bytes(fh.read(), actual_ext, max_seconds)
                        with open(path, "wb") as fh:
                            fh.write(trimmed)
                    except Exception:
                        try:
                            os.remove(path)
                        except OSError:
                            pass
                        raise
            return local
    for key in ("audio_url", "url"):
        if output.get(key):
            return save_generated_url(str(output[key]), fallback_ext)
        if response.get(key):
            return save_generated_url(str(response[key]), fallback_ext)
    return ""


def _dashscope_tts(
    provider: dict[str, Any],
    model: dict[str, Any],
    prompt: str,
    params: dict[str, Any],
    *,
    title: str = "",
) -> dict[str, Any]:
    key = provider_catalog.provider_key(provider)
    base = str(provider.get("base_url") or "").rstrip("/")
    mode = str(params.get("ttsMode") or "design")
    voice_id = str(params.get("voiceId") or params.get("ttsVoice") or "").strip()
    output_format = str(params.get("outputFormat") or "mp3").lower()
    if output_format not in {"wav", "mp3"}:
        output_format = "mp3"
    sample_rate = _safe_int(params.get("sampleRate"), 24000, 8000, 96000)
    preview_duration = _safe_float(params.get("previewDuration"), 4.5, 0.5, 4.5)

    if mode == "speech":
        if not voice_id:
            raise RuntimeError("语音合成模式需要填写 voiceId")
        if not prompt.strip():
            raise RuntimeError("语音合成文本为空")
        body = {
            "model": model["remoteModel"],
            "input": {
                "text": prompt.strip(),
                "voice": voice_id,
                "format": output_format,
                "sample_rate": sample_rate,
            },
        }
        _apply_declared_params(body, model, params)
        response = _submit_json(
            "POST",
            f"{base}/services/audio/tts/SpeechSynthesizer",
            key,
            body,
            timeout=SUBMIT_TIMEOUT_SECONDS,
        )
        url = _audio_payload_result(
            response,
            fallback_mime="audio/mpeg" if output_format == "mp3" else "audio/wav",
            fallback_ext=".mp3" if output_format == "mp3" else ".wav",
        )
        if not url:
            raise RuntimeError(f"百炼语音合成未返回音频：{json.dumps(response, ensure_ascii=False)[:500]}")
        return {
            "url": url,
            "voiceId": voice_id,
            "mode": "speech",
            "format": output_format,
            "sampleRate": sample_rate,
            "raw": response,
        }

    voice_prompt = str(params.get("voicePrompt") or prompt or title).strip()[:500]
    if not voice_prompt:
        raise RuntimeError("音色设计需要填写音色描述")
    preview_text = str(params.get("previewText") or "").strip()
    if len(preview_text) < 15:
        speaker = re.sub(r"^音频\s*[｜|]\s*", "", str(title or "").strip())
        speaker = re.sub(r"[（(]\s*图\s*\d+\s*[)）]\s*$", "", speaker).strip()
        seed_text = preview_text or speaker or "当前角色"
        preview_text = (
            f"{seed_text}，这是一段用于短剧对白的角色音色试听，"
            "请保持自然语气、清晰咬字和稳定节奏。"
        )
    preview_text = preview_text[:200]
    # DashScope voice enrollment requires prefix <= 10 characters.
    prefix = f"cv{secrets.token_hex(4)}"
    body = {
        "model": "voice-enrollment",
        "input": {
            "action": "create_voice",
            "target_model": model["remoteModel"],
            "voice_prompt": voice_prompt,
            "preview_text": preview_text,
            "prefix": prefix,
            "language_hints": ["zh"],
        },
        "parameters": {
            "sample_rate": sample_rate,
            "response_format": output_format,
        },
    }
    _apply_declared_params(body, model, params)
    response = _submit_json(
        "POST",
        f"{base}/services/audio/tts/customization",
        key,
        body,
        timeout=SUBMIT_TIMEOUT_SECONDS,
    )
    output = response.get("output") if isinstance(response.get("output"), dict) else {}
    created_voice = str(output.get("voice_id") or output.get("voice") or response.get("voice") or "").strip()
    url = _audio_payload_result(
        response,
        fallback_mime="audio/mpeg" if output_format == "mp3" else "audio/wav",
        fallback_ext=".mp3" if output_format == "mp3" else ".wav",
        max_seconds=preview_duration,
    )
    if not url:
        if not created_voice:
            raise RuntimeError(f"音色设计未返回 voiceId 或试听音频：{json.dumps(response, ensure_ascii=False)[:500]}")
        speech = {
            "model": model["remoteModel"],
            "input": {
                "text": preview_text[:500],
                "voice": created_voice,
                "format": output_format,
                "sample_rate": sample_rate,
            },
        }
        speech_response = _submit_json(
            "POST",
            f"{base}/services/audio/tts/SpeechSynthesizer",
            key,
            speech,
            timeout=SUBMIT_TIMEOUT_SECONDS,
        )
        url = _audio_payload_result(
            speech_response,
            fallback_mime="audio/mpeg" if output_format == "mp3" else "audio/wav",
            fallback_ext=".mp3" if output_format == "mp3" else ".wav",
            max_seconds=preview_duration,
        )
        response = {"design": response, "speech": speech_response}
    if not url:
        raise RuntimeError("百炼音色设计完成，但未取得可播放音频")
    return {
        "url": url,
        "voiceId": created_voice,
        "mode": "design",
        "format": output_format,
        "sampleRate": sample_rate,
        "raw": response,
    }


def generate_image(
    provider: dict[str, Any],
    model: dict[str, Any],
    prompt: str,
    params: dict[str, Any],
    refs: list[dict[str, Any]],
) -> dict[str, Any]:
    adapter = str(model.get("adapter") or "")
    if adapter == "ark_image":
        return _ark_image(provider, model, prompt, params, refs)
    if adapter == "openai_image":
        return _openai_image(provider, model, prompt, params, refs)
    if adapter == "newapi_image":
        return _newapi_image(provider, model, prompt, params, refs)
    if adapter == "agnes_image":
        return _agnes_image(provider, model, prompt, params, refs)
    raise RuntimeError(f"图片模型适配器未实现：{adapter}")


def generate_video(
    provider: dict[str, Any],
    model: dict[str, Any],
    prompt: str,
    params: dict[str, Any],
    refs: list[dict[str, Any]],
    voices: list[dict[str, Any]],
    *,
    on_task_created: TaskEventCallback | None = None,
    on_task_update: TaskEventCallback | None = None,
) -> dict[str, Any]:
    adapter = str(model.get("adapter") or "")
    if adapter == "ark_video":
        return _ark_video(
            provider, model, prompt, params, refs, voices,
            on_task_created=on_task_created,
            on_task_update=on_task_update,
        )
    if adapter == "newapi_video":
        return _newapi_video(
            provider, model, prompt, params, refs, voices,
            on_task_created=on_task_created,
            on_task_update=on_task_update,
        )
    if adapter == "agnes_video":
        return _agnes_video(
            provider, model, prompt, params, refs, voices,
            on_task_created=on_task_created,
            on_task_update=on_task_update,
        )
    raise RuntimeError(f"视频模型适配器未实现：{adapter}")


def check_video_task(
    provider: dict[str, Any],
    model: dict[str, Any],
    task_id: str,
) -> dict[str, Any]:
    """Read a remote video task without creating or charging a new one."""
    task_id = str(task_id or "").strip()
    if not task_id:
        raise RuntimeError("视频任务 ID 为空")
    adapter = str(model.get("adapter") or "")
    key = provider_catalog.provider_key(provider)
    if adapter == "agnes_video":
        poll_url = (
            f"{_agnes_api_origin(provider['base_url'])}/agnesapi?"
            + urllib.parse.urlencode({"video_id": task_id, "model_name": model["remoteModel"]})
        )
    elif adapter == "ark_video":
        poll_url = _join_url(
            provider["base_url"],
            "/contents/generations/tasks/" + urllib.parse.quote(task_id, safe=""),
        )
    elif adapter == "newapi_video":
        poll_url = _join_url(
            provider["base_url"],
            "/videos/" + urllib.parse.quote(task_id, safe=""),
        )
    else:
        raise RuntimeError(f"不支持恢复的视频适配器：{adapter}")
    task = _request_json("GET", poll_url, key, timeout=30)
    raw_status = str(task.get("status") or "").lower()
    if raw_status in {"completed", "succeeded", "success", "done"}:
        url = _video_url_from_task(task)
        if not url:
            raise RuntimeError(f"视频任务完成但没有视频地址：{json.dumps(task, ensure_ascii=False)[:500]}")
        state = "completed"
    elif raw_status in {"failed", "error", "cancelled", "canceled"}:
        state = "failed"
    else:
        state = "running"
    error = task.get("error")
    detail = error.get("message") if isinstance(error, dict) else error
    return {
        "taskId": task_id,
        "status": state,
        "rawStatus": raw_status,
        "progress": task.get("progress"),
        "url": _video_url_from_task(task) if state == "completed" else "",
        "error": detail or "",
        "raw": task,
    }


def generate_text(
    provider: dict[str, Any],
    model: dict[str, Any],
    prompt: str,
    params: dict[str, Any],
    refs: list[dict[str, Any]],
) -> dict[str, Any]:
    adapter = str(model.get("adapter") or "")
    if adapter == "openai_chat":
        return _openai_chat(provider, model, prompt, params, refs)
    raise RuntimeError(f"文本模型适配器未实现：{adapter}")


def generate_audio(
    provider: dict[str, Any],
    model: dict[str, Any],
    prompt: str,
    params: dict[str, Any],
    *,
    title: str = "",
    refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    adapter = str(model.get("adapter") or "")
    if adapter == "dashscope_tts":
        refs = refs or []
        text_context = _text_refs(refs)
        if text_context:
            params = {**params, "voicePrompt": "\n\n".join(
                item for item in (str(params.get("voicePrompt") or prompt), text_context) if item
            )}
        return _dashscope_tts(provider, model, prompt, params, title=title)
    raise RuntimeError(f"音频模型适配器未实现：{adapter}")
