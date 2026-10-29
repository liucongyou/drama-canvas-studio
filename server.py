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
import math
import os
import re
import sys
import base64
import binascii
import hashlib
import mimetypes
import secrets
import shutil
import subprocess
import threading
import time
import urllib.parse
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from email.utils import formatdate
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, unquote

import canvas_store as store
import canvas_feed_contract
import canvas_repository
import import_feed
import gate_check
import generation_budget
import imgbb_storage
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
SERVER_STARTED_AT = time.monotonic()
PROJECT_WRITE_LOCK = threading.RLock()
PROJECT_LOCKS = canvas_repository.PROJECT_LOCKS
PROJECT_LOCKS_GUARD = canvas_repository.PROJECT_LOCKS_GUARD
GENERATION_BUDGET_LOCK = threading.RLock()
GENERATION_INFLIGHT: set[tuple[str, str]] = set()
GENERATION_INFLIGHT_LOCK = threading.Lock()
_PROVIDER_AUTH_CACHE: dict[tuple[str, str, str], tuple[float, str]] = {}
_PROVIDER_AUTH_CACHE_LOCK = threading.Lock()
RESUMED_VIDEO_TASKS: set[tuple[str, str]] = set()
RESUMED_VIDEO_TASKS_LOCK = threading.Lock()
GENERATION_QUEUE_CONDITION = threading.Condition()
GENERATION_QUEUE_ACTIVE_GLOBAL = 0
GENERATION_QUEUE_ACTIVE_PROVIDERS: dict[str, int] = {}
GENERATION_QUEUE_ACTIVE_TENANTS: dict[str, int] = {}
GENERATION_QUEUE_ACTIVE_MODELS: dict[str, int] = {}
GENERATION_QUEUE_PROVIDER_COOLDOWN_UNTIL: dict[str, float] = {}
GENERATION_QUEUE_GLOBAL_LIMIT = max(
    1, int(os.environ.get("CANVAS_GENERATION_GLOBAL_CONCURRENCY", "80"))
)
GENERATION_QUEUE_PROVIDER_LIMIT = max(
    1, int(os.environ.get("CANVAS_GENERATION_PROVIDER_CONCURRENCY", "20"))
)
GENERATION_QUEUE_WAIT_TIMEOUT = max(
    60, int(os.environ.get("CANVAS_GENERATION_QUEUE_TIMEOUT", "21600"))
)
GENERATION_QUEUE_TENANT_LIMIT = max(
    1, int(os.environ.get("CANVAS_GENERATION_TENANT_CONCURRENCY", "32"))
)
GENERATION_QUEUE_PROVIDER_COOLDOWN_SECONDS = max(
    1, int(os.environ.get("CANVAS_GENERATION_RATE_LIMIT_COOLDOWN", "30"))
)
GENERATION_QUEUE_KIND_DEFAULT_LIMITS = {
    "image": 16,
    "video": 10,
    "audio": 8,
    "text": 8,
}
CANVAS_SESSION_COOKIE = os.environ.get("CANVAS_SESSION_COOKIE", "canvas_session")
CANVAS_SESSION_TTL = max(
    300, int(os.environ.get("CANVAS_SESSION_TTL_SECONDS", str(30 * 24 * 3600)))
)
MAX_JSON_BODY_BYTES = max(
    1024 * 1024,
    int(os.environ.get("CANVAS_MAX_JSON_BODY_BYTES", str(8 * 1024 * 1024))),
)
MAX_JSON_DEPTH = max(
    8,
    int(os.environ.get("CANVAS_MAX_JSON_DEPTH", "64")),
)


class RequestBoundaryError(ValueError):
    def __init__(self, status: int, message: str, code: str):
        super().__init__(message)
        self.status = status
        self.code = code


project_write_lock = canvas_repository.project_write_lock


def generation_budget_status():
    budget = provider_catalog.budget_config()
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    day = store.generation_cost_total(day_start.isoformat())
    month = store.generation_cost_total(month_start.isoformat())
    return {
        "budget": budget,
        "dayStart": day_start.isoformat(),
        "monthStart": month_start.isoformat(),
        "daySpend": day["amount"],
        "monthSpend": month["amount"],
        "spendCurrency": day["currency"] or month["currency"] or budget["currency"],
    }


def check_generation_budget(kind, params, payload):
    budget = provider_catalog.budget_config()
    params = params if isinstance(params, dict) else {}
    try:
        _provider, model, _cfg = provider_catalog.resolve_model(
            params.get("provider") or None,
            params.get("model") or None,
            kind,
            require_api_key=False,
        )
    except (TypeError, ValueError, RuntimeError):
        model = {}
    refs = params.get("refs") if isinstance(params.get("refs"), list) else []
    ref_types = {
        str(node.get("id") or ""): str(node.get("type") or "")
        for node in payload.get("nodes", [])
        if isinstance(node, dict)
    }
    has_video_input = any(
        isinstance(ref, dict)
        and ref_types.get(str(ref.get("nodeId") or "")) == "video"
        for ref in refs
    )
    estimate = generation_budget.estimate_generation_cost(
        kind,
        params,
        model,
        has_video_input=has_video_input,
    )
    if not budget.get("enabled"):
        return None, estimate
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    daily = store.generation_cost_total(day_start.isoformat())
    monthly = store.generation_cost_total(month_start.isoformat())
    spent_currencies = {
        currency
        for currency in [*daily["currencies"], *monthly["currencies"]]
        if currency
    }
    if spent_currencies and spent_currencies != {str(budget.get("currency") or "CNY")}:
        return {
            "code": "BUDGET_SPEND_CURRENCY_MIXED",
            "message": "已有任务使用了不同币种的费用预留，无法安全比较预算。请统一模型计费币种后再生成。",
        }, estimate
    issue = generation_budget.check_budget(
        budget=budget,
        estimate=estimate,
        daily_spend=daily["amount"],
        monthly_spend=monthly["amount"],
    )
    return issue, estimate


def project_id_for_path(path: str) -> str:
    match = re.match(r"^/api/projects/([^/]+)", str(path or ""))
    return unquote(match.group(1)) if match else ""


def _json_depth(value: Any, depth: int = 0) -> int:
    if depth > MAX_JSON_DEPTH:
        return depth
    if isinstance(value, dict):
        maximum = depth
        for item in value.values():
            maximum = max(maximum, _json_depth(item, depth + 1))
            if maximum > MAX_JSON_DEPTH:
                break
        return maximum
    if isinstance(value, list):
        maximum = depth
        for item in value:
            maximum = max(maximum, _json_depth(item, depth + 1))
            if maximum > MAX_JSON_DEPTH:
                break
        return maximum
    return depth


def _upload_dir():
    context = tenant_context.current()
    return str(context.upload_dir) if context is not None else UPLOAD_DIR


def _cookie_secure() -> bool:
    raw = os.environ.get("CANVAS_COOKIE_SECURE", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _security_headers() -> dict[str, str]:
    connect_sources = ["'self'", "https:"]
    api_host = _api_host()
    if api_host:
        connect_sources.append(f"https://{api_host}")
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Content-Security-Policy": (
            "default-src 'self'; "
            "base-uri 'self'; "
            "object-src 'none'; "
            "frame-ancestors 'none'; "
            "form-action 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https:; "
            "media-src 'self' blob: https:; "
            "font-src 'self' data:; "
            f"connect-src {' '.join(dict.fromkeys(connect_sources))}; "
            "worker-src 'self' blob:"
        ),
    }
    if _cookie_secure():
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers


def _feed_import_roots() -> list[str]:
    raw = os.environ.get("CANVAS_FEED_IMPORT_ROOTS", "")
    return [
        os.path.realpath(item.strip())
        for item in raw.split(os.pathsep)
        if item.strip()
    ]


def _resolve_feed_import_path(value: str) -> str:
    requested = os.path.realpath(os.path.expanduser(str(value or "").strip()))
    if not requested:
        raise ValueError("载荷文件路径为空")
    if not tenant_context.multi_tenant_enabled():
        return requested
    roots = _feed_import_roots()
    if not roots:
        raise PermissionError("当前部署不允许读取服务器本地载荷路径，请上传 JSON 文件")
    for root in roots:
        try:
            if os.path.commonpath([requested, root]) == root:
                return requested
        except ValueError:
            continue
    raise PermissionError("载荷文件不在允许的导入目录内")


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


def generation_model_concurrency(model_id=None, kind=None):
    fallback = GENERATION_QUEUE_KIND_DEFAULT_LIMITS.get(str(kind or ""), 8)
    raw_model = str(model_id or "").strip()
    if not raw_model:
        return fallback
    try:
        catalog = provider_catalog.public_catalog()
    except Exception:
        return fallback
    for model in catalog.get("models") or []:
        if raw_model not in {
            str(model.get("id") or ""),
            str(model.get("remoteModel") or ""),
        }:
            continue
        try:
            configured = int((model.get("limits") or {}).get("maxConcurrency") or 0)
        except (TypeError, ValueError):
            configured = 0
        return configured if configured > 0 else fallback
    return fallback


def is_provider_rate_limit_error(error) -> bool:
    status_code = getattr(error, "status_code", None)
    if status_code == 429:
        return True
    text = str(error or "").lower()
    return "http 429" in text or "http429" in text or "rate limit" in text or "too many requests" in text


def provider_retry_after_seconds(error, default=None):
    fallback = (
        float(default)
        if default is not None
        else float(GENERATION_QUEUE_PROVIDER_COOLDOWN_SECONDS)
    )
    headers = getattr(error, "headers", None)
    try:
        raw = headers.get("Retry-After") if headers is not None else None
        value = float(str(raw).strip()) if raw not in (None, "") else fallback
    except (TypeError, ValueError):
        value = fallback
    return max(1.0, min(value, 900.0))


def mark_generation_provider_rate_limited(provider_id, retry_after=None):
    provider_key = str(provider_id or "__default__").strip() or "__default__"
    tenant_key = tenant_context.current_tenant_id()
    composite_key = f"{tenant_key}\x1f{provider_key}"
    until = time.monotonic() + provider_retry_after_seconds(None, retry_after)
    with GENERATION_QUEUE_CONDITION:
        GENERATION_QUEUE_PROVIDER_COOLDOWN_UNTIL[composite_key] = max(
            GENERATION_QUEUE_PROVIDER_COOLDOWN_UNTIL.get(composite_key, 0),
            until,
        )
        GENERATION_QUEUE_CONDITION.notify_all()


