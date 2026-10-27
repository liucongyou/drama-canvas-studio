# -*- coding: utf-8 -*-
"""server.py — 本地画布工作流系统后端（标准库 http.server · 零依赖）

启动：python server.py  → http://127.0.0.1:8765
API：
  GET  /api/projects                         项目列表
  POST /api/projects {title}                 新建空项目
  GET  /api/projects/{id}                    项目 payload
  DELETE /api/projects/{id}                  删除项目
  POST /api/projects/{id}/import {feed_path|feed_json}   导入投喂载荷 → 五区画布
  POST /api/projects/{id}/gate-check         挂接机检
  GET  /api/config                          方舟配置状态
  POST /api/config {ark_api_key,...}        保存方舟配置
  GET  /api/model-catalog                   已脱敏的供应商 / 模型 / 参数目录
  POST /api/providers                       新增或更新供应商
  POST /api/models                          新增或更新模型
  POST /api/providers/test                  非计费连接探测
  POST /api/projects/{id}/nodes/{nid}/generate {kind?}   按节点 provider/model 路由生成
"""
import json
import os
import re
import sys
import base64
import binascii
import mimetypes
import secrets
import subprocess
import threading
import time
import urllib.parse
from contextlib import nullcontext
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

import canvas_store as store
import import_feed
import gate_check
import provider_catalog
import provider_client
import tos_storage
import tenant_bootstrap
import tenant_context
import tenant_registry

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
# 后端根路径真正要端出去的前端产物。
# 历史包袱：web/index.html 是 10-23 的旧静态页（标题「戏剧画布工作流」），
# 真正的应用是 web-vimax（React+Vite，dev 8891，build 到 web-vimax/dist）。
# 以前 / 返回的是 web/index.html，用户从 8890 进来看见的是一个功能完全不同的旧页面。
# 现在优先 dist，找不到才退回旧页（不删旧文件，避免打断任何外部引用）。
WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web-vimax", "dist")
# 上传落地目录：data/uploads/<yyyymm>/<随机名>.<ext>
# 为什么必须真落盘：前端原来的 uploadWorkspaceFile 只是 URL.createObjectURL 造了个
# blob: 地址，刷新即失效 → 拖进来的图全变破图（用户实测）。落盘后给出稳定 URL，
# 刷新、换浏览器都能继续显示。
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "uploads")
PORT = 8890
PROJECT_WRITE_LOCK = threading.RLock()
GENERATION_INFLIGHT: set[tuple[str, str]] = set()
GENERATION_INFLIGHT_LOCK = threading.Lock()
RESUMED_VIDEO_TASKS: set[tuple[str, str]] = set()
RESUMED_VIDEO_TASKS_LOCK = threading.Lock()
GENERATION_QUEUE_CONDITION = threading.Condition()
GENERATION_QUEUE_ACTIVE_GLOBAL = 0
GENERATION_QUEUE_ACTIVE_PROVIDERS: dict[str, int] = {}
GENERATION_QUEUE_ACTIVE_TENANTS: dict[str, int] = {}
GENERATION_QUEUE_GLOBAL_LIMIT = max(
    1, int(os.environ.get("CANVAS_GENERATION_GLOBAL_CONCURRENCY", "2"))
)
GENERATION_QUEUE_PROVIDER_LIMIT = max(
    1, int(os.environ.get("CANVAS_GENERATION_PROVIDER_CONCURRENCY", "1"))
)
GENERATION_QUEUE_WAIT_TIMEOUT = max(
    60, int(os.environ.get("CANVAS_GENERATION_QUEUE_TIMEOUT", "21600"))
)
GENERATION_QUEUE_TENANT_LIMIT = max(
    1, int(os.environ.get("CANVAS_GENERATION_TENANT_CONCURRENCY", "1"))
)
CANVAS_SESSION_COOKIE = os.environ.get("CANVAS_SESSION_COOKIE", "canvas_session")
CANVAS_SESSION_TTL = max(
    300, int(os.environ.get("CANVAS_SESSION_TTL_SECONDS", str(30 * 24 * 3600)))
)


def _upload_dir():
    context = tenant_context.current()
    return str(context.upload_dir) if context is not None else UPLOAD_DIR


def _cookie_secure() -> bool:
    raw = os.environ.get("CANVAS_COOKIE_SECURE", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _auth_mode() -> str:
    return os.environ.get("CANVAS_AUTH_MODE", "host").strip().lower()


def _api_host() -> str:
    return os.environ.get("CANVAS_API_HOST", "").strip().lower().rstrip(".")


def _frontend_origins() -> set[str]:
    raw = os.environ.get("CANVAS_ALLOWED_ORIGINS", "")
    if not raw:
        raw = os.environ.get("CANVAS_FRONTEND_ORIGIN", "")
    origins = {
        item.strip().rstrip("/").lower()
        for item in str(raw or "").split(",")
        if item.strip()
    }
    return origins


def _normalized_host(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    raw = raw.split("/", 1)[0]
    if raw.startswith("["):
        return raw.split("]", 1)[0].lstrip("[")
    if ":" in raw:
        raw = raw.rsplit(":", 1)[0]
    return raw.rstrip(".")


def _active_tenants():
    if not tenant_context.multi_tenant_enabled():
        return [None]
    return tenant_registry.list_tenants(include_disabled=False)


def _run_for_active_tenants(func):
    total = 0
    for tenant in _active_tenants():
        try:
            if tenant is None:
                result = func()
            else:
                with tenant_context.use_tenant(tenant["id"], tenant["slug"]):
                    result = func()
            if isinstance(result, int):
                total += result
        except Exception as exc:
            tenant_label = tenant["slug"] if tenant else "legacy"
            print(f"[tenant:{tenant_label}] {func.__name__} 失败：{exc}", file=sys.stderr)
    return total


def _run_in_tenant(tenant_id, slug, func, *args, **kwargs):
    if tenant_id is None:
        with tenant_context.use_context(None):
            return func(*args, **kwargs)
    with tenant_context.use_tenant(tenant_id, slug):
        return func(*args, **kwargs)


class GenerationQueueCancelled(RuntimeError):
    pass


def acquire_generation_slot(task_id, provider_id):
    """Reserve one real provider slot and persist the queue state.

    Generation requests stay synchronous, but different nodes are no longer
    allowed to stampede a provider. The defaults are deliberately conservative:
    one concurrent request per provider and two globally.
    """
    global GENERATION_QUEUE_ACTIVE_GLOBAL
    tenant_key = tenant_context.current_tenant_id()
    provider_key = str(provider_id or "__default__").strip() or "__default__"
    composite_key = f"{tenant_key}\x1f{provider_key}"
    started = time.monotonic()
    with GENERATION_QUEUE_CONDITION:
        while True:
            if task_id:
                current_task = store.get_generation_task(task_id)
                if current_task and current_task.get("status") == "interrupted":
                    raise GenerationQueueCancelled("排队任务已取消")
            global_active = GENERATION_QUEUE_ACTIVE_GLOBAL
            provider_active = GENERATION_QUEUE_ACTIVE_PROVIDERS.get(composite_key, 0)
            tenant_active = GENERATION_QUEUE_ACTIVE_TENANTS.get(tenant_key, 0)
            if (
                global_active < GENERATION_QUEUE_GLOBAL_LIMIT
                and provider_active < GENERATION_QUEUE_PROVIDER_LIMIT
                and tenant_active < GENERATION_QUEUE_TENANT_LIMIT
            ):
                GENERATION_QUEUE_ACTIVE_GLOBAL += 1
                GENERATION_QUEUE_ACTIVE_PROVIDERS[composite_key] = provider_active + 1
                GENERATION_QUEUE_ACTIVE_TENANTS[tenant_key] = tenant_active + 1
                if task_id:
                    store.update_generation_task(
                        task_id,
                        status="submitting",
                        phase="已获得执行槽位",
                        started_at=store._now_iso(),
                    )
                return {
                    "tenant": tenant_key,
                    "provider": provider_key,
                    "composite": composite_key,
                }
            if time.monotonic() - started >= GENERATION_QUEUE_WAIT_TIMEOUT:
                if task_id:
                    store.update_generation_task(
                        task_id,
                        status="failed",
                        phase="排队超时",
                        error="生成任务排队超时，请稍后重新提交",
                        finished_at=store._now_iso(),
                    )
                raise RuntimeError("生成任务排队超时，请稍后重新提交")
            if task_id:
                store.update_generation_task(
                    task_id,
                    status="queued",
                    phase=(
                        f"排队等待（全局 {global_active}/{GENERATION_QUEUE_GLOBAL_LIMIT}，"
                        f"租户 {tenant_active}/{GENERATION_QUEUE_TENANT_LIMIT}，"
                        f"供应商 {provider_active}/{GENERATION_QUEUE_PROVIDER_LIMIT}）"
                    ),
                )
            GENERATION_QUEUE_CONDITION.wait(timeout=5)


def release_generation_slot(slot):
    global GENERATION_QUEUE_ACTIVE_GLOBAL
    if isinstance(slot, dict):
        tenant_key = str(slot.get("tenant") or "legacy")
        provider_key = str(slot.get("provider") or "__default__")
        composite_key = str(slot.get("composite") or f"{tenant_key}\x1f{provider_key}")
    elif isinstance(slot, tuple) and len(slot) == 2:
        tenant_key, provider_key = str(slot[0] or "legacy"), str(slot[1] or "__default__")
        composite_key = f"{tenant_key}\x1f{provider_key}"
    else:
        tenant_key = tenant_context.current_tenant_id()
        provider_key = str(slot or "__default__")
        composite_key = f"{tenant_key}\x1f{provider_key}"
    with GENERATION_QUEUE_CONDITION:
        GENERATION_QUEUE_ACTIVE_GLOBAL = max(0, GENERATION_QUEUE_ACTIVE_GLOBAL - 1)
        active = max(0, GENERATION_QUEUE_ACTIVE_PROVIDERS.get(composite_key, 0) - 1)
        if active:
            GENERATION_QUEUE_ACTIVE_PROVIDERS[composite_key] = active
        else:
            GENERATION_QUEUE_ACTIVE_PROVIDERS.pop(composite_key, None)
        tenant_active = max(0, GENERATION_QUEUE_ACTIVE_TENANTS.get(tenant_key, 0) - 1)
        if tenant_active:
            GENERATION_QUEUE_ACTIVE_TENANTS[tenant_key] = tenant_active
        else:
            GENERATION_QUEUE_ACTIVE_TENANTS.pop(tenant_key, None)
        GENERATION_QUEUE_CONDITION.notify_all()


def persist_generated_media(url, fallback_ext):
    """Keep generated media usable after a provider's temporary URL expires."""
    raw = str(url or "").strip()
    if not raw or raw.startswith(("/uploads/", "data:")):
        return raw
    try:
        return provider_client.save_generated_url(raw, fallback_ext)
    except Exception as exc:
        # Generation already succeeded. Preserve the provider URL instead of
        # presenting a false generation failure; the original error remains visible
        # in the server log for diagnosis.
        print(f"[media-cache] failed to localize {raw[:120]}: {exc}", file=sys.stderr)
        return raw


def archive_generated_image(generated_url, provider, model, task_id, generated_at):
    """Archive the untouched generated file before a temporary Ark URL expires."""
    storage_cfg = provider_catalog.storage_config()
    if not storage_cfg.get("enabled") or not storage_cfg.get("auto_persist_images", True):
        return None
    try:
        return tos_storage.persist_local_url(
            generated_url,
            kind="image",
            fallback_ext=".png",
            source={
                "provider": provider.get("id"),
                "model": model.get("remoteModel") or model.get("id"),
                "taskId": task_id,
                "generatedAt": generated_at,
                "url": generated_url,
            },
        )
    except Exception as exc:
        print(f"[tos] failed to archive generated image {generated_url[:120]}: {exc}", file=sys.stderr)
        return {"status": "failed", "error": str(exc)}


def validate_generated_video(url, expected_duration=None):
    raw = str(url or "").strip()
    if not raw.startswith("/uploads/"):
        return {"status": "unverified", "message": "远程文件暂未校验"}
    path = os.path.join(_upload_dir(), raw[len("/uploads/"):])
    if not os.path.isfile(path):
        return {"status": "invalid", "message": "本地文件不存在"}
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration,size,format_name",
                "-show_entries", "stream=codec_type,codec_name,width,height",
                "-of", "json", path,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or "ffprobe 校验失败").strip()[:300])
        info = json.loads(proc.stdout or "{}")
        streams = info.get("streams") or []
        video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
        has_audio = any(item.get("codec_type") == "audio" for item in streams)
        duration = float((info.get("format") or {}).get("duration") or 0)
        size = int((info.get("format") or {}).get("size") or os.path.getsize(path))
        if not video_stream:
            return {"status": "invalid", "message": "文件没有视频轨", "duration": duration, "size": size}
        expected = float(expected_duration or 0)
        duration_delta = abs(duration - expected) if expected > 0 else 0
        status = "valid"
        message = "文件正常"
        if expected > 0 and duration_delta > max(1.5, expected * 0.12):
            status = "warning"
            message = f"时长偏差 {duration:.1f}s / 请求 {expected:.1f}s"
        return {
            "status": status,
            "message": message,
            "duration": round(duration, 3),
            "size": size,
            "width": video_stream.get("width"),
            "height": video_stream.get("height"),
            "videoCodec": video_stream.get("codec_name"),
            "hasAudio": has_audio,
        }
    except Exception as exc:
        return {"status": "invalid", "message": f"文件校验失败：{exc}"}


def backfill_generated_video_validation():
    updated = 0
    for project_row in store.list_projects():
        project = store.get_project(project_row.get("id"))
        if not project:
            continue
        for node in project["payload"].get("nodes", []):
            if node.get("type") != "video":
                continue
            meta = node.get("metadata") or {}
            if meta.get("status") != "done" or meta.get("videoValidation"):
                continue
            url = str(meta.get("content") or "")
            if not url.startswith("/uploads/"):
                continue
            validation = validate_generated_video(url, (meta.get("params") or {}).get("duration"))
            commit_node_metadata(project["id"], node["id"], {
                "videoValidation": validation,
                "actualDuration": validation.get("duration"),
            })
            updated += 1
    return updated
def commit_node_metadata(project_id, node_id, updates):
    """Merge generated fields into the current node without overwriting peers."""
    with PROJECT_WRITE_LOCK:
        current = store.get_project(project_id)
        if not current:
            return False
        current_node = next(
            (item for item in current["payload"].get("nodes", []) if item.get("id") == node_id),
            None,
        )
        if not current_node:
            return False
        meta = current_node.setdefault("metadata", {})
        pending = dict(updates)
        params_patch = pending.pop("_paramsPatch", None)
        for key, value in pending.items():
            if value is None:
                meta.pop(key, None)
            else:
                meta[key] = value
        if isinstance(params_patch, dict) and params_patch:
            meta["params"] = {**(meta.get("params") or {}), **params_patch}
        current_node["updatedAt"] = store._now_iso()
        store.update_project(project_id, payload=current["payload"])
        return True


GENERATION_OWNED_METADATA_FIELDS = {
    "status",
    "content",
    "lastError",
    "taskId",
    "generationStartedAt",
    "generationFinishedAt",
    "providerTaskStatus",
    "providerTaskPhase",
    "providerTaskProgress",
    "generatedAt",
    "production",
    "videoValidation",
    "actualDuration",
    "storage",
    "storageError",
}

GENERATION_OWNED_PARAM_FIELDS = {
    "mediaVersions",
    "mediaVersionIndex",
    "videoVersions",
    "videoVersionIndex",
    "thumbUrl",
}


def _editable_param_view(meta):
    params = meta.get("params") if isinstance(meta, dict) else {}
    if not isinstance(params, dict):
        return {}
    return {
        key: value for key, value in params.items()
        if key not in GENERATION_OWNED_PARAM_FIELDS
    }


def _editable_node_view(node, include_position=False):
    meta = node.get("metadata") or {}
    view = {
        "title": node.get("title") or "",
        "prompt": meta.get("prompt") or "",
        "refs": meta.get("refs") or [],
        "params": _editable_param_view(meta),
    }
    if include_position:
        view["position"] = node.get("position") or {}
    return view


def _node_save_conflict(incoming, current, include_position=False):
    base_updated_at = str((incoming or {}).get("baseUpdatedAt") or "")
    current_updated_at = str((current or {}).get("updatedAt") or "")
    if not base_updated_at or not current_updated_at or base_updated_at >= current_updated_at:
        return False
    return _editable_node_view(incoming, include_position) != _editable_node_view(
        current,
        include_position,
    )


def merge_node_metadata(meta, incoming, generation_active=False):
    """Deep-merge client metadata without letting a stale tab stop a live task."""
    for key, value in (incoming or {}).items():
        if generation_active and key in GENERATION_OWNED_METADATA_FIELDS:
            continue
        meta[key] = value


MAX_MEDIA_VERSIONS = 20


def _record_media_version(meta, kind, generated_url, task_id, generated_at, storage=None):
    media_params = meta.setdefault("params", {})
    source_versions = media_params.get("mediaVersions")
    if not source_versions and kind == "video":
        source_versions = media_params.get("videoVersions")
    versions = [
        item for item in (source_versions or [])
        if isinstance(item, dict) and str(item.get("url") or "").strip()
    ]
    previous_url = str(meta.get("content") or "").strip()
    if previous_url and not any(str(item.get("url") or "") == previous_url for item in versions):
        previous_production = meta.get("production") or {}
        versions.append({
            "url": previous_url,
            "taskId": str(meta.get("taskId") or previous_production.get("taskId") or ""),
            "generatedAt": str(meta.get("generatedAt") or previous_production.get("generatedAt") or ""),
            "kind": kind,
            "provider": str(meta.get("provider") or previous_production.get("provider") or ""),
            "model": str(meta.get("model") or previous_production.get("model") or ""),
        })
    versions = [item for item in versions if str(item.get("url") or "") != generated_url]
    production = meta.get("production") or {}
    current_version = {
        "url": generated_url,
        "taskId": str(task_id or ""),
        "generatedAt": generated_at,
        "kind": kind,
        "provider": str(production.get("provider") or meta.get("provider") or ""),
        "model": str(production.get("model") or meta.get("model") or ""),
    }
    if isinstance(storage, dict):
        current_version["storage"] = storage
    versions.append(current_version)
    versions = versions[-MAX_MEDIA_VERSIONS:]
    media_params["mediaVersions"] = versions
    media_params["mediaVersionIndex"] = len(versions) - 1
    if kind == "video":
        media_params["videoVersions"] = [dict(item) for item in versions]
        media_params["videoVersionIndex"] = len(versions) - 1
    return versions


def _record_video_version(meta, generated_url, task_id, generated_at):
    return _record_media_version(meta, "video", generated_url, task_id, generated_at)


def _provider_task_phase(status, progress=None):
    raw = str(status or "").strip().lower()
    if raw in {"queued", "pending", "created", "submitted"}:
        return "排队中"
    if raw in {"failed", "error", "cancelled", "canceled"}:
        return "生成失败"
    if raw in {"completed", "succeeded", "success", "done"}:
        return "已完成"
    try:
        numeric = int(progress)
    except (TypeError, ValueError):
        numeric = None
    return f"生成中 {numeric}%" if numeric is not None else "生成中"


def _generation_error_status(exc):
    """Map local validation and upstream provider errors to actionable HTTP codes."""
    text = str(exc or "")
    if isinstance(exc, provider_client.ProviderHTTPError):
        code = int(getattr(exc, "status_code", 500) or 500)
        if code == 429 or 400 <= code < 500:
            return code
        return 502
    if any(
        marker in text
        for marker in (
            "参考素材缺失",
            "参考图片",
            "参考音频",
            "参考视频",
            "当前模型最多接受",
            "提示词超过",
            "不支持",
            "缺少",
            "不合法的模式",
            "只支持",
        )
    ):
        return 400
    if "超时" in text or "timed out" in text.lower():
        return 504
    if "连接失败" in text or "connection" in text.lower():
        return 503
    return 500


LOCAL_VALIDATION_MARKERS = (
    "参考素材未就绪",
    "参考素材缺失",
    "引用节点不存在",
    "提示词为空",
    "当前模型最多",
    "音色节点尚未生成",
    "源节点尚未生成",
    "原始产物转存到 TOS",
    "信任窗口只有",
)


def is_local_validation_error(message):
    text = str(message or "")
    return any(marker in text for marker in LOCAL_VALIDATION_MARKERS)


def _find_generation_task(project_id, node_id, provider_task_id=""):
    for item in store.list_generation_tasks(project_id, 120):
        if item.get("node_id") != node_id:
            continue
        if item.get("status") not in {"queued", "submitting", "running"}:
            continue
        if provider_task_id and item.get("provider_task_id") not in {"", provider_task_id}:
            continue
        return item
    return None


def _finalize_resumed_video(project_id, node_id, result, provider, model):
    current = store.get_project(project_id)
    if not current:
        return False
    node = next((item for item in current["payload"].get("nodes", []) if item.get("id") == node_id), None)
    if not node:
        return False
    meta = node.setdefault("metadata", {})
    generated_at = store._now_iso()
    generated_url = persist_generated_media(result["url"], ".mp4")
    task_id = str(result.get("taskId") or meta.get("taskId") or "")
    _record_video_version(meta, generated_url, task_id, generated_at)
    validation = validate_generated_video(generated_url, (meta.get("params") or {}).get("duration"))
    versions = (meta.get("params") or {}).get("videoVersions") or []
    if versions:
        versions[-1]["actualDuration"] = validation.get("duration")
        versions[-1]["validationStatus"] = validation.get("status")
    media_versions = (meta.get("params") or {}).get("mediaVersions") or []
    if media_versions:
        media_versions[-1]["actualDuration"] = validation.get("duration")
        media_versions[-1]["validationStatus"] = validation.get("status")
    meta.update({
        "status": "done",
        "content": generated_url,
        "lastError": "",
        "taskId": task_id,
        "generatedAt": generated_at,
        "providerTaskStatus": "completed",
        "providerTaskPhase": "已完成",
        "generationFinishedAt": generated_at,
        "videoValidation": validation,
        "actualDuration": validation.get("duration"),
        "provider": provider.get("id"),
        "model": model.get("id"),
        "remoteModel": model.get("remoteModel"),
        "production": {
            "provider": provider.get("id"),
            "providerName": provider.get("name"),
            "model": model.get("id"),
            "remoteModel": model.get("remoteModel"),
            "adapter": model.get("adapter"),
            "generatedAt": generated_at,
            "taskId": task_id,
        },
    })
    commit_node_metadata(project_id, node_id, {
        "status": "done",
        "content": generated_url,
        "lastError": "",
        "taskId": task_id,
        "generatedAt": generated_at,
        "providerTaskStatus": "completed",
        "providerTaskPhase": "已完成",
        "generationFinishedAt": generated_at,
        "videoValidation": validation,
        "actualDuration": validation.get("duration"),
        "provider": provider.get("id"),
        "model": model.get("id"),
        "remoteModel": model.get("remoteModel"),
        "production": meta["production"],
        "_paramsPatch": {
            "mediaVersions": meta["params"].get("mediaVersions", []),
            "mediaVersionIndex": meta["params"].get("mediaVersionIndex", 0),
            "videoVersions": meta["params"].get("videoVersions", []),
            "videoVersionIndex": meta["params"].get("videoVersionIndex", 0),
        },
    })
    generation_task = _find_generation_task(project_id, node_id, task_id)
    if generation_task:
        store.update_generation_task(
            generation_task["id"], status="succeeded", phase="已完成", progress=100,
            error="", finished_at=store._now_iso(),
        )
    return True


def _resume_video_task(project_id, node_id, task_id, provider, model):
    key = (project_id, node_id)
    generation_task = _find_generation_task(project_id, node_id, task_id)
    try:
        deadline = time.time() + 900
        while time.time() < deadline:
            try:
                state = provider_client.check_video_task(provider, model, task_id)
            except Exception as exc:
                commit_node_metadata(project_id, node_id, {
                    "providerTaskStatus": "retrying",
                    "providerTaskPhase": "查询重试中",
                    "lastError": str(exc),
                })
                if generation_task:
                    store.update_generation_task(
                        generation_task["id"], status="running", phase="查询重试中",
                        error=str(exc),
                    )
                time.sleep(8)
                continue
            progress = state.get("progress")
            commit_node_metadata(project_id, node_id, {
                "providerTaskStatus": state.get("rawStatus") or state.get("status"),
                "providerTaskPhase": _provider_task_phase(state.get("rawStatus") or state.get("status"), progress),
                "providerTaskProgress": progress,
                "lastError": "" if state.get("status") != "failed" else str(state.get("error") or "生成失败"),
            })
            if generation_task:
                store.update_generation_task(
                    generation_task["id"], status="running",
                    phase=_provider_task_phase(state.get("rawStatus") or state.get("status"), progress),
                    progress=int(progress) if isinstance(progress, (int, float)) else None,
                )
            if state.get("status") == "completed":
                _finalize_resumed_video(project_id, node_id, state, provider, model)
                return
            if state.get("status") == "failed":
                commit_node_metadata(project_id, node_id, {
                    "status": "failed",
                    "lastError": str(state.get("error") or "视频生成失败"),
                    "providerTaskStatus": "failed",
                    "providerTaskPhase": "生成失败",
                    "generationFinishedAt": store._now_iso(),
                })
                if generation_task:
                    store.update_generation_task(
                        generation_task["id"], status="failed", phase="生成失败",
                        error=str(state.get("error") or "视频生成失败"),
                        finished_at=store._now_iso(),
                    )
                return
            time.sleep(5)
        commit_node_metadata(project_id, node_id, {
            "status": "failed",
            "lastError": "恢复供应商视频任务超时（900s）",
            "providerTaskStatus": "timeout",
            "providerTaskPhase": "生成超时",
            "generationFinishedAt": store._now_iso(),
        })
        if generation_task:
            store.update_generation_task(
                generation_task["id"], status="failed", phase="生成超时",
                error="恢复供应商视频任务超时（900s）",
                finished_at=store._now_iso(),
            )
    finally:
        with RESUMED_VIDEO_TASKS_LOCK:
            RESUMED_VIDEO_TASKS.discard(key)