def acquire_generation_slot(task_id, provider_id, model_id=None, kind=None):
    """Reserve one real provider slot and persist the queue state.

    Model/type-level concurrency is the primary control. Provider, tenant and
    global limits are safety ceilings, not the old one-task-per-user throttle.
    A provider rate-limit response only cools that tenant's provider lane.
    """
    global GENERATION_QUEUE_ACTIVE_GLOBAL
    tenant_key = tenant_context.current_tenant_id()
    provider_key = str(provider_id or "__default__").strip() or "__default__"
    composite_key = f"{tenant_key}\x1f{provider_key}"
    model_key = (
        f"{tenant_key}\x1f{provider_key}\x1f"
        f"{str(model_id or kind or '__default__').strip() or '__default__'}"
    )
    model_limit = generation_model_concurrency(model_id, kind)
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
            model_active = GENERATION_QUEUE_ACTIVE_MODELS.get(model_key, 0)
            cooldown_remaining = max(
                0.0,
                GENERATION_QUEUE_PROVIDER_COOLDOWN_UNTIL.get(composite_key, 0)
                - time.monotonic(),
            )
            if cooldown_remaining > 0:
                if task_id:
                    store.update_generation_task(
                        task_id,
                        status="queued",
                        phase=f"供应商限流冷却 {math.ceil(cooldown_remaining)}s",
                    )
                GENERATION_QUEUE_CONDITION.wait(timeout=min(5.0, cooldown_remaining))
                continue
            if (
                global_active < GENERATION_QUEUE_GLOBAL_LIMIT
                and provider_active < GENERATION_QUEUE_PROVIDER_LIMIT
                and tenant_active < GENERATION_QUEUE_TENANT_LIMIT
                and model_active < model_limit
            ):
                GENERATION_QUEUE_ACTIVE_GLOBAL += 1
                GENERATION_QUEUE_ACTIVE_PROVIDERS[composite_key] = provider_active + 1
                GENERATION_QUEUE_ACTIVE_TENANTS[tenant_key] = tenant_active + 1
                GENERATION_QUEUE_ACTIVE_MODELS[model_key] = model_active + 1
                if task_id:
                    store.update_generation_task(
                        task_id,
                        status="submitting",
                        phase="已获得执行槽位",
                        error="",
                        finished_at="",
                        started_at=store._now_iso(),
                    )
                return {
                    "tenant": tenant_key,
                    "provider": provider_key,
                    "composite": composite_key,
                    "model": model_key,
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
                        f"供应商 {provider_active}/{GENERATION_QUEUE_PROVIDER_LIMIT}，"
                        f"模型 {model_active}/{model_limit}）"
                    ),
                )
            GENERATION_QUEUE_CONDITION.wait(timeout=5)


def release_generation_slot(slot):
    global GENERATION_QUEUE_ACTIVE_GLOBAL
    if isinstance(slot, dict):
        tenant_key = str(slot.get("tenant") or "legacy")
        provider_key = str(slot.get("provider") or "__default__")
        composite_key = str(slot.get("composite") or f"{tenant_key}\x1f{provider_key}")
        model_key = str(slot.get("model") or "")
    elif isinstance(slot, tuple) and len(slot) == 2:
        tenant_key, provider_key = str(slot[0] or "legacy"), str(slot[1] or "__default__")
        composite_key = f"{tenant_key}\x1f{provider_key}"
        model_key = ""
    else:
        tenant_key = tenant_context.current_tenant_id()
        provider_key = str(slot or "__default__")
        composite_key = f"{tenant_key}\x1f{provider_key}"
        model_key = ""
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
        if model_key:
            model_active = max(0, GENERATION_QUEUE_ACTIVE_MODELS.get(model_key, 0) - 1)
            if model_active:
                GENERATION_QUEUE_ACTIVE_MODELS[model_key] = model_active
            else:
                GENERATION_QUEUE_ACTIVE_MODELS.pop(model_key, None)
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
    if (
        not storage_cfg.get("enabled")
        or str(storage_cfg.get("provider") or "tos") != "tos"
        or not storage_cfg.get("auto_persist_images", True)
    ):
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


def mirror_generated_image_to_imgbb(generated_url, provider, model, task_id, generated_at):
    """Optionally mirror a locally cached generated image to ImgBB."""
    if not imgbb_storage.is_ready():
        return None
    raw = str(generated_url or "").strip()
    if not raw.startswith("/uploads/"):
        return {"status": "failed", "error": "图片尚未形成稳定本地文件，未上传 ImgBB"}
    filename = raw[len("/uploads/"):]
    if not filename or "/" in filename or "\\" in filename:
        return {"status": "failed", "error": "图片缓存路径不合法"}
    path = os.path.join(_upload_dir(), filename)
    if not os.path.isfile(path):
        return {"status": "failed", "error": "图片缓存文件不存在"}
    mime = mimetypes.guess_type(filename)[0] or "image/png"
    try:
        with open(path, "rb") as handle:
            data = handle.read()
        return imgbb_storage.upload_bytes(
            data,
            mime=mime,
            name=os.path.splitext(filename)[0],
            source={
                "provider": provider.get("id"),
                "model": model.get("remoteModel") or model.get("id"),
                "taskId": task_id,
                "generatedAt": generated_at,
                "localUrl": generated_url,
            },
        )
    except Exception as exc:
        print(f"[imgbb] failed to mirror {generated_url}: {exc}", file=sys.stderr)
        return {"status": "failed", "error": str(exc)}


def queue_generated_image_imgbb_mirror(
    project_id,
    node_id,
    generated_url,
    provider_id,
    model_id,
    task_id,
    generated_at,
):
    """Mirror an already-saved local image without delaying the generation response."""
    if not imgbb_storage.is_ready():
        return False
    context = tenant_context.current()
    tenant_id = context.tenant_id if context else None
    tenant_slug = context.slug if context else "local"

    def run():
        try:
            result = mirror_generated_image_to_imgbb(
                generated_url,
                {"id": provider_id},
                {"id": model_id, "remoteModel": model_id},
                task_id,
                generated_at,
            )
        except Exception as exc:
            result = {"status": "failed", "error": str(exc)}
        image_host_error = (
            str(result.get("error") or "")
            if isinstance(result, dict) and result.get("status") == "failed"
            else ""
        )
        commit_node_metadata(project_id, node_id, {
            "imgbb": result if isinstance(result, dict) else {},
            "imageHostError": image_host_error,
        })

    threading.Thread(
        target=_run_in_tenant,
        args=(tenant_id, tenant_slug, run),
        name=f"imgbb-mirror-{node_id}",
        daemon=True,
    ).start()
    return True