def reconcile_generation_task_records():
    """Align persisted task rows with the authoritative canvas node state."""
    projects = {}
    nodes = {}
    for row in store.list_projects():
        project = store.get_project(row.get("id"))
        if not project:
            continue
        projects[project["id"]] = project
        for node in project["payload"].get("nodes", []):
            nodes[(project["id"], node.get("id"))] = node

    reconciled = 0
    for task in store.list_generation_tasks(None, 300):
        if task.get("status") not in {"queued", "submitting", "running"}:
            continue
        node = nodes.get((task.get("project_id"), task.get("node_id")))
        meta = (node or {}).get("metadata") or {}
        node_status = str(meta.get("status") or "")
        fields = None
        if not node:
            fields = {
                "status": "interrupted",
                "phase": "节点已不存在",
                "error": "对应画布节点已删除，任务已结束",
            }
        elif node_status == "done":
            fields = {
                "status": "succeeded",
                "phase": "已完成",
                "progress": 100,
                "error": "",
            }
        elif node_status == "failed":
            fields = {
                "status": "failed",
                "phase": str(meta.get("providerTaskPhase") or "生成失败"),
                "error": str(meta.get("lastError") or "生成失败"),
            }
        if not fields:
            continue
        if node_status in {"done", "failed"}:
            updated_raw = str(task.get("updated_at") or "")
            try:
                updated_at = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - updated_at).total_seconds()
            except (TypeError, ValueError):
                age = 60
            if age < 20:
                continue
        fields["finished_at"] = store._now_iso()
        store.update_generation_task(task["id"], **fields)
        reconciled += 1
    return reconciled


def start_generation_task_reconciler():
    def run():
        while True:
            try:
                _run_for_active_tenants(reconcile_generation_task_records)
            except Exception as exc:
                print(f"[generation-tasks] 定时对账失败：{exc}", file=sys.stderr)
            time.sleep(30)

    threading.Thread(target=run, name="generation-task-reconciler", daemon=True).start()


def start_pending_video_resumers():
    for tenant in _active_tenants():
        def resume_tenant(tenant_record=tenant):
            for project_row in store.list_projects():
                project = store.get_project(project_row.get("id"))
                if not project:
                    continue
                for node in project["payload"].get("nodes", []):
                    meta = node.get("metadata") or {}
                    task_id = str(meta.get("taskId") or "").strip()
                    provider_status = str(meta.get("providerTaskStatus") or "").strip().lower()
                    if (node.get("type") != "video"
                            or meta.get("status") != "generating"
                            or not task_id
                            or provider_status not in {"queued", "pending", "created", "submitted", "running", "processing", "retrying"}):
                        continue
                    try:
                        params = meta.get("params") or {}
                        provider, model, _ = provider_catalog.resolve_model(
                            params.get("provider") or meta.get("provider"),
                            params.get("model") or meta.get("model"),
                            "video",
                        )
                    except Exception as exc:
                        commit_node_metadata(project["id"], node["id"], {
                            "status": "failed",
                            "lastError": f"无法恢复视频任务：{exc}",
                            "providerTaskPhase": "恢复失败",
                        })
                        continue
                    tenant_id = tenant_record["id"] if tenant_record else None
                    tenant_slug = tenant_record["slug"] if tenant_record else "local"
                    key = (tenant_id or "legacy", project["id"], node["id"])
                    with RESUMED_VIDEO_TASKS_LOCK:
                        if key in RESUMED_VIDEO_TASKS:
                            continue
                        RESUMED_VIDEO_TASKS.add(key)
                    threading.Thread(
                        target=_run_in_tenant,
                        args=(
                            tenant_id,
                            tenant_slug,
                            _resume_video_task,
                            project["id"],
                            node["id"],
                            task_id,
                            provider,
                            model,
                        ),
                        daemon=True,
                    ).start()

        if tenant is None:
            resume_tenant()
        else:
            with tenant_context.use_tenant(tenant["id"], tenant["slug"]):
                resume_tenant(tenant)


def reconcile_interrupted_generation_nodes():
    reconciled = 0
    resumable = {"queued", "pending", "created", "submitted", "running", "processing", "retrying"}
    for project_row in store.list_projects():
        project = store.get_project(project_row.get("id"))
        if not project:
            continue
        for node in project["payload"].get("nodes", []):
            meta = node.get("metadata") or {}
            if meta.get("status") != "generating":
                continue
            provider_task_id = str(meta.get("taskId") or "").strip()
            provider_status = str(meta.get("providerTaskStatus") or "").strip().lower()
            if provider_task_id and provider_status in resumable:
                continue
            task = _find_generation_task(project["id"], node["id"], provider_task_id)
            if not task:
                task = store.create_generation_task(
                    project["id"], node["id"], title=node.get("title", ""), kind=node.get("type", ""),
                    provider=(meta.get("params") or {}).get("provider") or meta.get("provider") or "",
                    model=(meta.get("params") or {}).get("model") or meta.get("model") or "",
                )
            if task:
                store.update_generation_task(
                    task["id"], status="interrupted", phase="服务中断",
                    error="后端重启，旧任务未取得可恢复的供应商任务 ID",
                    provider_task_id=provider_task_id,
                    finished_at=store._now_iso(),
                )
            commit_node_metadata(project["id"], node["id"], {
                "status": "failed",
                "providerTaskStatus": "interrupted",
                "providerTaskPhase": "服务中断",
                "lastError": "后端重启，旧任务未取得可恢复的供应商任务 ID，请确认后重新生成",
                "generationFinishedAt": store._now_iso(),
            })
            reconciled += 1
    return reconciled


def backfill_multi_episode_payloads():
    recovered = 0
    for project_row in store.list_projects():
        project = store.get_project(project_row.get("id"))
        if not project:
            continue
        current = project["payload"]
        current_groups = import_feed._ensure_episode_imports(current)
        if len(current_groups) > 1:
            continue
        candidates = {}
        for snapshot in store.list_snapshot_payloads(project["id"], 30):
            payload = snapshot.get("payload")
            if not isinstance(payload, dict) or not payload.get("nodes"):
                continue
            groups = import_feed._ensure_episode_imports(payload)
            episode_id = str(groups[0].get("id") or "EP001").upper()
            candidates[episode_id] = payload
        current_episode = str(current_groups[0].get("id") or "EP001").upper()
        candidates[current_episode] = current
        if len(candidates) <= 1:
            continue
        merged = None
        for episode_id in sorted(candidates):
            payload = candidates[episode_id]
            merged = payload if merged is None else import_feed.merge_payload_by_episode(merged, payload)
        if merged and len(merged.get("episodeImports") or []) > 1:
            store.update_project(project["id"], title=merged.get("title") or project["title"], payload=merged)
            recovered += 1
    return recovered


def backfill_episode_shared_asset_links():
    updated = 0
    for project_row in store.list_projects():
        project = store.get_project(project_row.get("id"))
        if not project:
            continue
        payload = project["payload"]
        if len(payload.get("episodeImports") or []) <= 1:
            continue
        added = import_feed.bind_shared_assets_for_all_episodes(payload)
        if added:
            store.update_project(project["id"], payload=payload)
            updated += 1
    return updated


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stdout.write("[%s] %s\n" % (self.server.server_address, fmt % args))

    # ---------- helpers ----------
    def _cors_headers(self):
        origin = str(self.headers.get("Origin", "")).strip().rstrip("/")
        allowed = _frontend_origins()
        if not origin or origin.lower() not in allowed:
            return {}
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Max-Age": "600",
            "Vary": "Origin",
        }

    def do_OPTIONS(self):
        return self._send(204, {}, raw=b"")

    def _send(self, code, obj=None, raw=None, content_type="application/json", headers=None):
        if raw is not None:
            body = raw.encode("utf-8") if isinstance(raw, str) else raw
        else:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        response_headers = {**self._cors_headers(), **(headers or {})}
        for key, value in response_headers.items():
            self.send_header(str(key), str(value))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = raw.decode("gbk")
            except UnicodeDecodeError:
                return {"_error": "body 编码无法解析"}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_raw": text}

    # ---------- 文件上传 ----------
    # 允许的扩展名白名单：只收图片/音频/视频，避免上传目录被塞入可执行文件
    _UPLOAD_EXT = {
        ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif",
        ".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac",
        ".mp4", ".webm", ".mov",
    }
    _MAX_UPLOAD = 24 * 1024 * 1024   # 24MB

    def _handle_upload(self, body):
        data_url = body.get("dataUrl") or body.get("data_url") or ""
        raw_name = (body.get("name") or "").strip()
        if not isinstance(data_url, str) or not data_url.startswith("data:"):
            return self._send(400, {"error": "dataUrl 缺失或格式不对"})
        if "," not in data_url:
            return self._send(400, {"error": "dataUrl 格式不对"})
        header, b64 = data_url.split(",", 1)
        # header 形如 data:image/png;base64
        m = re.match(r"^data:([^;]+);base64$", header)
        if not m:
            return self._send(400, {"error": "只支持 base64 dataUrl"})
        try:
            blob = base64.b64decode(b64, validate=False)
        except (binascii.Error, ValueError):
            return self._send(400, {"error": "base64 解码失败"})
        if len(blob) == 0:
            return self._send(400, {"error": "文件为空"})
        if len(blob) > self._MAX_UPLOAD:
            return self._send(413, {"error": "文件超过 24MB"})
        # 扩展名：优先用原始文件名，其次按 MIME 猜
        ext = os.path.splitext(raw_name)[1].lower()
        if ext not in self._UPLOAD_EXT:
            guess = mimetypes.guess_extension(m.group(1).split(";")[0].strip()) or ""
            ext = guess.lower() if guess.lower() in self._UPLOAD_EXT else ".bin"
        if ext == ".bin":
            return self._send(400, {"error": "不支持的文件类型"})
        upload_dir = _upload_dir()
        os.makedirs(upload_dir, exist_ok=True)
        fname = secrets.token_hex(6) + ext
        fpath = os.path.join(upload_dir, fname)
        with open(fpath, "wb") as f:
            f.write(blob)
        return self._send(200, {
            "ok": True,
            "url": "/uploads/" + fname,
            "name": raw_name or fname,
            "size": len(blob),
            "mime": m.group(1).split(";")[0].strip(),
        })

    # ---------- 前端静态产物 ----------
    # 把 web-vimax/dist 直接端在 8890 根路径上，8890 单开也能用完整应用。
    _STATIC_EXT = {
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".mjs": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".ico": "image/x-icon",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
        ".ttf": "font/ttf",
        ".map": "application/json; charset=utf-8",
    }

    def _serve_webapp(self, path):
        """从 dist 读文件；返回 True 表示已响应。

        /api 与 /uploads 已在前面分流，这里只管静态资源与 SPA 回退。
        安全：resolve 后必须仍在 WEBAPP_DIR 内，挡掉 ../ 穿越。
        """
        if not os.path.isdir(WEBAPP_DIR):
            return False
        rel = unquote(path).lstrip("/") or "index.html"
        target = os.path.normpath(os.path.join(WEBAPP_DIR, rel))
        try:
            if os.path.commonpath([target, WEBAPP_DIR]) != WEBAPP_DIR:
                return False
        except ValueError:      # 跨盘符（Windows 上 C:/ 与 D:/ 无法求公共前缀）
            return False

        if not os.path.isfile(target):
            # 带扩展名的资源缺失就该 404（前端 index.html 里有 chunk-404 自动强刷逻辑）；
            # 无扩展名视为前端路由（如 /l0CcworH4Dv6vtGDTPoO9S），回退 index.html。
            if os.path.splitext(rel)[1]:
                return False
            target = os.path.join(WEBAPP_DIR, "index.html")
            if not os.path.isfile(target):
                return False

        ext = os.path.splitext(target)[1].lower()
        ctype = self._STATIC_EXT.get(ext) or mimetypes.guess_type(target)[0] or "application/octet-stream"
        with open(target, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # assets 下都是带指纹的文件，可长缓存；index.html 必须每次回源，
        # 否则重建后旧 HTML 会继续引用已被删除的旧 chunk → 白屏。
        if rel.startswith("assets/"):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        else:
            self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)
        return True

    # ---------- tenant request boundary ----------
    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin", "").strip()
        if not origin:
            return True
        if origin.rstrip("/").lower() in _frontend_origins():
            return True
        origin_host = urlparse(origin).netloc.lower()
        request_host = self.headers.get("Host", "").strip().lower()
        return bool(origin_host and origin_host == request_host)

    def _request_tenant_scope(self):
        if self.command not in {"GET", "HEAD"} and not self._origin_allowed():
            self._send(403, {"error": "跨站请求已拒绝", "code": "ORIGIN_REJECTED"})
            return None
        path = urlparse(self.path).path
        if _auth_mode() == "accounts":
            host = _normalized_host(self.headers.get("Host", ""))
            api_host = _api_host()
            if not api_host:
                self._send(500, {"error": "未配置 CANVAS_API_HOST"})
                return None
            is_api_path = path.startswith("/api/") or path.startswith("/uploads/")
            if host != api_host and not is_api_path and self.command in {"GET", "HEAD"}:
                # The frontend host can still serve its static build from this
                # process during local diagnostics. API calls require a session.
                return nullcontext()
            if host != api_host and not is_api_path:
                self._send(404, {"error": "资源不存在"})
                return None
            if path in {"/api/auth/login", "/api/auth/session", "/api/auth/logout"}:
                return nullcontext()
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
            except Exception:
                cookie = SimpleCookie()
            morsel = cookie.get(CANVAS_SESSION_COOKIE)
            raw_session = morsel.value if morsel is not None else ""
            session = tenant_registry.get_session(raw_session) if raw_session else None
            if not session:
                self._send(401, {
                    "error": "请先登录。",
                    "code": "ACCOUNT_AUTH_REQUIRED",
                })
                return None
            return tenant_context.use_tenant(session["tenant_id"], session["tenant_slug"])
        if not tenant_context.multi_tenant_enabled():
            return nullcontext()
        kind, slug = tenant_context.classify_host(self.headers.get("Host", ""))
        if kind != "tenant":
            self._send(404, {"error": "资源不存在"})
            return None
        if path.startswith("/invite/"):
            # The invite is the only unauthenticated route. Host still selects
            # the tenant, and the one-time token is validated inside the route.
            tenant = tenant_registry.get_tenant_by_slug(slug)
            if not tenant:
                self._send(410, {"error": "邀请链接无效。", "code": "INVITE_INVALID"})
                return None
            return tenant_context.use_tenant(tenant["id"], tenant["slug"])

        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            cookie = SimpleCookie()
        morsel = cookie.get(CANVAS_SESSION_COOKIE)
        raw_session = morsel.value if morsel is not None else ""
        session = tenant_registry.get_session(raw_session) if raw_session else None
        if not session or session.get("tenant_slug") != slug:
            self._send(401, {
                "error": "当前子域名需要有效邀请会话，请使用管理员生成的邀请链接进入。",
                "code": "TENANT_AUTH_REQUIRED",
            })
            return None
        return tenant_context.use_tenant(session["tenant_id"], session["tenant_slug"])

    def _session_cookie(self, token: str, *, max_age: int | None = None) -> str:
        age = CANVAS_SESSION_TTL if max_age is None else max_age
        value = (
            f"{CANVAS_SESSION_COOKIE}={token}; Path=/; Max-Age={age}; "
            "HttpOnly; SameSite=Lax"
        )
        if _cookie_secure():
            value += "; Secure"
        return value

    def _handle_auth_login(self, body):
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        if not username or not password:
            return self._send(400, {"error": "请输入账号和密码", "code": "AUTH_FIELDS_REQUIRED"})
        try:
            account = tenant_registry.authenticate_account(username, password)
        except ValueError:
            account = None
        if not account:
            return self._send(401, {"error": "账号或密码错误", "code": "AUTH_INVALID"})
        session = tenant_registry.create_session(
            account["tenant_id"],
            account_id=account["id"],
            ttl_seconds=CANVAS_SESSION_TTL,
            user_agent=self.headers.get("User-Agent", ""),
            ip=self.client_address[0] if self.client_address else "",
        )
        return self._send(
            200,
            {
                "authenticated": True,
                "username": account["username"],
                "tenantId": account["tenant_id"],
                "slug": tenant_registry.get_tenant_by_id(account["tenant_id"])["slug"],
            },
            headers={"Set-Cookie": self._session_cookie(session["token"])},
        )

    def _handle_auth_session(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            cookie = SimpleCookie()
        morsel = cookie.get(CANVAS_SESSION_COOKIE)
        session = tenant_registry.get_session(morsel.value) if morsel is not None and morsel.value else None
        if not session:
            return self._send(200, {"authenticated": False})
        return self._send(200, {
            "authenticated": True,
            "username": session.get("account_username") or "",
            "tenantId": session["tenant_id"],
            "slug": session["tenant_slug"],
        })

    def _handle_auth_logout(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            cookie = SimpleCookie()
        morsel = cookie.get(CANVAS_SESSION_COOKIE)
        if morsel is not None and morsel.value:
            tenant_registry.revoke_session(morsel.value)
        return self._send(
            200,
            {"ok": True},
            headers={"Set-Cookie": self._session_cookie("", max_age=0)},
        )

    def _handle_invite(self, path):
        if not tenant_context.multi_tenant_enabled():
            return self._send(404, {"error": "资源不存在"})
        kind, slug = tenant_context.classify_host(self.headers.get("Host", ""))
        if kind != "tenant":
            return self._send(404, {"error": "资源不存在"})
        raw_token = unquote(path[len("/invite/"):]).strip()
        tenant = tenant_registry.consume_invite(slug, raw_token)
        if not tenant:
            return self._send(410, {
                "error": "邀请链接无效、已过期或已经使用。",
                "code": "INVITE_INVALID",
            })
        session = tenant_registry.create_session(
            tenant["id"],
            ttl_seconds=CANVAS_SESSION_TTL,
            user_agent=self.headers.get("User-Agent", ""),
            ip=self.client_address[0] if self.client_address else "",
        )
        cookie = (
            f"{CANVAS_SESSION_COOKIE}={session['token']}; "
            f"Path=/; Max-Age={CANVAS_SESSION_TTL}; HttpOnly; SameSite=Lax"
        )
        if _cookie_secure():
            cookie += "; Secure"
        return self._send(
            302,
            {"ok": True},
            headers={"Location": "/", "Set-Cookie": cookie},
        )

    # ---------- routes ----------
    def do_GET(self):
        scope = self._request_tenant_scope()
        if scope is None:
            return
        with scope:
            return self._do_get()

    def _do_get(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/auth/session":
            return self._handle_auth_session()
        if path.startswith("/invite/"):
            return self._handle_invite(path)
        if path == "/" or path == "/index.html":
            if self._serve_webapp("/index.html"):
                return
            html_path = os.path.join(WEB_DIR, "index.html")
            if not os.path.exists(html_path):
                return self._send(404, {"error": "web-vimax/dist 与 web/index.html 都不存在，先跑 npx vite build"})
            with open(html_path, "rb") as f:
                return self._send(200, raw=f.read(), content_type="text/html; charset=utf-8")
        if path == "/api/projects":
            projects = store.list_projects()
            tasks_by_project = {}
            for task in store.list_generation_tasks(None, 500):
                tasks_by_project.setdefault(task.get("project_id"), []).append(task)
            for project in projects:
                project_data = store.get_project(project["id"])
                nodes = project_data["payload"].get("nodes", []) if project_data else []
                task_rows = tasks_by_project.get(project["id"], [])
                project["running_count"] = sum(
                    1 for node in nodes
                    if (node.get("metadata") or {}).get("status") == "generating"
                )
                failed_nodes = [
                    node for node in nodes
                    if (node.get("metadata") or {}).get("status") == "failed"
                ]
                project["local_blocked_count"] = sum(
                    1 for node in failed_nodes
                    if is_local_validation_error((node.get("metadata") or {}).get("lastError"))
                )
                project["failed_count"] = sum(
                    1 for node in nodes
                    if (node.get("metadata") or {}).get("status") == "failed"
                    and not is_local_validation_error((node.get("metadata") or {}).get("lastError"))
                )
                latest = task_rows[0] if task_rows else None
                project["latest_task_status"] = latest.get("status") if latest else ""
                project["latest_task_phase"] = latest.get("phase") if latest else ""
                project["latest_task_error_kind"] = (
                    "local_validation"
                    if latest and latest.get("status") == "failed" and is_local_validation_error(latest.get("error"))
                    else ("provider" if latest and latest.get("status") == "failed" else "")
                )
            return self._send(200, {"projects": projects})
        if path == "/api/storage":
            return self._send(200, {"storage": provider_catalog.public_storage_config()})
        if path == "/api/generation-tasks":
            query = urllib.parse.parse_qs(parsed.query)
            project_id = str((query.get("projectId") or [""])[0]).strip() or None
            limit = int((query.get("limit") or ["80"])[0])
            tasks = store.list_generation_tasks(project_id, limit)
            for task in tasks:
                if task.get("status") == "failed":
                    task["error_kind"] = (
                        "local_validation"
                        if is_local_validation_error(task.get("error"))
                        else "provider"
                    )
            queued = sorted(
                (
                    task for task in tasks
                    if task.get("status") == "queued"
                ),
                key=lambda task: str(task.get("created_at") or ""),
            )
            queue_positions = {
                task.get("id"): index + 1
                for index, task in enumerate(queued)
            }
            for task in tasks:
                task["queue_position"] = queue_positions.get(task.get("id"))
            return self._send(200, {"tasks": tasks})
        # 上传文件回读：/uploads/<name>
        # 只允许读取 UPLOAD_DIR 下的单层文件名，禁止 ../ 穿越
        if path.startswith("/uploads/"):
            name = unquote(path[len("/uploads/"):])
            if not name or "/" in name or "\\" in name or name.startswith("."):
                return self._send(400, {"error": "非法文件名"})
            fpath = os.path.join(_upload_dir(), name)
            if not os.path.isfile(fpath):
                return self._send(404, {"error": "文件不存在"})
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            with open(fpath, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # 上传文件内容不变，可以长缓存（文件名带 8 位随机串，不会撞名）
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
            self.end_headers()
            self.wfile.write(body)
            return
        # 上传目录诊断（便于排查文件是否真落盘）
        if path == "/api/uploads":
            upload_dir = _upload_dir()
            if not os.path.isdir(upload_dir):
                return self._send(200, {"dir": upload_dir, "exists": False, "files": []})
            files = []
            for n in sorted(os.listdir(upload_dir))[-50:]:
                fp = os.path.join(upload_dir, n)
                if os.path.isfile(fp):
                    files.append({"name": n, "size": os.path.getsize(fp), "url": "/uploads/" + n})
            return self._send(200, {"dir": upload_dir, "exists": True, "files": files})
        if path == "/api/config":
            return self._send(200, provider_catalog.config_status())
        if path == "/api/tenant/session":
            context = tenant_context.current()
            return self._send(200, {
                "authenticated": True,
                "tenantId": context.tenant_id if context else "legacy",
                "slug": context.slug if context else "local",
                "isolated": bool(context),
            })
        if path in ("/api/model-catalog", "/api/providers"):
            return self._send(200, provider_catalog.public_catalog())
        m = re.match(r"^/api/projects/([^/]+)/catalog$", path)
        if m:
            proj = store.get_project(unquote(m.group(1)))
            if not proj:
                return self._send(404, {"error": "项目不存在"})
            catalog = {"images": [], "audios": [], "videos": []}
            for n in proj["payload"].get("nodes", []):
                item = {
                    "id": n["id"], "title": n.get("title", ""), "type": n.get("type"),
                    "label": n.get("title", ""), "url": (n.get("metadata", {}) or {}).get("content")
                    if (n.get("metadata", {}) or {}).get("status") == "done" else "",
                    "code": "",
                }
                t = n.get("title", "") or ""
                m2 = __import__("re").search(r"（(图\d+)）", t)
                if m2:
                    item["code"] = m2.group(1)
                if n.get("type") == "image":
                    catalog["images"].append(item)
                elif n.get("type") == "audio":
                    catalog["audios"].append(item)
                elif n.get("type") == "video":
                    catalog["videos"].append(item)
            return self._send(200, catalog)
        m = re.match(r"^/api/projects/([^/]+)$", path)
        if m:
            pid = unquote(m.group(1))
            proj = store.get_project(pid)
            if not proj:
                return self._send(404, {"error": "项目不存在"})
            return self._send(200, proj)
        # 未知的 /api、/uploads 必须回 404 JSON —— 不能被下面的 SPA 回退吃掉返回 index.html
        # （老问题：上传文件 404 时前端拿到 200+text/html，图片永远显示不出来）
        if not (path.startswith("/api/") or path.startswith("/uploads/")):
            # 静态资源（/assets/xxx.js、/favicon.svg…）；无扩展名视为前端路由，回退 index.html
            if self._serve_webapp(path):
                return
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        scope = self._request_tenant_scope()
        if scope is None:
            return
        with scope:
            return self._do_post()

    def _do_post(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_json()
        if path == "/api/auth/login":
            return self._handle_auth_login(body)
        if path == "/api/auth/logout":
            return self._handle_auth_logout()
        if path == "/api/tenant/logout":
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
            except Exception:
                cookie = SimpleCookie()
            morsel = cookie.get(CANVAS_SESSION_COOKIE)
            if morsel is not None and morsel.value:
                tenant_registry.revoke_session(morsel.value)
            clear_cookie = (
                f"{CANVAS_SESSION_COOKIE}=; Path=/; Max-Age=0; "
                "HttpOnly; SameSite=Lax"
            )
            if _cookie_secure():
                clear_cookie += "; Secure"
            return self._send(200, {"ok": True}, headers={"Set-Cookie": clear_cookie})
        if path == "/api/storage":
            try:
                storage = provider_catalog.save_storage_config(body)
                return self._send(200, {"ok": True, "storage": storage})
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
        if path == "/api/storage/test":
            try:
                return self._send(200, {"ok": True, **tos_storage.test_connection()})
            except tos_storage.TosAccountOverdueError as exc:
                return self._send(402, {"error": str(exc), "code": "TOS_ACCOUNT_OVERDUE"})
            except Exception as exc:
                return self._send(400, {"error": str(exc)})
        # 文件上传：{name, dataUrl} → 落盘返回稳定 URL
        # 走 JSON+base64 而不是 multipart：标准库解析 multipart 要手写边界处理，
        # 这里图片体积可控（前端限制 12MB），base64 简单可靠。
        if path == "/api/upload":
            return self._handle_upload(body)
        if path == "/api/projects":
            title = body.get("title") or "未命名画布"
            with PROJECT_WRITE_LOCK:
                pid = store.create_project(title)
            return self._send(200, {"id": pid, "title": title})
        m = re.match(r"^/api/projects/([^/]+)/duplicate$", path)
        if m:
            source_id = unquote(m.group(1))
            source = store.get_project(source_id)
            if not source:
                return self._send(404, {"error": "项目不存在"})
            with PROJECT_WRITE_LOCK:
                duplicated = json.loads(json.dumps(source["payload"], ensure_ascii=False))
                title = str(body.get("title") or f"{source['title']} · 副本").strip()
                new_id = store.create_project(title, duplicated)
                created = store.get_project(new_id)
                payload = created["payload"] if created else duplicated
                now = store._now_iso()
                payload["id"] = new_id
                payload["projectId"] = new_id
                payload["workspaceProjectId"] = new_id
                payload["title"] = title
                payload["createdAt"] = now
                payload["updatedAt"] = now
                payload["revision"] = 1
                store.update_project(new_id, title=title, payload=payload)
            return self._send(200, {"id": new_id, "title": title})
        if path == "/api/config":
            # Backward-compatible flat configuration endpoint.
            cfg = provider_catalog.load_config()
            providers = {p.get("id"): p for p in cfg.get("providers", []) if isinstance(p, dict)}
            if isinstance(body.get("ark_api_key"), str) and body["ark_api_key"].strip():
                providers.setdefault("ark", {"id": "ark"})["api_key"] = body["ark_api_key"].strip()
            if isinstance(body.get("team_router_api_key"), str) and body["team_router_api_key"].strip():
                providers.setdefault("teamorouter", {"id": "teamorouter"})["api_key"] = body["team_router_api_key"].strip()
            if isinstance(body.get("dashscope_api_key"), str) and body["dashscope_api_key"].strip():
                providers.setdefault("aliyun_bailian", {"id": "aliyun_bailian"})["api_key"] = body["dashscope_api_key"].strip()
            catalog_changed = False
            if isinstance(body.get("providers"), list):
                for provider in body["providers"]:
                    if isinstance(provider, dict):
                        try:
                            provider_catalog.upsert_provider(provider)
                            catalog_changed = True
                        except ValueError as exc:
                            return self._send(400, {"error": str(exc)})
            if isinstance(body.get("models"), list):
                for model in body["models"]:
                    if isinstance(model, dict):
                        try:
                            provider_catalog.upsert_model(model)
                            catalog_changed = True
                        except ValueError as exc:
                            return self._send(400, {"error": str(exc)})
            if not catalog_changed:
                provider_catalog.save_config(cfg)
            return self._send(200, provider_catalog.config_status())
        if path == "/api/providers":
            try:
                return self._send(200, provider_catalog.upsert_provider(body))
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
        if path == "/api/providers/test":
            provider_id = str(body.get("providerId") or body.get("id") or "")
            return self._send(200, provider_catalog.test_provider(provider_id))
        if path == "/api/models":
            try:
                return self._send(200, provider_catalog.upsert_model(body))
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
        if path == "/api/config/test":
            section = str(body.get("section") or body.get("providerId") or "")
            return self._send(200, provider_catalog.test_provider(section))
        m = re.match(r"^/api/projects/([^/]+)/import$", path)
        if m:
            pid = unquote(m.group(1))
            with PROJECT_WRITE_LOCK:
                proj = store.get_project(pid)
                if not proj:
                    return self._send(404, {"error": "项目不存在"})
                feed_path = body.get("feed_path") or ""
                feed_json = body.get("feed_json")
                if not feed_json and feed_path:
                    if not os.path.exists(feed_path):
                        return self._send(400, {"error": f"载荷文件不存在：{feed_path}"})
                    with open(feed_path, encoding="utf-8-sig") as f:
                        feed_json = json.load(f)
                if not isinstance(feed_json, dict) or "asset_prompts" not in feed_json:
                    return self._send(400, {"error": "载荷缺少 asset_prompts 字段"})
                asset_pool = []
                for project_row in store.list_projects():
                    source_project = store.get_project(project_row.get("id"))
                    if not source_project:
                        continue
                    for source_node in source_project["payload"].get("nodes", []):
                        if source_node.get("type") != "image":
                            continue
                        source_meta = source_node.get("metadata") or {}
                        same_target_project = source_project.get("id") == pid
                        if not same_target_project and (source_meta.get("status") != "done" or not source_meta.get("content")):
                            continue
                        asset_pool.append({
                            "assetKey": source_meta.get("assetKey", ""),
                            "id": source_node.get("id", ""),
                            "projectId": source_project.get("id", ""),
                            "title": source_node.get("title", ""),
                            "prompt": source_meta.get("prompt", ""),
                            "composerContent": source_meta.get("composerContent", ""),
                            "content": source_meta.get("content", ""),
                            "status": source_meta.get("status", ""),
                            "params": source_meta.get("params") or {},
                            "reusedAsset": source_meta.get("reusedAsset") or {},
                            "createdAt": source_node.get("createdAt", ""),
                            "updatedAt": source_node.get("updatedAt", ""),
                        })
                payload = import_feed.build_payload(
                    feed_json,
                    asset_pool=asset_pool,
                    target_project_id=pid,
                )
                payload = import_feed.merge_payload_by_episode(proj["payload"], payload)
                title = payload.get("title") or proj["title"]
                store.update_project(pid, title=title, payload=payload)
            readiness = import_feed.episode_readiness(
                payload,
                payload.get("episodeId") or (payload.get("episodeImports") or [{}])[-1].get("id"),
            )
            return self._send(200, {"id": pid, "title": title, "nodes": len(payload["nodes"]),
                                    "connections": len(payload["connections"]),
                                    "reusedAssets": int((payload.get("importSummary") or {}).get("assetsReused") or 0),
                                    "reusedGeneratedAssets": int((payload.get("importSummary") or {}).get("assetsReusedGenerated") or 0),
                                    "episodes": len(payload.get("episodeImports") or []),
                                    "readiness": readiness,
                                    "reimportPolicy": import_feed.REIMPORT_IDENTITY_POLICY})
        m = re.match(r"^/api/projects/([^/]+)/gate-check$", path)
        if m:
            pid = unquote(m.group(1))
            proj = store.get_project(pid)
            if not proj:
                return self._send(404, {"error": "项目不存在"})
            query = urllib.parse.parse_qs(parsed.query)
            episode_id = str((query.get("episodeId") or [""])[0]).strip() or None
            res = gate_check.run_check_json(proj["payload"], episode_id=episode_id)
            res["readiness"] = import_feed.episode_readiness(proj["payload"], episode_id)
            return self._send(200, res)
        m = re.match(r"^/api/projects/([^/]+)/notes$", path)
        if m:
            pid = unquote(m.group(1))
            with PROJECT_WRITE_LOCK:
                proj = store.get_project(pid)
                if not proj:
                    return self._send(404, {"error": "项目不存在"})
                payload = proj["payload"]
                payload.setdefault("extras", {})
                payload["extras"]["notes"] = body.get("notes") or []
                store.update_project(pid, payload=payload)
            return self._send(200, {"ok": True, "notes": payload["extras"]["notes"]})
        m = re.match(r"^/api/projects/([^/]+)/nodes/([^/]+)/generate$", path)
        if m:
            pid, nid = unquote(m.group(1)), unquote(m.group(2))
            proj = store.get_project(pid)
            if not proj:
                return self._send(404, {"error": "项目不存在"})
            node = next((n for n in proj["payload"].get("nodes", []) if n["id"] == nid), None)
            if not node:
                return self._send(404, {"error": "节点不存在"})
            # 允许按请求体覆盖类型：候选类型包含 text（文本节点也能 AI 生成）
            if body.get("type") in ("text", "image", "audio", "video"):
                node["type"] = body["type"]
            meta = node.get("metadata") or {}
            params = meta.get("params") or {}
            blockers = import_feed.node_dependency_blockers(proj["payload"], node)
            if blockers:
                names = "、".join(
                    f"{item.get('title') or item.get('nodeId')}（{item.get('reason')}）"
                    for item in blockers[:6]
                )
                suffix = "…" if len(blockers) > 6 else ""
                return self._send(409, {
                    "error": f"参考素材未就绪 {len(blockers)} 项：{names}{suffix}",
                    "code": "REFERENCE_NOT_READY",
                    "missing": blockers,
                })
            task = store.create_generation_task(
                pid, node["id"], title=node.get("title", ""), kind=node.get("type", ""),
                provider=params.get("provider") or meta.get("provider") or "",
                model=params.get("model") or meta.get("model") or "",
            )
            if (
                task
                and task.get("status") in {"queued", "submitting", "running"}
                and not task.get("created_new")
            ):
                return self._send(409, {
                    "error": "该节点已有供应商任务正在运行，请到任务中心查看（GENERATION_IN_PROGRESS）",
                    "code": "GENERATION_IN_PROGRESS",
                    "taskId": task.get("id"),
                    "providerTaskId": task.get("provider_task_id"),
                })
            provider_key = str(
                task.get("provider")
                or params.get("provider")
                or meta.get("provider")
                or "__default__"
            )
            try:
                slot = acquire_generation_slot(task["id"], provider_key)
            except GenerationQueueCancelled as exc:
                return self._send(409, {
                    "error": str(exc),
                    "code": "GENERATION_QUEUE_CANCELLED",
                    "taskId": task.get("id"),
                })
            except Exception as exc:
                return self._send(503, {
                    "error": str(exc),
                    "code": "GENERATION_QUEUE_TIMEOUT",
                    "taskId": task.get("id"),
                })
            try:
                return self._generate(pid, proj, node, task_id=task["id"])
            finally:
                release_generation_slot(slot)
        m = re.match(r"^/api/generation-tasks/([^/]+)/cancel$", path)
        if m:
            task_id = unquote(m.group(1))
            task = store.get_generation_task(task_id)
            if not task:
                return self._send(404, {"error": "任务不存在"})
            if task.get("status") != "queued":
                return self._send(409, {
                    "error": "只有尚未提交供应商的排队任务可以取消",
                    "code": "GENERATION_ALREADY_SUBMITTED",
                })
            finished_at = store._now_iso()
            store.update_generation_task(
                task_id,
                status="interrupted",
                phase="已取消",
                error="用户取消了排队任务",
                finished_at=finished_at,
            )
            commit_node_metadata(task["project_id"], task["node_id"], {
                "status": "failed",
                "lastError": "已取消生成排队",
                "providerTaskStatus": "interrupted",
                "providerTaskPhase": "已取消",
                "generationFinishedAt": finished_at,
            })
            with GENERATION_QUEUE_CONDITION:
                GENERATION_QUEUE_CONDITION.notify_all()
            return self._send(200, {"ok": True, "taskId": task_id})
        return self._send(404, {"error": "not found"})

    def do_PUT(self):
        scope = self._request_tenant_scope()
        if scope is None:
            return
        with scope:
            return self._do_put()

    def _do_put(self):
        with PROJECT_WRITE_LOCK:
            return self._do_put_locked()

    def _do_put_locked(self):
        path = urlparse(self.path).path
        body = self._read_json()
        # 项目根：重命名标题（PUT /api/projects/{id}，body.title）
        m = re.match(r"^/api/projects/([^/]+)$", path)
        if m:
            pid = unquote(m.group(1))
            proj = store.get_project(pid)
            if not proj:
                return self._send(404, {"error": "项目不存在"})
            if isinstance(body.get("title"), str) and body["title"].strip():
                new_title = body["title"].strip()
                proj["payload"]["title"] = new_title
                store.update_project(pid, title=new_title, payload=proj["payload"])
                return self._send(200, {"ok": True, "title": proj["payload"]["title"]})
            return self._send(400, {"error": "title 字段缺失或为空"})
        # Position-only save: dragging/layout code must never write stale metadata
        # (status/content/refs) back over another tab's newer generation state.
        m = re.match(r"^/api/projects/([^/]+)/nodes/positions$", path)
        if m:
            pid = unquote(m.group(1))
            proj = store.get_project(pid)
            if not proj:
                return self._send(404, {"error": "项目不存在"})
            incoming = body.get("positions")
            if not isinstance(incoming, list):
                return self._send(400, {"error": "positions 字段缺失或不是数组"})
            by_id = {node.get("id"): node for node in proj["payload"].get("nodes", [])}
            episode_id = str(body.get("episodeId") or "").upper()
            episode_layout = (
                proj["payload"].setdefault("episodeLayouts", {}).setdefault(episode_id, {})
                if episode_id
                else None
            )
            position_conflicts = []
            for item in incoming:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                node = by_id.get(item["id"])
                position = item.get("position")
                base_updated_at = str(item.get("baseUpdatedAt") or "")
                if (
                    node
                    and isinstance(position, dict)
                    and base_updated_at
                    and str(node.get("updatedAt") or "") > base_updated_at
                    and node.get("position") != position
                ):
                    position_conflicts.append({
                        "nodeId": item["id"],
                        "title": node.get("title") or item["id"],
                    })
            if position_conflicts:
                names = "、".join(item["title"] for item in position_conflicts[:5])
                suffix = "…" if len(position_conflicts) > 5 else ""
                return self._send(409, {
                    "error": f"另一个标签页刚移动过此节点，已拒绝覆盖位置：{names}{suffix}。请刷新后再拖动。",
                    "code": "NODE_POSITION_CONFLICT",
                    "conflicts": position_conflicts,
                })
            updated = 0
            for item in incoming:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                position = item.get("position")
                node = by_id.get(item["id"])
                if node is None or not isinstance(position, dict):
                    continue
                position = {
                    "x": float(position.get("x") or 0),
                    "y": float(position.get("y") or 0),
                }
                if episode_layout is not None:
                    episode_layout[item["id"]] = position
                else:
                    node["position"] = position
                node["updatedAt"] = store._now_iso()
                updated += 1
            if updated:
                store.update_project(pid, payload=proj["payload"])
            return self._send(200, {"ok": True, "updated": updated})
        # 批量保存：一次写库完成全部节点的 upsert。
        # 旧接口是逐个 PUT，而每次 update_project 都要重写整个 DB（实测 ~250ms/次），
        # 20+ 节点全量保存要 6 秒；新节点排在数组末尾，往往还没轮到就被刷新掉。
        m = re.match(r"^/api/projects/([^/]+)/nodes$", path)
        if m:
            pid = unquote(m.group(1))
            proj = store.get_project(pid)
            if not proj:
                return self._send(404, {"error": "项目不存在"})
            incoming = body.get("nodes")
            if not isinstance(incoming, list):
                return self._send(400, {"error": "nodes 字段缺失或不是数组"})
            payload = proj["payload"]
            nodes_list = payload.setdefault("nodes", [])
            by_id = {n.get("id"): n for n in nodes_list}
            active_task_nodes = {
                task.get("node_id")
                for task in store.list_generation_tasks(pid, 300)
                if task.get("status") in {"queued", "submitting", "running"}
            }
            conflicts = []
            for item in incoming:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                current = by_id.get(item["id"])
                if current and _node_save_conflict(
                    item,
                    current,
                    include_position=isinstance(item.get("position"), dict),
                ):
                    conflicts.append({
                        "nodeId": item["id"],
                        "title": current.get("title") or item.get("title") or item["id"],
                        "serverUpdatedAt": current.get("updatedAt"),
                        "clientUpdatedAt": item.get("baseUpdatedAt"),
                    })
            if conflicts:
                names = "、".join(item["title"] for item in conflicts[:5])
                suffix = "…" if len(conflicts) > 5 else ""
                return self._send(409, {
                    "error": f"另一个标签页已更新此节点，已拒绝覆盖：{names}{suffix}。请刷新后再编辑。",
                    "code": "NODE_EDIT_CONFLICT",
                    "conflicts": conflicts,
                })
            created = 0
            for item in incoming:
                if not isinstance(item, dict):
                    continue
                nid = item.get("id")
                if not nid:
                    continue
                node = by_id.get(nid)
                if node is None:
                    node = {
                        "id": nid,
                        "type": item.get("type") if item.get("type") in ("text", "image", "audio", "video") else "text",
                        "title": item.get("title") or "未命名节点",
                        "position": item.get("position") if isinstance(item.get("position"), dict) else {"x": 0, "y": 0},
                        "metadata": dict(item.get("metadata") or {}),
                        "createdAt": store._now_iso(),
                    }
                    nodes_list.append(node)
                    by_id[nid] = node
                    created += 1
                else:
                    if isinstance(item.get("metadata"), dict):
                        meta = node.setdefault("metadata", {})
                        merge_node_metadata(
                            meta,
                            item["metadata"],
                            generation_active=nid in active_task_nodes,
                        )
                    if "title" in item:
                        node["title"] = item["title"]
                    if isinstance(item.get("position"), dict):
                        node["position"] = item["position"]
                    if item.get("type") in ("text", "image", "audio", "video"):
                        node["type"] = item["type"]
                    node["updatedAt"] = store._now_iso()
            store.update_project(pid, payload=payload)
            return self._send(200, {"ok": True, "nodes": len(nodes_list), "created": created})
        m = re.match(r"^/api/projects/([^/]+)/nodes/([^/]+)$", path)
        if not m:
            return self._send(404, {"error": "not found"})
        pid, nid = unquote(m.group(1)), unquote(m.group(2))
        proj = store.get_project(pid)
        if not proj:
            return self._send(404, {"error": "项目不存在"})
        node = next((n for n in proj["payload"].get("nodes", []) if n["id"] == nid), None)
        if not node:
            # upsert：前端新建的卡片 id 形如 m-<timestamp>，载荷里本就不存在，
            # 直接 404 会让"＋添加"添加的一切节点永远存不进去 —— 表现为"加了卡片，刷新就没了"。
            # 这里按 body 建新节点后再走同一套合并逻辑。
            node = {
                "id": nid,
                "type": body.get("type") if body.get("type") in ("text", "image", "audio", "video") else "text",
                "title": body.get("title") or "未命名节点",
                "position": body.get("position") if isinstance(body.get("position"), dict) else {"x": 0, "y": 0},
                "metadata": dict(body.get("metadata") or {}),
                "createdAt": store._now_iso(),
            }
            proj["payload"].setdefault("nodes", []).append(node)
            node["updatedAt"] = store._now_iso()
            store.update_project(pid, payload=proj["payload"])
            return self._send(200, {"ok": True, "nodeId": nid, "created": True})
        incoming_node = {
            "id": nid,
            "title": body.get("title", node.get("title")),
            "metadata": body.get("metadata") if isinstance(body.get("metadata"), dict) else node.get("metadata"),
            "position": body.get("position") if isinstance(body.get("position"), dict) else node.get("position"),
            "baseUpdatedAt": body.get("baseUpdatedAt"),
        }
        if _node_save_conflict(
            incoming_node,
            node,
            include_position=isinstance(body.get("position"), dict),
        ):
            return self._send(409, {
                "error": "另一个标签页已更新此节点，已拒绝覆盖。请刷新后再编辑。",
                "code": "NODE_EDIT_CONFLICT",
                "nodeId": nid,
                "serverUpdatedAt": node.get("updatedAt"),
            })
        # 合并更新（metadata 深合并，position/title 直接覆盖）
        if isinstance(body.get("metadata"), dict):
            meta = node.setdefault("metadata", {})
            active_task_nodes = {
                task.get("node_id")
                for task in store.list_generation_tasks(pid, 300)
                if task.get("status") in {"queued", "submitting", "running"}
            }
            merge_node_metadata(
                meta,
                body["metadata"],
                generation_active=nid in active_task_nodes,
            )
        if "title" in body:
            node["title"] = body["title"]
        if "position" in body and isinstance(body["position"], dict):
            node["position"] = body["position"]
        node["updatedAt"] = store._now_iso()
        store.update_project(pid, payload=proj["payload"])
        return self._send(200, {"ok": True, "nodeId": nid})

    def do_DELETE(self):
        scope = self._request_tenant_scope()
        if scope is None:
            return
        with scope:
            return self._do_delete()

    def _do_delete(self):
        with PROJECT_WRITE_LOCK:
            return self._do_delete_locked()

    def _do_delete_locked(self):
        path = urlparse(self.path).path
        m = re.match(r"^/api/providers/([^/]+)$", path)
        if m:
            try:
                return self._send(200, provider_catalog.delete_provider(unquote(m.group(1))))
            except ValueError as exc:
                return self._send(404, {"error": str(exc)})
        m = re.match(r"^/api/models/([^/]+)$", path)
        if m:
            try:
                return self._send(200, provider_catalog.delete_model(unquote(m.group(1))))
            except ValueError as exc:
                return self._send(404, {"error": str(exc)})
        # 删除单个节点：前端删除媒体/文本卡时必须落库。
        # 此前只有"删整个项目"的路由，而保存接口是逐个 PUT 更新，
        # 本地删掉的节点在 payload.nodes 里一直留着 → 刷新后卡片"复活"。
        m = re.match(r"^/api/projects/([^/]+)/nodes/([^/]+)$", path)
        if m:
            pid, nid = unquote(m.group(1)), unquote(m.group(2))
            proj = store.get_project(pid)
            if not proj:
                return self._send(404, {"error": "项目不存在"})
            payload = proj["payload"]
            nodes = payload.get("nodes", [])
            deleted_node = next((n for n in nodes if n.get("id") == nid), None)
            remain = [n for n in nodes if n.get("id") != nid]
            if len(remain) == len(nodes):
                return self._send(404, {"error": "节点不存在"})
            payload["nodes"] = remain
            # 同步清理引用该节点的连线，避免留下悬空关系（渲染端会找不到端点）
            conns = payload.get("connections", []) or []
            payload["connections"] = [
                c for c in conns
                if c.get("fromNodeId") != nid and c.get("toNodeId") != nid
            ]
            # 其它节点 metadata.refs 里指向已删节点的引用也要摘掉
            deleted_title = str((deleted_node or {}).get("title") or "").strip()
            for other in payload["nodes"]:
                meta = other.get("metadata") or {}
                refs = meta.get("refs")
                if isinstance(refs, list) and refs:
                    meta["refs"] = [r for r in refs if not isinstance(r, dict) or r.get("nodeId") != nid]
                params = meta.get("params") if isinstance(meta.get("params"), dict) else {}
                if isinstance(params.get("refs"), list):
                    params["refs"] = [
                        r for r in params["refs"]
                        if not isinstance(r, dict) or r.get("nodeId") != nid
                    ]
                for key in ("voiceRef", "voiceRefs"):
                    value = params.get(key)
                    if isinstance(value, list):
                        params[key] = [
                            r for r in value
                            if not isinstance(r, dict) or r.get("nodeId") != nid
                        ]
                        if not params[key]:
                            params.pop(key, None)
                    elif isinstance(value, dict) and value.get("nodeId") == nid:
                        params.pop(key, None)
                if deleted_title:
                    prompt = str(meta.get("prompt") or "")
                    cleaned = prompt.split(f"@{deleted_title}")
                    if len(cleaned) > 1:
                        meta["prompt"] = re.sub(r"[ \t]{2,}", " ", "".join(cleaned)).rstrip()
            store.update_project(pid, payload=payload)
            return self._send(200, {"ok": True, "nodeId": nid, "nodes": len(remain)})
        m = re.match(r"^/api/projects/([^/]+)$", path)
        if m:
            store.delete_project(unquote(m.group(1)))
            return self._send(200, {"ok": True})
        return self._send(404, {"error": "not found"})

    # ---------- generate ----------
    def _generate_legacy(self, pid, proj, node):
        ntype = node.get("type")
        meta = node.get("metadata", {}) or {}
        params = meta.get("params") or {}   # 前端属性面板参数（模型/尺寸/比例/时长/引用/音色）
        # refs 解析：refs[].nodeId → 该节点 content（已生成）；未生成则计入 missing。
        #
        # ★★ 三条硬性约定（用户明确要求「引用的资产是要喂给模型的，不能出现一丁点问题」）：
        #   ① **label 用真实节点名**：读 refs[].label 时若为空/泛称（「资产图」「角色音色」），
        #      一律回源到源节点 title。否则 generate_image 拼出的
        #      「参考图1: 资产图 (url) / 参考图2: 资产图 (url)…」模型根本分不清谁是谁。
        #   ② **保持 refs 原始顺序**：generate_image 会把 urls[0] 当主参考图，
        #      顺序一变「谁是第一张」就变了，出图必然对不上。绝不在这里排序/去重。
        #   ③ **缺失要能追溯**：返回 missing 明细（label + nodeId + 原因），
        #      而不是只丢一个字符串列表 —— 否则用户在界面上完全看不出少了哪张。
        GENERIC_LABELS = {"", "资产图", "角色音色", "图片", "音频", "视频", "文本", "素材"}

        def _resolve_refs(refs):
            out = []
            missing = []
            for r in refs or []:
                nid = r.get("nodeId")
                src = next((n for n in proj["payload"].get("nodes", []) if n["id"] == nid), None)
                # label 优先级：源节点真实标题 > ref 自带 label > 兜底
                real_title = ((src or {}).get("title") or "").strip()
                ref_label = str(r.get("label") or "").strip()
                if real_title:
                    label = real_title
                elif ref_label and ref_label not in GENERIC_LABELS:
                    label = ref_label
                else:
                    label = ref_label or (nid or "未知引用")
                if not src:
                    missing.append({"nodeId": nid, "label": label, "reason": "引用的节点已不存在"})
                    continue
                smeta = src.get("metadata", {}) or {}
                url = r.get("url") or smeta.get("content", "")
                if not url:
                    missing.append({"nodeId": nid, "label": label, "reason": "源节点尚未生成（无 content）"})
                    continue
                if not str(url).startswith(("http", "https")):
                    # blob:/data: 这类地址只在浏览器会话内有效，后端根本取不到 → 必须报出来
                    missing.append({"nodeId": nid, "label": label,
                                    "reason": f"地址不是可下载的 http 链接（{str(url)[:24]}…）"})
                    continue
                out.append({"url": str(url), "label": label, "nodeId": nid})
            return out, missing

        def _missing_note(miss):
            """把 missing 明细写成一句可读提示，并落到 metadata.lastNote 供前端显示。"""
            if not miss:
                return ""
            lines = "；".join(f"{m['label']}（{m['reason']}）" for m in miss)
            return f"参考素材缺失 {len(miss)} 项：{lines}"
        try:
            if ntype == "image":
                refs, miss = _resolve_refs(meta.get("refs") or params.get("refs"))
                url, _ = ark_client.generate_image(
                    meta.get("prompt", ""), size=params.get("size") or meta.get("size"),
                    model=params.get("model") or meta.get("model"),
                    refs=refs, guidance=params.get("guidance") or meta.get("guidance"))
                meta["content"] = url
                meta["status"] = "done"
                meta["lastNote"] = _missing_note(miss)
            elif ntype == "video":
                refs, miss = _resolve_refs(meta.get("refs") or params.get("refs"))
                # ★★ 音色引用：**多值**（第 17 轮）。
                #   旧实现只读 params.voiceRef（单值），而前端连了 4 个音频节点时
                #   只有最后一个能活下来 —— 720 上表现为"引用资产只显示一个"，
                #   这里表现为"另外 3 个角色的音色完全没进模型"。
                #   兼容：voiceRefs（新，数组）优先；无则回退 voiceRef（老，单值）。
                vr_list = params.get("voiceRefs") or meta.get("voiceRefs") or []
                if not vr_list:
                    single = params.get("voiceRef") or meta.get("voiceRef")
                    vr_list = [single] if single else []
                voices = []
                for vr in vr_list:
                    if not vr: continue
                    vnode = next((n for n in proj["payload"].get("nodes", []) if n["id"] == vr.get("nodeId")), None)
                    if not vnode:
                        continue
                    vmeta = vnode.get("metadata", {}) or {}
                    vurl = vmeta.get("content", "") or ""
                    vlabel = (vnode.get("title") or "").strip() or vr.get("label") or "角色音色"
                    vp = vmeta.get("prompt", "") or ""
                    # 音色也没生成出来 → 同样计入缺失，别让"音色参考"悄悄变成空
                    if not (vurl and str(vurl).startswith(("http", "https"))):
                        miss.append({"nodeId": vr.get("nodeId"), "label": vlabel,
                                     "reason": "音色节点尚未生成（无 content）"})
                        # 提示词仍要带上（至少让模型知道有这个角色在说话）
                    voices.append({"prompt": vp, "url": vurl, "label": vlabel})
                url = ark_client.generate_video(
                    meta.get("prompt", ""), ratio=params.get("ratio") or meta.get("ratio"),
                    duration=int(params.get("duration") or meta.get("duration") or meta.get("seconds", 15)),
                    resolution=params.get("resolution") or meta.get("resolution", "480P"),
                    model=params.get("model") or meta.get("model"),
                    refs=refs, voices=voices)
                meta["content"] = url
                meta["status"] = "done"
                meta["lastNote"] = _missing_note(miss)
            elif ntype == "audio":
                resp = ark_client.generate_tts(meta.get("prompt", ""),
                                               voice=params.get("ttsVoice") or meta.get("voice", ""))
                meta["content"] = json.dumps(resp, ensure_ascii=False)
                meta["status"] = "done"
            elif ntype == "text":
                # 文本节点：正文由对话模型生成，生成结果直接覆盖 metadata.prompt
                # （文本卡渲染的就是 prompt，不像图/视频走 content 的 URL）。
                # 上游文本引用（其它文本节点已生成的正文）作为上下文一并送进去，
                # 让"本集梗概 → 分镜说明"这类串联生成能沿用同一套设定。
                ref_texts = []
                for r in (params.get("refs") or []):
                    src = next((n for n in proj["payload"].get("nodes", []) if n["id"] == r.get("nodeId")), None)
                    if not src:
                        continue
                    smeta = src.get("metadata", {}) or {}
                    body = smeta.get("prompt") if src.get("type") == "text" else smeta.get("content")
                    if body and str(body).strip():
                        ref_texts.append({"label": r.get("label") or (src.get("title") or ""), "text": str(body)})
                text, _ = ark_client.generate_text(
                    meta.get("prompt", ""),
                    model=params.get("model") or meta.get("model"),
                    system=params.get("system") or meta.get("system"),
                    max_tokens=int(params.get("maxTokens") or 2000),
                    temperature=params.get("temperature"),
                    refs=ref_texts,
                )
                meta["prompt"] = text
                meta["status"] = "done"
                meta["generatedAt"] = store._now_iso()
            else:
                if task_id:
                    store.update_generation_task(
                        task_id, status="failed", phase="不支持的类型",
                        error=f"不支持生成类型 {ntype}", finished_at=store._now_iso(),
                    )
                return self._send(400, {"error": f"不支持生成类型 {ntype}"})
        except RuntimeError as e:
            meta["status"] = "error"
            meta["lastError"] = str(e)
            store.update_project(pid, payload=proj["payload"])
            return self._send(_generation_error_status(e), {"error": str(e)})
        node["updatedAt"] = store._now_iso()
        store.update_project(pid, payload=proj["payload"])
        # text 节点没有 content（不是 URL），正文在 prompt 里 —— 单独回传 text，
        # 前端据此覆盖卡片正文；其它类型仍回 url。
        result = {"nodeId": node["id"], "url": meta.get("content"), "status": "done"}
        if ntype == "text":
            result["text"] = meta.get("prompt")
        return self._send(200, result)

    def _generate(self, pid, proj, node, task_id=None):
        key = (str(pid), str(node.get("id") or ""))
        with GENERATION_INFLIGHT_LOCK:
            if key in GENERATION_INFLIGHT:
                if task_id:
                    store.update_generation_task(
                        task_id, status="running", phase="已有任务运行中",
                        error="该节点正在生成，请勿重复提交",
                    )
                return self._send(409, {
                    "error": "该节点正在生成，请勿重复提交（GENERATION_IN_PROGRESS）",
                    "code": "GENERATION_IN_PROGRESS",
                    "status": "generating",
                    "taskId": task_id or "",
                })
            GENERATION_INFLIGHT.add(key)
        if task_id:
            store.update_generation_task(
                task_id, status="running", phase="准备提交",
                progress=10, started_at=store._now_iso(),
            )
        try:
            return self._generate_once(pid, proj, node, task_id=task_id)
        except Exception as exc:
            if task_id:
                store.update_generation_task(
                    task_id, status="failed", phase="任务异常", error=str(exc),
                    finished_at=store._now_iso(),
                )
            raise
        finally:
            with GENERATION_INFLIGHT_LOCK:
                GENERATION_INFLIGHT.discard(key)

    def _generate_once(self, pid, proj, node, task_id=None):
        ntype = str(node.get("type") or "")
        meta = node.get("metadata", {}) or {}
        params = meta.get("params") or {}
        generic_labels = {"", "资产图", "角色音色", "图片", "音频", "视频", "文本", "素材"}
        provider = None
        model = None

        def resolve_refs(raw_refs):
            resolved = []
            missing = []
            seen_refs = set()
            for raw in raw_refs or []:
                if not isinstance(raw, dict):
                    continue
                ref_id = raw.get("nodeId")
                if ref_id in seen_refs:
                    continue
                seen_refs.add(ref_id)
                source = next(
                    (item for item in proj["payload"].get("nodes", []) if item.get("id") == ref_id),
                    None,
                )
                title = str((source or {}).get("title") or "").strip()
                claimed = str(raw.get("label") or "").strip()
                label = title or (claimed if claimed not in generic_labels else "") or ref_id or "未知引用"
                if not source:
                    missing.append({"nodeId": ref_id, "label": label, "reason": "引用的节点已不存在"})
                    continue
                source_meta = source.get("metadata", {}) or {}
                source_type = str(source.get("type") or "")
                if source_type == "text":
                    text = str(source_meta.get("prompt") or source_meta.get("content") or "").strip()
                    if not text:
                        missing.append({"nodeId": ref_id, "label": label, "reason": "引用文本为空"})
                        continue
                    resolved.append({
                        "kind": "text",
                        "text": text,
                        "label": label,
                        "nodeId": ref_id,
                    })
                    continue
                # The source node is authoritative. params.refs[].url is only a
                # snapshot from the moment the reference was created and can be
                # stale after the asset was regenerated or replaced.
                url = str(source_meta.get("content") or raw.get("url") or "").strip()
                if not url:
                    missing.append({"nodeId": ref_id, "label": label, "reason": "源节点尚未生成"})
                    continue
                if not url.startswith(("http://", "https://", "/uploads/", "data:")):
                    missing.append({
                        "nodeId": ref_id,
                        "label": label,
                        "reason": f"素材地址不可用（{url[:32]}）",
                    })
                    continue
                kind = "audio" if source_type == "audio" else "video" if source_type == "video" else "image"
                source_model = str(
                    source_meta.get("remoteModel")
                    or source_meta.get("model")
                    or ""
                ).strip()
                source_provider = str(source_meta.get("provider") or "").strip()
                storage = source_meta.get("storage")
                if (
                    kind == "image"
                    and str(model.get("adapter") or "") == "ark_video"
                    and not (isinstance(storage, dict) and storage.get("key"))
                    and tos_storage.is_ready()
                ):
                    try:
                        storage = tos_storage.ensure_local_image(
                            url,
                            source={
                                "provider": source_provider,
                                "model": source_model,
                                "taskId": str(source_meta.get("taskId") or ""),
                                "generatedAt": str(source_meta.get("generatedAt") or ""),
                                "url": url,
                            },
                        )
                        if storage:
                            source_meta["storage"] = storage
                            source_meta["storageError"] = ""
                    except Exception as exc:
                        source_meta["storageError"] = str(exc)
                resolved.append({
                    "kind": kind,
                    "url": url,
                    "label": label,
                    "nodeId": ref_id,
                    "prompt": str(source_meta.get("prompt") or ""),
                    "storage": storage if isinstance(storage, dict) else None,
                    "sourceModel": source_model,
                    "sourceProvider": source_provider,
                })
            return resolved, missing

        def missing_note(missing):
            if not missing:
                return ""
            detail = "；".join(f"{item['label']}（{item['reason']}）" for item in missing)
            return f"参考素材缺失 {len(missing)} 项：{detail}"

        def validate_refs(refs, voices=None):
            limits = (model or {}).get("limits") or {}
            voice_count = len(voices or [])
            total = len(refs) + voice_count
            max_refs = int(limits.get("maxRefs") or 100)
            if total > max_refs:
                raise RuntimeError(f"当前模型最多接受 {max_refs} 个参考素材，实际 {total} 个")
            counts = {"image": 0, "video": 0, "audio": voice_count, "text": 0}
            for item in refs:
                kind = str(item.get("kind") or "")
                if kind in counts:
                    counts[kind] += 1
            checks = (
                ("maxImages", counts["image"], "图片"),
                ("maxVideos", counts["video"], "视频"),
                ("maxAudios", counts["audio"], "音频"),
                ("maxTextRefs", counts["text"], "文本"),
            )
            for key, count, label in checks:
                limit = limits.get(key)
                if limit is not None and count > int(limit):
                    raise RuntimeError(f"当前模型最多接受 {limit} 个参考{label}，实际 {count} 个")

        try:
            requested_provider = str(params.get("provider") or meta.get("provider") or "").strip()
            requested_model = str(
                params.get("model")
                or meta.get("model")
                or (params.get("ttsModel") if ntype == "audio" else "")
                or ""
            ).strip()
            provider, model, _cfg = provider_catalog.resolve_model(
                requested_provider or None,
                requested_model or None,
                ntype,
            )
            if task_id:
                store.update_generation_task(
                    task_id, provider=provider.get("id", ""), model=model.get("id", ""),
                    status="running", phase="提交供应商", progress=15,
                )
            effective = {**(model.get("defaultParams") or {}), **params}
            for key in ("ratio", "resolution", "duration", "guidance", "size", "system", "temperature", "maxTokens"):
                if effective.get(key) in (None, "") and meta.get(key) not in (None, ""):
                    effective[key] = meta.get(key)
            prompt = str(meta.get("prompt") or "")
            max_prompt = int((model.get("limits") or {}).get("maxPromptChars") or 40000)
            if len(prompt) > max_prompt:
                raise RuntimeError(f"提示词超过当前模型上限（{max_prompt} 字符）")
            if not prompt.strip():
                raise RuntimeError("提示词为空，不能提交生成")
            refs, missing = resolve_refs(
                params.get("refs") if params.get("refs") is not None else meta.get("refs")
            )
            result = {}
            if ntype == "image":
                refs = [item for item in refs if item.get("kind") in ("image", "text")]
                if missing:
                    raise RuntimeError(missing_note(missing))
                validate_refs(refs)
                result = provider_client.generate_image(provider, model, prompt, effective, refs)
                generated_url = persist_generated_media(result["url"], ".png")
                generated_at = store._now_iso()
                storage = archive_generated_image(
                    generated_url,
                    provider,
                    model,
                    str(result.get("taskId") or ""),
                    generated_at,
                )
                _record_media_version(
                    meta,
                    "image",
                    generated_url,
                    str(result.get("taskId") or ""),
                    generated_at,
                    storage,
                )
                meta["content"] = generated_url
                meta["status"] = "done"
                meta["generatedAt"] = generated_at
                meta["storage"] = storage if isinstance(storage, dict) else {}
                meta["storageError"] = str(storage.get("error") or "") if isinstance(storage, dict) else ""
                meta["lastNote"] = missing_note(missing)
                meta["promptStale"] = False
                meta["promptStaleReason"] = ""
                meta.pop("sourcePromptUpdatedAt", None)
            elif ntype == "video":
                voice_refs = params.get("voiceRefs") or meta.get("voiceRefs") or []
                if not voice_refs:
                    single = params.get("voiceRef") or meta.get("voiceRef")
                    voice_refs = [single] if single else []
                voices = []
                for voice_ref in voice_refs:
                    if not isinstance(voice_ref, dict):
                        continue
                    voice_node = next(
                        (item for item in proj["payload"].get("nodes", [])
                         if item.get("id") == voice_ref.get("nodeId")),
                        None,
                    )
                    if not voice_node:
                        continue
                    voice_meta = voice_node.get("metadata", {}) or {}
                    voice_url = str(voice_meta.get("content") or "").strip()
                    voice_label = str(voice_node.get("title") or voice_ref.get("label") or "角色音色").strip()
                    if not voice_url:
                        missing.append({
                            "nodeId": voice_ref.get("nodeId"),
                            "label": voice_label,
                            "reason": "音色节点尚未生成",
                        })
                        continue
                    if not voice_url.startswith(("http://", "https://", "/uploads/", "data:")):
                        missing.append({
                            "nodeId": voice_ref.get("nodeId"),
                            "label": voice_label,
                            "reason": "音色地址不可用",
                        })
                        continue
                    voices.append({
                        "kind": "audio",
                        "prompt": str(voice_meta.get("prompt") or ""),
                        "url": voice_url,
                        "label": voice_label,
                    })
                refs = [item for item in refs if item.get("kind") in ("image", "video", "text")]
                if missing:
                    raise RuntimeError(missing_note(missing))
                validate_refs(refs, voices)
                generation_started_at = store._now_iso()
                generation_task_id = task_id

                def on_task_created(task):
                    provider_task_id = str(task.get("taskId") or "")
                    meta.update({
                        "taskId": provider_task_id,
                        "providerTaskStatus": "queued",
                        "providerTaskPhase": "排队中",
                        "providerTaskProgress": 0,
                        "generationStartedAt": generation_started_at,
                        "provider": provider.get("id"),
                        "model": model.get("id"),
                        "remoteModel": model.get("remoteModel"),
                    })
                    commit_node_metadata(pid, node["id"], {
                        "taskId": provider_task_id,
                        "providerTaskStatus": "queued",
                        "providerTaskPhase": "排队中",
                        "providerTaskProgress": 0,
                        "generationStartedAt": generation_started_at,
                        "provider": provider.get("id"),
                        "model": model.get("id"),
                        "remoteModel": model.get("remoteModel"),
                    })
                    if generation_task_id:
                        store.update_generation_task(
                            generation_task_id, provider_task_id=provider_task_id,
                            status="running", phase="排队中", progress=20,
                        )

                def on_task_update(task):
                    raw_status = str(task.get("status") or "running")
                    progress = task.get("progress")
                    phase = _provider_task_phase(raw_status, progress)
                    meta.update({
                        "providerTaskStatus": raw_status,
                        "providerTaskPhase": phase,
                        "providerTaskProgress": progress,
                    })
                    commit_node_metadata(pid, node["id"], {
                        "providerTaskStatus": raw_status,
                        "providerTaskPhase": phase,
                        "providerTaskProgress": progress,
                    })
                    if task_id:
                        store.update_generation_task(
                            task_id, status="running", phase=phase,
                            progress=int(progress) if isinstance(progress, (int, float)) else None,
                        )

                result = provider_client.generate_video(
                    provider, model, prompt, effective, refs, voices,
                    on_task_created=on_task_created,
                    on_task_update=on_task_update,
                )
                generated_url = persist_generated_media(result["url"], ".mp4")
                generated_at = store._now_iso()
                _record_video_version(
                    meta,
                    generated_url,
                    str(result.get("taskId") or ""),
                    generated_at,
                )
                validation = validate_generated_video(
                    generated_url,
                    (meta.get("params") or {}).get("duration"),
                )
                versions = (meta.get("params") or {}).get("videoVersions") or []
                if versions:
                    versions[-1]["actualDuration"] = validation.get("duration")
                    versions[-1]["validationStatus"] = validation.get("status")
                media_versions = (meta.get("params") or {}).get("mediaVersions") or []
                if media_versions:
                    media_versions[-1]["actualDuration"] = validation.get("duration")
                    media_versions[-1]["validationStatus"] = validation.get("status")
                meta["content"] = generated_url
                meta["status"] = "done"
                meta["lastNote"] = missing_note(missing)
                meta["taskId"] = result.get("taskId", "")
                meta["generatedAt"] = generated_at
                meta["generationFinishedAt"] = generated_at
                meta["providerTaskStatus"] = "completed"
                meta["providerTaskPhase"] = "已完成"
                meta["providerTaskProgress"] = 100
                meta["videoValidation"] = validation
                meta["actualDuration"] = validation.get("duration")
            elif ntype == "audio":
                audio_refs = [item for item in refs if item.get("kind") in ("image", "text")]
                if missing:
                    raise RuntimeError(missing_note(missing))
                validate_refs(audio_refs)
                result = provider_client.generate_audio(
                    provider,
                    model,
                    prompt,
                    effective,
                    title=str(node.get("title") or ""),
                    refs=audio_refs,
                )
                generated_url = str(result["url"] or "")
                _record_media_version(
                    meta,
                    "audio",
                    generated_url,
                    str(result.get("taskId") or ""),
                    store._now_iso(),
                )
                meta["content"] = generated_url
                meta["voiceId"] = result.get("voiceId", "")
                meta["status"] = "done"
                meta["lastNote"] = missing_note(missing)
                meta["promptStale"] = False
                meta["promptStaleReason"] = ""
                meta.pop("sourcePromptUpdatedAt", None)
            elif ntype == "text":
                refs = [item for item in refs if item.get("kind") == "text"]
                if missing:
                    raise RuntimeError(missing_note(missing))
                validate_refs(refs)
                result = provider_client.generate_text(provider, model, prompt, effective, refs)
                meta["prompt"] = result["text"]
                meta["status"] = "done"
                meta["generatedAt"] = store._now_iso()
                meta["lastNote"] = missing_note(missing)
            else:
                return self._send(400, {"error": f"不支持生成类型 {ntype}"})

            # A successful retry supersedes any transient 429/5xx recorded by an
            # earlier attempt. Preserve an error only when the latest attempt fails.
            meta["lastError"] = ""
            meta["provider"] = provider.get("id")
            meta["model"] = model.get("id")
            meta["remoteModel"] = model.get("remoteModel")
            meta["production"] = {
                "provider": provider.get("id"),
                "providerName": provider.get("name"),
                "model": model.get("id"),
                "remoteModel": model.get("remoteModel"),
                "adapter": model.get("adapter"),
                "generatedAt": store._now_iso(),
            }
            if ntype in ("image", "audio", "video"):
                meta["generatedAt"] = meta["production"]["generatedAt"]
                meta["generationFinishedAt"] = meta["production"]["generatedAt"]
            if result.get("taskId"):
                meta["production"]["taskId"] = result["taskId"]
            version_list = (meta.get("params") or {}).get("mediaVersions") or []
            if version_list:
                version_list[-1].update({
                    "provider": provider.get("id", ""),
                    "model": model.get("id", ""),
                    "generatedAt": meta["production"]["generatedAt"],
                    "taskId": str(result.get("taskId") or ""),
                })
                if ntype == "video":
                    meta["params"]["videoVersions"] = [dict(item) for item in version_list]
        except Exception as exc:
            meta["status"] = "failed"
            meta["lastError"] = str(exc)
            commit_node_metadata(pid, node["id"], {
                "status": "failed",
                "lastError": str(exc),
                "providerTaskStatus": "failed",
                "providerTaskPhase": "生成失败",
                "generationFinishedAt": store._now_iso(),
            })
            if task_id:
                store.update_generation_task(
                    task_id, status="failed", phase="生成失败", error=str(exc),
                    finished_at=store._now_iso(),
                )
            status = _generation_error_status(exc)
            return self._send(status, {
                "error": str(exc),
                "code": "GENERATION_INPUT_INVALID" if status == 400 else "GENERATION_PROVIDER_ERROR",
            })

        generated_updates = {
            key: meta.get(key) for key in (
                "status", "content", "prompt", "lastError", "lastNote", "taskId",
                "voiceId", "provider", "model", "remoteModel", "production", "generatedAt",
                "generationStartedAt", "generationFinishedAt", "providerTaskStatus",
                "providerTaskPhase", "providerTaskProgress",
                "videoValidation", "actualDuration", "storage", "storageError",
            )
        }
        if ntype in ("image", "audio", "video"):
            generated_updates["_paramsPatch"] = {
                "mediaVersions": meta.get("params", {}).get("mediaVersions", []),
                "mediaVersionIndex": meta.get("params", {}).get("mediaVersionIndex", 0),
                **(
                    {
                        "videoVersions": meta.get("params", {}).get("videoVersions", []),
                        "videoVersionIndex": meta.get("params", {}).get("videoVersionIndex", 0),
                    }
                    if ntype == "video"
                    else {}
                ),
            }
        commit_node_metadata(pid, node["id"], generated_updates)
        if task_id:
            store.update_generation_task(
                task_id, status="succeeded", phase="已完成", progress=100,
                error="", provider_task_id=str(result.get("taskId") or ""),
                finished_at=store._now_iso(),
            )
        response = {"nodeId": node["id"], "url": meta.get("content"), "status": "done"}
        if ntype == "text":
            response["text"] = meta.get("prompt")
        if ntype == "audio":
            response["voiceId"] = meta.get("voiceId", "")
        return self._send(200, response)


class CanvasHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128


def main():
    print(
        f"drama-canvas-studio server: {__file__} | "
        f"{getattr(import_feed, 'REIMPORT_IDENTITY_POLICY', 'missing')}",
        flush=True,
    )
    os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"), exist_ok=True)
    try:
        tenant_bootstrap.bootstrap()
        if tenant_context.multi_tenant_enabled():
            print(
                f"多租户模式已启用，基础域名："
                f"{os.environ.get('CANVAS_BASE_DOMAIN', '').strip() or '未配置'}"
            )
    except Exception as exc:
        print(f"[tenant] 初始化失败：{exc}", file=sys.stderr)
        raise
    try:
        recovered = _run_for_active_tenants(store.backfill_video_version_history)
        if recovered:
            print(f"已恢复 {recovered} 个项目的媒体版本历史")
    except Exception as exc:
        print(f"[media-history] 回填失败：{exc}", file=sys.stderr)
    try:
        recovered_episodes = _run_for_active_tenants(backfill_multi_episode_payloads)
        if recovered_episodes:
            print(f"已恢复 {recovered_episodes} 个项目的多集导入数据")
    except Exception as exc:
        print(f"[episode-import] 多集恢复失败：{exc}", file=sys.stderr)
    try:
        linked_projects = _run_for_active_tenants(backfill_episode_shared_asset_links)
        if linked_projects:
            print(f"已补齐 {linked_projects} 个项目的跨集资产连线")
    except Exception as exc:
        print(f"[episode-import] 跨集资产连线失败：{exc}", file=sys.stderr)
    try:
        validated = _run_for_active_tenants(backfill_generated_video_validation)
        if validated:
            print(f"已校验 {validated} 个历史视频")
    except Exception as exc:
        print(f"[video-validation] 回填失败：{exc}", file=sys.stderr)
    try:
        _run_for_active_tenants(store.interrupt_orphan_generation_tasks)
    except Exception as exc:
        print(f"[generation-tasks] 清理中断任务失败：{exc}", file=sys.stderr)
    try:
        reconciled = _run_for_active_tenants(reconcile_interrupted_generation_nodes)
        if reconciled:
            print(f"已标记 {reconciled} 个中断的生成节点")
    except Exception as exc:
        print(f"[generation-tasks] 对账中断节点失败：{exc}", file=sys.stderr)
    try:
        reconciled_tasks = _run_for_active_tenants(reconcile_generation_task_records)
        if reconciled_tasks:
            print(f"已对账 {reconciled_tasks} 条生成任务")
    except Exception as exc:
        print(f"[generation-tasks] 对账任务记录失败：{exc}", file=sys.stderr)
    start_generation_task_reconciler()
    try:
        start_pending_video_resumers()
    except Exception as exc:
        print(f"[video-resume] 恢复进行中任务失败：{exc}", file=sys.stderr)
    server = CanvasHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"戏剧画布工作流系统已启动： http://127.0.0.1:{PORT}")
    print("关闭：Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