def validate_generated_video(url, expected_duration=None):
    raw = str(url or "").strip()
    if not raw.startswith("/uploads/"):
        return {"status": "unverified", "message": "远程文件暂未校验"}
    path = os.path.join(_upload_dir(), raw[len("/uploads/"):])
    if not os.path.isfile(path):
        return {"status": "invalid", "message": "本地文件不存在"}
    try:
        size = os.path.getsize(path)
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            proc = subprocess.run(
                [
                    ffprobe, "-v", "error",
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
            size = int((info.get("format") or {}).get("size") or size)
            video_codec = (video_stream or {}).get("codec_name")
            width = (video_stream or {}).get("width")
            height = (video_stream or {}).get("height")
        else:
            ffmpeg = provider_client._ffmpeg_executable()
            if not ffmpeg:
                return {
                    "status": "unverified",
                    "message": "未找到 ffprobe 或 ffmpeg，文件已保留",
                    "size": size,
                }
            proc = subprocess.run(
                [ffmpeg, "-hide_banner", "-i", path, "-map", "0", "-c", "copy", "-f", "null", "-"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            detail = proc.stderr or ""
            if proc.returncode != 0:
                raise RuntimeError((detail or "ffmpeg 校验失败").strip()[:300])
            duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", detail)
            duration = 0.0
            if duration_match:
                hours, minutes, seconds = duration_match.groups()
                duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            video_match = re.search(
                r"Stream #\S+.*?Video:\s*([A-Za-z0-9_]+).*?(\d{2,5})x(\d{2,5})",
                detail,
            )
            video_stream = bool(video_match)
            has_audio = "Audio:" in detail
            video_codec = video_match.group(1) if video_match else None
            width = int(video_match.group(2)) if video_match else None
            height = int(video_match.group(3)) if video_match else None
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
            "width": width,
            "height": height,
            "videoCodec": video_codec,
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
    with project_write_lock(project_id):
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
    "imgbb",
    "imageHostError",
    "source",
}

GENERATION_OWNED_PARAM_FIELDS = {
    "mediaVersions",
    "mediaVersionIndex",
    "videoVersions",
    "videoVersionIndex",
    "thumbUrl",
}

LOCAL_MEDIA_METADATA_FIELDS = {
    "status",
    "content",
    "generatedAt",
    "actualDuration",
    "videoValidation",
    "source",
}

LOCAL_MEDIA_PARAM_FIELDS = {
    "mediaVersions",
    "mediaVersionIndex",
    "videoVersions",
    "videoVersionIndex",
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
    """Merge editable client fields while generation result fields stay authoritative."""
    explicit_media_import = bool((incoming or {}).get("_mediaImport"))
    explicit_media_version_selection = bool(
        (incoming or {}).get("_mediaVersionSelection")
    )
    local_media_import = (
        explicit_media_import
        and
        isinstance((incoming or {}).get("source"), dict)
        and (incoming or {}).get("source", {}).get("type") == "local"
    )
    current_versions = (
        ((meta.get("params") or {}).get("mediaVersions"))
        or ((meta.get("params") or {}).get("videoVersions"))
        or []
    )
    current_version_urls = {
        str(item.get("url") or "")
        for item in current_versions
        if isinstance(item, dict) and item.get("url")
    }
    requested_content = str((incoming or {}).get("content") or "")
    media_version_selection = bool(
        explicit_media_version_selection
        and
        isinstance((incoming or {}).get("source"), dict)
        and requested_content
        and requested_content in current_version_urls
    )
    for key, value in (incoming or {}).items():
        if key in {"_mediaImport", "_mediaVersionSelection"}:
            continue
        if key == "params" and isinstance(value, dict):
            current_params = meta.get("params")
            params = dict(current_params) if isinstance(current_params, dict) else {}
            for param_key, param_value in value.items():
                if (
                    param_key in GENERATION_OWNED_PARAM_FIELDS
                    and not (local_media_import and param_key in LOCAL_MEDIA_PARAM_FIELDS)
                    and not (
                        media_version_selection
                        and param_key in {"mediaVersionIndex", "videoVersionIndex"}
                    )
                ):
                    continue
                params[param_key] = param_value
            versions = [
                item for item in (params.get("mediaVersions") or [])
                if isinstance(item, dict) and str(item.get("url") or "").strip()
            ]
            if not versions:
                versions = [
                    item for item in (params.get("videoVersions") or [])
                    if isinstance(item, dict) and str(item.get("url") or "").strip()
                ]
            requested_index = (
                value.get(
                    "videoVersionIndex"
                    if versions and params.get("videoVersions")
                    else "mediaVersionIndex"
                )
                if local_media_import or media_version_selection
                else None
            )
            try:
                requested_index = int(requested_index)
            except (TypeError, ValueError):
                requested_index = -1
            if versions and 0 <= requested_index < len(versions):
                params["mediaVersionIndex"] = requested_index
                if params.get("videoVersions"):
                    params["videoVersionIndex"] = requested_index
                meta["content"] = str(versions[requested_index].get("url") or "")
            meta["params"] = params
            continue
        if (
            key in GENERATION_OWNED_METADATA_FIELDS
            and not (
                (local_media_import and key in LOCAL_MEDIA_METADATA_FIELDS)
                or (
                    media_version_selection
                    and key in {"content", "status", "generatedAt", "actualDuration", "source"}
                )
            )
        ):
            continue
        meta[key] = value


def apply_generation_input(node, body):
    """Apply the explicit UI snapshot used by one generation action."""
    meta = node.setdefault("metadata", {})
    if isinstance((body or {}).get("prompt"), str):
        meta["prompt"] = body["prompt"]
    if isinstance((body or {}).get("params"), dict):
        editable_params = {
            key: value
            for key, value in body["params"].items()
            if key not in GENERATION_OWNED_PARAM_FIELDS
        }
        meta["params"] = {
            **(meta.get("params") or {}),
            **editable_params,
        }
    return meta


def is_client_disconnect_error(exc):
    if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
        return True
    errno = getattr(exc, "errno", None)
    return errno in {32, 104, 10053, 10054}


def preserve_generation_outcome(task_id, exc):
    """A dropped browser response must never rewrite a paid generation result."""
    if is_client_disconnect_error(exc):
        return True
    if task_id:
        task = store.get_generation_task(task_id)
        return bool(task and task.get("status") == "succeeded")
    return False


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
        "sourceType": "generated",
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


def _backfill_image_host_versions(payload):
    changed = 0
    for node in payload.get("nodes") or []:
        if not isinstance(node, dict) or node.get("type") != "image":
            continue
        meta = node.get("metadata") or {}
        imgbb = meta.get("imgbb") if isinstance(meta.get("imgbb"), dict) else {}
        hosted_url = str(imgbb.get("url") or "").strip()
        if imgbb.get("status") != "stored" or not hosted_url:
            continue
        source = imgbb.get("source") if isinstance(imgbb.get("source"), dict) else {}
        local_url = str(source.get("localUrl") or "").strip()
        local_filename = local_url[len("/uploads/"):] if local_url.startswith("/uploads/") else ""
        local_available = bool(
            local_filename
            and os.path.isfile(os.path.join(_upload_dir(), local_filename))
        )
        params = meta.setdefault("params", {})
        versions = [
            item for item in (params.get("mediaVersions") or [])
            if isinstance(item, dict) and str(item.get("url") or "").strip()
        ]
        if local_available:
            local_versions = [
                item for item in versions
                if str(item.get("url") or "") != hosted_url
            ]
            if not any(str(item.get("url") or "") == local_url for item in local_versions):
                local_versions.append({
                    "url": local_url,
                    "taskId": str(source.get("taskId") or ""),
                    "generatedAt": str(source.get("generatedAt") or meta.get("generatedAt") or ""),
                    "kind": "image",
                    "provider": str(source.get("provider") or meta.get("provider") or ""),
                    "model": str(source.get("model") or meta.get("model") or ""),
                })
            local_versions = local_versions[-MAX_MEDIA_VERSIONS:]
            local_index = next(
                index for index, item in enumerate(local_versions)
                if str(item.get("url") or "") == local_url
            )
            if meta.get("content") != local_url:
                meta["content"] = local_url
                changed += 1
            if versions != local_versions:
                params["mediaVersions"] = local_versions
                changed += 1
            if params.get("mediaVersionIndex") != local_index:
                params["mediaVersionIndex"] = local_index
                changed += 1
            meta["params"] = params
            meta["status"] = "done"
            continue
        if not any(str(item.get("url") or "") == hosted_url for item in versions):
            versions.append({
                "url": hosted_url,
                "taskId": str((imgbb.get("source") or {}).get("taskId") or ""),
                "generatedAt": str(imgbb.get("uploadedAt") or meta.get("generatedAt") or ""),
                "kind": "image",
                "provider": str((imgbb.get("source") or {}).get("provider") or meta.get("provider") or ""),
                "model": str((imgbb.get("source") or {}).get("model") or meta.get("model") or ""),
            })
            changed += 1
        versions = versions[-MAX_MEDIA_VERSIONS:]
        params["mediaVersions"] = versions
        params["mediaVersionIndex"] = next(
            index for index, item in enumerate(versions)
            if str(item.get("url") or "") == hosted_url
        )
        meta["params"] = params
        meta["content"] = hosted_url
        meta["status"] = "done"
    return changed


def backfill_image_host_versions():
    changed = 0
    for project_row in store.list_projects():
        project = store.get_project(project_row.get("id"))
        if not project:
            continue
        payload = project["payload"]
        project_changed = _backfill_image_host_versions(payload)
        if project_changed:
            store.update_project(project["id"], payload=payload)
            changed += project_changed
    return changed


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


def ensure_provider_auth(provider):
    """Run a non-billing auth probe before a paid provider submission.

    A generation request must never spend time waiting for a task id when the
    configured key is already known to be invalid. The probe uses the same
    /models endpoint as the settings test and is cached per tenant/provider for
    a short window so batch generation does not hammer the provider.
    """
    provider_id = str((provider or {}).get("id") or "").strip()
    if not provider_id:
        return
    key = provider_catalog.provider_key(provider)
    if not key:
        raise RuntimeError(
            f"供应商 {provider_id} 尚未配置 API Key。"
            "请在「模型接口」中填写并测试连接后再生成。"
        )
    tenant_id = tenant_context.current_tenant_id()
    cache_key = (
        str(tenant_id or "legacy"),
        provider_id,
        str((provider or {}).get("base_url") or ""),
        key[-8:],
    )
    now = time.time()
    with _PROVIDER_AUTH_CACHE_LOCK:
        cached = _PROVIDER_AUTH_CACHE.get(cache_key)
        if cached and cached[0] > now:
            if cached[1] == "auth_failed":
                raise RuntimeError(
                    f"供应商 {provider_id} 鉴权失败。"
                    "请在「模型接口」中更新 API Key 后重新测试。"
                )
            if cached[1] == "balance_failed":
                raise RuntimeError(
                    f"供应商 {provider_id} 账户余额或额度不足。"
                    "请先充值或调整额度后再生成。"
                )
            return
    result = provider_catalog.test_provider(provider_id)
    failed = bool(result.get("authFailed"))
    balance_failed = bool(result.get("balanceFailed"))
    probe_missing = bool(result.get("probeMissing"))
    message = str(result.get("message") or "")
    if not result.get("ok") and ("HTTP 401" in message or "HTTP 403" in message):
        failed = True
    ttl = 15 if (failed or balance_failed) else 60
    with _PROVIDER_AUTH_CACHE_LOCK:
        _PROVIDER_AUTH_CACHE[cache_key] = (
            now + ttl,
            "auth_failed" if failed else ("balance_failed" if balance_failed else "ok"),
        )
    # A missing probes endpoint is not an auth failure. Ark/DashScope and some
    # relays intentionally expose no /models route; the real generation call
    # must still run and report its own provider error.
    if probe_missing:
        return
    if balance_failed:
        raise RuntimeError(
            f"供应商 {provider_id} 账户余额或额度不足：{message or '请检查供应商账户余额'}。"
            "充值或调整额度后重试。"
        )
    if failed:
        raise RuntimeError(
            f"供应商 {provider_id} 鉴权失败：{message or 'API Key 无效'}。"
            "请在「模型接口」中更新 API Key 后重试。"
        )


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


def reconcile_active_generation_task_for_node(project_id, node_id, node):
    """Close ghost active rows before another click is rejected as in-progress."""
    task = _find_generation_task(project_id, node_id)
    if not task:
        return None
    meta = (node or {}).get("metadata") or {}
    node_status = str(meta.get("status") or "")
    provider_task_id = str(meta.get("taskId") or "").strip()
    if node_status == "done":
        return store.update_generation_task(
            task["id"],
            status="succeeded",
            phase="已完成",
            progress=100,
            error="",
            provider_task_id=provider_task_id or task.get("provider_task_id") or "",
            finished_at=store._now_iso(),
        )
    if node_status == "failed":
        return store.update_generation_task(
            task["id"],
            status="failed",
            phase=str(meta.get("providerTaskPhase") or "生成失败"),
            error=str(meta.get("lastError") or "生成失败"),
            provider_task_id=provider_task_id or task.get("provider_task_id") or "",
            finished_at=store._now_iso(),
        )
    if provider_task_id and not task.get("provider_task_id"):
        return store.update_generation_task(task["id"], provider_task_id=provider_task_id)
    key = (str(project_id), str(node_id))
    with GENERATION_INFLIGHT_LOCK:
        in_flight = key in GENERATION_INFLIGHT
    if in_flight:
        return task
    updated_raw = str(task.get("updated_at") or task.get("created_at") or "")
    try:
        updated_at = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - updated_at).total_seconds()
    except (TypeError, ValueError):
        age = 999
    if not task.get("provider_task_id") and age >= 90:
        return store.update_generation_task(
            task["id"],
            status="interrupted",
            phase="任务已中断",
            error="任务在取得供应商任务 ID 前已停止，请重新生成",
            finished_at=store._now_iso(),
        )
    return task


def _latest_generation_task(project_id, node_id):
    for item in store.list_generation_tasks(project_id, 120):
        if item.get("node_id") == node_id:
            return item
    return None


def _latest_terminal_generation_task(project_id, node_id):
    interrupted = None
    for item in store.list_generation_tasks(project_id, 120):
        if item.get("node_id") != node_id:
            continue
        status = item.get("status")
        if status == "failed":
            return item
        if status == "interrupted" and interrupted is None:
            interrupted = item
    return interrupted


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
            if task.get("status") in {"failed", "interrupted"}:
                node = nodes.get((task.get("project_id"), task.get("node_id")))
                meta = (node or {}).get("metadata") or {}
                if not node or str(meta.get("status") or "") != "generating":
                    continue
                task_provider_id = str(task.get("provider_task_id") or "")
                node_provider_id = str(meta.get("taskId") or "")
                if task_provider_id and node_provider_id and task_provider_id != node_provider_id:
                    continue
                error = str(task.get("error") or "生成任务失败")
                commit_node_metadata(task["project_id"], task["node_id"], {
                    "status": "failed",
                    "lastError": error,
                    "providerTaskStatus": "failed",
                    "providerTaskPhase": str(task.get("phase") or "生成失败"),
                    "generationFinishedAt": task.get("finished_at") or store._now_iso(),
                })
                reconciled += 1
            continue
        node = nodes.get((task.get("project_id"), task.get("node_id")))
        meta = (node or {}).get("metadata") or {}
        node_status = str(meta.get("status") or "")
        if (
            node_status == "generating"
            and not str(task.get("provider_task_id") or "").strip()
            and str(task.get("phase") or "") in {"准备提交", "提交供应商"}
        ):
            # A submit request that never produced a provider task id is not a
            # durable worker: process restarts, hung sockets, or a killed
            # request leave it looking "running" forever. Submit timeouts are
            # short; after that grace period it is safe to release the node for
            # an explicit retry instead of showing a ghost task.
            updated_raw = str(task.get("updated_at") or task.get("started_at") or task.get("created_at") or "")
            try:
                updated_at = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - updated_at).total_seconds()
            except (TypeError, ValueError):
                age = 999
            if age >= max(90, provider_client.SUBMIT_TIMEOUT_SECONDS + 30):
                error = "提交供应商超时，未取得供应商任务 ID。请检查网络/供应商状态后重试。"
                store.update_generation_task(
                    task["id"], status="interrupted", phase="提交超时",
                    error=error, finished_at=store._now_iso(),
                )
                commit_node_metadata(task["project_id"], task["node_id"], {
                    "status": "failed",
                    "lastError": error,
                    "providerTaskStatus": "interrupted",
                    "providerTaskPhase": "提交超时",
                    "generationFinishedAt": store._now_iso(),
                })
                reconciled += 1
                continue
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
            provider_status = str(meta.get("providerTaskStatus") or "").strip().lower()
            if (
                task.get("provider_task_id")
                and provider_status in {
                    "queued", "pending", "created", "submitted",
                    "running", "processing", "retrying", "in_progress",
                }
            ):
                continue
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


def reconcile_failed_generation_task_nodes():
    """Restore provider failure text after a restart produced a stale node state."""
    repaired = 0
    for project_row in store.list_projects():
        project = store.get_project(project_row.get("id"))
        if not project:
            continue
        for node in project["payload"].get("nodes", []):
            if node.get("type") not in {"image", "audio", "video"}:
                continue
            meta = node.get("metadata") or {}
            if meta.get("status") not in {"failed", "generating"}:
                continue
            task = _latest_terminal_generation_task(project["id"], node["id"])
            if not task or task.get("status") != "failed":
                continue
            error = str(task.get("error") or "生成失败")
            phase = str(task.get("phase") or "生成失败")
            if meta.get("status") == "failed" and meta.get("lastError") == error:
                continue
            commit_node_metadata(project["id"], node["id"], {
                "status": "failed",
                "lastError": error,
                "providerTaskStatus": "failed",
                "providerTaskPhase": phase,
                "generationFinishedAt": task.get("finished_at") or store._now_iso(),
            })
            repaired += 1
    return repaired


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
            latest_task = _latest_terminal_generation_task(project["id"], node["id"])
            if not task:
                if latest_task:
                    task = latest_task
                else:
                    task = store.create_generation_task(
                        project["id"], node["id"], title=node.get("title", ""), kind=node.get("type", ""),
                        provider=(meta.get("params") or {}).get("provider") or meta.get("provider") or "",
                        model=(meta.get("params") or {}).get("model") or meta.get("model") or "",
                    )
            task_error = "后端重启，旧任务未取得可恢复的供应商任务 ID，请确认后重新生成"
            task_phase = "服务中断"
            if latest_task and latest_task.get("status") in {"failed", "interrupted"}:
                task_error = str(latest_task.get("error") or task_error)
                task_phase = str(latest_task.get("phase") or task_phase)
            if task and task.get("status") == "failed":
                # Preserve the provider's concrete failure instead of creating
                # a misleading interrupted task on the next restart.
                pass
            elif task:
                store.update_generation_task(
                    task["id"], status="interrupted", phase=task_phase,
                    error=task_error,
                    provider_task_id=provider_task_id,
                    finished_at=store._now_iso(),
                )
            commit_node_metadata(project["id"], node["id"], {
                "status": "failed",
                "providerTaskStatus": "interrupted",
                "providerTaskPhase": task_phase,
                "lastError": task_error,
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
        if not payload.get("episodeImports"):
            continue
        added = import_feed.bind_shared_assets_for_all_episodes(payload)
        if added:
            store.update_project(project["id"], payload=payload)
            updated += 1
    return updated


def backfill_project_asset_isolation():
    updated = 0
    for project_row in store.list_projects():
        project_id = str(project_row.get("id") or "")
        project = store.get_project(project_id)
        if not project:
            continue
        payload = project["payload"]
        if import_feed.detach_cross_project_reused_assets(payload, project_id):
            store.update_project(project_id, payload=payload)
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
            "Access-Control-Allow-Headers": "Content-Type,Authorization,X-File-Name,X-File-Type",
            "Access-Control-Expose-Headers": "Accept-Ranges,Content-Length,Content-Range,ETag,Last-Modified",
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
        response_headers = {
            **_security_headers(),
            **self._cors_headers(),
            **(headers or {}),
        }
        for key, value in response_headers.items():
            self.send_header(str(key), str(value))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _read_json(self):
        raw_length = str(self.headers.get("Content-Length") or "0").strip()
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise RequestBoundaryError(
                400,
                "Content-Length 非法",
                "INVALID_CONTENT_LENGTH",
            ) from exc
        if length < 0:
            raise RequestBoundaryError(
                400,
                "Content-Length 不能为负数",
                "INVALID_CONTENT_LENGTH",
            )
        if length <= 0:
            return {}
        if length > MAX_JSON_BODY_BYTES:
            raise RequestBoundaryError(
                413,
                f"JSON 请求超过 {MAX_JSON_BODY_BYTES // (1024 * 1024)}MB 上限",
                "JSON_BODY_TOO_LARGE",
            )
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise RequestBoundaryError(
                400,
                "请求体读取不完整",
                "JSON_BODY_TRUNCATED",
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = raw.decode("gbk")
            except UnicodeDecodeError:
                raise RequestBoundaryError(
                    400,
                    "请求体编码无法解析",
                    "JSON_BODY_ENCODING_INVALID",
                )
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RequestBoundaryError(
                400,
                f"JSON 格式错误：{exc.msg}",
                "JSON_PARSE_ERROR",
            ) from exc
        if _json_depth(value) > MAX_JSON_DEPTH:
            raise RequestBoundaryError(
                400,
                f"JSON 嵌套超过 {MAX_JSON_DEPTH} 层",
                "JSON_DEPTH_EXCEEDED",
            )
        return value

    def _send_upload_file(self, fpath: str, name: str):
        """Serve tenant media with session auth, CORS and single-range support."""
        stat = os.stat(fpath)
        size = stat.st_size
        etag = f'"{size:x}-{stat.st_mtime_ns:x}"'
        last_modified = formatdate(stat.st_mtime, usegmt=True)
        cache_headers = {
            "ETag": etag,
            "Last-Modified": last_modified,
            "Cache-Control": "private, max-age=31536000, immutable",
        }
        if self.command in {"GET", "HEAD"} and self.headers.get("If-None-Match", "").strip() == etag:
            self.send_response(304)
            for key, value in {
                **_security_headers(),
                **cache_headers,
                **self._cors_headers(),
            }.items():
                self.send_header(str(key), str(value))
            self.end_headers()
            return
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        range_header = str(self.headers.get("Range", "") or "").strip()
        if_range = str(self.headers.get("If-Range", "") or "").strip()
        if range_header and if_range and if_range != etag:
            range_header = ""
        start = 0
        end = max(0, size - 1)
        partial = False
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            if not match or size <= 0:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                for key, value in {
                    **_security_headers(),
                    **self._cors_headers(),
                }.items():
                    self.send_header(str(key), str(value))
                self.end_headers()
                return
            raw_start, raw_end = match.groups()
            if raw_start:
                start = int(raw_start)
                end = int(raw_end) if raw_end else size - 1
            else:
                suffix_length = int(raw_end or 0)
                start = max(0, size - suffix_length)
                end = size - 1
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                for key, value in {
                    **_security_headers(),
                    **self._cors_headers(),
                }.items():
                    self.send_header(str(key), str(value))
                self.end_headers()
                return
            end = min(end, size - 1)
            partial = True

        length = max(0, end - start + 1)
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        for key, value in cache_headers.items():
            self.send_header(key, value)
        self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        for key, value in {
            **_security_headers(),
            **self._cors_headers(),
        }.items():
            self.send_header(str(key), str(value))
        self.end_headers()
        if self.command == "HEAD" or length == 0:
            return
        with open(fpath, "rb") as stream:
            stream.seek(start)
            remaining = length
            while remaining > 0:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    # ---------- 文件上传 ----------
    # 允许的扩展名白名单：只收图片/音频/视频，避免上传目录被塞入可执行文件
    _UPLOAD_EXT = {
        ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif",
        ".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac",
        ".mp4", ".webm", ".mov",
    }
    _IMAGE_UPLOAD_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif"}
    _AUDIO_UPLOAD_EXT = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"}
    _VIDEO_UPLOAD_EXT = {".mp4", ".webm", ".mov"}
    _MAX_UPLOAD = 24 * 1024 * 1024   # 24MB
    _MAX_STREAM_UPLOAD_BY_KIND = {
        "image": 128 * 1024 * 1024,
        "audio": 256 * 1024 * 1024,
        "video": 1024 * 1024 * 1024,
    }

    @classmethod
    def _upload_kind_for_extension(cls, ext):
        if ext in cls._IMAGE_UPLOAD_EXT:
            return "image"
        if ext in cls._AUDIO_UPLOAD_EXT:
            return "audio"
        if ext in cls._VIDEO_UPLOAD_EXT:
            return "video"
        return ""

    @staticmethod
    def _detect_upload_kind(head, ext, declared_mime):
        ext = str(ext or "").lower()
        if ext in {".png"} and head.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image"
        if ext in {".jpg", ".jpeg"} and head.startswith(b"\xff\xd8\xff"):
            return "image"
        if ext == ".gif" and head.startswith((b"GIF87a", b"GIF89a")):
            return "image"
        if ext == ".bmp" and head.startswith(b"BM"):
            return "image"
        if ext == ".webp" and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return "image"
        if ext == ".avif" and head[4:8] == b"ftyp" and head[8:12] in {b"avif", b"avis"}:
            return "image"
        if ext == ".wav" and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
            return "audio"
        if ext == ".ogg" and head.startswith(b"OggS"):
            return "audio"
        if ext == ".flac" and head.startswith(b"fLaC"):
            return "audio"
        if ext == ".m4a" and head[4:8] == b"ftyp":
            return "audio"
        if ext == ".aac" and len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xF6) == 0xF0:
            return "audio"
        if ext == ".mp3" and (
            head.startswith(b"ID3")
            or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)
        ):
            return "audio"
        if ext in {".mp4", ".mov"} and head[4:8] == b"ftyp":
            return "video"
        if ext == ".webm" and head.startswith(b"\x1aE\xdf\xa3"):
            return "video"
        return ""

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

    def _handle_stream_upload(self):
        raw_name = urllib.parse.unquote(str(self.headers.get("X-File-Name") or "").strip())
        declared_mime = str(self.headers.get("X-File-Type") or "").strip().lower()
        try:
            content_length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            content_length = 0
        ext = os.path.splitext(raw_name)[1].lower()
        kind = self._upload_kind_for_extension(ext)
        if not kind and declared_mime:
            guessed_ext = (mimetypes.guess_extension(declared_mime) or "").lower()
            if guessed_ext in self._UPLOAD_EXT:
                ext = ".jpg" if guessed_ext == ".jpe" else guessed_ext
                kind = self._upload_kind_for_extension(ext)
        if not kind:
            return self._send(400, {"error": "不支持的文件类型"})
        max_bytes = self._MAX_STREAM_UPLOAD_BY_KIND[kind]
        if content_length <= 0:
            return self._send(400, {"error": "上传文件为空"})
        if content_length > max_bytes:
            return self._send(413, {
                "error": f"{kind} 文件超过上限 {max_bytes // (1024 * 1024)}MB",
            })
        if declared_mime and not declared_mime.startswith(f"{kind}/") and declared_mime != "application/octet-stream":
            return self._send(400, {"error": "文件类型与节点类型不匹配"})

        upload_dir = _upload_dir()
        os.makedirs(upload_dir, exist_ok=True)
        fname = secrets.token_hex(12) + ext
        fpath = os.path.join(upload_dir, fname)
        part_path = fpath + ".part"
        digest = hashlib.sha256()
        remaining = content_length
        head = b""
        try:
            with open(part_path, "wb") as handle:
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise RuntimeError("上传连接中断")
                    if len(head) < 64:
                        head += chunk[:64 - len(head)]
                    digest.update(chunk)
                    handle.write(chunk)
                    remaining -= len(chunk)
            detected_kind = self._detect_upload_kind(head, ext, declared_mime)
            if detected_kind != kind:
                raise RuntimeError("文件内容与扩展名不匹配")
            os.replace(part_path, fpath)
        except Exception as exc:
            try:
                if os.path.exists(part_path):
                    os.remove(part_path)
            except OSError:
                pass
            return self._send(400, {"error": str(exc)})
        return self._send(200, {
            "ok": True,
            "url": "/uploads/" + fname,
            "name": raw_name or fname,
            "size": content_length,
            "mime": declared_mime or mimetypes.guess_type(fname)[0] or "application/octet-stream",
            "sha256": digest.hexdigest(),
            "kind": kind,
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
        for key, value in _security_headers().items():
            self.send_header(key, value)
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
        if _auth_mode() != "accounts":
            local_hosts = {"127.0.0.1", "localhost", "::1"}
            if (
                _normalized_host(origin_host) in local_hosts
                and _normalized_host(request_host) in local_hosts
            ):
                return True
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
                "role": account.get("role") or "member",
                "isAdmin": account.get("role") == "admin",
            },
            headers={"Set-Cookie": self._session_cookie(session["token"])},
        )

    def _handle_auth_session(self):
        if _auth_mode() != "accounts":
            context = tenant_context.current()
            return self._send(200, {
                "authenticated": True,
                "username": "local",
                "tenantId": context.tenant_id if context else "legacy",
                "slug": context.slug if context else "local",
                "role": "admin",
                "isAdmin": True,
                "local": True,
            })
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
            "role": session.get("account_role") or "member",
            "isAdmin": session.get("account_role") == "admin",
        })

    def _current_session(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        morsel = cookie.get(CANVAS_SESSION_COOKIE)
        if morsel is None or not morsel.value:
            return None
        return tenant_registry.get_session(morsel.value)

    def _admin_session(self):
        session = self._current_session()
        if not session or session.get("account_role") != "admin":
            self._send(403, {
                "error": "只有管理员可以管理账号",
                "code": "ADMIN_REQUIRED",
            })
            return None
        return session

    def _handle_admin_create_account(self, body):
        if self._admin_session() is None:
            return
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        if len(password) < 8:
            return self._send(400, {"error": "密码至少需要 8 位", "code": "PASSWORD_TOO_SHORT"})
        try:
            created = tenant_bootstrap.register_user(username, password)
        except ValueError as exc:
            return self._send(400, {"error": str(exc), "code": "ACCOUNT_CREATE_INVALID"})
        account = created["account"]
        tenant = created["tenant"]
        store.record_audit_event(
            "account.create",
            actor=(self._current_session() or {}).get("account_username", ""),
            target_type="account",
            target_id=account["id"],
            details={"username": account["username"], "tenantId": tenant["id"]},
        )
        return self._send(200, {
            "ok": True,
            "accountId": account["id"],
            "username": account["username"],
            "tenantId": tenant["id"],
            "slug": tenant["slug"],
        })

    def _handle_admin_reset_account_password(self, account_id, body):
        session = self._admin_session()
        if session is None:
            return
        if str(session.get("account_id") or "") == str(account_id):
            return self._send(400, {
                "error": "请通过右上角的“修改密码”修改当前管理员密码",
                "code": "USE_CHANGE_PASSWORD",
            })
        password = str(body.get("password") or "")
        if len(password) < 8:
            return self._send(400, {"error": "新密码至少需要 8 位", "code": "PASSWORD_TOO_SHORT"})
        try:
            account = tenant_registry.set_account_password(account_id, password)
        except ValueError as exc:
            return self._send(404, {"error": str(exc), "code": "ACCOUNT_NOT_FOUND"})
        store.record_audit_event(
            "account.password_reset",
            actor=session.get("account_username") or "",
            target_type="account",
            target_id=account["id"],
            details={"username": account["username"]},
        )
        return self._send(200, {
            "ok": True,
            "accountId": account["id"],
            "username": account["username"],
        })

    def _handle_admin_set_account_status(self, account_id, body):
        session = self._admin_session()
        if session is None:
            return
        status = str(body.get("status") or "").strip().lower()
        if status not in {"active", "disabled"}:
            return self._send(400, {"error": "账号状态只能是 active 或 disabled"})
        account = tenant_registry.get_account_by_id(account_id)
        if not account:
            return self._send(404, {"error": "账号不存在", "code": "ACCOUNT_NOT_FOUND"})
        if status == "disabled" and str(session.get("account_id") or "") == str(account_id):
            return self._send(400, {"error": "不能停用当前登录的管理员账号"})
        if status == "disabled" and account.get("role") == "admin":
            active_admins = [
                item for item in tenant_registry.list_accounts()
                if item.get("role") == "admin" and item.get("status") == "active"
            ]
            if len(active_admins) <= 1:
                return self._send(400, {"error": "必须至少保留一个启用的管理员账号"})
        updated = tenant_registry.set_account_status(account_id, status)
        store.record_audit_event(
            "account.status",
            actor=session.get("account_username") or "",
            target_type="account",
            target_id=updated["id"],
            details={"username": updated["username"], "status": status},
        )
        return self._send(200, {
            "ok": True,
            "accountId": updated["id"],
            "username": updated["username"],
            "status": updated["status"],
        })

    def _handle_admin_list_accounts(self):
        if self._admin_session() is None:
            return
        rows = []
        for account in tenant_registry.list_accounts():
            tenant = tenant_registry.get_tenant_by_id(account["tenant_id"]) or {}
            rows.append({
                "id": account["id"],
                "username": account["username"],
                "role": account["role"],
                "status": account["status"],
                "tenantId": account["tenant_id"],
                "slug": tenant.get("slug") or "",
                "createdAt": account["created_at"],
                "lastLoginAt": account["last_login_at"],
                "lockedUntil": account["locked_until"],
            })
        return self._send(200, {"accounts": rows})

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

    def _handle_auth_change_password(self, body):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            cookie = SimpleCookie()
        morsel = cookie.get(CANVAS_SESSION_COOKIE)
        session = tenant_registry.get_session(morsel.value) if morsel is not None and morsel.value else None
        if not session or not session.get("account_id"):
            return self._send(401, {"error": "请先登录", "code": "ACCOUNT_AUTH_REQUIRED"})
        current_password = str(body.get("currentPassword") or "")
        new_password = str(body.get("newPassword") or "")
        if len(new_password) < 8:
            return self._send(400, {"error": "新密码至少需要 8 位", "code": "PASSWORD_TOO_SHORT"})
        if not tenant_registry.verify_account_password(session["account_id"], current_password):
            return self._send(400, {"error": "当前密码不正确", "code": "CURRENT_PASSWORD_INVALID"})
        tenant_registry.set_account_password(session["account_id"], new_password)
        renewed = tenant_registry.create_session(
            session["tenant_id"],
            account_id=session["account_id"],
            ttl_seconds=CANVAS_SESSION_TTL,
            user_agent=self.headers.get("User-Agent", ""),
            ip=self.client_address[0] if self.client_address else "",
        )
        return self._send(
            200,
            {"ok": True},
            headers={"Set-Cookie": self._session_cookie(renewed["token"])},
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

    def do_HEAD(self):
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
        if path == "/api/admin/accounts":
            return self._handle_admin_list_accounts()
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
            return self._send_upload_file(fpath, name)
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
        if path == "/api/audit":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                limit = int((query.get("limit") or ["200"])[0])
            except (TypeError, ValueError):
                return self._send(400, {"error": "limit 必须是整数"})
            return self._send(200, {"events": store.list_audit_events(limit)})
        if path == "/api/metrics":
            tenant_key = tenant_context.current_tenant_id()
            return self._send(200, {
                **store.runtime_metrics(),
                "uptimeSeconds": round(time.monotonic() - SERVER_STARTED_AT, 3),
                "queue": {
                    "tenantActive": GENERATION_QUEUE_ACTIVE_TENANTS.get(tenant_key, 0),
                    "tenantLimit": GENERATION_QUEUE_TENANT_LIMIT,
                    "providerActive": {
                        key.split("\x1f", 1)[1]: count
                        for key, count in GENERATION_QUEUE_ACTIVE_PROVIDERS.items()
                        if key.startswith(f"{tenant_key}\x1f")
                    },
                },
            })
        if path == "/api/budget":
            return self._send(200, generation_budget_status())
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
        m = re.match(r"^/api/projects/([^/]+)/versions/([^/]+)$", path)
        if m:
            pid, version_id = unquote(m.group(1)), unquote(m.group(2))
            try:
                return self._send(200, canvas_repository.repository.preview_version(
                    pid,
                    version_id,
                ))
            except KeyError as exc:
                return self._send(404, {"error": str(exc).strip("'")})
        m = re.match(r"^/api/projects/([^/]+)/versions$", path)
        if m:
            pid = unquote(m.group(1))
            try:
                project = canvas_repository.repository.get_project(pid)
                if not project:
                    return self._send(404, {"error": "项目不存在"})
                limit = 50
                if parsed.query:
                    query = urllib.parse.parse_qs(parsed.query)
                    limit = int((query.get("limit") or ["50"])[0])
                return self._send(200, {
                    "currentRevision": project["revision"],
                    "versions": canvas_repository.repository.list_versions(
                        pid,
                        limit,
                    ),
                })
            except (TypeError, ValueError):
                return self._send(400, {"error": "limit 必须是整数"})
            except KeyError as exc:
                return self._send(404, {"error": str(exc).strip("'")})
        m = re.match(r"^/api/projects/([^/]+)/assets/export$", path)
        if m:
            pid = unquote(m.group(1))
            proj = store.get_project(pid)
            if not proj:
                return self._send(404, {"error": "项目不存在"})
            payload = proj["payload"]
            assets = []
            for node in payload.get("nodes") or []:
                if node.get("type") not in {"image", "audio", "video"}:
                    continue
                meta = node.get("metadata") or {}
                params = meta.get("params") or {}
                code_match = re.search(r"图\s*(\d+)", str(node.get("title") or ""))
                versions = (
                    params.get("mediaVersions")
                    or params.get("videoVersions")
                    or []
                )
                episode_ids = [
                    str(group.get("id") or "")
                    for group in payload.get("episodeImports") or []
                    if node.get("id") in (group.get("nodeIds") or [])
                ]
                assets.append({
                    "id": node.get("id"),
                    "kind": node.get("type"),
                    "title": node.get("title") or "",
                    "code": f"图{code_match.group(1)}" if code_match else "",
                    "assetKey": meta.get("assetKey") or "",
                    "assetId": meta.get("assetId") or params.get("assetId") or "",
                    "seriesId": meta.get("seriesId") or params.get("seriesId") or "",
                    "variantKey": meta.get("variantKey") or params.get("variantKey") or "default",
                    "status": meta.get("status") or "idle",
                    "url": meta.get("content") or "",
                    "prompt": meta.get("prompt") or "",
                    "episodeIds": episode_ids,
                    "versions": versions,
                    "reusedAsset": meta.get("reusedAsset") or None,
                    "storage": meta.get("storage") or None,
                })
            return self._send(200, {
                "schema": "drama-canvas-assets/v1",
                "projectId": pid,
                "title": proj.get("title") or payload.get("title") or "",
                "revision": proj.get("revision"),
                "exportedAt": store._now_iso(),
                "episodes": [
                    {
                        "id": group.get("id") or "",
                        "title": group.get("title") or "",
                        **{
                            key: group.get(key)
                            for key in (
                                "sourceFeedName",
                                "sourceFeedPath",
                                "feedSha256",
                                "skillVersion",
                                "packageRevision",
                                "schemaVersion",
                                "importedAt",
                                "importWarnings",
                            )
                            if group.get(key) is not None
                        },
                    }
                    for group in payload.get("episodeImports") or []
                ],
                "assets": assets,
            })
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
            proj = canvas_repository.repository.get_project(pid)
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
        try:
            with scope:
                return self._do_post()
        except RequestBoundaryError as exc:
            self.close_connection = True
            return self._send(exc.status, {
                "error": str(exc),
                "code": exc.code,
            })

    def _do_post(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/upload/stream":
            return self._handle_stream_upload()
        body = self._read_json()
        if path == "/api/auth/login":
            return self._handle_auth_login(body)
        if path == "/api/admin/accounts":
            return self._handle_admin_create_account(body)
        m = re.match(r"^/api/admin/accounts/([^/]+)/reset-password$", path)
        if m:
            return self._handle_admin_reset_account_password(
                unquote(m.group(1)),
                body,
            )
        m = re.match(r"^/api/admin/accounts/([^/]+)/status$", path)
        if m:
            return self._handle_admin_set_account_status(
                unquote(m.group(1)),
                body,
            )
        if path == "/api/auth/logout":
            return self._handle_auth_logout()
        if path == "/api/auth/change-password":
            return self._handle_auth_change_password(body)
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
                store.record_audit_event(
                    "storage.update",
                    actor=(self._current_session() or {}).get("account_username", ""),
                    target_type="storage",
                    details={"provider": storage.get("provider"), "enabled": storage.get("enabled")},
                )
                return self._send(200, {"ok": True, "storage": storage})
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
        if path == "/api/budget":
            try:
                provider_catalog.save_budget_config(body)
                store.record_audit_event(
                    "budget.update",
                    actor=(self._current_session() or {}).get("account_username", ""),
                    target_type="budget",
                    details={"budget": provider_catalog.budget_config()},
                )
                return self._send(200, {"ok": True, **generation_budget_status()})
            except (TypeError, ValueError) as exc:
                return self._send(400, {"error": str(exc)})
        if path == "/api/storage/test":
            try:
                return self._send(200, {"ok": True, **tos_storage.test_connection()})
            except tos_storage.TosAccountOverdueError as exc:
                return self._send(402, {"error": str(exc), "code": "TOS_ACCOUNT_OVERDUE"})
            except Exception as exc:
                return self._send(400, {"error": str(exc)})
        if path == "/api/storage/imgbb/test":
            try:
                return self._send(200, {"ok": True, **imgbb_storage.test_connection()})
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
                pid = canvas_repository.repository.create_project(title)
            store.record_audit_event(
                "project.create",
                actor=(self._current_session() or {}).get("account_username", ""),
                target_type="project",
                target_id=pid,
                details={"title": title},
            )
            return self._send(200, {"id": pid, "title": title})
        m = re.match(r"^/api/projects/([^/]+)/episodes$", path)
        if m:
            pid = unquote(m.group(1))
            with project_write_lock(pid):
                project = store.get_project(pid)
                if not project:
                    return self._send(404, {"error": "项目不存在"})
                payload = project["payload"]
                groups = import_feed._ensure_episode_imports(payload)
                existing_ids = {
                    str(group.get("id") or "").strip().upper()
                    for group in groups
                    if isinstance(group, dict)
                }
                existing_numbers = {
                    int(found.group(1))
                    for episode_id in existing_ids
                    if (found := re.fullmatch(r"EP0*(\d{1,4})", episode_id))
                }
                raw_episode_id = str(
                    body.get("episodeId") or body.get("id") or ""
                ).strip()
                if raw_episode_id:
                    match = re.fullmatch(r"(?:EP)?0*(\d{1,4})", raw_episode_id, re.I)
                    if not match or int(match.group(1)) < 1:
                        return self._send(400, {
                            "error": "集编号必须是 1-9999，或 EP001 这种格式",
                            "code": "EPISODE_ID_INVALID",
                        })
                    episode_number = int(match.group(1))
                else:
                    episode_number = max(existing_numbers, default=0) + 1
                if episode_number > 9999:
                    return self._send(400, {
                        "error": "集编号不能超过 9999",
                        "code": "EPISODE_ID_INVALID",
                    })
                episode_id = f"EP{episode_number:03d}"
                if episode_number in existing_numbers:
                    return self._send(409, {
                        "error": f"{episode_id} 已存在",
                        "code": "EPISODE_EXISTS",
                    })
                title = str(body.get("title") or "").strip() or f"第{episode_number:02d}集"
                now = store._now_iso()
                groups.append({
                    "id": episode_id,
                    "title": title,
                    "nodeIds": [],
                    "videoNodeIds": [],
                    "connectionIds": [],
                    "importedAt": now,
                })
                groups.sort(key=lambda item: (
                    int(found.group(1))
                    if (found := re.fullmatch(r"EP0*(\d{1,4})", str(item.get("id") or "")))
                    else 99999,
                    str(item.get("id") or ""),
                ))
                payload["episodeImports"] = groups
                payload.setdefault("episodeLayouts", {}).setdefault(episode_id, {})
                store.update_project(pid, payload=payload)
                updated = store.get_project(pid)
                return self._send(200, {
                    "ok": True,
                    "episode": {"id": episode_id, "title": title},
                    "episodes": [
                        str(group.get("id") or "")
                        for group in groups
                        if isinstance(group, dict)
                    ],
                    "revision": updated["revision"] if updated else None,
                })
        m = re.match(r"^/api/projects/([^/]+)/versions/([^/]+)/restore$", path)
        if m:
            pid, version_id = unquote(m.group(1)), unquote(m.group(2))
            if_revision = body.get("if_revision")
            if if_revision is not None:
                try:
                    if_revision = int(if_revision)
                except (TypeError, ValueError):
                    return self._send(400, {"error": "if_revision 必须是整数"})
            try:
                return self._send(200, canvas_repository.repository.restore_version(
                    pid,
                    version_id,
                    if_revision=if_revision,
                ))
            except store.RevisionConflictError as exc:
                return self._send(409, {
                    "error": str(exc),
                    "code": "REVISION_CONFLICT",
                })
            except KeyError as exc:
                return self._send(404, {"error": str(exc).strip("'")})
        m = re.match(r"^/api/projects/([^/]+)/versions$", path)
        if m:
            pid = unquote(m.group(1))
            if_revision = body.get("if_revision")
            if if_revision is not None:
                try:
                    if_revision = int(if_revision)
                except (TypeError, ValueError):
                    return self._send(400, {"error": "if_revision 必须是整数"})
            try:
                version = canvas_repository.repository.create_version(
                    pid,
                    title=str(body.get("title") or "").strip() or None,
                    if_revision=if_revision,
                )
                project = canvas_repository.repository.get_project(pid)
                return self._send(200, {
                    "version": version,
                    "currentRevision": project["revision"] if project else None,
                })
            except store.RevisionConflictError as exc:
                return self._send(409, {
                    "error": str(exc),
                    "code": "REVISION_CONFLICT",
                })
            except KeyError as exc:
                return self._send(404, {"error": str(exc).strip("'")})
        m = re.match(r"^/api/projects/([^/]+)/duplicate$", path)
        if m:
            source_id = unquote(m.group(1))
            source = store.get_project(source_id)
            if not source:
                return self._send(404, {"error": "项目不存在"})
            with project_write_lock(source_id):
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
            store.record_audit_event(
                "config.update",
                actor=(self._current_session() or {}).get("account_username", ""),
                target_type="config",
                details={"catalogChanged": catalog_changed},
            )
            return self._send(200, provider_catalog.config_status())
        if path == "/api/providers":
            try:
                result = provider_catalog.upsert_provider(body)
                store.record_audit_event(
                    "provider.update",
                    actor=(self._current_session() or {}).get("account_username", ""),
                    target_type="provider",
                    target_id=str(body.get("id") or (body.get("provider") or {}).get("id") or ""),
                )
                return self._send(200, result)
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
        m = re.match(r"^/api/providers/([^/]+)/models$", path)
        if m and self.command == "POST":
            provider_id = unquote(m.group(1))
            try:
                return self._send(200, provider_catalog.discover_provider_models(provider_id))
            except ValueError as exc:
                return self._send(400, {"error": str(exc), "code": "PROVIDER_MODELS_INVALID"})
            except provider_catalog.ProviderRequestTimeoutError as exc:
                return self._send(504, {"error": str(exc), "code": "PROVIDER_MODELS_TIMEOUT"})
            except RuntimeError as exc:
                return self._send(502, {"error": str(exc), "code": "PROVIDER_MODELS_FETCH_FAILED"})
        if path == "/api/providers/test":
            provider_id = str(body.get("providerId") or body.get("id") or "")
            return self._send(200, provider_catalog.test_provider(provider_id))
        if path == "/api/models":
            try:
                result = provider_catalog.upsert_model(body)
                store.record_audit_event(
                    "model.update",
                    actor=(self._current_session() or {}).get("account_username", ""),
                    target_type="model",
                    target_id=str(body.get("id") or (body.get("model") or {}).get("id") or ""),
                )
                return self._send(200, result)
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
        if path == "/api/config/test":
            section = str(body.get("section") or body.get("providerId") or "")
            return self._send(200, provider_catalog.test_provider(section))
        m = re.match(r"^/api/projects/([^/]+)/import(?:/preview)?$", path)
        if m:
            pid = unquote(m.group(1))
            preview_only = path.endswith("/import/preview")
            with project_write_lock(pid):
                proj = store.get_project(pid)
                if not proj:
                    return self._send(404, {"error": "项目不存在"})
                feed_path = body.get("feed_path") or ""
                feed_json = body.get("feed_json")
                resolved_feed_path = ""
                if not feed_json and feed_path:
                    try:
                        resolved_feed_path = _resolve_feed_import_path(feed_path)
                    except PermissionError as exc:
                        return self._send(403, {
                            "error": str(exc),
                            "code": canvas_feed_contract.ErrorCode.FEED_PATH_FORBIDDEN,
                        })
                    except ValueError as exc:
                        return self._send(400, {
                            "error": str(exc),
                            "code": canvas_feed_contract.ErrorCode.FEED_PATH_FORBIDDEN,
                        })
                    if not os.path.isfile(resolved_feed_path):
                        return self._send(400, {"error": f"载荷文件不存在：{feed_path}"})
                    try:
                        with open(resolved_feed_path, encoding="utf-8-sig") as f:
                            feed_json = json.load(f)
                    except (OSError, json.JSONDecodeError) as exc:
                        return self._send(400, {
                            "error": f"载荷解析失败：{exc}",
                            "code": canvas_feed_contract.ErrorCode.FEED_PARSE_ERROR,
                            "issues": [],
                        })
                if not isinstance(feed_json, dict):
                    return self._send(400, {
                        "error": "载荷根节点必须是 JSON 对象",
                        "code": canvas_feed_contract.ErrorCode.FEED_PARSE_ERROR,
                        "issues": [],
                    })
                source_meta = body.get("source") if isinstance(body.get("source"), dict) else {}
                source_meta = {
                    **source_meta,
                    "name": source_meta.get("name") or (
                        os.path.basename(resolved_feed_path or feed_path)
                        if feed_path else ""
                    ),
                    "path": source_meta.get("path") or (
                        "" if tenant_context.multi_tenant_enabled() else resolved_feed_path
                    ),
                }
                plan = canvas_feed_contract.prepare_import(
                    proj["payload"],
                    feed_json,
                    pid,
                    int(proj.get("revision") or 0),
                    source=source_meta,
                )
                issue_payload = [canvas_feed_contract.issue_dict(item) for item in plan.errors]
                warning_payload = [canvas_feed_contract.issue_dict(item) for item in plan.warnings]
                if plan.errors:
                    store.record_audit_event(
                        "import.validate",
                        actor=(self._current_session() or {}).get("account_username", ""),
                        target_type="project",
                        target_id=pid,
                        status="failed",
                        details={
                            "preview": preview_only,
                            "episodeId": plan.episode_id,
                            "errors": len(plan.errors),
                        },
                    )
                    return self._send(422, {
                        "error": "导入校验失败，项目未被修改",
                        "code": "FEED_VALIDATION_FAILED",
                        "revision": plan.revision,
                        "issues": issue_payload,
                        "warnings": warning_payload,
                    })
                if preview_only:
                    return self._send(200, {
                        "dryRun": True,
                        "id": pid,
                        "revision": plan.revision,
                        "episodeId": plan.episode_id,
                        "summary": plan.summary,
                        "warnings": warning_payload,
                        "provenance": plan.provenance,
                    })
                if_revision = body.get("if_revision")
                try:
                    requested_revision = int(if_revision) if if_revision is not None else None
                except (TypeError, ValueError):
                    return self._send(400, {"error": "if_revision 必须是整数"})
                if requested_revision is not None and requested_revision != int(plan.revision):
                    retry_warning = canvas_feed_contract.ValidationIssue(
                        code=canvas_feed_contract.WarningCode.WARN_RETRIED_AFTER_REVISION,
                        message=(
                            f"预览 revision={requested_revision}，提交时已更新为 "
                            f"revision={plan.revision}，系统已重新预检一次"
                        ),
                        path="revision",
                    )
                    warning_payload.append(canvas_feed_contract.issue_dict(retry_warning))
                    for group in (plan.merged_payload or {}).get("episodeImports") or []:
                        if str(group.get("id") or "").upper() == str(plan.episode_id).upper():
                            group["importWarnings"] = warning_payload
                            break
                try:
                    commit_result = canvas_feed_contract.commit_import(
                        plan,
                        if_revision=plan.revision,
                    )
                except canvas_feed_contract.RevisionConflictError as exc:
                    return self._send(409, {
                        "error": str(exc),
                        "code": canvas_feed_contract.ErrorCode.REVISION_CONFLICT,
                        "revision": plan.revision,
                        "warnings": warning_payload,
                    })
                payload = plan.merged_payload
                readiness = import_feed.episode_readiness(payload, plan.episode_id)
                store.record_audit_event(
                    "import.commit",
                    actor=(self._current_session() or {}).get("account_username", ""),
                    target_type="project",
                    target_id=pid,
                    details={
                        "episodeId": plan.episode_id,
                        "revision": commit_result["revision"],
                        "nodes": len(payload["nodes"]),
                        "connections": len(payload["connections"]),
                        "warnings": len(warning_payload),
                    },
                )
            return self._send(200, {
                "ok": True,
                "id": pid,
                "title": commit_result["title"],
                "revision": commit_result["revision"],
                "episodeId": plan.episode_id,
                "nodes": len(payload["nodes"]),
                "connections": len(payload["connections"]),
                "episodes": int(plan.summary.get("episodesAfter") or 0),
                "reusedAssets": int(plan.summary.get("assetsReused") or 0),
                "reusedGeneratedAssets": int(plan.summary.get("assetsReusedGenerated") or 0),
                "summary": plan.summary,
                "warnings": warning_payload,
                "provenance": plan.provenance,
                "readiness": readiness,
                "reimportPolicy": import_feed.REIMPORT_IDENTITY_POLICY,
            })
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
            with project_write_lock(pid):
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
            meta = apply_generation_input(node, body)
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
            # Reserve budget and create the task atomically within this process.
            # Otherwise two simultaneous submissions can both pass the same
            # remaining-budget check before either task is persisted.
            with GENERATION_BUDGET_LOCK:
                budget_issue, budget_estimate = check_generation_budget(
                    node.get("type") or "",
                    params,
                    proj["payload"],
                )
                if budget_issue:
                    return self._send(402, {
                        **budget_issue,
                        "estimate": budget_estimate,
                    })
                reconcile_active_generation_task_for_node(pid, node["id"], node)
                task = store.create_generation_task(
                    pid, node["id"], title=node.get("title", ""), kind=node.get("type", ""),
                    provider=params.get("provider") or meta.get("provider") or "",
                    model=params.get("model") or meta.get("model") or "",
                    estimated_cost=(budget_estimate or {}).get("amount") or 0,
                    cost_currency=(budget_estimate or {}).get("currency") or "",
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
                store.record_audit_event(
                    "generation.submit",
                    actor=(self._current_session() or {}).get("account_username", ""),
                    target_type="generation_task",
                    target_id=str(task.get("id") or ""),
                    details={
                        "projectId": pid,
                        "nodeId": node["id"],
                        "kind": node.get("type"),
                        "provider": task.get("provider"),
                        "model": task.get("model"),
                        "estimatedCost": task.get("estimated_cost"),
                        "currency": task.get("cost_currency"),
                    },
                )
            provider_key = str(
                task.get("provider")
                or params.get("provider")
                or meta.get("provider")
                or "__default__"
            )
            try:
                slot = acquire_generation_slot(
                    task["id"],
                    provider_key,
                    task.get("model") or params.get("model") or meta.get("model"),
                    task.get("kind") or node.get("type"),
                )
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
            generation_started_at = store._now_iso()
            start_updates = {
                "status": "generating",
                "lastError": None,
                "taskId": None,
                "generationStartedAt": generation_started_at,
                "generationFinishedAt": None,
                "providerTaskStatus": "queued",
                "providerTaskPhase": "准备提交",
                "providerTaskProgress": 0,
                "_paramsPatch": params,
            }
            if isinstance(body.get("prompt"), str):
                start_updates["prompt"] = body["prompt"]
            commit_node_metadata(pid, nid, start_updates)
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
        try:
            with scope:
                return self._do_put()
        except RequestBoundaryError as exc:
            self.close_connection = True
            return self._send(exc.status, {
                "error": str(exc),
                "code": exc.code,
            })

    def _do_put(self):
        path = urlparse(self.path).path
        project_id = project_id_for_path(path)
        if project_id:
            with project_write_lock(project_id):
                return self._do_put_locked()
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
        # Manual graph connections are editable data, not localStorage-only UI
        # state. Replace only user edges while preserving imported/derived links.
        m = re.match(r"^/api/projects/([^/]+)/connections$", path)
        if m:
            pid = unquote(m.group(1))
            incoming = body.get("connections")
            if not isinstance(incoming, list):
                return self._send(400, {"error": "connections 字段缺失或不是数组"})
            # Whole-payload writes from a connection edit can overwrite a newer
            # prompt/media save. Re-read and retry with an optimistic revision so
            # only the connections field changes against the latest project.
            for _ in range(3):
                proj = store.get_project(pid)
                if not proj:
                    return self._send(404, {"error": "项目不存在"})
                nodes = proj["payload"].get("nodes", [])
                node_ids = {
                    str(node.get("id") or "")
                    for node in nodes
                    if isinstance(node, dict) and node.get("id")
                }
                existing = [
                    connection
                    for connection in (proj["payload"].get("connections") or [])
                    if isinstance(connection, dict)
                ]
                automatic = [
                    connection for connection in existing
                    if connection.get("source") != "user"
                    and not str(connection.get("id") or "").startswith("e-user-")
                ]
                automatic_pairs = {
                    (str(item.get("fromNodeId") or ""), str(item.get("toNodeId") or ""))
                    for item in automatic
                }
                manual = []
                manual_pairs = set()
                invalid = []
                for item in incoming:
                    if not isinstance(item, dict):
                        continue
                    from_id = str(item.get("fromNodeId") or "").strip()
                    to_id = str(item.get("toNodeId") or "").strip()
                    if not from_id or not to_id or from_id == to_id:
                        continue
                    if from_id not in node_ids or to_id not in node_ids:
                        invalid.append({"fromNodeId": from_id, "toNodeId": to_id})
                        continue
                    pair = (from_id, to_id)
                    if pair in automatic_pairs or pair in manual_pairs:
                        continue
                    manual_pairs.add(pair)
                    manual.append({
                        "id": str(item.get("id") or f"e-user-{len(manual) + 1}"),
                        "fromNodeId": from_id,
                        "toNodeId": to_id,
                        "source": "user",
                    })
                if invalid:
                    return self._send(400, {
                        "error": f"连线引用不存在节点：{len(invalid)} 条",
                        "code": "CONNECTION_NODE_MISSING",
                        "invalid": invalid[:20],
                    })
                old_manual_count = len(existing) - len(automatic)
                next_connections = [*automatic, *manual]
                if next_connections == existing:
                    return self._send(200, {
                        "ok": True,
                        "total": len(next_connections),
                        "manual": len(manual),
                        "removed": max(0, old_manual_count - len(manual)),
                    })
                payload = proj["payload"]
                payload["connections"] = next_connections
                try:
                    store.update_project(
                        pid,
                        payload=payload,
                        if_revision=proj["revision"],
                    )
                except store.RevisionConflictError:
                    continue
                return self._send(200, {
                    "ok": True,
                    "total": len(next_connections),
                    "manual": len(manual),
                    "removed": max(0, old_manual_count - len(manual)),
                })
            return self._send(409, {
                "error": "项目正在被其他操作更新，请稍后重试连线保存",
                "code": "CONNECTION_REVISION_CONFLICT",
            })
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
            write_owner = str(body.get("writeOwner") or "").strip()[:120]
            by_id = {node.get("id"): node for node in proj["payload"].get("nodes", [])}
            episode_id = str(body.get("episodeId") or "").upper()
            episode_layout = (
                proj["payload"].setdefault("episodeLayouts", {}).setdefault(episode_id, {})
                if episode_id
                else None
            )
            updates = []
            unchanged_ids = []
            missing_ids = []
            for item in incoming:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                position = item.get("position")
                node = by_id.get(item["id"])
                if node is None or not isinstance(position, dict):
                    if node is None:
                        missing_ids.append(item["id"])
                    continue
                position = {
                    "x": float(position.get("x") or 0),
                    "y": float(position.get("y") or 0),
                }
                try:
                    write_seq = int(item.get("seq") or 0)
                except (TypeError, ValueError):
                    write_seq = 0
                if (
                    write_owner
                    and write_seq
                    and node.get("positionWriteOwner") == write_owner
                    and int(node.get("positionWriteSeq") or 0) > write_seq
                ):
                    unchanged_ids.append(item["id"])
                    continue
                current_position = (
                    episode_layout.get(item["id"])
                    if episode_layout is not None
                    else node.get("position")
                )
                if (
                    isinstance(current_position, dict)
                    and current_position.get("x") == position["x"]
                    and current_position.get("y") == position["y"]
                ):
                    unchanged_ids.append(item["id"])
                    continue
                position_updated_at = store._now_iso()
                if episode_layout is not None:
                    episode_layout[item["id"]] = position
                else:
                    node["position"] = position
                if write_owner:
                    node["positionWriteOwner"] = write_owner
                    node["positionWriteSeq"] = write_seq
                node["positionUpdatedAt"] = position_updated_at
                node["updatedAt"] = position_updated_at
                updates.append({
                    "nodeId": item["id"],
                    "position": position,
                    "positionUpdatedAt": position_updated_at,
                    "updatedAt": position_updated_at,
                })
            if updates:
                store.update_project(pid, payload=proj["payload"])
            return self._send(200, {
                "ok": True,
                "updated": len(updates),
                "updates": updates,
                "unchanged": unchanged_ids,
                "missing": missing_ids,
            })
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
                    include_position=False,
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
            save_updates = []
            for item in incoming:
                if not isinstance(item, dict):
                    continue
                nid = item.get("id")
                if not nid:
                    continue
                node = by_id.get(nid)
                if node is None:
                    created_at = store._now_iso()
                    node = {
                        "id": nid,
                        "type": item.get("type") if item.get("type") in ("text", "image", "audio", "video") else "text",
                        "title": item.get("title") or "未命名节点",
                        "position": item.get("position") if isinstance(item.get("position"), dict) else {"x": 0, "y": 0},
                        "metadata": dict(item.get("metadata") or {}),
                        "createdAt": created_at,
                        "positionUpdatedAt": created_at,
                        "updatedAt": created_at,
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
                    if item.get("type") in ("text", "image", "audio", "video"):
                        node["type"] = item["type"]
                    node["updatedAt"] = store._now_iso()
                save_updates.append({
                    "id": nid,
                    "updatedAt": node.get("updatedAt"),
                    "positionUpdatedAt": node.get("positionUpdatedAt"),
                })
            episode_id = str(body.get("episodeId") or "").strip().upper()
            if episode_id:
                import_feed.attach_nodes_to_episode(
                    payload,
                    episode_id,
                    [item.get("id") for item in incoming if isinstance(item, dict)],
                )
            store.update_project(pid, payload=payload)
            return self._send(200, {
                "ok": True,
                "nodes": len(nodes_list),
                "created": created,
                "updates": save_updates,
            })
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
            include_position=False,
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
        path = urlparse(self.path).path
        project_id = project_id_for_path(path)
        if project_id:
            with project_write_lock(project_id):
                return self._do_delete_locked()
        with PROJECT_WRITE_LOCK:
            return self._do_delete_locked()

    def _do_delete_locked(self):
        path = urlparse(self.path).path
        m = re.match(r"^/api/projects/([^/]+)/episodes/([^/]+)$", path)
        if m:
            pid, episode_id = unquote(m.group(1)), unquote(m.group(2))
            project = store.get_project(pid)
            if not project:
                return self._send(404, {"error": "项目不存在"})
            payload = project["payload"]
            groups = [
                group for group in payload.get("episodeImports") or []
                if isinstance(group, dict)
            ]
            target = next(
                (
                    group for group in groups
                    if str(group.get("id") or "").strip().upper()
                    == episode_id.strip().upper()
                ),
                None,
            )
            if target is None:
                return self._send(404, {
                    "error": f"{episode_id.upper()} 不存在",
                    "code": "EPISODE_NOT_FOUND",
                })
            node_ids = [
                str(node_id)
                for node_id in target.get("nodeIds") or []
                if node_id
            ]
            if node_ids:
                return self._send(409, {
                    "error": f"{target['id']} 还有 {len(node_ids)} 个节点，请先移除或迁移后再删除集数",
                    "code": "EPISODE_NOT_EMPTY",
                    "nodes": len(node_ids),
                })
            payload["episodeImports"] = [
                group for group in groups if group is not target
            ]
            payload.setdefault("episodeLayouts", {}).pop(target["id"], None)
            store.update_project(pid, payload=payload)
            updated = store.get_project(pid)
            return self._send(200, {
                "ok": True,
                "removed": target["id"],
                "episodes": [
                    str(group.get("id") or "")
                    for group in payload["episodeImports"]
                    if isinstance(group, dict)
                ],
                "revision": updated["revision"] if updated else None,
            })
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
            removed_connection_ids = {
                c.get("id")
                for c in conns
                if c.get("id") and (c.get("fromNodeId") == nid or c.get("toNodeId") == nid)
            }
            for group in payload.get("episodeImports") or []:
                if not isinstance(group, dict):
                    continue
                group["nodeIds"] = [
                    node_id for node_id in group.get("nodeIds") or []
                    if node_id != nid
                ]
                group["videoNodeIds"] = [
                    node_id for node_id in group.get("videoNodeIds") or []
                    if node_id != nid
                ]
                group["connectionIds"] = [
                    connection_id for connection_id in group.get("connectionIds") or []
                    if connection_id not in removed_connection_ids
                ]
            for layout in (payload.get("episodeLayouts") or {}).values():
                if isinstance(layout, dict):
                    layout.pop(nid, None)
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
            deleted_id = unquote(m.group(1))
            store.delete_project(deleted_id)
            store.record_audit_event(
                "project.delete",
                actor=(self._current_session() or {}).get("account_username", ""),
                target_type="project",
                target_id=deleted_id,
            )
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
            if provider and is_provider_rate_limit_error(e):
                mark_generation_provider_rate_limited(
                    provider.get("id"),
                    provider_retry_after_seconds(e),
                )
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
                        task_id, status="interrupted", phase="重复提交已忽略",
                        error="该节点已有任务正在生成，本次重复提交已忽略",
                        finished_at=store._now_iso(),
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
                progress=10, error="", finished_at="",
                started_at=store._now_iso(),
            )
        try:
            return self._generate_once(pid, proj, node, task_id=task_id)
        except Exception as exc:
            if preserve_generation_outcome(task_id, exc):
                print(
                    f"[generation] client disconnected after result was persisted; "
                    f"task={task_id or '-'} error={exc}",
                    file=sys.stderr,
                )
                return None
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
                    error="", finished_at="",
                )
            ensure_provider_auth(provider)
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
                local_generated_url = persist_generated_media(result["url"], ".png")
                generated_at = store._now_iso()
                storage = archive_generated_image(
                    local_generated_url,
                    provider,
                    model,
                    str(result.get("taskId") or ""),
                    generated_at,
                )
                generated_url = local_generated_url
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
                imgbb_pending = imgbb_storage.is_ready()
                meta["imgbb"] = {
                    "status": "pending",
                    "source": {"localUrl": local_generated_url},
                } if imgbb_pending else {}
                meta["imageHostError"] = ""
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
                            error="", finished_at="",
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
                            error="", finished_at="",
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
                meta["source"] = {
                    "type": "generated",
                    "provider": str(provider.get("id") or ""),
                    "model": str(model.get("remoteModel") or model.get("id") or ""),
                    "generatedAt": meta["production"]["generatedAt"],
                }
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
            store.record_audit_event(
                "generation.complete",
                target_type="generation_task",
                target_id=str(task_id or node["id"]),
                status="failed",
                details={
                    "projectId": pid,
                    "nodeId": node["id"],
                    "kind": ntype,
                    "httpStatus": status,
                    "error": str(exc)[:500],
                },
            )
            return self._send(status, {
                "error": str(exc),
                "code": "GENERATION_INPUT_INVALID" if status == 400 else "GENERATION_PROVIDER_ERROR",
            })

        generated_updates = {
            key: meta.get(key) for key in (
                "status", "content", "lastError", "lastNote", "taskId",
                "voiceId", "provider", "model", "remoteModel", "production", "generatedAt",
                "generationStartedAt", "generationFinishedAt", "providerTaskStatus",
                "providerTaskPhase", "providerTaskProgress",
                "videoValidation", "actualDuration", "storage", "storageError",
                "imgbb", "imageHostError", "source",
            )
        }
        if ntype == "text":
            generated_updates["prompt"] = meta.get("prompt")
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
        if ntype == "image" and str((meta.get("imgbb") or {}).get("status") or "") == "pending":
            queue_generated_image_imgbb_mirror(
                pid,
                node["id"],
                str(meta.get("content") or ""),
                str(provider.get("id") or ""),
                str(model.get("remoteModel") or model.get("id") or ""),
                str(result.get("taskId") or ""),
                str(meta.get("generatedAt") or ""),
            )
        if task_id:
            store.update_generation_task(
                task_id, status="succeeded", phase="已完成", progress=100,
                error="", provider_task_id=str(result.get("taskId") or ""),
                finished_at=store._now_iso(),
            )
        store.record_audit_event(
            "generation.complete",
            target_type="generation_task",
            target_id=str(task_id or node["id"]),
            status="success",
            details={
                "projectId": pid,
                "nodeId": node["id"],
                "kind": ntype,
                "provider": provider.get("id"),
                "model": model.get("id"),
            },
        )
        response = {"nodeId": node["id"], "url": meta.get("content"), "status": "done"}
        if ntype == "text":
            response["text"] = meta.get("prompt")
        if ntype == "audio":
            response["voiceId"] = meta.get("voiceId", "")
        if ntype in {"image", "audio", "video"}:
            response["params"] = {
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
        isolated_projects = _run_for_active_tenants(backfill_project_asset_isolation)
        if isolated_projects:
            print(f"已隔离 {isolated_projects} 个项目的跨项目资产引用")
    except Exception as exc:
        print(f"[asset-isolation] 项目资产隔离失败：{exc}", file=sys.stderr)
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
        hosted = _run_for_active_tenants(backfill_image_host_versions)
        if hosted:
            print(f"已恢复 {hosted} 个 ImgBB 图片版本")
    except Exception as exc:
        print(f"[imgbb] 版本回填失败：{exc}", file=sys.stderr)
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
        repaired_failures = _run_for_active_tenants(reconcile_failed_generation_task_nodes)
        if repaired_failures:
            print(f"已恢复 {repaired_failures} 个供应商失败节点状态")
    except Exception as exc:
        print(f"[generation-tasks] 恢复失败节点状态失败：{exc}", file=sys.stderr)
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
