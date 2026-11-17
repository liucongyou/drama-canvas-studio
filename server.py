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
import queue
import re
import sys
import base64
import binascii
import gzip
import hashlib
import hmac
import inspect
import mimetypes
import secrets
import shutil
import signal
import subprocess
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
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
import generation_budget_guard
import generation_queue
import generation_outcome
import generation_acceptance
import explicit_references
import media_delivery
import media_storage
import observability
import operational_metrics
import provider_catalog
import provider_client
import pg_compat
import schema_migrations
import tos_storage
import tenant_bootstrap
import tenant_context
import tenant_registry
import tenant_security
import agent_gateway

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
# 后端根路径真正要端出去的前端产物。
# 历史包袱：web/index.html 是 10-23 的旧静态页（标题「戏剧画布工作流」），
# 真正的应用是 web-vimax（React+Vite，dev 8891，build 到 web-vimax/dist）。
# 以前 / 返回的是 web/index.html，用户从 8890 进来看见的是一个功能完全不同的旧页面。
# 现在优先 dist，找不到才退回旧页（不删旧文件，避免打断任何外部引用）。
WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web-vimax", "dist")

# 静态产物是首屏的关键路径：当前 CSS 583KB + JS 533KB 全走明文，弱网下光传输就
# 要好几秒。文本类资源 gzip 后通常剩 15%，且带指纹的文件内容永不变，压缩一次可
# 以一直复用。图片/字体/媒体本身已压缩，再 gzip 只会更大更慢，直接排除。
_STATIC_COMPRESSIBLE_EXT = {
    ".html", ".htm", ".js", ".mjs", ".css", ".json", ".svg", ".map",
    ".txt", ".xml", ".webmanifest",
}
_STATIC_COMPRESS_MIN_BYTES = 1024
DOWNLOAD_TICKET_TTL_SECONDS = max(
    60,
    min(
        3600,
        int(os.environ.get("CANVAS_DOWNLOAD_TICKET_TTL_SECONDS", "600")),
    ),
)


def request_reference_candidates(
    *,
    params: dict[str, Any],
    meta: dict[str, Any],
    node_type: str,
    node_id: str,
    connections: list[Any],
    nodes: list[Any],
) -> tuple[list[dict[str, Any]], bool]:
    params_refs = params.get("refs")
    meta_refs = meta.get("refs")
    removed_ref_ids = {
        str(node_id or "").strip()
        for node_id in (params.get("removedRefNodeIds") or [])
        if str(node_id or "").strip()
    }
    explicit_reference_list = (
        params_refs is not None or isinstance(meta_refs, list)
    )
    # meta.refs is the canonical slot list produced by import and the asset
    # picker. params.refs is often an older request snapshot. They must be
    # combined, otherwise a server-side linked background asset disappears as
    # soon as the card has a params.refs array.
    source_refs: list[Any] = []
    for collection in (meta_refs, params_refs):
        if not isinstance(collection, list):
            continue
        source_refs.extend(
            ref for ref in collection
            if isinstance(ref, dict)
        )
    candidates = []
    seen_candidate_ids = set()
    for source_ref in source_refs:
        if not isinstance(source_ref, dict):
            continue
        ref = dict(source_ref)
        key = (
            str(ref.get("nodeId") or "")
            or str(ref.get("assetId") or "")
            or str(ref.get("label") or "")
        )
        if key in seen_candidate_ids:
            continue
        if str(ref.get("nodeId") or "").strip() in removed_ref_ids:
            continue
        seen_candidate_ids.add(key)
        candidates.append(ref)
    for connection in connections:
        if not isinstance(connection, dict):
            continue
        if str(connection.get("toNodeId") or "") != str(node_id or ""):
            continue
        source_id = str(connection.get("fromNodeId") or "")
        if not source_id:
            continue
        if source_id in removed_ref_ids:
            continue
        source = next(
            (
                item for item in nodes
                if isinstance(item, dict)
                and str(item.get("id") or "") == source_id
            ),
            None,
        )
        if not source or str(source.get("type") or "") not in {
            "image", "video", "text",
        }:
            continue
        source_type = str(source.get("type") or "")
        if (
            node_type == "video"
            and explicit_reference_list
            and source_type == "video"
        ):
            # Canvas video links describe story order. An adjacent shot must
            # never become a provider reference unless it was explicitly
            # selected as a video slot.
            continue
        source_meta = source.get("metadata") or {}
        source_asset_id = str(source_meta.get("assetId") or "")
        key = source_id or source_asset_id
        if key in seen_candidate_ids:
            continue
        seen_candidate_ids.add(key)
        candidates.append({
            "nodeId": source_id,
            "assetId": source_asset_id,
            "seriesId": str(source_meta.get("seriesId") or ""),
            "variantKey": str(source_meta.get("variantKey") or "default"),
            "label": str(source.get("title") or ""),
        })
    return candidates, explicit_reference_list


def _download_ticket_key() -> bytes:
    return hashlib.sha256(
        b"canvas-download-v1\0" + tenant_security.master_key()
    ).digest()


def _download_content_disposition(download_name: str) -> str:
    raw = re.sub(
        r"[\x00-\x1f\x7f]+",
        "_",
        str(download_name or "").strip(),
    )
    raw = os.path.basename(raw.replace("\\", "/"))[:240] or "download.bin"
    stem, suffix = os.path.splitext(raw)
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", suffix or ""):
        suffix = ".bin"
    safe_ascii = re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        stem,
    ).strip("._") or "download"
    return (
        f'attachment; filename="{safe_ascii}{suffix}"; '
        f"filename*=UTF-8''{urllib.parse.quote(raw, safe='')}"
    )


def issue_media_download_ticket(
    *,
    project_id: str,
    node_id: str,
    version_id: str,
    download_name: str,
) -> str:
    project_id = str(project_id or "").strip()
    node_id = str(node_id or "").strip()
    version_id = str(version_id or "").strip()
    download_name = str(download_name or "").strip()
    if (
        not project_id
        or not node_id
        or not download_name
    ):
        raise ValueError("下载票据参数无效")
    payload = {
        "v": 1,
        "t": tenant_context.current_tenant_id(),
        "p": project_id,
        "node": node_id,
        "vid": version_id,
        "n": download_name[:240],
        "e": int(time.time()) + DOWNLOAD_TICKET_TTL_SECONDS,
        "j": secrets.token_hex(8),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).rstrip(b"=")
    signature = hmac.new(
        _download_ticket_key(),
        encoded,
        hashlib.sha256,
    ).digest()
    return (
        encoded.decode("ascii")
        + "."
        + base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    )


def verify_media_download_ticket(token: str) -> dict:
    raw = str(token or "").strip()
    if not raw or len(raw) > 2048 or "." not in raw:
        raise ValueError("下载票据无效")
    encoded, signature = raw.rsplit(".", 1)
    try:
        expected = hmac.new(
            _download_ticket_key(),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        actual = base64.urlsafe_b64decode(
            signature + "=" * ((4 - len(signature) % 4) % 4)
        )
        if not hmac.compare_digest(actual, expected):
            raise ValueError("下载票据签名无效")
        payload = json.loads(
            base64.urlsafe_b64decode(
                encoded + "=" * ((4 - len(encoded) % 4) % 4)
            ).decode("utf-8")
        )
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("下载票据无效") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("v") != 1
        or str(payload.get("t") or "")
        != tenant_context.current_tenant_id()
        or int(payload.get("e") or 0) < int(time.time())
    ):
        raise ValueError("下载票据已过期或不属于当前租户")
    return payload


def resolve_media_download_source(
    project_id: str,
    node_id: str,
    version_id: str = "",
) -> dict[str, Any]:
    """Resolve one node media version from durable project state.

    Never trust a browser-provided file path or object URL. The ticket can only
    name a project node/version; this function resolves the actual source.
    """
    project = store.get_project(project_id)
    if not project:
        raise KeyError("项目不存在")
    node = next(
        (
            item
            for item in (project.get("payload") or {}).get("nodes") or []
            if isinstance(item, dict)
            and str(item.get("id") or "") == str(node_id or "")
        ),
        None,
    )
    if not node:
        raise KeyError("节点不存在")
    meta = node.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    params = meta.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    versions = [
        item
        for item in (
            params.get("mediaVersions")
            or params.get("videoVersions")
            or []
        )
        if isinstance(item, dict)
    ]
    requested = str(version_id or "").strip()
    active_id = str(params.get("activeVersionId") or "").strip()
    version = next(
        (
            item
            for item in versions
            if requested
            and str(item.get("versionId") or "") == requested
        ),
        None,
    )
    if not version and requested:
        version = next(
            (
                item
                for item in versions
                if str(item.get("taskId") or "") == requested
            ),
            None,
        )
    if requested and not version:
        raise FileNotFoundError("请求的媒体版本不存在")
    if not requested and active_id:
        version = next(
            (
                item
                for item in versions
                if str(item.get("versionId") or "") == active_id
            ),
            None,
        )
    is_active = bool(
        version
        and (
            not active_id
            or str(version.get("versionId") or "") == active_id
        )
    )
    candidates = [
        str((version or {}).get("url") or ""),
        str(meta.get("content") or "") if is_active else "",
    ]
    local_filename = ""
    for candidate in candidates:
        if not candidate.startswith("/uploads/"):
            continue
        filename = os.path.basename(
            urllib.parse.unquote(candidate[len("/uploads/"):])
        )
        if (
            filename
            and filename not in {".", ".."}
            and os.path.isfile(os.path.join(_upload_dir(), filename))
        ):
            local_filename = filename
            break
    storage = (
        (version or {}).get("storage")
        if isinstance((version or {}).get("storage"), dict)
        else {}
    )
    if (
        (not storage or storage.get("status") != "stored")
        and is_active
        and isinstance(meta.get("storage"), dict)
    ):
        storage = meta.get("storage") or {}
    if (
        not local_filename
        and storage.get("status") == "stored"
        and storage.get("key")
    ):
        return {
            "node": node,
            "version": version or {},
            "localFilename": "",
            "storage": storage,
        }
    if not local_filename:
        raise FileNotFoundError("媒体文件不存在")
    return {
        "node": node,
        "version": version or {},
        "localFilename": local_filename,
        "storage": storage if isinstance(storage, dict) else {},
    }


# key=(绝对路径, mtime_ns, size)：重新构建后 mtime 变化，旧的压缩结果自然失效。
_STATIC_GZIP_CACHE: dict[tuple[str, int, int], bytes] = {}
_STATIC_GZIP_CACHE_MAX_BYTES = 24 * 1024 * 1024
_STATIC_GZIP_LOCK = threading.Lock()


def _accepts_gzip(header: str) -> bool:
    """解析 Accept-Encoding，尊重 `gzip;q=0` 这种显式拒绝。"""
    explicit_gzip: float | None = None
    wildcard: float | None = None
    for item in str(header or "").split(","):
        part = item.strip().lower()
        if not part:
            continue
        name, _, params = part.partition(";")
        coding = name.strip()
        if coding not in {"gzip", "*"}:
            continue
        quality = 1.0
        for param in params.split(";"):
            key, _, value = param.partition("=")
            if key.strip() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    quality = 0.0
        if coding == "gzip":
            explicit_gzip = quality if explicit_gzip is None else max(explicit_gzip, quality)
        else:
            wildcard = quality if wildcard is None else max(wildcard, quality)
    # An explicit gzip entry wins over wildcard, including `gzip;q=0, *;q=1`.
    if explicit_gzip is not None:
        return explicit_gzip > 0
    return wildcard is not None and wildcard > 0


def _compressible_content_type(content_type: str) -> bool:
    """只压文本类响应：图片/音视频/zip 本身已压缩，再 gzip 是纯浪费 CPU。"""
    base = str(content_type or "").split(";")[0].strip().lower()
    if not base:
        return False
    return (
        base.startswith("text/")
        or base == "application/json"
        or base.endswith("+json")
        or base == "application/xml"
        or base.endswith("+xml")
        or base in {"application/javascript", "application/x-javascript"}
    )


def _static_gzip(path: str, mtime_ns: int, size: int, body: bytes) -> bytes | None:
    """按 (路径, mtime, 大小) 缓存 gzip 结果。

    dist 的文件内容一旦构建就固定，同一个文件会被每个访客反复请求，压缩只做一次。
    缓存上限 24MB，超了就整体清空——dist 总共才 1.6MB，正常永远碰不到上限。
    """
    key = (path, mtime_ns, size)
    with _STATIC_GZIP_LOCK:
        cached = _STATIC_GZIP_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        packed = gzip.compress(body, compresslevel=6)
    except (OSError, ValueError):
        # 压缩失败不能影响静态资源本身，退回明文即可。
        return None
    if len(packed) >= len(body):
        # 已经压不动的资源（极小 JSON、已优化过的 svg）发原样更划算。
        return None
    with _STATIC_GZIP_LOCK:
        total = sum(len(value) for value in _STATIC_GZIP_CACHE.values())
        if total + len(packed) > _STATIC_GZIP_CACHE_MAX_BYTES:
            _STATIC_GZIP_CACHE.clear()
            return None
        _STATIC_GZIP_CACHE[key] = packed
    return packed

# 上传落地目录：data/uploads/<yyyymm>/<随机名>.<ext>
# 为什么必须真落盘：前端原来的 uploadWorkspaceFile 只是 URL.createObjectURL 造了个
# blob: 地址，刷新即失效 → 拖进来的图全变破图（用户实测）。落盘后给出稳定 URL，
# 刷新、换浏览器都能继续显示。
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "uploads")
PORT = int(os.environ.get("CANVAS_PORT", "8890"))
SERVER_STARTED_AT = time.monotonic()
PROJECT_WRITE_LOCK = threading.RLock()
PROJECT_LOCKS = canvas_repository.PROJECT_LOCKS
PROJECT_LOCKS_GUARD = canvas_repository.PROJECT_LOCKS_GUARD
GENERATION_BUDGET_LOCK = generation_budget_guard.Guard()
TASK_LEASE_SECONDS = 45
TASK_HEARTBEAT_SECONDS = 15
SHUTDOWN_DRAIN_SECONDS = max(
    0.0,
    float(os.environ.get("CANVAS_SHUTDOWN_DRAIN_SECONDS", "30")),
)
SAFE_LOCAL_READONLY = os.environ.get(
    "CANVAS_SAFE_LOCAL_READONLY",
    "",
).strip() == "1"
SERVER_STOPPING = threading.Event()
GENERATION_WORKERS_LOCK = threading.Lock()
GENERATION_WORKERS: set[threading.Thread] = set()
_PROVIDER_AUTH_CACHE: dict[tuple[str, str, str], tuple[float, str]] = {}
_PROVIDER_AUTH_CACHE_LOCK = threading.Lock()
RESUMED_VIDEO_TASKS: set[tuple[str, ...]] = set()
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
CANVAS_SHARE_COOKIE = os.environ.get(
    "CANVAS_SHARE_COOKIE",
    "canvas_share_session",
)
CANVAS_SHARE_HEADER = "X-Share-Token"
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
# Successful browser traffic is already captured by the product audit store
# and nginx access logs. Echoing every request to systemd journal caused
# hundreds of thousands of nearly identical lines per week.
CANVAS_ACCESS_LOG_MODE = os.environ.get(
    "CANVAS_ACCESS_LOG_MODE",
    "errors",
).strip().lower()
SCHEMA_MIGRATIONS_REQUIRED = os.environ.get(
    "CANVAS_SCHEMA_MIGRATIONS_REQUIRED",
    "",
).strip() == "1"
GENERATION_EXECUTION_MODE = os.environ.get(
    "CANVAS_GENERATION_EXECUTION_MODE",
    "inline",
).strip().lower()
if GENERATION_EXECUTION_MODE not in {"inline", "worker"}:
    raise RuntimeError(
        "CANVAS_GENERATION_EXECUTION_MODE 必须是 inline 或 worker"
    )
SKIP_STARTUP_BACKFILLS = os.environ.get(
    "CANVAS_SKIP_STARTUP_BACKFILLS",
    "",
).strip() == "1"


def release_commit():
    env_value = os.environ.get("CANVAS_RELEASE_COMMIT", "").strip()
    if env_value:
        return env_value
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "RELEASE_COMMIT",
    )
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def verify_required_schema_migrations():
    if not SCHEMA_MIGRATIONS_REQUIRED:
        return
    schemas = {}
    for tenant in _active_tenants():
        context = (
            tenant_context.make_context(tenant["id"], tenant["slug"])
            if tenant is not None
            else None
        )
        path = context.db_path if context is not None else tenant_context.current_db_path()
        schemas[pg_compat.schema_for_path(path, "canvas")] = "canvas"
    control_schema = pg_compat.schema_for_path("control.db", "control")
    schemas[control_schema] = "control"
    control = tenant_registry._connect()
    try:
        report = schema_migrations.verify_schema_set(control, schemas)
    finally:
        control.close()
    if not report.get("ok"):
        failures = [
            {"tenant": schema, **item}
            for schema, item in report.get("schemas", {}).items()
            if not item.get("ok")
        ]
        raise RuntimeError(
            "数据库迁移版本未就绪，拒绝启动："
            + ", ".join(
                f"{item.get('tenant')}:"
                f"{item.get('missing') or item.get('mismatched')}"
                for item in failures
            )
        )


def access_log_enabled(status_code: int) -> bool:
    if CANVAS_ACCESS_LOG_MODE in {"all", "1", "true", "yes"}:
        return True
    if CANVAS_ACCESS_LOG_MODE in {"off", "0", "false", "no", "none"}:
        return False
    # Default: keep failed requests and server errors, suppress expected 2xx
    # polling/playback traffic. This preserves diagnosis without journal noise.
    return int(status_code or 0) >= 400


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
        "dayReserved": day["reserved"],
        "dayCommitted": day["committed"],
        "monthReserved": month["reserved"],
        "monthCommitted": month["committed"],
        "dayReleasedTasks": day["releasedTasks"],
        "monthReleasedTasks": month["releasedTasks"],
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
    connect_sources = ["'self'", "https:", "http://127.0.0.1:8895", "http://localhost:8895"]
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


def _collect_for_active_tenants(func):
    """Run a list-producing function in every active tenant and combine it."""
    collected = []
    for tenant in _active_tenants():
        try:
            if tenant is None:
                with tenant_context.use_context(None):
                    result = func()
            else:
                with tenant_context.use_tenant(
                    tenant["id"],
                    tenant["slug"],
                ):
                    result = func()
        except Exception as exc:
            tenant_label = tenant["slug"] if tenant else "legacy"
            print(
                f"[tenant:{tenant_label}] {func.__name__} 失败：{exc}",
                file=sys.stderr,
            )
            continue
        if isinstance(result, list):
            collected.extend(result)
    return collected


def _admin_tenant_audit_context(tenant_id):
    if not tenant_context.multi_tenant_enabled():
        return nullcontext()
    tenant = tenant_registry.get_tenant_by_id(tenant_id)
    if not tenant:
        raise ValueError("租户不存在")
    return tenant_context.use_tenant(tenant["id"], tenant["slug"])


def _resolve_share_scope(token):
    if not tenant_context.multi_tenant_enabled():
        share = store.resolve_project_share(token)
        if not share:
            return None
        return nullcontext(), share
    for tenant in _active_tenants():
        with tenant_context.use_tenant(tenant["id"], tenant["slug"]):
            share = store.resolve_project_share(token)
        if share:
            return tenant_context.use_tenant(tenant["id"], tenant["slug"]), share
    return None


def _public_share_payload(share, project):
    if not share or not project:
        return None
    role = str(share.get("role") or "viewer")
    can_write = role == "editor"
    return {
        "share": {
            "projectId": share["projectId"],
            "expiresAt": share["expiresAt"],
            "role": role,
            "label": str(share.get("label") or ""),
            "canWrite": can_write,
            "readOnly": not can_write,
        },
        "project": project_project_media_urls(project),
    }


def _audit_query_from_url(parsed):
    query = urllib.parse.parse_qs(parsed.query)

    def value(name, max_len=200):
        raw = (query.get(name) or [""])[0]
        return str(raw or "").strip()[:max_len]

    try:
        limit = max(1, min(int(value("limit", 8) or "100"), 500))
    except (TypeError, ValueError):
        raise ValueError("limit 必须是整数")
    return {
        "limit": limit,
        "project_id": value("projectId", 180),
        "action": value("action", 120),
        "actor": value("actor", 120),
        "status": value("status", 32),
        "since": value("since", 64),
        "until": value("until", 64),
        "before_id": value("beforeId", 180),
        "request_id": value("requestId", 64),
    }


def _resume_expired_provider_tasks(expired):
    """Resume persisted provider tasks that are still waiting for an outcome."""
    started = 0
    # A task that has just expired was already claimed by this reconciliation
    # pass. Only recover rows that are not the same local task twice.
    candidates = list(expired or [])
    seen = set()
    for item in candidates:
        item_id = str(item.get("id") or "")
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        provider_task_id = str(
            item.get("providerTaskId")
            or item.get("provider_task_id")
            or ""
        ).strip()
        project_id = str(
            item.get("projectId")
            or item.get("project_id")
            or ""
        ).strip()
        node_id = str(
            item.get("nodeId")
            or item.get("node_id")
            or ""
        ).strip()
        if not provider_task_id or not project_id or not node_id:
            continue
        task = store.get_generation_task(item_id)
        if (
            not task
            or task.get("status") not in {"running", "interrupted", "failed"}
        ):
            continue
        if _recover_existing_local_task_result(
            project_id,
            node_id,
            task,
        ):
            continue
        if generation_outcome.is_provider_terminal_failure(task):
            continue
        project = store.get_project(project_id)
        node = next(
            (
                candidate
                for candidate in (project or {}).get(
                    "payload",
                    {},
                ).get("nodes", [])
                if candidate.get("id") == node_id
            ),
            None,
        )
        if not node:
            continue
        current_meta = node.get("metadata") or {}
        if (
            str(current_meta.get("status") or "") == "done"
            and str(current_meta.get("content") or "").startswith("/uploads/")
        ):
            continue
        if str(current_meta.get("taskId") or "") != provider_task_id:
            commit_node_metadata(project_id, node_id, {
                "status": "generating",
                "taskId": provider_task_id,
                "providerTaskStatus": "queued",
                "providerTaskPhase": "等待恢复",
                "lastError": "",
                "generationFinishedAt": "",
            })
        task_kind = str(task.get("kind") or "").strip()
        if task_kind not in {"image", "video"}:
            continue
        try:
            provider, model, _ = provider_catalog.resolve_model(
                task.get("provider"),
                task.get("model"),
                task_kind,
            )
            if task_kind == "image" and str(model.get("adapter") or "") != "newapi_image":
                continue
        except Exception:
            continue
        key = (
            str(item.get("tenantId") or "legacy"),
            project_id,
            node_id,
            provider_task_id,
        )
        with RESUMED_VIDEO_TASKS_LOCK:
            if key in RESUMED_VIDEO_TASKS:
                continue
            RESUMED_VIDEO_TASKS.add(key)

        tenant_id = item.get("tenantId")
        tenant_slug = item.get("tenantSlug") or "local"

        def run_resumer(
            tenant_id=tenant_id,
            tenant_slug=tenant_slug,
            project_id=project_id,
            node_id=node_id,
            provider_task_id=provider_task_id,
            provider=provider,
            model=model,
            task_kind=task_kind,
        ):
            try:
                _run_in_tenant(
                    tenant_id,
                    tenant_slug,
                    _resume_image_task if task_kind == "image" else _resume_video_task,
                    project_id,
                    node_id,
                    provider_task_id,
                    provider,
                    model,
                )
            except Exception as exc:
                print(
                    f"[generation-resume] expired task failed "
                    f"node={node_id} task={provider_task_id} error={exc}",
                    file=sys.stderr,
                )
            finally:
                with RESUMED_VIDEO_TASKS_LOCK:
                    RESUMED_VIDEO_TASKS.discard(key)

        try:
            threading.Thread(
                target=run_resumer,
                name=f"generation-resume-{node_id}",
                daemon=True,
            ).start()
            started += 1
        except Exception as exc:
            with RESUMED_VIDEO_TASKS_LOCK:
                RESUMED_VIDEO_TASKS.discard(key)
            print(
                f"[generation-resume] expired task start failed "
                f"node={node_id} task={provider_task_id} error={exc}",
                file=sys.stderr,
            )
    return started


def _start_image_resumer(project_id, node_id, provider_task_id, provider, model):
    """Start one background query for a just-submitted New API image task."""
    project_id = str(project_id or "").strip()
    node_id = str(node_id or "").strip()
    provider_task_id = str(provider_task_id or "").strip()
    if not project_id or not node_id or not provider_task_id:
        return False
    key = (project_id, node_id, provider_task_id)
    with RESUMED_VIDEO_TASKS_LOCK:
        if key in RESUMED_VIDEO_TASKS:
            return False
        RESUMED_VIDEO_TASKS.add(key)

    def run():
        try:
            # The reservation guards only thread creation. Release it before
            # entering the actual poller; the poller owns its own provider-task
            # key. Keeping both made the poller see its own reservation and
            # return immediately, so a successful provider task was never fetched.
            with RESUMED_VIDEO_TASKS_LOCK:
                RESUMED_VIDEO_TASKS.discard(key)
            _resume_image_task(project_id, node_id, provider_task_id, provider, model)
        except Exception as exc:
            print(
                f"[generation-resume] immediate image poller failed "
                f"node={node_id} task={provider_task_id} error={exc}",
                file=sys.stderr,
            )
        finally:
            with RESUMED_VIDEO_TASKS_LOCK:
                RESUMED_VIDEO_TASKS.discard(key)

    try:
        threading.Thread(
            target=run,
            name=f"generation-resume-{node_id}",
            daemon=True,
        ).start()
        return True
    except Exception:
        with RESUMED_VIDEO_TASKS_LOCK:
            RESUMED_VIDEO_TASKS.discard(key)
        raise


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
    retry_seconds = provider_retry_after_seconds(None, retry_after)
    until = time.monotonic() + retry_seconds
    with GENERATION_QUEUE_CONDITION:
        GENERATION_QUEUE_PROVIDER_COOLDOWN_UNTIL[composite_key] = max(
            GENERATION_QUEUE_PROVIDER_COOLDOWN_UNTIL.get(composite_key, 0),
            until,
        )
        GENERATION_QUEUE_CONDITION.notify_all()
    if pg_compat.postgres_enabled():
        try:
            generation_queue.mark_provider_cooldown(
                provider_key,
                retry_seconds,
            )
        except Exception as exc:
            print(
                "[generation] persist provider cooldown failed: "
                f"provider={provider_key} error={exc}",
                file=sys.stderr,
            )


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
                    store.finish_generation_task(
                        task_id,
                        status="failed",
                        phase="排队超时",
                        error="生成任务排队超时，请稍后重新提交",
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


def persist_generated_media(url, fallback_ext, *, provider=None, model=None):
    """Keep generated media usable after a provider's temporary URL expires."""
    raw = str(url or "").strip()
    if not raw or raw.startswith(("/uploads/", "data:")):
        return raw
    key = ""
    if (
        provider
        and str((model or {}).get("adapter") or "") in {
            "canvaspro_video",
            "openai_video",
            "newapi_image",
        }
    ):
        key = provider_catalog.provider_key(provider)
    return provider_client.save_generated_url(raw, fallback_ext, key=key)


_EVENT_SUBSCRIBERS: dict[str, list[queue.Queue]] = {}
_EVENT_SUBSCRIBERS_LOCK = threading.Lock()
_EVENT_TENANT_CONNECTIONS: dict[str, int] = {}
_EVENT_SUBSCRIBER_TENANTS: dict[queue.Queue, str] = {}
SSE_TENANT_LIMIT = max(
    1,
    int(os.environ.get("CANVAS_SSE_TENANT_LIMIT", "16")),
)
SSE_PROJECT_LIMIT = max(
    1,
    int(os.environ.get("CANVAS_SSE_PROJECT_LIMIT", "4")),
)


def _event_tenant_key():
    context = tenant_context.current()
    if context is None:
        return "legacy"
    return str(context.tenant_id or context.slug or "legacy")


def _acquire_event_subscriber(project_id):
    project_key = str(project_id or "").strip()
    if not project_key:
        return None, "SSE_PROJECT_REQUIRED"
    tenant_key = _event_tenant_key()
    with _EVENT_SUBSCRIBERS_LOCK:
        subscribers = _EVENT_SUBSCRIBERS.get(project_key) or []
        tenant_count = int(_EVENT_TENANT_CONNECTIONS.get(tenant_key) or 0)
        if (
            len(subscribers) >= SSE_PROJECT_LIMIT
            or tenant_count >= SSE_TENANT_LIMIT
        ):
            return None, "SSE_CONNECTION_LIMIT"
        subscriber: queue.Queue = queue.Queue(maxsize=256)
        _EVENT_SUBSCRIBERS.setdefault(project_key, []).append(subscriber)
        _EVENT_SUBSCRIBER_TENANTS[subscriber] = tenant_key
        _EVENT_TENANT_CONNECTIONS[tenant_key] = tenant_count + 1
        return subscriber, ""


def _release_event_subscriber(project_id, subscriber):
    project_key = str(project_id or "").strip()
    if not project_key or subscriber is None:
        return
    with _EVENT_SUBSCRIBERS_LOCK:
        tenant_key = _EVENT_SUBSCRIBER_TENANTS.pop(subscriber, None)
        subscribers = _EVENT_SUBSCRIBERS.get(project_key) or []
        if subscriber in subscribers:
            subscribers.remove(subscriber)
        if not subscribers:
            _EVENT_SUBSCRIBERS.pop(project_key, None)
        if not tenant_key:
            return
        tenant_count = int(_EVENT_TENANT_CONNECTIONS.get(tenant_key) or 0)
        if tenant_count <= 1:
            _EVENT_TENANT_CONNECTIONS.pop(tenant_key, None)
        else:
            _EVENT_TENANT_CONNECTIONS[tenant_key] = tenant_count - 1


def _project_event_episode_id(project_id, node_id):
    """Resolve event scope from durable node ownership without guessing."""
    node_id = str(node_id or "").strip()
    if not node_id:
        return ""
    try:
        row = store.get_projection_node(project_id, node_id)
    except Exception:
        return ""
    if not isinstance(row, dict):
        return ""
    metadata = row.get("metadata")
    return str(
        row.get("episodeId")
        or (metadata.get("episodeId") if isinstance(metadata, dict) else "")
        or ""
    ).strip().upper()


def publish_project_event(project_id, kind, node_id="", payload=None):
    """事件先落库，再唤醒订阅者。

    只做内存广播是不够的：页面刷新、网络抖动、切项目都会断线一段时间，
    断线期间发生的变化必须能按 Last-Event-ID 补回来，否则卡片会永久停在旧状态。
    """
    scoped_payload = dict(payload or {})
    if node_id and not scoped_payload.get("episodeId"):
        episode_id = _project_event_episode_id(project_id, node_id)
        if episode_id:
            scoped_payload["episodeId"] = episode_id
    try:
        event_id = store.append_event(project_id, kind, node_id, scoped_payload)
    except Exception as exc:
        print(f"[events] 写入事件失败：{exc}", file=sys.stderr)
        return 0
    with _EVENT_SUBSCRIBERS_LOCK:
        subscribers = list(_EVENT_SUBSCRIBERS.get(project_id) or ())
    for subscriber in subscribers:
        try:
            subscriber.put_nowait(event_id)
        except queue.Full:
            # 订阅者积压说明它自己会去补历史，丢一次通知不影响正确性
            pass
    return event_id


def _notify_project_event_subscribers(project_id, event_id):
    if not event_id:
        return
    with _EVENT_SUBSCRIBERS_LOCK:
        subscribers = list(_EVENT_SUBSCRIBERS.get(project_id) or ())
    for subscriber in subscribers:
        try:
            subscriber.put_nowait(event_id)
        except queue.Full:
            pass


def _roll_forward_next_project_event(project_id, node_id, after_event_id=0):
    """Publish the next durable event if an interrupted transaction left one pending."""
    try:
        events = store.list_events(project_id, int(after_event_id or 0), 8)
    except Exception:
        return 0
    pending = next(
        (event for event in events if event.get("nodeId") == node_id),
        None,
    )
    if not pending:
        return 0
    if pending.get("kind") == "media":
        publish_project_event(
            project_id,
            "media",
            node_id,
            dict(pending.get("payload") or {}),
        )
    return int(pending.get("id") or 0)


def ensure_project_event_subscriber(project_id):
    """Wake a project's SSE readers after a durable write rolls forward."""

    def run():
        try:
            events = store.list_events(project_id, 0, 1)
            if events:
                _notify_project_event_subscribers(project_id, events[-1]["id"])
        except Exception as exc:
            print(
                f"[events] wake after recovery failed: {exc}",
                file=sys.stderr,
            )

    threading.Thread(
        target=_run_in_tenant,
        args=(
            tenant_context.current_tenant_id(),
            tenant_context.current_slug(),
            run,
        ),
        name=f"event-wake-{project_id}",
        daemon=True,
    ).start()


def _generation_terminal_event_payload(node_id, updates, *, task=None):
    payload = {
        key: updates[key]
        for key in (
            "status", "content", "lastError", "providerTaskStatus",
            "providerTaskPhase", "providerTaskProgress", "generationFinishedAt",
            "providerResultUrl", "finalizePending", "storage", "storageError", "videoValidation",
            "actualDuration", "assetId", "seriesId", "variantKey", "semanticCode",
        )
        if key in (updates or {})
    }
    if "content" in updates:
        payload["url"] = updates.get("content") or ""
    if "lastError" in updates:
        payload["error"] = updates.get("lastError") or ""
    if "text" in updates:
        payload["text"] = updates["text"]
    params_patch = updates.get("_paramsPatch")
    if isinstance(params_patch, dict):
        if "mediaVersions" in params_patch:
            payload["mediaVersions"] = params_patch["mediaVersions"]
        if "mediaVersionIndex" in params_patch:
            payload["mediaVersionIndex"] = params_patch["mediaVersionIndex"]
        if "thumbUrl" in params_patch:
            payload["thumbUrl"] = params_patch["thumbUrl"]
    if task:
        payload["taskId"] = str(task.get("id") or "")
        payload["taskStatus"] = str((task or {}).get("status") or "")
        payload["taskPhase"] = str((task or {}).get("phase") or "")
        payload["taskFinishedAt"] = str((task or {}).get("finished_at") or "")
    payload["nodeId"] = node_id
    return payload


def commit_generation_terminal(
    project_id,
    node_id,
    node_updates,
    *,
    task_id="",
    task_updates=None,
    lease_owner=None,
    accept_durable_late_result=False,
):
    """Atomically persist the node, task and event, then wake SSE readers."""
    task_updates = dict(task_updates or {})
    if task_updates:
        task_updates.setdefault("finished_at", "")
        task_updates.setdefault("node_written_at", "")
        # A terminal task owns no execution lease. Persist this in the same
        # transaction as the node write; otherwise a restarted reconciler can
        # mistake the finished row for a live worker and stall recovery.
        task_updates["lease_owner"] = ""
        task_updates["lease_expires_at"] = None
    payload = _generation_terminal_event_payload(
        node_id,
        node_updates,
        task={"id": task_id, **task_updates},
    )
    kind = "media"
    result = store.commit_generation_terminal(
        project_id,
        node_id,
        node_updates,
        task_id=task_id,
        task_updates=task_updates,
        event_kind=kind,
        event_payload=payload,
        lease_owner=lease_owner,
        accept_durable_late_result=accept_durable_late_result,
    )
    if result.get("committed"):
        persisted_task = result.get("task") or {}
        event_payload = dict(result.get("eventPayload") or payload)
        event_payload["eventId"] = int(result.get("eventId") or 0)
        if persisted_task:
            event_payload["taskStatus"] = str(
                persisted_task.get("status") or ""
            )
            event_payload["taskPhase"] = str(
                persisted_task.get("phase") or ""
            )
            event_payload["taskFinishedAt"] = str(
                persisted_task.get("finished_at") or ""
            )
            event_payload["taskUpdatedAt"] = str(
                persisted_task.get("updated_at") or ""
            )
        result["eventPayload"] = event_payload
        _notify_project_event_subscribers(project_id, result.get("eventId"))
    return result


def mark_provider_ready(project_id, node, meta, task_id, provider_url, ready_at):
    """供应商已产出可用地址时，立刻把节点写成 done。

    本地落盘 + 云存储转存动辄几秒（视频几十 MB 更久），而用户等的是"东西出来
    了没有"。先把供应商地址挂上去让卡片亮起来，落盘完成后再用自有地址覆盖一次；
    若覆盖失败，供应商地址依然可用，不会比原来更差。
    """
    url = str(provider_url or "").strip()
    if not url:
        return False
    updates = {
        "content": url,
        "status": "done",
        "generatedAt": ready_at,
        "generationFinishedAt": ready_at,
        "providerUrl": url,
        "providerResultUrl": url,
        "providerTaskStatus": "completed",
        "providerTaskPhase": "已完成",
        "providerTaskProgress": 100,
        "finalizePending": False,
    }
    meta.update(updates)
    commit_node_metadata(project_id, node["id"], updates)
    publish_project_event(project_id, "media", node["id"], {
        "status": "done",
        "url": url,
        "finalizePending": False,
        "providerTaskStatus": "completed",
        "providerTaskPhase": "已完成",
        "providerTaskProgress": 100,
        "generationFinishedAt": ready_at,
    })
    if task_id:
        store.update_generation_task(
            task_id, status="succeeded", progress=100,
            provider_finished_at=ready_at, phase="已完成",
            error="", finished_at=ready_at,
        )
    return True


def archive_generated_image(generated_url, provider, model, task_id, generated_at):
    """Archive the untouched generated file before a temporary Ark URL expires."""
    storage_cfg = provider_catalog.storage_config()
    if (
        not storage_cfg.get("enabled")
        or not media_storage.is_ready(storage_cfg)
        or not storage_cfg.get("auto_persist_images", True)
    ):
        return None
    try:
        return media_storage.persist_local_url(
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
        print(f"[storage] failed to archive generated image {generated_url[:120]}: {exc}", file=sys.stderr)
        return {"status": "failed", "error": str(exc)}


def finalize_generated_image(
    generated_url,
    provider,
    model,
    task_id,
    generated_at,
):
    """Cache a completed image without changing the generation outcome."""
    local_url = persist_generated_media(generated_url, ".png")
    storage = archive_generated_image(
        local_url,
        provider,
        model,
        task_id,
        generated_at,
    )
    return {
        "localUrl": local_url,
        "storage": storage if isinstance(storage, dict) else {},
    }


def queue_generated_image_finalize(
    project_id,
    node_id,
    generated_url,
    provider,
    model,
    task_id,
    generated_at,
):
    """Run image caching after success is visible; never downgrade it to failed."""
    context = tenant_context.current()
    tenant_id = context.tenant_id if context else None
    tenant_slug = context.slug if context else "local"

    def run():
        try:
            result = finalize_generated_image(
                generated_url,
                provider,
                model,
                task_id,
                generated_at,
            )
        except Exception as exc:
            result = {
                "storage": {},
                "storageError": str(exc),
            }
        updates = {
            "storage": result.get("storage") or {},
            "storageError": str((result.get("storage") or {}).get("error") or ""),
        }
        local_url = str(result.get("localUrl") or "")
        if local_url.startswith("/uploads/"):
            updates["content"] = local_url
        commit_node_metadata(project_id, node_id, updates)

    threading.Thread(
        target=_run_in_tenant,
        args=(tenant_id, tenant_slug, run),
        name=f"image-finalize-{node_id}",
        daemon=True,
    ).start()
    return True


def queue_generated_image_tos_archive(
    project_id,
    node_id,
    local_url,
    provider,
    model,
    task_id,
    generated_at,
):
    """Archive a locally cached image without delaying the visible result."""
    context = tenant_context.current()
    tenant_id = context.tenant_id if context else None
    tenant_slug = context.slug if context else "local"

    def run():
        storage = archive_generated_image(
            local_url,
            provider,
            model,
            task_id,
            generated_at,
        )
        storage = storage if isinstance(storage, dict) else {}
        storage_error = str(storage.get("error") or "")
        with project_write_lock(project_id):
            current = store.get_project(project_id)
            if not current:
                return
            current_node = next(
                (
                    item for item in current["payload"].get("nodes", [])
                    if item.get("id") == node_id
                ),
                None,
            )
            if not current_node:
                return
            meta = current_node.setdefault("metadata", {})
            params = meta.setdefault("params", {})
            versions = params.get("mediaVersions") or []
            stored_url = (
                str(storage.get("url") or local_url)
                if storage.get("status") == "stored"
                else ""
            )
            for item in versions:
                if not isinstance(item, dict):
                    continue
                if (
                    item.get("url") != local_url
                    and str(item.get("taskId") or "") != str(task_id or "")
                ):
                    continue
                item["storage"] = storage
                if stored_url:
                    original_url = str(item.get("url") or "")
                    item["url"] = stored_url
                    item["providerResultUrl"] = str(
                        item.get("providerResultUrl")
                        or original_url
                        or ""
                    )
            meta["storage"] = storage
            meta["storageError"] = storage_error
            if stored_url:
                meta["content"] = stored_url
            current_node["updatedAt"] = store._now_iso()
            store.update_project(project_id, payload=current["payload"])
            _publish_node_metadata_event(
                project_id,
                node_id,
                {
                    "content": meta.get("content"),
                    "storage": storage,
                    "storageError": storage_error,
                },
            )

    threading.Thread(
        target=_run_in_tenant,
        args=(tenant_id, tenant_slug, run),
        name=f"image-tos-archive-{node_id}",
        daemon=True,
    ).start()
    return True


def queue_generated_video_finalize(
    project_id,
    node_id,
    local_source_url,
    provider,
    model,
    task_id,
    generated_at,
):
    context = tenant_context.current()
    tenant_id = context.tenant_id if context else None
    tenant_slug = context.slug if context else "local"

    def run():
        updates = {
            "storage": {},
            "storageError": "",
            "finalizePending": False,
        }
        try:
            local_url = str(local_source_url or "")
            if not local_url.startswith("/uploads/"):
                local_url = persist_generated_media(local_url, ".mp4")
            validation = validate_generated_video(local_url)
            video_thumb_url = ensure_video_thumbnail(local_url)
            updates.update({
                "content": local_url,
                "videoValidation": validation,
                "actualDuration": validation.get("duration"),
            })
            if video_thumb_url:
                updates["_paramsPatch"] = {"thumbUrl": video_thumb_url}
            storage_cfg = provider_catalog.storage_config()
            if (
                storage_cfg.get("enabled")
                and storage_cfg.get("auto_persist_videos", True)
            ):
                try:
                    storage = media_storage.persist_local_url(
                        local_url,
                        kind="video",
                        fallback_ext=".mp4",
                        source={
                            "provider": provider.get("id"),
                            "model": model.get("remoteModel") or model.get("id"),
                            "taskId": task_id,
                            "generatedAt": generated_at,
                            "url": local_url,
                        },
                    )
                    updates["storage"] = storage
                except Exception as exc:
                    updates["storageError"] = str(exc)
                    print(
                        f"[storage] failed to archive generated video {local_url[:120]}: {exc}",
                        file=sys.stderr,
                    )
            stored_url = (
                str(updates["storage"].get("url") or local_url)
                if updates["storage"].get("status") == "stored"
                else ""
            )
            if stored_url:
                updates["content"] = stored_url
                with project_write_lock(project_id):
                    current = store.get_project(project_id)
                    if current:
                        current_node = next(
                            (
                                item for item in current["payload"].get("nodes", [])
                                if item.get("id") == node_id
                            ),
                            None,
                        )
                        if current_node:
                            meta = current_node.setdefault("metadata", {})
                            versions = (meta.get("params") or {}).get("mediaVersions") or []
                            for version in versions:
                                if not isinstance(version, dict):
                                    continue
                                if (
                                    version.get("url") != local_url
                                    and str(version.get("taskId") or "") != str(task_id or "")
                                ):
                                    continue
                                version["providerResultUrl"] = str(
                                    version.get("providerResultUrl")
                                    or version.get("url")
                                    or ""
                                )
                                if str(updates["storage"].get("url") or "").strip():
                                    version["url"] = stored_url
                                version["storage"] = updates["storage"]
                            video_versions = (
                                (meta.get("params") or {}).get("videoVersions")
                                or []
                            )
                            for version in video_versions:
                                if not isinstance(version, dict):
                                    continue
                                if (
                                    version.get("url") != local_url
                                    and str(version.get("taskId") or "") != str(task_id or "")
                                ):
                                    continue
                                version["providerResultUrl"] = str(
                                    version.get("providerResultUrl")
                                    or version.get("url")
                                    or ""
                                )
                                if str(updates["storage"].get("url") or "").strip():
                                    version["url"] = stored_url
                                version["storage"] = updates["storage"]
                            meta["content"] = stored_url
                            meta["storage"] = updates["storage"]
                            meta["updatedAt"] = store._now_iso()
                            store.update_project(project_id, payload=current["payload"])
            commit_node_metadata(project_id, node_id, updates)
        except Exception as exc:
            commit_node_metadata(project_id, node_id, {
                "storageError": str(exc),
                "finalizePending": False,
            })
            print(
                f"[video-finalize] {local_source_url[:120]}: {exc}",
                file=sys.stderr,
            )

    threading.Thread(
        target=_run_in_tenant,
        args=(tenant_id, tenant_slug, run),
        name=f"video-finalize-{node_id}",
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


def ensure_video_thumbnail(url):
    """Create a small persisted cover from the first decoded video frame."""
    raw = str(url or "").strip()
    if not raw.startswith("/uploads/"):
        return ""
    path = os.path.join(_upload_dir(), raw[len("/uploads/"):])
    if not os.path.isfile(path):
        return ""
    stem, _ext = os.path.splitext(path)
    cover_path = f"{stem}-cover.jpg"
    if os.path.isfile(cover_path) and os.path.getsize(cover_path) > 0:
        return f"/uploads/{os.path.basename(cover_path)}"
    ffmpeg = provider_client._ffmpeg_executable()
    if not ffmpeg:
        return ""
    temp_path = f"{cover_path}.tmp.jpg"
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                "0.15",
                "-i",
                path,
                "-frames:v",
                "1",
                "-vf",
                "scale='min(640,iw)':-2",
                "-q:v",
                "3",
                temp_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0 or not os.path.isfile(temp_path):
            raise RuntimeError((proc.stderr or "ffmpeg 未生成视频封面").strip()[:300])
        os.replace(temp_path, cover_path)
        return f"/uploads/{os.path.basename(cover_path)}"
    except Exception as exc:
        print(f"[video-thumbnail] {raw[:120]}: {exc}", file=sys.stderr)
        return ""
    finally:
        try:
            if os.path.isfile(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


def ensure_image_thumbnail(url):
    """Create a small persisted preview for a local image."""
    raw = str(url or "").strip()
    if not raw.startswith("/uploads/"):
        return ""
    path = os.path.join(_upload_dir(), raw[len("/uploads/"):])
    if not os.path.isfile(path):
        return ""
    stem, _ext = os.path.splitext(path)
    thumb_path = f"{stem}-thumb.jpg"
    if os.path.isfile(thumb_path) and os.path.getsize(thumb_path) > 0:
        return f"/uploads/{os.path.basename(thumb_path)}"
    ffmpeg = provider_client._ffmpeg_executable()
    if not ffmpeg:
        return ""
    temp_path = f"{thumb_path}.tmp.jpg"
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                path,
                "-frames:v",
                "1",
                "-vf",
                "scale='min(640,iw)':-2",
                "-q:v",
                "4",
                temp_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0 or not os.path.isfile(temp_path):
            raise RuntimeError((proc.stderr or "ffmpeg 未生成图片缩略图").strip()[:300])
        os.replace(temp_path, thumb_path)
        return f"/uploads/{os.path.basename(thumb_path)}"
    except Exception as exc:
        print(f"[image-thumbnail] {raw[:120]}: {exc}", file=sys.stderr)
        return ""
    finally:
        try:
            if os.path.isfile(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


def _video_thumbnail_is_usable(url):
    raw = str(url or "").strip()
    if not raw.startswith("/uploads/"):
        return False
    path = os.path.join(_upload_dir(), raw[len("/uploads/"):])
    return os.path.isfile(path) and os.path.getsize(path) > 0


def _image_thumbnail_is_usable(url):
    raw = str(url or "").strip()
    if not raw.startswith("/uploads/"):
        return False
    path = os.path.join(_upload_dir(), raw[len("/uploads/"):])
    return os.path.isfile(path) and os.path.getsize(path) > 0


def rebuild_image_thumbnail_metadata(meta):
    """Return a params patch with durable thumbnails for image versions."""
    if not isinstance(meta, dict):
        return {}
    params = meta.get("params") if isinstance(meta.get("params"), dict) else {}
    versions = params.get("mediaVersions")
    if not isinstance(versions, list):
        return {}
    patched_versions = []
    changed = False
    for item in versions:
        if not isinstance(item, dict):
            patched_versions.append(item)
            continue
        version = dict(item)
        url = str(version.get("url") or "").strip()
        thumb = str(version.get("thumbUrl") or "").strip()
        if url.startswith("/uploads/") and not _image_thumbnail_is_usable(thumb):
            generated = ensure_image_thumbnail(url)
            if generated:
                version["thumbUrl"] = generated
                changed = True
        patched_versions.append(version)
    patch = {"mediaVersions": patched_versions} if changed else {}
    active_version_id = str(params.get("activeVersionId") or "").strip()
    active = next(
        (
            item for item in patched_versions
            if isinstance(item, dict)
            and (
                (
                    active_version_id
                    and str(item.get("versionId") or "") == active_version_id
                )
                or (
                    not active_version_id
                    and str(item.get("url") or "") == str(meta.get("content") or "")
                )
            )
        ),
        None,
    )
    if isinstance(active, dict) and str(active.get("thumbUrl") or "").strip():
        thumb = str(active["thumbUrl"]).strip()
        if thumb != str(params.get("thumbUrl") or "").strip():
            patch["thumbUrl"] = thumb
    return patch


def backfill_generated_image_thumbnails():
    updated = 0
    for project_row in store.list_projects():
        project = store.get_project(project_row.get("id"))
        if not project:
            continue
        for node in project["payload"].get("nodes", []):
            if node.get("type") != "image":
                continue
            patch = rebuild_image_thumbnail_metadata(node.get("metadata"))
            if not patch:
                continue
            commit_node_metadata(project["id"], node["id"], {
                "_paramsPatch": patch,
            })
            updated += 1
    if updated:
        print(f"[image-thumbnail] backfilled {updated}", file=sys.stderr)
    return updated


_IMAGE_THUMBNAIL_BACKFILL_LOCK = threading.Lock()
_IMAGE_THUMBNAIL_BACKFILL_STARTED = False


def start_image_thumbnail_backfill():
    """Backfill legacy image previews without delaying API startup."""
    global _IMAGE_THUMBNAIL_BACKFILL_STARTED
    with _IMAGE_THUMBNAIL_BACKFILL_LOCK:
        if _IMAGE_THUMBNAIL_BACKFILL_STARTED:
            return False
        _IMAGE_THUMBNAIL_BACKFILL_STARTED = True

    def run():
        try:
            updated = _run_for_active_tenants(backfill_generated_image_thumbnails)
            if updated:
                print(f"已补齐 {updated} 个历史图片缩略图", file=sys.stderr)
        except Exception as exc:
            print(f"[image-thumbnail] 回填失败：{exc}", file=sys.stderr)

    threading.Thread(
        target=run,
        name="image-thumbnail-backfill",
        daemon=True,
    ).start()
    return True


def rebuild_video_thumbnail_metadata(meta):
    """Return a corrected params patch for current and historical video versions.

    A persisted ``thumbUrl`` is not proof that its file still exists. Check the
    bytes before trusting it, and keep each local history version paired with
    its own cover so switching versions cannot leave the previous poster on the
    card while the new video loads.
    """
    if not isinstance(meta, dict):
        return {}
    params = meta.get("params") if isinstance(meta.get("params"), dict) else {}
    current_url = str(meta.get("content") or "").strip()
    versions_key = "mediaVersions"
    versions = params.get(versions_key)
    if not isinstance(versions, list) or not versions:
        versions_key = "videoVersions"
        versions = params.get(versions_key)
    if not isinstance(versions, list):
        versions = []

    patched_versions = []
    versions_changed = False
    for item in versions:
        if not isinstance(item, dict):
            patched_versions.append(item)
            continue
        version = dict(item)
        url = str(version.get("url") or "").strip()
        thumb = str(version.get("thumbUrl") or "").strip()
        if url.startswith("/uploads/") and not _video_thumbnail_is_usable(thumb):
            generated = ensure_video_thumbnail(url)
            if generated:
                version["thumbUrl"] = generated
                versions_changed = True
        patched_versions.append(version)

    patch = {}
    if versions_changed:
        patch[versions_key] = patched_versions

    current_thumb = str(params.get("thumbUrl") or "").strip()
    selected = next(
        (
            item for item in patched_versions
            if isinstance(item, dict)
            and str(item.get("url") or "").strip() == current_url
            and str(item.get("thumbUrl") or "").strip()
        ),
        None,
    )
    next_thumb = (
        str(selected.get("thumbUrl") or "").strip()
        if selected else current_thumb
    )
    if (
        current_url.startswith("/uploads/")
        and not _video_thumbnail_is_usable(next_thumb)
    ):
        next_thumb = ensure_video_thumbnail(current_url)
    if current_url.startswith("/uploads/") and next_thumb:
        if next_thumb != current_thumb:
            patch["thumbUrl"] = next_thumb
        elif current_thumb and not _video_thumbnail_is_usable(current_thumb):
            patch["thumbUrl"] = next_thumb
    return patch


def backfill_generated_video_thumbnails():
    updated = 0
    for project_row in store.list_projects():
        project = store.get_project(project_row.get("id"))
        if not project:
            continue
        for node in project["payload"].get("nodes", []):
            if node.get("type") != "video":
                continue
            patch = rebuild_video_thumbnail_metadata(node.get("metadata"))
            if not patch:
                continue
            commit_node_metadata(project["id"], node["id"], {
                "_paramsPatch": patch,
            })
            updated += 1
    if updated:
        print(f"[video-thumbnail] backfilled {updated}", file=sys.stderr)
    return updated


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
def _publish_node_metadata_event(project_id, node_id, updates):
    """Project a persisted node update into the canvas event stream.

    Every terminal writer, including recovery and reconciliation paths, must
    notify open canvases. Publishing from the commit boundary avoids the
    historical gap where a result existed on disk but the browser kept showing
    the stale node until a manual reload.
    """
    if not isinstance(updates, dict):
        return
    status = str(updates.get("status") or "").strip().lower()
    changed_fields = {
        "content",
        "lastError",
        "providerTaskStatus",
        "providerTaskPhase",
        "providerTaskProgress",
        "generationFinishedAt",
        "finalizePending",
        "storage",
        "storageError",
        "videoValidation",
        "actualDuration",
    }
    if status not in {"done", "failed", "generating"} and not any(
        key in updates for key in changed_fields
    ):
        return
    payload = {
        key: updates[key]
        for key in (
            "status", "content", "lastError", "providerTaskStatus",
            "providerTaskPhase", "providerTaskProgress", "generationFinishedAt",
            "finalizePending", "storage", "storageError", "videoValidation",
            "actualDuration",
        )
        if key in updates
    }
    if "content" in updates:
        payload["url"] = updates.get("content") or ""
    if "lastError" in updates and "error" not in payload:
        payload["error"] = updates.get("lastError") or ""
    params_patch = updates.get("_paramsPatch")
    if isinstance(params_patch, dict):
        if "mediaVersions" in params_patch:
            payload["mediaVersions"] = params_patch["mediaVersions"]
        if "mediaVersionIndex" in params_patch:
            payload["mediaVersionIndex"] = params_patch["mediaVersionIndex"]
    # Avoid a duplicate event when the caller explicitly publishes its own
    # richer terminal payload after this commit.
    if not payload:
        return
    publish_project_event(project_id, "media", node_id, payload)


def commit_node_metadata(project_id, node_id, updates, episode_id=None):
    """Merge generated fields into the current node without overwriting peers."""
    with project_write_lock(project_id):
        saved = store.merge_projection_node_metadata(
            project_id,
            node_id,
            updates,
            episode_id=episode_id,
        )
        if not saved:
            return False
        _publish_node_metadata_event(project_id, node_id, updates)
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
    "providerSubmittedAt",
    "generatedAt",
    "providerResultUrl",
    "production",
    "videoValidation",
    "actualDuration",
    "storage",
    "storageError",
    "deliveryState",
    "durable",
    "durabilityRequired",
    "source",
    "finalizePending",
    "provider",
    "model",
    "remoteModel",
}

GENERATION_OWNED_PARAM_FIELDS = {
    "mediaVersions",
    "mediaVersionIndex",
    "activeVersionId",
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


def clear_superseded_validation_error(meta, incoming, *, generation_active=False):
    """Clear stale request-validation errors after editable inputs change.

    Missing references, empty prompts and model limits describe the current
    request. If one of those errors is kept in generation-owned metadata, a
    corrected prompt continues to show an error that no longer exists.
    Provider failures, uncertain outcomes and provider task receipts remain
    generation-owned and are never cleared by this helper.
    """
    if generation_active or not isinstance(meta, dict) or not isinstance(incoming, dict):
        return
    if str(meta.get("status") or "").strip().lower() not in {"failed", "error"}:
        return
    if str(meta.get("taskId") or "").strip():
        return
    if not generation_outcome.is_local_validation_error(meta.get("lastError")):
        return
    changed = any(
        key in incoming
        for key in ("prompt", "refs", "voiceRef", "voiceRefs")
    )
    incoming_params = incoming.get("params")
    if isinstance(incoming_params, dict):
        changed = changed or bool({
            "prompt",
            "refs",
            "voiceRef",
            "voiceRefs",
            "removedRefNodeIds",
            "model",
            "provider",
        }.intersection(incoming_params))
    if not changed:
        return
    meta["status"] = "done" if str(meta.get("content") or "").strip() else "idle"
    meta["lastError"] = ""
    meta["providerTaskStatus"] = ""
    meta["providerTaskPhase"] = ""
    meta["providerTaskProgress"] = 0
    meta["generationFinishedAt"] = ""


def _editable_param_view(meta):
    params = meta.get("params") if isinstance(meta, dict) else {}
    if not isinstance(params, dict):
        return {}
    return {
        key: value for key, value in params.items()
        if key not in GENERATION_OWNED_PARAM_FIELDS
    }


def _active_media_version(meta):
    """Return the active version; durable versionId precedes legacy URL state."""
    if not isinstance(meta, dict):
        return {}
    params = meta.get("params")
    if not isinstance(params, dict):
        return {}
    versions = params.get("mediaVersions")
    if not isinstance(versions, list) or not versions:
        versions = params.get("videoVersions")
    if not isinstance(versions, list):
        return {}
    content = str(meta.get("content") or "").strip()
    active_version_id = str(params.get("activeVersionId") or "").strip()
    active_index = -1
    if active_version_id:
        for index, item in enumerate(versions):
            if (
                isinstance(item, dict)
                and str(item.get("versionId") or "").strip()
                == active_version_id
            ):
                # versionId is the durable identity. TOS URLs are renewed on
                # every read, so exact URL equality must never be required.
                active_index = index
                break
    if active_index >= 0:
        # New rows are authoritative by stable versionId. Signed TOS URLs and
        # legacy duplicated URLs must never override the recorded ID.
        return versions[active_index]
    if not content:
        return {}
    index_key = (
        "videoVersionIndex"
        if isinstance(params.get("videoVersions"), list)
        and params.get("videoVersions")
        else "mediaVersionIndex"
    )
    try:
        index = int(params.get(index_key))
    except (TypeError, ValueError):
        index = -1
    if (
        0 <= index < len(versions)
        and isinstance(versions[index], dict)
        and str(versions[index].get("url") or "").strip() == content
    ):
        return versions[index]
    for item in reversed(versions):
        if (
            isinstance(item, dict)
            and str(item.get("url") or "").strip() == content
        ):
            return item
    return {}


def _active_reference_url(source_meta, raw=None):
    """Resolve a reference URL from the active version, never from a stale snapshot."""
    if not isinstance(source_meta, dict):
        return ""
    active = _active_media_version(source_meta)
    active_storage = (
        active.get("storage") if isinstance(active, dict) else None
    )
    for candidate in (
        active.get("url") if isinstance(active, dict) else "",
        source_meta.get("content"),
        (raw or {}).get("url") if isinstance(raw, dict) else "",
    ):
        value = str(candidate or "").strip()
        if value:
            if not value.startswith("/uploads/"):
                descriptor = (
                    media_storage._stored_descriptor(active_storage)
                    or media_storage._stored_descriptor(source_meta.get("storage"))
                )
                if descriptor:
                    try:
                        signed = media_storage.public_url(descriptor)
                        if signed:
                            return signed
                    except Exception:
                        pass
            return value
    return ""


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


def merge_node_metadata(meta, incoming, generation_active=False, node_type=""):
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
    requested_params = (incoming or {}).get("params")
    requested_version_id = (
        str(requested_params.get("activeVersionId") or "").strip()
        if isinstance(requested_params, dict)
        else ""
    )
    requested_signed_url = bool(
        re.match(
            r"^https?://[^/]+\.tos-[^/]+\.volces\.com/",
            requested_content,
            flags=re.IGNORECASE,
        )
    )
    current_version_ids = {
        str(item.get("versionId") or "")
        for item in current_versions
        if isinstance(item, dict) and item.get("versionId")
    }
    media_version_selection = bool(
        explicit_media_version_selection
        and
        isinstance((incoming or {}).get("source"), dict)
        and requested_content
        and (
            requested_content in current_version_urls
            or (
                requested_version_id
                and requested_version_id in current_version_ids
            )
            or (requested_version_id and requested_signed_url)
        )
    )
    clear_superseded_validation_error(
        meta,
        incoming,
        generation_active=generation_active,
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
            requested_version_id = str(
                value.get("activeVersionId") or ""
            ).strip()
            requested_version_index = -1
            if media_version_selection and requested_version_id and versions:
                requested_version_index = next(
                    (
                        index
                        for index, item in enumerate(versions)
                        if str(item.get("versionId") or "") == requested_version_id
                    ),
                    -1,
                )
            if media_version_selection and requested_version_index < 0 and requested_content:
                requested_version_index = next(
                    (
                        index
                        for index, item in enumerate(versions)
                        if str(item.get("url") or "").strip() == requested_content
                    ),
                    -1,
                )
            if requested_version_index >= 0:
                requested_index = requested_version_index
            try:
                requested_index = int(requested_index)
            except (TypeError, ValueError):
                requested_index = -1
            if versions and 0 <= requested_index < len(versions):
                params["mediaVersionIndex"] = requested_index
                if params.get("videoVersions"):
                    params["videoVersionIndex"] = requested_index
                selected = versions[requested_index]
                params["activeVersionId"] = str(
                    selected.get("versionId") or ""
                )
                meta["content"] = str(selected.get("url") or "")
                meta["generatedAt"] = str(selected.get("generatedAt") or "")
                if selected.get("provider"):
                    meta["provider"] = str(selected.get("provider"))
                if selected.get("model"):
                    meta["model"] = str(selected.get("model"))
                if isinstance(selected.get("storage"), dict):
                    meta["storage"] = dict(selected["storage"])
                else:
                    meta["storage"] = {}
                meta["providerResultUrl"] = str(
                    selected.get("providerResultUrl") or ""
                )
                selected_thumb = str(selected.get("thumbUrl") or "").strip()
                selected_url = str(selected.get("url") or "").strip()
                if not selected_thumb and selected_url.startswith("/uploads/"):
                    selected_thumb = ensure_video_thumbnail(selected_url)
                    if selected_thumb:
                        selected["thumbUrl"] = selected_thumb
                if selected_thumb and node_type == "video":
                    params["thumbUrl"] = selected_thumb
                elif node_type == "video":
                    params.pop("thumbUrl", None)
            meta["params"] = params
            continue
        if (
            key in GENERATION_OWNED_METADATA_FIELDS
            and not (
                (local_media_import and key in LOCAL_MEDIA_METADATA_FIELDS)
                or (
                    media_version_selection
                    and key in {
                        "content", "status", "generatedAt", "actualDuration",
                        "source", "storage", "providerResultUrl",
                    }
                )
            )
        ):
            continue
        meta[key] = value


def workspace_media_availability(meta):
    """Return backend-confirmed media availability for a node projection."""
    if not isinstance(meta, dict):
        return ""
    projected = str(meta.get("mediaAvailability") or "").strip().lower()
    if projected in {"available", "unknown", "missing"}:
        return projected
    content = str(meta.get("content") or "").strip()
    if not content:
        return ""
    if content.startswith("/uploads/"):
        try:
            return (
                "available"
                if provider_client.local_upload_path(content)
                else "missing"
            )
        except RuntimeError:
            return "missing"
    if content.startswith(("http://", "https://", "data:")):
        # Signed provider URLs expire while TOS bytes remain. Establish the
        # durable local/storage copy before calling a remote URL unavailable.
        storage = meta.get("storage")
        if str(meta.get("storageError") or "").strip():
            return "missing"
        if (
            isinstance(storage, dict)
            and storage.get("status") == "stored"
            and (storage.get("key") or storage.get("url"))
        ):
            return "unknown"
        return "unknown"
    return "missing"


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


def _generation_tenant_from_task(task_id):
    """Resolve the task owner even when this thread has no request context."""
    context = tenant_context.current()
    if context is not None:
        task = store.get_generation_task(task_id) or {}
        if task:
            return context
    for tenant in _active_tenants():
        tenant_id = tenant.get("id") if tenant else None
        tenant_slug = str((tenant or {}).get("slug") or "local")
        try:
            scope = (
                tenant_context.use_tenant(tenant_id, tenant_slug)
                if tenant
                else tenant_context.use_context(None)
            )
            with scope:
                if store.get_generation_task(task_id):
                    return tenant_context.current()
        except Exception:
            continue
    return context


def preserve_generation_outcome(task_id, exc):
    """A dropped browser response must never rewrite a paid generation result."""
    if is_client_disconnect_error(exc):
        return True
    if task_id:
        task = store.get_generation_task(task_id)
        return bool(task and task.get("status") == "succeeded")
    return False


def _submitted_provider_task_id(task_id):
    if not task_id:
        return ""
    try:
        task = store.get_generation_task(task_id)
        return str((task or {}).get("provider_task_id") or "").strip()
    except Exception:
        return ""


def _latest_provider_result_url(project_id, node_id):
    try:
        task = store.get_latest_succeeded_generation_task_for_node(
            project_id,
            node_id,
            kind="image",
        )
    except Exception:
        return ""
    value = str((task or {}).get("provider_result_url") or "").strip()
    return value if value.startswith("https://") else ""


def _start_generation_task_heartbeat(task_id, lease_owner):
    if not task_id or not lease_owner:
        return None, None
    heartbeat_tenant = _generation_tenant_from_task(task_id)
    stop_event = threading.Event()

    def run():
        scope = (
            tenant_context.use_context(heartbeat_tenant)
            if heartbeat_tenant is not None
            else tenant_context.use_context(None)
        )
        with scope:
            while not stop_event.wait(TASK_HEARTBEAT_SECONDS):
                try:
                    if not store.renew_generation_task_lease(
                        task_id,
                        lease_owner,
                        TASK_LEASE_SECONDS,
                    ):
                        latest = store.get_generation_task(task_id) or {}
                        if latest.get("status") not in {
                            "queued", "submitting", "running",
                        }:
                            return
                        print(
                            "[generation] task lease lost: "
                            f"task={task_id} "
                            f"tenant={tenant_context.current_tenant_id()} "
                            f"status={latest.get('status') or '-'} "
                            f"owner={latest.get('lease_owner') or '-'} "
                            f"expires={latest.get('lease_expires_at') or '-'}",
                            file=sys.stderr,
                        )
                        return
                    latest = store.get_generation_task(task_id) or {}
                    if latest.get("queue_state") in {
                        "admitted",
                        "provider_running",
                        "polling",
                        "delivering",
                    }:
                        if not generation_queue.renew_generation_admission(
                            task_id,
                            lease_owner,
                            lease_seconds=max(60, TASK_LEASE_SECONDS * 4),
                        ):
                            print(
                                "[generation] admission lease lost: "
                                f"task={task_id} lease_owner={lease_owner}",
                                file=sys.stderr,
                            )
                except Exception as exc:
                    print(
                        "[generation] heartbeat failed: "
                        f"task={task_id} "
                        f"tenant={tenant_context.current_tenant_id()} "
                        f"error={exc}",
                        file=sys.stderr,
                    )

    thread = threading.Thread(
        target=run,
        name=f"generation-heartbeat-{task_id}",
        daemon=True,
    )
    thread.start()
    return stop_event, thread


def _finish_generation_task_lease(task_id, lease_owner, heartbeat):
    stop_event, thread = heartbeat if heartbeat else (None, None)
    if stop_event:
        stop_event.set()
    if thread and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=1)
    try:
        store.release_generation_task_lease(task_id, lease_owner)
    except Exception as exc:
        print(
            f"[generation] release lease failed: task={task_id} error={exc}",
            file=sys.stderr,
        )


def _mark_generation_outcome_uncertain(task_id, project_id, node_id, exc, provider_task_id=""):
    """Keep an accepted paid task recoverable instead of calling it a failure."""
    provider_task_id = str(provider_task_id or _submitted_provider_task_id(task_id)).strip()
    message = str(exc)
    evidence = {}
    if task_id:
        current_task = store.get_generation_task(task_id) or {}
        if current_task.get("request_json"):
            try:
                snapshot = json.loads(current_task["request_json"])
                evidence = {
                    "operationId": str(current_task.get("client_operation_id") or ""),
                    "provider": str(current_task.get("provider") or ""),
                    "model": str(current_task.get("model") or ""),
                    "createdAt": str(current_task.get("created_at") or ""),
                    "requestSnapshot": {
                        key: snapshot.get(key)
                        for key in ("kind", "provider", "model", "params", "createdAt")
                    },
                }
            except (TypeError, ValueError):
                evidence = {}
    response = getattr(exc, "response", None)
    request_id = ""
    if isinstance(response, dict):
        request_id = str(
            response.get("requestId")
            or response.get("request_id")
            or response.get("RequestId")
            or ""
        ).strip()
    evidence.update({
        "providerRequestId": request_id,
        "transportError": message[:2000],
    })
    audit_payload = {
        "event": "generation_submit_uncertain",
        "taskId": task_id or "",
        "projectId": project_id,
        "nodeId": node_id,
        "evidence": evidence,
    }
    try:
        store.append_event(
            project_id,
            "generation.submit_uncertain",
            node_id,
            audit_payload,
        )
    except Exception:
        pass
    updates = {
        "status": "error",
        "lastError": message,
        "taskId": provider_task_id,
        "providerTaskStatus": "uncertain",
        "providerTaskPhase": "结果待确认",
        "generationFinishedAt": "",
    }
    if not task_id:
        commit_node_metadata(project_id, node_id, updates)
        return True
    current = store.get_generation_task(task_id) or {}
    effective_provider_task_id = str(
        provider_task_id
        or current.get("provider_task_id")
        or ""
    ).strip()
    if effective_provider_task_id:
        store.schedule_provider_task_poll(
            task_id,
            store._now_iso(),
        )
    if current.get("status") == "succeeded":
        return False
    try:
        committed = commit_generation_terminal(
            project_id,
            node_id,
            updates,
            task_id=task_id,
            task_updates={
                "status": "interrupted",
                "phase": "结果待确认",
                "error": message,
                "provider_task_id": provider_task_id,
                "delivery_status": "pending",
                "next_poll_at": (
                    datetime.now(timezone.utc) + timedelta(seconds=8)
                ).isoformat(),
                "finished_at": "",
                "node_written_at": "",
            },
        )
    except Exception:
        # The provider id is also in the independent journal. Preserve the
        # recoverable node state even if the database write is failing.
        commit_node_metadata(project_id, node_id, updates)
        return True
    if committed.get("committed"):
        return True
    if committed.get("reason") == "task_not_found":
        commit_node_metadata(project_id, node_id, updates)
        return True
    return False


MAX_MEDIA_VERSIONS = 20


LOCAL_MEDIA_PARAM_FIELDS.add("activeVersionId")


def _append_local_media_version(meta, node_type, upload):
    """Append one server-authoritative local-media version and select it.

    The browser uploads bytes first, then sends only the upload receipt. The
    server reads the current node row inside the atomic patch transaction and
    appends there, so concurrent autosaves cannot overwrite one another with a
    stale client-side version array.
    """
    if not isinstance(meta, dict) or not isinstance(upload, dict):
        raise ValueError("本地素材版本参数无效")
    kind = str(node_type or "").strip().lower()
    if kind not in {"image", "audio", "video"}:
        raise ValueError("该节点不支持本地素材版本")
    url = str(upload.get("url") or "").strip()
    if not url.startswith("/uploads/"):
        raise ValueError("本地素材地址无效")
    try:
        media_storage._local_path(url)
    except media_storage.MediaStorageError as exc:
        raise ValueError(str(exc)) from exc

    params = meta.get("params")
    params = dict(params) if isinstance(params, dict) else {}
    _normalize_media_versions(meta, kind)
    params = dict(meta.get("params") or params)
    source_versions = params.get("mediaVersions")
    if not isinstance(source_versions, list) and kind == "video":
        source_versions = params.get("videoVersions")
    versions = [
        dict(item)
        for item in (source_versions or [])
        if isinstance(item, dict) and str(item.get("url") or "").strip()
    ]

    current_url = str(meta.get("content") or "").strip()
    if current_url and not any(
        str(item.get("url") or "").strip() == current_url
        for item in versions
    ):
        previous_production = meta.get("production") or {}
        versions.append({
            "versionId": (
                str(params.get("activeVersionId") or "").strip()
                or f"{kind}:legacy:{len(versions)}"
            ),
            "url": current_url,
            "sourceType": str((meta.get("source") or {}).get("type") or "generated"),
            "generatedAt": str(meta.get("generatedAt") or ""),
            "provider": str(meta.get("provider") or previous_production.get("provider") or ""),
            "model": str(meta.get("model") or previous_production.get("model") or ""),
            "providerResultUrl": str(meta.get("providerResultUrl") or ""),
            **({"storage": dict(meta["storage"])} if isinstance(meta.get("storage"), dict) and meta.get("storage") else {}),
        })

    sha256 = str(upload.get("sha256") or "").strip()
    version_id = f"{kind}:local:{sha256[:32]}" if sha256 else f"{kind}:local:{secrets.token_hex(16)}"
    versions = [
        item for item in versions
        if str(item.get("versionId") or "").strip() != version_id
        and str(item.get("url") or "").strip() != url
    ]
    uploaded_at = str(upload.get("uploadedAt") or "").strip() or store._now_iso()
    source = {
        "type": "local",
        "fileName": str(
            upload.get("fileName") or upload.get("name") or ""
        ).strip(),
        "mime": str(upload.get("mime") or "").strip(),
        "size": max(0, int(upload.get("size") or 0)),
        "uploadedAt": uploaded_at,
    }
    for key in ("sha256", "width", "height"):
        value = upload.get(key)
        if value not in (None, ""):
            source[key] = value
    version = {
        "versionId": version_id,
        "url": url,
        "sourceType": "local",
        "generatedAt": uploaded_at,
        "fileName": source["fileName"],
        "mime": source["mime"],
        "size": source["size"],
        **({"sha256": sha256} if sha256 else {}),
        **({"width": int(upload["width"])} if upload.get("width") not in (None, "") else {}),
        **({"height": int(upload["height"])} if upload.get("height") not in (None, "") else {}),
        **({"actualDuration": float(upload["duration"])} if upload.get("duration") not in (None, "") else {}),
    }
    storage = upload.get("storage")
    if isinstance(storage, dict) and storage:
        version["storage"] = dict(storage)
    versions.append(version)
    versions = versions[-MAX_MEDIA_VERSIONS:]
    selected_index = len(versions) - 1

    params["mediaVersions"] = versions
    params["mediaVersionIndex"] = selected_index
    params["activeVersionId"] = version_id
    if kind == "video":
        # Generate the poster before copying the version into the legacy
        # videoVersions list. Otherwise mediaVersions has a thumb while the
        # older list used by some projections remains posterless.
        thumb = ensure_video_thumbnail(url)
        if thumb:
            version["thumbUrl"] = thumb
            params["thumbUrl"] = thumb
        else:
            params.pop("thumbUrl", None)
        params["videoVersions"] = [dict(item) for item in versions]
        params["videoVersionIndex"] = selected_index
    elif kind == "image":
        thumb = ensure_image_thumbnail(url)
        if thumb:
            version["thumbUrl"] = thumb
            params["thumbUrl"] = thumb
        else:
            params.pop("thumbUrl", None)
    ratio = str(upload.get("ratio") or "").strip()
    if ratio and kind in {"image", "video"}:
        params["ratio"] = ratio
    meta["params"] = params
    meta["content"] = url
    meta["status"] = "done"
    meta["lastError"] = ""
    meta["source"] = source
    meta["storage"] = dict(storage) if isinstance(storage, dict) and storage else {}
    meta["storageError"] = ""
    meta["providerResultUrl"] = ""
    meta["generatedAt"] = uploaded_at
    meta["videoValidation"] = {}
    if upload.get("duration") not in (None, ""):
        meta["actualDuration"] = float(upload["duration"])
    else:
        meta.pop("actualDuration", None)
    return version


def _media_version_id(kind, task_id, generated_at, version_index):
    normalized_task_id = str(task_id or "").strip()
    if normalized_task_id:
        return f"{kind}:{normalized_task_id}"
    stamp = str(generated_at or "").strip()
    if stamp:
        return f"{kind}:{stamp}:{int(version_index)}"
    return ""


def _normalize_media_versions(meta, kind):
    """Backfill stable version ids and the current-version pointer.

    Existing projects are index-based. This compatibility pass gives each
    version a durable id without deleting or reordering history, and records
    the active id so callers never need to guess from a URL.
    """
    params = meta.setdefault("params", {})
    source = params.get("mediaVersions")
    if not source and kind == "video":
        source = params.get("videoVersions")
    versions = []
    seen_ids = {
        str(item.get("versionId") or "")
        for item in (source or [])
        if isinstance(item, dict) and str(item.get("versionId") or "")
    }
    for offset, item in enumerate(source or []):
        if not isinstance(item, dict):
            continue
        version = dict(item)
        if not str(version.get("versionId") or "").strip():
            version_id = _media_version_id(
                str(version.get("kind") or kind),
                version.get("taskId"),
                version.get("generatedAt") or version.get("generated_at"),
                offset,
            )
            if not version_id:
                base = f"legacy:{kind}:{version.get('url') or ''}:{offset}"
                version_id = base
                suffix = 1
                while version_id in seen_ids:
                    suffix += 1
                    version_id = f"{base}:{suffix}"
            version["versionId"] = version_id
            seen_ids.add(version_id)
        versions.append(version)
    params["mediaVersions"] = versions
    if kind == "video":
        params["videoVersions"] = [dict(item) for item in versions]
    active_id = str(params.get("activeVersionId") or "").strip()
    active_match = next(
        (
            index
            for index, item in enumerate(versions)
            if active_id and str(item.get("versionId") or "") == active_id
        ),
        -1,
    )
    if active_match >= 0:
        return params
    # Only rows without a usable active ID are legacy rows. URL and index are
    # one-time migration heuristics; they must never override a valid ID above.
    active_url = str(meta.get("content") or "").strip()
    matching_indexes = [
        index
        for index, item in enumerate(versions)
        if active_url and (
            str(item.get("url") or "") == active_url
            or media_storage._same_remote_object(
                str(item.get("url") or ""),
                active_url,
            )
        )
    ]
    # A legacy list can repeat the same URL after regeneration. The latest
    # matching entry is the active one because _record_media_version appends
    # chronologically. Falling back to the first match would point the current
    # card at an older task that produced the same URL.
    selected_index = matching_indexes[-1] if matching_indexes else -1
    if selected_index < 0:
        try:
            selected_index = int(
                params.get(
                    "videoVersionIndex"
                    if kind == "video"
                    else "mediaVersionIndex"
                )
            )
        except (TypeError, ValueError):
            selected_index = len(versions) - 1
    selected_index = max(0, min(selected_index, len(versions) - 1))
    if versions:
        params["activeVersionId"] = str(
            versions[selected_index].get("versionId") or ""
        )
        params["mediaVersionIndex"] = selected_index
        if kind == "video":
            params["videoVersionIndex"] = selected_index
    else:
        params.pop("activeVersionId", None)
    return params


class MediaVersionError(ValueError):
    """A stable media-version selection could not be satisfied safely."""

    def __init__(self, message, *, code="MEDIA_VERSION_INVALID", status=409):
        super().__init__(message)
        self.code = code
        self.status = status


def _media_url_matches(left, right):
    """Compare durable upload paths even when the browser sends an API URL."""
    left = str(left or "").strip()
    right = str(right or "").strip()
    if not left or not right:
        return False
    if left == right:
        return True
    left_path = urllib.parse.urlsplit(left).path
    right_path = urllib.parse.urlsplit(right).path
    if left_path == right_path:
        return True

    def upload_name(path):
        if path.startswith("/uploads/"):
            return unquote(path[len("/uploads/"):])
        marker = "/api/share/"
        if marker in path and "/media/" in path:
            return unquote(path.rsplit("/media/", 1)[1])
        return ""

    left_name = upload_name(left_path)
    right_name = upload_name(right_path)
    return bool(left_name and left_name == right_name)


def _select_media_version(meta, node_type, version_id, version_url=""):
    """Atomically select one version and return its durable node projection.

    The browser never supplies a media URL here. It names only a stable version
    id; the server resolves the version, verifies that its bytes can still be
    projected, then updates every current-version pointer from that one object.
    """
    if not isinstance(meta, dict):
        raise MediaVersionError("节点元数据无效", status=400)
    kind = str(node_type or "").strip().lower()
    if kind not in {"image", "audio", "video"}:
        raise MediaVersionError("该节点不支持切换素材版本", status=400)
    requested_id = str(version_id or "").strip()
    if not requested_id:
        raise MediaVersionError("切换素材版本必须指定版本 ID", status=400)

    params = _normalize_media_versions(meta, kind)
    versions = params.get("mediaVersions")
    if not isinstance(versions, list) or not versions:
        raise MediaVersionError(
            "该节点没有可切换的历史版本",
            code="MEDIA_VERSION_NOT_FOUND",
            status=404,
        )
    matching_indexes = [
        index
        for index, version in enumerate(versions)
        if isinstance(version, dict)
        and str(version.get("versionId") or "").strip() == requested_id
    ]
    if not matching_indexes:
        raise MediaVersionError(
            "该历史版本不存在或已被裁剪",
            code="MEDIA_VERSION_NOT_FOUND",
            status=404,
        )

    resolved = []
    for index in matching_indexes:
        candidate = versions[index]
        descriptor, display_url, error, availability = (
            media_storage.resolve_version_media(meta, candidate)
        )
        durable_url = str(candidate.get("url") or "").strip()
        expected_match = bool(
            version_url
            and (
                _media_url_matches(version_url, durable_url)
                or _media_url_matches(version_url, display_url)
                or _media_url_matches(
                    version_url,
                    candidate.get("providerResultUrl"),
                )
            )
        )
        resolved.append({
            "index": index,
            "version": dict(candidate),
            "storage": descriptor,
            "display_url": display_url,
            "error": error,
            "availability": availability,
            "expected_match": expected_match,
            "durable_local": (
                durable_url.startswith("/uploads/")
                or bool(descriptor)
            ),
        })
    selected_candidate = min(
        resolved,
        key=lambda item: (
            0 if item["expected_match"] else 1,
            0 if item["availability"] == "available" else 1,
            0 if "sourceType" in item["version"] else 1,
            0 if item["durable_local"] else 1,
            item["index"],
        ),
    )
    selected_index = int(selected_candidate["index"])
    selected = dict(selected_candidate["version"])
    version_storage = selected_candidate["storage"]
    selected_url = selected_candidate["display_url"]
    _version_error = selected_candidate["error"]
    availability = selected_candidate["availability"]
    if not selected_url or availability != "available":
        raise MediaVersionError(
            "该历史版本的媒体文件已失效，不能切换到已丢失的素材",
            code="MEDIA_VERSION_MISSING",
            status=409,
        )

    # Projection may renew a TOS signature or normalize an old local URL. The
    # durable URL is what the card should display; never store a signed URL into
    # current-version truth.
    durable_url = str(selected.get("url") or "").strip()
    display_url = str(selected_url or durable_url).strip()
    selected["url"] = durable_url
    if version_storage:
        selected["storage"] = dict(version_storage)
    selected["thumbUrl"] = str(selected.get("thumbUrl") or "").strip()
    if kind == "video" and not selected["thumbUrl"] and durable_url.startswith("/uploads/"):
        thumb = ensure_video_thumbnail(durable_url)
        if thumb:
            selected["thumbUrl"] = thumb

    params["mediaVersionIndex"] = selected_index
    if kind == "video":
        params["videoVersionIndex"] = selected_index
        params["videoVersions"] = [dict(item) for item in versions]
        if selected.get("thumbUrl"):
            params["thumbUrl"] = selected["thumbUrl"]
        else:
            params.pop("thumbUrl", None)
    params["activeVersionId"] = requested_id
    params["mediaVersions"] = versions
    meta["params"] = params
    meta["content"] = display_url
    meta["status"] = "done"
    meta["lastError"] = ""
    meta["generatedAt"] = str(selected.get("generatedAt") or "")
    meta["source"] = {
        **(meta.get("source") if isinstance(meta.get("source"), dict) else {}),
        **{
            key: selected[key]
            for key in ("sourceType", "fileName", "mime", "size", "sha256", "uploadedAt")
            if selected.get(key) not in (None, "")
        },
    }
    meta["storage"] = (
        dict(selected["storage"])
        if isinstance(selected.get("storage"), dict)
        else {}
    )
    meta["storageError"] = ""
    meta["providerResultUrl"] = str(selected.get("providerResultUrl") or "")
    for index, version in enumerate(versions):
        if (
            isinstance(version, dict)
            and str(version.get("versionId") or "").strip()
            == requested_id
        ):
            version["active"] = index == selected_index
    for key in ("provider", "model"):
        if selected.get(key):
            meta[key] = str(selected[key])
        else:
            meta.pop(key, None)
    if selected.get("actualDuration") not in (None, ""):
        meta["actualDuration"] = float(selected["actualDuration"])
    else:
        meta.pop("actualDuration", None)
    meta["videoValidation"] = {}
    # The public projection above only verified bytes. Return a response-only
    # URL so the immediate POST response and a later workspace read agree.
    return {
        "versionId": requested_id,
        "index": selected_index,
        "version": selected,
        "displayUrl": display_url,
    }


def _record_media_version(
    meta,
    kind,
    generated_url,
    task_id,
    generated_at,
    storage=None,
    provider_result_url="",
    thumb_url="",
):
    media_params = meta.setdefault("params", {})
    resolved_thumb_url = str(thumb_url or "").strip()
    if kind == "image" and not resolved_thumb_url:
        resolved_thumb_url = ensure_image_thumbnail(generated_url)
    _normalize_media_versions(meta, kind)
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
            "providerResultUrl": str(
                meta.get("providerResultUrl")
                or meta.get("providerUrl")
                or ""
            ),
        })
    # One paid provider task must produce one persisted media version. A
    # recovery attempt may download the same completed provider result to a new
    # local filename, so URL-only dedupe is insufficient.
    normalized_task_id = str(task_id or "").strip()
    if normalized_task_id:
        versions = [
            item
            for item in versions
            if str(item.get("taskId") or "").strip() != normalized_task_id
        ]
    versions = [
        item
        for item in versions
        if str(item.get("url") or "") != generated_url
    ]
    production = meta.get("production") or {}
    current_version = {
        "versionId": _media_version_id(
            kind,
            task_id,
            generated_at,
            len(versions),
        ),
        "url": generated_url,
        "taskId": str(task_id or ""),
        "generatedAt": generated_at,
        "kind": kind,
        "sourceType": "generated",
        "provider": str(production.get("provider") or meta.get("provider") or ""),
        "model": str(production.get("model") or meta.get("model") or ""),
        "providerResultUrl": str(provider_result_url or ""),
    }
    if isinstance(storage, dict):
        current_version["storage"] = storage
    if resolved_thumb_url:
        current_version["thumbUrl"] = resolved_thumb_url
    versions.append(current_version)
    for offset, item in enumerate(versions):
        if not str(item.get("versionId") or "").strip():
            item["versionId"] = _media_version_id(
                str(item.get("kind") or kind),
                item.get("taskId"),
                item.get("generatedAt"),
                offset,
            )
    versions = versions[-MAX_MEDIA_VERSIONS:]
    media_params["mediaVersions"] = versions
    if resolved_thumb_url:
        media_params["thumbUrl"] = resolved_thumb_url
    media_params["activeVersionId"] = str(
        current_version.get("versionId") or ""
    )
    media_params["mediaVersionIndex"] = len(versions) - 1
    if kind == "video":
        media_params["videoVersions"] = [dict(item) for item in versions]
        media_params["videoVersionIndex"] = len(versions) - 1
    # When a task was synced before provider-result receipts were introduced,
    # refresh the version with the new durable receipt rather than adding a
    # second version for the same paid result.
    normalized_provider_result = str(provider_result_url or "").strip()
    if normalized_task_id and normalized_provider_result:
        for item in versions:
            if str(item.get("taskId") or "").strip() == normalized_task_id:
                item["providerResultUrl"] = normalized_provider_result
    return versions


def _record_video_version(
    meta,
    generated_url,
    task_id,
    generated_at,
    thumb_url="",
):
    return _record_media_version(
        meta,
        "video",
        generated_url,
        task_id,
        generated_at,
        thumb_url=thumb_url,
    )


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
    if numeric is None or numeric <= 0 or numeric >= 100:
        return "生成中"
    return f"生成中 {numeric}%"


def _with_progress_changed_at(meta, raw_status, progress):
    """Refresh the progress receipt only when the provider value actually moves."""
    now = store._now_iso()
    try:
        previous = int(meta.get("providerTaskProgress"))
    except (TypeError, ValueError):
        previous = None
    try:
        current = int(progress)
    except (TypeError, ValueError):
        current = None
    if current is None:
        return
    if previous != current or not str(meta.get("providerTaskProgressChangedAt") or "").strip():
        meta["providerTaskProgressChangedAt"] = now


RETRYABLE_PROVIDER_QUERY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


def _provider_query_error_kind(exc):
    """Classify a read-only task query without guessing a paid outcome."""
    if isinstance(exc, provider_client.ProviderHTTPError):
        status_code = int(getattr(exc, "status_code", 0) or 0)
        if status_code == 404:
            return "not_found"
        if status_code in RETRYABLE_PROVIDER_QUERY_STATUSES:
            return "retryable"
        if 400 <= status_code < 500:
            return "terminal"
        return "retryable"
    if isinstance(
        exc,
        (
            TimeoutError,
            ConnectionError,
            OSError,
            RuntimeError,
        ),
    ):
        return "retryable"
    return "retryable"


def _provider_query_failure_message(exc, kind):
    if kind == "not_found":
        return (
            "供应商明确返回任务不存在（HTTP 404）。"
            "系统已停止查询该任务，不会自动重新提交；请确认后由用户主动重新生成。"
        )
    if kind == "terminal":
        return (
            f"供应商查询返回不可恢复错误：{exc}。"
            "系统已停止自动查询，不会自动重新提交；请确认后由用户主动重新生成。"
        )
    return str(exc)


def _filter_generation_tasks_for_episode(project_id, tasks, episode_id, node_ids=None):
    """Keep the task center scoped to nodes owned by the selected episode."""
    episode_id = str(episode_id or "").strip().upper()
    if not episode_id:
        return list(tasks or [])
    if node_ids is None:
        if not project_id:
            return []
        node_ids = store.get_episode_node_ids(project_id, episode_id)
    node_ids = {
        str(node_id)
        for node_id in node_ids
        if node_id
    }
    if not node_ids:
        return []
    return [
        task
        for task in (tasks or [])
        if str(task.get("node_id") or "") in node_ids
    ]


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
            "引用 @",
            "无法解析引用",
            "对应多个资产",
            "参考图片",
            "参考音频",
            "参考视频",
            "当前模型最多接受",
            "提示词超过",
            "不支持",
            "缺少",
            "不合法的模式",
            "只支持",
            "未产生新图片",
        )
    ):
        return 409 if "未产生新图片" in text else 400
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
    "Seedream 5.0 角色图进入 Seedance",
    "信任窗口只有",
)


def is_local_validation_error(message):
    return generation_outcome.is_local_validation_error(message)


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


def _generation_task_for_provider(project_id, node_id, provider_task_id):
    """Find the local row for a provider task regardless of its current state."""
    provider_task_id = str(provider_task_id or "").strip()
    if not project_id or not node_id or not provider_task_id:
        return None
    for item in store.list_generation_tasks(project_id, 300):
        if item.get("node_id") != node_id:
            continue
        if str(item.get("provider_task_id") or "").strip() != provider_task_id:
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
    provider_task_status = str(
        meta.get("providerTaskStatus") or ""
    ).strip().lower()
    live_provider_statuses = {
        "queued", "pending", "created", "submitted",
        "running", "processing", "retrying", "in_progress", "uncertain",
    }
    if (
        node_status in {"failed", "error"}
        and provider_task_id
        and provider_task_status in live_provider_statuses
        and str(task.get("status") or "") == "running"
    ):
        # A stale node label is not proof that the paid provider task failed.
        # Keep the task active and require a query of the same provider task.
        return task
    if node_status == "done":
        return store.finish_generation_task(
            task["id"],
            status="succeeded",
            phase="已完成",
            progress=100,
            error="",
            provider_task_id=provider_task_id or task.get("provider_task_id") or "",
            delivery_status="ready",
        )
    if node_status in {"failed", "error"}:
        return store.finish_generation_task(
            task["id"],
            status="failed",
            phase=str(meta.get("providerTaskPhase") or "生成失败"),
            error=str(meta.get("lastError") or "生成失败"),
            provider_task_id=provider_task_id or task.get("provider_task_id") or "",
            delivery_status="failed",
        )
    if provider_task_id and not task.get("provider_task_id"):
        return store.update_generation_task(task["id"], provider_task_id=provider_task_id)
    updated_raw = str(task.get("updated_at") or task.get("created_at") or "")
    try:
        updated_at = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - updated_at).total_seconds()
    except (TypeError, ValueError):
        age = 999
    # Async New API image tasks need the same task-id reconciliation as video.
    # Other image/audio adapters may still be synchronous and have no task id.
    if (
        str(task.get("kind") or "") in {"image", "video"}
        and str(task.get("provider") or "") == "new-apl"
        and not task.get("provider_task_id")
        and age >= 90
    ):
        return store.finish_generation_task(
            task["id"],
            status="interrupted",
            phase="任务已中断",
            error="任务在取得供应商任务 ID 前已停止，请重新生成",
            delivery_status="failed",
        )
    return task


def _unresolved_provider_task_for_node(project_id, node_id):
    """Return a recoverable paid task that must be queried before a new submit."""
    for item in store.list_generation_tasks(project_id, 300):
        if item.get("node_id") != node_id:
            continue
        if item.get("status") != "interrupted":
            continue
        if not str(item.get("provider_task_id") or "").strip():
            continue
        if str(item.get("local_result_url") or "").strip():
            continue
        return item
    return None


def _latest_generation_task(project_id, node_id):
    for item in store.list_generation_tasks(project_id, 120):
        if item.get("node_id") == node_id:
            return item
    return None


def _latest_terminal_generation_task(project_id, node_id):
    """Return the newest task outcome, never an older failure over a success."""
    for item in store.list_generation_tasks(project_id, 120):
        if item.get("node_id") != node_id:
            continue
        status = item.get("status")
        if status in {"succeeded", "failed", "interrupted"}:
            return item
    return None


def _generation_task_order_key(task):
    """Order tasks by creation, with row id as the tie-breaker."""
    return (
        str((task or {}).get("created_at") or ""),
        str((task or {}).get("updated_at") or ""),
        str((task or {}).get("id") or ""),
    )


def _task_is_newer_than_node_success(task, meta):
    """Return whether a terminal task started after the current node result."""
    task_created = str((task or {}).get("created_at") or "").strip()
    node_success = str(
        (meta or {}).get("generationFinishedAt")
        or (meta or {}).get("generatedAt")
        or ""
    ).strip()
    if not node_success:
        return False
    try:
        task_dt = datetime.fromisoformat(
            task_created.replace("Z", "+00:00")
        )
        success_dt = datetime.fromisoformat(
            node_success.replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return False
    return task_dt > success_dt


def _node_has_durable_media(meta):
    """Return whether a media node has a persisted playable result."""
    if not isinstance(meta, dict):
        return False
    if not str(meta.get("content") or "").strip():
        return False
    return str(meta.get("status") or "").strip().lower() in {
        "done", "error", "failed",
    }


def _project_media_delivery_state(meta):
    if not isinstance(meta, dict):
        return
    state = media_delivery.delivery_state(meta)
    meta["deliveryState"] = state
    meta["durable"] = media_delivery.durable(state)
    meta["durabilityRequired"] = media_delivery.mode() == "object"
    params = meta.get("params")
    if not isinstance(params, dict):
        return
    for key in ("mediaVersions", "videoVersions"):
        versions = params.get(key)
        if not isinstance(versions, list):
            continue
        for version in versions:
            if not isinstance(version, dict):
                continue
            version_state = media_delivery.delivery_state(version)
            version["deliveryState"] = version_state
            version["durable"] = media_delivery.durable(version_state)


def _latest_generation_task_by_node(project_id, node_ids):
    """Return the newest task row per node in one bounded query."""
    wanted = {
        str(node_id)
        for node_id in (node_ids or [])
        if str(node_id or "").strip()
    }
    if not project_id or not wanted:
        return {}
    latest = {}
    for task in store.list_generation_tasks_for_node_ids(
        project_id,
        sorted(wanted),
        limit=1000,
    ):
        node_id = str(task.get("node_id") or "")
        if not node_id:
            continue
        current = latest.get(node_id)
        if current is None or _generation_task_order_key(task) > _generation_task_order_key(current):
            latest[node_id] = task
    return latest


def project_workspace_nodes(project_id, workspace):
    """Project backend generation truth onto compact workspace nodes."""
    if not isinstance(workspace, dict) or not isinstance(workspace.get("nodes"), list):
        return workspace
    node_ids = [
        str(node.get("id") or "")
        for node in workspace["nodes"]
        if isinstance(node, dict) and node.get("id")
    ]
    latest_by_node = _latest_generation_task_by_node(project_id, node_ids)
    workspace["nodes"] = [
        project_generation_node_state(
            node,
            latest_by_node.get(str(node.get("id") or "")),
        )
        for node in workspace["nodes"]
    ]
    for node in workspace["nodes"]:
        media_storage.project_node_media_url(node)
        meta = node.get("metadata") if isinstance(node, dict) else None
        availability = workspace_media_availability(meta)
        if availability:
            node.setdefault("metadata", {})["mediaAvailability"] = availability
        _project_media_delivery_state(meta)
        # A broken historical version must not poison the active card. Fall
        # back to the newest version whose durable storage/local bytes exist.
        if availability == "missing" and isinstance(meta, dict):
            params = meta.get("params")
            params = params if isinstance(params, dict) else {}
            versions = params.get("mediaVersions") or params.get("videoVersions") or []
            durable_index = -1
            durable_version = None
            for index in range(len(versions) - 1, -1, -1):
                version = versions[index]
                if not isinstance(version, dict):
                    continue
                # project_node_media_url just attempted the real local/TOS
                # projection for every version. A storage declaration alone is
                # not proof that this version can still be read.
                if version.get("mediaAvailability") == "available":
                    durable_index = index
                    durable_version = version
                    break
            if durable_version:
                selected_url = media_storage.project_node_media_url({
                    "metadata": {
                        "content": str(durable_version.get("url") or ""),
                        "storage": durable_version.get("storage"),
                        "params": {},
                    },
                })
                params["mediaVersionIndex"] = durable_index
                if params.get("videoVersions"):
                    params["videoVersionIndex"] = durable_index
                params["activeVersionId"] = str(
                    durable_version.get("versionId") or ""
                )
                meta["params"] = params
                meta["content"] = selected_url or str(
                    durable_version.get("url") or ""
                )
                meta["status"] = "done"
                meta["mediaAvailability"] = "available"
                meta["storage"] = durable_version.get("storage") or {}
                meta["storageError"] = ""
    return workspace


def project_share_workspace_nodes(project_id, workspace):
    """Apply the same generation/media projection to read-only shares.

    A share must not use a weaker URL and availability path than the owner
    workspace; doing so is how a live history entry turns into "asset missing"
    only after someone opens the shared link.
    """
    if not isinstance(workspace, dict) or not isinstance(workspace.get("nodes"), list):
        return workspace
    for node in workspace["nodes"]:
        if not isinstance(node, dict):
            continue
        media_storage.project_node_media_url(node)
        meta = node.get("metadata")
        availability = workspace_media_availability(meta)
        if availability and isinstance(meta, dict):
            meta["mediaAvailability"] = availability
        _project_media_delivery_state(meta)
    return workspace


def project_project_media_urls(project):
    """Resolve private object-store receipts to fresh browser URLs on read."""
    if not isinstance(project, dict):
        return project
    payload = project.get("payload")
    if not isinstance(payload, dict):
        return project
    for node in payload.get("nodes") or []:
        media_storage.project_node_media_url(node)
        _project_media_delivery_state(
            node.get("metadata") if isinstance(node, dict) else None
        )
    return project


def project_generation_node_state(node, latest_task):
    """Return the one card state consumed by every client.

    Node metadata is the durable projection. The task ledger resolves the
    narrow crash window where the provider/task terminal state was committed
    but the node row still carries an older state. This function is pure so
    HTTP reads, recovery and tests all use exactly the same rules.
    """
    if not isinstance(node, dict):
        return node

    projected = json.loads(json.dumps(node, ensure_ascii=False))
    meta = projected.setdefault("metadata", {})
    task = latest_task if isinstance(latest_task, dict) else None
    now = store._now_iso()

    has_task = isinstance(task, dict)
    task = task if has_task else None
    task_status = str(task.get("status") or "").strip().lower() if task else ""

    if task:
        provider_task_id = str(task.get("provider_task_id") or "").strip()
        if task_status in {"queued", "submitting", "running"}:
            meta.update({
                "status": "generating",
                "lastError": "",
                "taskId": provider_task_id or meta.get("taskId") or "",
                "generationStartedAt": (
                    task.get("started_at")
                    or task.get("created_at")
                    or meta.get("generationStartedAt")
                    or now
                ),
                "generationFinishedAt": "",
                "providerTaskStatus": task_status,
                "providerTaskPhase": task.get("phase") or "生成中",
                "providerTaskProgress": int(task.get("progress") or 0),
                "providerTaskProgressChangedAt": (
                    task.get("progress_updated_at")
                    or task.get("updated_at")
                    or meta.get("providerTaskProgressChangedAt")
                ),
            })
            return projected

        if task_status == "succeeded":
            local_result_url = str(task.get("local_result_url") or "").strip()
            if local_result_url:
                meta.update({
                    "status": "done",
                    "content": local_result_url,
                    "lastError": "",
                    "taskId": provider_task_id or meta.get("taskId") or "",
                    "generatedAt": (
                        task.get("provider_finished_at")
                        or task.get("finished_at")
                        or meta.get("generatedAt")
                        or now
                    ),
                    "generationFinishedAt": (
                        task.get("finished_at")
                        or task.get("provider_finished_at")
                        or meta.get("generationFinishedAt")
                        or now
                    ),
                    "providerTaskStatus": "completed",
                    "providerTaskPhase": "已完成",
                    "providerTaskProgress": 100,
                })
                source_prompt_updated_at = str(
                    meta.get("sourcePromptUpdatedAt") or ""
                ).strip()
                if (
                    meta.get("promptStale") is True
                    and source_prompt_updated_at
                    and _task_is_newer_than_node_success(
                        task,
                        {"generationFinishedAt": source_prompt_updated_at},
                    )
                ):
                    # A successful regeneration that started after the prompt
                    # changed is the current version. Clear only the stale
                    # marker; keep content and every historical media version.
                    meta["promptStale"] = False
                    meta["promptStaleReason"] = ""
                    meta.pop("sourcePromptUpdatedAt", None)
                return projected

        if task_status == "failed":
            node_done_with_content = _node_has_durable_media(meta)
            if (
                node_done_with_content
                and not str(task.get("local_result_url") or "").strip()
                and not _task_is_newer_than_node_success(task, meta)
            ):
                # A stale task-row failure never overrides a newer durable
                # media output. Task Center keeps the history; the card stays
                # on the backend node result that was already committed.
                meta.update({
                    "status": "done",
                    "lastError": "",
                    "generationFinishedAt": (
                        meta.get("generationFinishedAt")
                        or meta.get("generatedAt")
                        or now
                    ),
                    "providerTaskStatus": "completed",
                    "providerTaskPhase": "已完成",
                })
                return projected
            current_provider_status = str(
                meta.get("providerTaskStatus") or ""
            ).strip().lower()
            if current_provider_status in {
                "queued", "pending", "created", "submitted",
                "running", "processing", "retrying",
                "in_progress", "uncertain",
            } and provider_task_id:
                # A failed local worker row with a provider task id is a
                # recoverable query lease, not a supplier-declared failure.
                meta["status"] = "generating"
                return projected
            meta.update({
                "status": "failed",
                "lastError": str(task.get("error") or meta.get("lastError") or "生成失败"),
                "generationFinishedAt": (
                    task.get("finished_at")
                    or task.get("updated_at")
                    or meta.get("generationFinishedAt")
                    or now
                ),
                "providerTaskStatus": "failed",
                "providerTaskPhase": str(task.get("phase") or "生成失败"),
            })
            return projected

        if task_status == "interrupted":
            task_phase = str(task.get("phase") or "").strip()
            node_done_with_content = _node_has_durable_media(meta)
            if (
                node_done_with_content
                and not _task_is_newer_than_node_success(task, meta)
            ):
                # An old local interruption without a provider receipt cannot
                # replace a durable node result from a later successful task.
                meta.update({
                    "status": "done",
                    "lastError": "",
                    "generationFinishedAt": (
                        meta.get("generationFinishedAt")
                        or meta.get("generatedAt")
                        or now
                    ),
                    "providerTaskStatus": "completed",
                    "providerTaskPhase": "已完成",
                })
                return projected
            if (
                not provider_task_id
                and task_phase == "重复提交已忽略"
                and str(meta.get("status") or "").strip().lower() == "done"
                and str(meta.get("content") or "").strip()
            ):
                # This is a rejected extra click, not a new generation outcome.
                # It belongs in task history and must not turn a completed card
                # into a failure.
                return projected
            if provider_task_id:
                current_status = str(meta.get("status") or "").strip().lower()
                current_provider_status = str(
                    meta.get("providerTaskStatus") or ""
                ).strip().lower()
                if (
                    current_status in {"failed", "generating"}
                    and current_provider_status in {
                        "queued", "pending", "created", "submitted",
                        "running", "processing", "retrying",
                        "in_progress", "uncertain",
                    }
                ):
                    # Keep the last durable progress receipt. An expired worker
                    # lease does not erase what the supplier already reported.
                    if current_status == "failed":
                        meta["status"] = "generating"
                    return projected
                meta.update({
                    "status": "generating",
                    "lastError": "",
                    "taskId": provider_task_id,
                    "generationFinishedAt": "",
                    "providerTaskStatus": "queued",
                    "providerTaskPhase": "等待恢复",
                })
            else:
                meta.update({
                    "status": "error",
                    "lastError": str(task.get("error") or "生成任务已中断"),
                    "generationFinishedAt": "",
                    "providerTaskStatus": "uncertain",
                    "providerTaskPhase": str(task.get("phase") or "任务已中断"),
                })
            return projected

    # No task is authoritative only when the node itself claims an active
    # operation. A media URL means the durable generation did finish and must
    # remain playable; idle local imports are left untouched.
    current_status = str(meta.get("status") or "").strip().lower()
    if (
        current_status in {"error", "failed"}
        and str(meta.get("content") or "").strip()
        and str(meta.get("providerTaskStatus") or "").strip().lower()
        in {"completed", "done", "success", "succeeded"}
    ):
        # A terminal media URL with a completed receipt is a durable success.
        # Do not leave a legacy error projection on top of an existing image,
        # video or audio result.
        meta.update({
            "status": "done",
            "lastError": "",
            "generationFinishedAt": (
                meta.get("generationFinishedAt")
                or meta.get("generatedAt")
                or now
            ),
        })
    elif current_status == "error":
        meta["status"] = "failed"
    elif (
        current_status == "generating"
        and not has_task
        and str(meta.get("content") or "").strip()
    ):
        meta.update({
            "status": "done",
            "lastError": "",
            "generationFinishedAt": (
                meta.get("generationFinishedAt")
                or meta.get("generatedAt")
                or now
            ),
            "providerTaskStatus": (
                meta.get("providerTaskStatus")
                or "completed"
            ),
            "providerTaskPhase": (
                meta.get("providerTaskPhase")
                or "已完成"
            ),
        })
    elif current_status == "generating" and not has_task and not str(meta.get("content") or "").strip():
        meta.update({
            "status": "failed",
            "lastError": (
                str(meta.get("lastError") or "").strip()
                or "生成任务记录已丢失，请重新生成"
            ),
            "generationFinishedAt": meta.get("generationFinishedAt") or now,
            "providerTaskStatus": "interrupted",
            "providerTaskPhase": "任务已结束",
        })
    return projected


def _finalize_resumed_video(project_id, node_id, result, provider, model):
    current = store.get_project(project_id)
    if not current:
        return False
    node = next((item for item in current["payload"].get("nodes", []) if item.get("id") == node_id), None)
    if not node:
        return False
    meta = node.setdefault("metadata", {})
    task_id = str(result.get("taskId") or meta.get("taskId") or "")
    generation_task = _generation_task_for_provider(
        project_id,
        node_id,
        task_id,
    )
    existing_versions = (
        (meta.get("params") or {}).get("videoVersions")
        or (meta.get("params") or {}).get("mediaVersions")
        or []
    )
    existing_result = next(
        (
            item
            for item in existing_versions
            if str(item.get("taskId") or "").strip() == task_id
            and str(item.get("url") or "").startswith("/uploads/")
        ),
        None,
    )
    generated_at = store._now_iso()
    generated_url = str((existing_result or {}).get("url") or "")
    if not generated_url:
        generated_url = persist_generated_media(
            result["url"],
            ".mp4",
            provider=provider,
            model=model,
        )
    if generation_task:
        store.update_generation_task(
            generation_task["id"],
            provider_result_url=str(result["url"]),
            local_result_url=generated_url,
            provider_finished_at=generated_at,
        )
    if not generated_url.startswith("/uploads/"):
        raise provider_client.ProviderOutcomeUncertain(
            "供应商视频已完成，但本地文件尚未落盘；"
            "系统会保留原任务并在后续同步中重试，不会重新提交。"
        )
    video_thumb_url = ensure_video_thumbnail(generated_url)
    _record_video_version(
        meta,
        generated_url,
        task_id,
        generated_at,
        thumb_url=video_thumb_url,
    )
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
        "params": {
            **((meta.get("params") or {})),
            **({"thumbUrl": video_thumb_url} if video_thumb_url else {}),
        },
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
    updates = {
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
            "activeVersionId": meta["params"].get("activeVersionId", ""),
            "videoVersions": meta["params"].get("videoVersions", []),
            "videoVersionIndex": meta["params"].get("videoVersionIndex", 0),
            **({"thumbUrl": video_thumb_url} if video_thumb_url else {}),
        },
    }
    committed = commit_generation_terminal(
        project_id,
        node_id,
        updates,
        task_id=str((generation_task or {}).get("id") or ""),
        task_updates=(
            {
                "status": "succeeded",
                "phase": "已完成",
                "progress": 100,
                "error": "",
                "delivery_status": "ready",
                "next_poll_at": "",
                "result_ready_at": generated_at,
                "finished_at": "",
                "node_written_at": "",
                "lease_owner": "",
                "lease_expires_at": None,
            }
            if generation_task
            else None
        ),
    )
    if not committed.get("committed"):
        raise provider_client.ProviderOutcomeUncertain(
            "供应商视频已完成，但画布终态写入暂未成功；"
            f"系统会保留同一任务并重试，不会重新提交。"
            f"原因：{committed.get('reason') or 'unknown'}"
        )
    return True


def _recover_persisted_result_to_canvas(project_id, node_id, task):
    """Resume from an already downloaded local result without re-downloading."""
    if not task:
        return False
    if str(task.get("status") or "") == "running":
        lease_expires_at = str(task.get("lease_expires_at") or "").strip()
        if lease_expires_at:
            try:
                lease_deadline = datetime.fromisoformat(
                    lease_expires_at.replace("Z", "+00:00")
                )
                if lease_deadline > datetime.now(timezone.utc):
                    return False
            except ValueError:
                pass
    local_url = str(task.get("local_result_url") or "").strip()
    if not local_url.startswith("/uploads/"):
        return False
    try:
        local_path = provider_client.local_upload_path(local_url)
    except RuntimeError:
        local_path = None
    if not local_path:
        detail = (
            "本地生成文件已不存在，无法补写画布。"
            "系统不会重新下载或自动重新生成；请确认后由用户主动重新生成。"
        )
        current_status = str(task.get("status") or "")
        terminal_status = (
            current_status
            if current_status in {"succeeded", "failed", "interrupted"}
            else "interrupted"
        )
        stamp = store._now_iso()
        store.update_generation_task(
            task.get("id"),
            status=terminal_status,
            phase="本地文件缺失",
            error=detail,
            delivery_status="unrecoverable",
            next_poll_at="",
            finished_at=task.get("finished_at") or stamp,
        )
        commit_node_metadata(project_id, node_id, {
            "status": "failed",
            "lastError": detail,
            "taskId": str(task.get("provider_task_id") or ""),
            "providerTaskStatus": "interrupted",
            "providerTaskPhase": "本地文件缺失",
            "generationFinishedAt": stamp,
        })
        return True
    project = store.get_project(project_id)
    if not project:
        return False
    node = next(
        (item for item in project["payload"].get("nodes", []) if item.get("id") == node_id),
        None,
    )
    if not node:
        return False
    meta = node.setdefault("metadata", {})
    task_id = str(task.get("provider_task_id") or "")
    generated_at = str(task.get("provider_finished_at") or store._now_iso())
    kind = str(task.get("kind") or "image")
    _record_media_version(
        meta,
        "video" if kind == "video" else "image",
        local_url,
        task_id,
        generated_at,
        provider_result_url=str(task.get("provider_result_url") or ""),
    )
    meta.update({
        "status": "done",
        "content": local_url,
        "lastError": "",
        "taskId": task_id,
        "generatedAt": generated_at,
        "providerResultUrl": str(task.get("provider_result_url") or ""),
        "providerTaskStatus": "completed",
        "providerTaskPhase": "已完成",
        "generationFinishedAt": generated_at,
    })
    updates = {
        "status": "done",
        "content": local_url,
        "lastError": "",
        "taskId": task_id,
        "generatedAt": generated_at,
        "providerResultUrl": str(task.get("provider_result_url") or ""),
        "providerTaskStatus": "completed",
        "providerTaskPhase": "已完成",
        "generationFinishedAt": generated_at,
        "_paramsPatch": {
            "mediaVersions": meta.get("params", {}).get("mediaVersions", []),
            "mediaVersionIndex": meta.get("params", {}).get("mediaVersionIndex", 0),
            "activeVersionId": meta.get("params", {}).get("activeVersionId", ""),
            **(
                {
                    "videoVersions": meta.get("params", {}).get("videoVersions", []),
                    "videoVersionIndex": meta.get("params", {}).get("videoVersionIndex", 0),
                }
                if kind == "video"
                else {}
            ),
        },
    }
    committed = commit_generation_terminal(
        project_id,
        node_id,
        updates,
        task_id=str(task.get("id") or ""),
        task_updates={
            "status": "succeeded",
            "phase": "已完成",
            "progress": 100,
            "error": "",
            "delivery_status": "ready",
            "next_poll_at": "",
            "result_ready_at": generated_at,
            "finished_at": "",
            "node_written_at": "",
            "lease_owner": "",
            "lease_expires_at": None,
        },
    )
    return bool(committed.get("committed"))


def materialize_saved_image_results(limit=100):
    """Localize already-paid image URLs without creating new generation tasks."""
    recovered = 0
    for task in store.list_materializing_image_tasks(limit=limit):
        task_id = str(task.get("id") or "").strip()
        project_id = str(task.get("project_id") or "").strip()
        node_id = str(task.get("node_id") or "").strip()
        provider_url = str(task.get("provider_result_url") or "").strip()
        if not task_id or not project_id or not node_id or not provider_url:
            continue
        try:
            provider, model, _ = provider_catalog.resolve_model(
                task.get("provider"),
                task.get("model"),
                "image",
            )
        except Exception:
            provider, model = {}, {}
        try:
            local_url = persist_generated_media(
                provider_url,
                ".png",
                provider=provider,
                model=model,
            )
        except Exception as exc:
            store.update_generation_task(
                task_id,
                status="interrupted",
                phase="结果下载中",
                error=f"供应商图片已生成，本地下载仍失败：{exc}",
                delivery_status="pending",
                finished_at="",
            )
            continue
        if not local_url.startswith("/uploads/"):
            continue
        updated = store.update_generation_task(
            task_id,
            local_result_url=local_url,
            delivery_status="materializing",
            provider_finished_at=(
                task.get("provider_finished_at") or store._now_iso()
            ),
        ) or {**task, "local_result_url": local_url}
        if _recover_persisted_result_to_canvas(project_id, node_id, updated):
            recovered += 1
    return recovered


def recover_missing_generated_media(project_id, node_id):
    """Restore a missing local file from its successful provider receipt only."""
    project = store.get_project(project_id)
    if not project:
        raise KeyError("项目不存在")
    node = next(
        (
            item
            for item in project["payload"].get("nodes", [])
            if item.get("id") == node_id
        ),
        None,
    )
    if not node or node.get("type") not in {"image", "audio", "video"}:
        raise ValueError("节点不是可恢复的媒体节点")
    meta = node.get("metadata") or {}
    content = str(meta.get("content") or "")
    if content.startswith("/uploads/"):
        try:
            existing = provider_client.local_upload_path(content)
        except RuntimeError:
            existing = None
        if existing:
            return {"restored": False, "reason": "本地文件仍存在", "content": content}
    task = store.list_latest_success_task_for_node(project_id, node_id)
    if not task:
        return {"restored": False, "reason": "没有可恢复的供应商成功记录"}
    task_kind = str(task.get("kind") or node.get("type") or "image")
    fallback_ext = {
        "image": ".png",
        "video": ".mp4",
        "audio": ".mp3",
    }.get(task_kind, ".bin")
    provider = {}
    model = {}
    try:
        provider, model, _ = provider_catalog.resolve_model(
            task.get("provider"),
            task.get("model"),
            task_kind,
        )
    except Exception:
        # A historical task can outlive a disabled model profile. Its persisted
        # result URL remains recoverable without issuing a new provider call.
        provider = {}
        model = {}
    provider_url = str(task.get("provider_result_url") or "").strip()
    if not provider_url:
        return {"restored": False, "reason": "任务没有保存供应商原链"}
    try:
        local_url = persist_generated_media(
            provider_url,
            fallback_ext,
            provider=provider,
            model=model,
        )
    except Exception as exc:
        return {
            "restored": False,
            "reason": f"供应商原链重新落盘失败：{exc}",
        }
    if not local_url.startswith("/uploads/"):
        return {"restored": False, "reason": "供应商原链未成功写入本地存储"}
    recovered = _recover_persisted_result_to_canvas(
        project_id,
        node_id,
        {
            **task,
            "status": "succeeded",
            "local_result_url": local_url,
            "delivery_status": "pending",
        },
    )
    return {
        "restored": bool(recovered),
        "reason": "" if recovered else "画布回写未完成",
        "content": local_url,
        "taskId": task.get("id"),
    }


def _recover_existing_local_task_result(project_id, node_id, task):
    """Use a durable local result before issuing any provider query."""
    if not task:
        return False
    local_url = str(task.get("local_result_url") or "").strip()
    if local_url.startswith("/uploads/"):
        try:
            if provider_client.local_upload_path(local_url):
                return _recover_persisted_result_to_canvas(
                    project_id,
                    node_id,
                    task,
                )
        except RuntimeError:
            pass
    project = store.get_project(project_id)
    node = next(
        (
            item
            for item in (project or {}).get("payload", {}).get("nodes", [])
            if item.get("id") == node_id
        ),
        None,
    )
    meta = (node or {}).get("metadata") or {}
    content = str(meta.get("content") or "").strip()
    if (
        meta.get("status") != "done"
        or not content.startswith("/uploads/")
    ):
        return False
    try:
        existing_path = provider_client.local_upload_path(content)
    except RuntimeError:
        return False
    if not existing_path:
        return False
    task_updates = {
        "status": "succeeded",
        "phase": "已完成",
        "progress": 100,
        "error": "",
        "delivery_status": "ready",
        "next_poll_at": "",
        "result_ready_at": task.get("result_ready_at") or store._now_iso(),
        "finished_at": task.get("finished_at") or store._now_iso(),
        "node_written_at": task.get("node_written_at") or store._now_iso(),
        "lease_owner": "",
        "lease_expires_at": None,
    }
    if not local_url:
        task_updates["local_result_url"] = content
    store.update_generation_task(task["id"], **task_updates)
    return True


def _mark_delivery_unrecoverable(task, detail):
    """Classify a paid result that has no durable receipt or local file."""
    task_id = str((task or {}).get("id") or "").strip()
    if not task_id:
        return None
    current_status = str(task.get("status") or "")
    terminal_status = (
        current_status
        if current_status in {"succeeded", "failed", "interrupted"}
        else "interrupted"
    )
    stamp = store._now_iso()
    saved = store.update_generation_task(
        task_id,
        status=terminal_status,
        phase="结果无法恢复",
        error=detail,
        delivery_status="unrecoverable",
        next_poll_at="",
        finished_at=task.get("finished_at") or stamp,
    )
    project_id = str((task or {}).get("project_id") or "").strip()
    node_id = str((task or {}).get("node_id") or "").strip()
    if project_id and node_id:
        try:
            commit_node_metadata(project_id, node_id, {
                "status": "failed",
                "lastError": detail,
                "providerTaskStatus": "unrecoverable",
                "providerTaskPhase": "结果无法恢复",
                "generationFinishedAt": stamp,
            })
        except Exception as exc:
            print(
                f"[generation-delivery] node classification deferred: "
                f"task={task_id} error={exc}",
                file=sys.stderr,
            )
    return saved


def reconcile_pending_generation_deliveries():
    """Finish provider-successful results not yet projected onto their nodes.

    This path may either reuse a durable local file or redownload the exact
    same provider result. It never submits a new provider task.
    """
    recovered = 0
    for pending_task in store.list_delivery_pending_tasks(200):
        task = pending_task
        local_url = str(task.get("local_result_url") or "").strip()
        project_id = str(task.get("project_id") or "")
        node_id = str(task.get("node_id") or "")
        if not project_id or not node_id:
            continue
        if local_url.startswith("/uploads/"):
            task = store.claim_local_delivery(str(task.get("id") or ""))
            if not task:
                continue
        else:
            provider_url = str(task.get("provider_result_url") or "").strip()
            if not provider_url:
                if (
                    str(task.get("status") or "") == "succeeded"
                    or str(task.get("delivery_status") or "") == "materializing"
                ):
                    _mark_delivery_unrecoverable(
                        task,
                        "结果无法恢复：供应商任务已结束，但本地文件和供应商结果地址均不存在；"
                        "系统不会自动重提，请查询原任务后由用户确认重新生成。",
                    )
                continue
            attempts = max(0, int(task.get("delivery_attempts") or 0))
            delay_seconds = min(30 * 60, 30 * (4 ** attempts))
            claimed = store.claim_delivery_retry(
                str(task.get("id") or ""),
                delay_seconds,
            )
            if not claimed:
                # Another reconciler won the claim, or backoff is not due yet.
                continue
            task = claimed
            provider = {}
            model = {}
            try:
                provider, model, _ = provider_catalog.resolve_model(
                    task.get("provider"),
                    task.get("model"),
                    str(task.get("kind") or ""),
                )
            except Exception as exc:
                # Deleted/renamed model profiles must not block recovery of an
                # already-paid result URL. The URL is the durable receipt.
                print(
                    f"[generation-delivery] model profile unavailable: "
                    f"task={task.get('id')} error={exc}",
                    file=sys.stderr,
                )
            try:
                local_url = persist_generated_media(
                    provider_url,
                    ".mp4" if task.get("kind") == "video" else ".png",
                    provider=provider,
                    model=model,
                )
            except Exception as exc:
                print(
                    f"[generation-delivery] redownload deferred: "
                    f"task={task.get('id')} attempt={task.get('delivery_attempts')} "
                    f"next={task.get('delivery_next_attempt_at')} error={exc}",
                    file=sys.stderr,
                )
                continue
            if not local_url.startswith("/uploads/"):
                continue
            task = store.update_generation_task(
                str(task.get("id") or ""),
                local_result_url=local_url,
                delivery_status="pending",
            ) or {**task, "local_result_url": local_url}
        try:
            if _recover_persisted_result_to_canvas(project_id, node_id, task):
                saved_task = store.get_generation_task(str(task.get("id") or ""))
                if saved_task and str(saved_task.get("status") or "") == "succeeded":
                    recovered += 1
        except Exception as exc:
            print(
                f"[generation-delivery] deferred: task={task.get('id')} error={exc}",
                file=sys.stderr,
            )
    return recovered


def _mark_resumed_task_terminal_query_failure(
    project_id,
    node_id,
    generation_task,
    provider_task_id,
    exc,
    kind,
):
    detail = _provider_query_failure_message(exc, kind)
    finished_at = store._now_iso()
    updates = {
        "status": "failed",
        "lastError": detail,
        "taskId": provider_task_id,
        "providerTaskStatus": "failed",
        "providerTaskPhase": "查询失败",
        "generationFinishedAt": finished_at,
    }
    if not generation_task:
        commit_node_metadata(project_id, node_id, updates)
        return True
    committed = commit_generation_terminal(
        project_id,
        node_id,
        updates,
        task_id=str(generation_task.get("id") or ""),
        task_updates={
            "status": "failed",
            "phase": "查询失败",
            "error": detail,
            "provider_task_id": provider_task_id,
            "delivery_status": "failed",
                "next_poll_at": "",
            "finished_at": "",
            "node_written_at": "",
        },
        lease_owner=str(generation_task.get("lease_owner") or ""),
    )
    return bool(committed.get("committed"))


def _mark_resumed_query_retry(project_id, node_id, generation_task, exc):
    detail = str(exc)
    updated_task = None
    if generation_task:
        updated_task = store.update_generation_task(
            generation_task["id"],
            status="running",
            phase="查询重试中",
            error=detail,
            _lease_owner=str(generation_task.get("lease_owner") or ""),
        )
        if not updated_task or (
            updated_task.get("status") != "running"
            or updated_task.get("lease_owner") != generation_task.get("lease_owner")
        ):
            return False
    commit_node_metadata(project_id, node_id, {
        "status": "generating",
        "providerTaskStatus": "retrying",
        "providerTaskPhase": "查询重试中",
        "lastError": "",
    })
    publish_project_event(project_id, "media.progress", node_id, {
        "status": "generating",
        "error": detail,
        "providerTaskStatus": "retrying",
        "providerTaskPhase": "查询重试中",
    })
    return True


def _resume_video_task(project_id, node_id, task_id, provider, model):
    key = (project_id, node_id)
    # The provider accepted this paid task before the local worker released the
    # row, so the row is normally `interrupted`, not `running`/`queued`. Looking
    # it up with the active-only helper caused the downloaded image to be
    # written to the node while the task row stayed pending forever. Resolve by
    # the durable provider task id across all local states so the node, task
    # receipt, delivery status, and event can commit atomically.
    generation_task = _generation_task_for_provider(
        project_id,
        node_id,
        task_id,
    )
    if not generation_task:
        generation_task = next(
            (
                item
                for item in store.list_generation_tasks(project_id, 300)
                if item.get("node_id") == node_id
                and item.get("provider_task_id") in {"", task_id}
                and item.get("status") in {"queued", "interrupted"}
            ),
            None,
        )
    if generation_task:
        store.schedule_provider_task_poll(
            generation_task["id"],
            store._now_iso(),
        )
    if _recover_persisted_result_to_canvas(project_id, node_id, generation_task):
        return
    if generation_task and generation_task.get("local_result_url"):
        return
    lease_owner = ""
    heartbeat = None
    if generation_task:
        lease_owner = f"resume:{secrets.token_hex(12)}"
        if not store.claim_generation_task(
            generation_task["id"],
            lease_owner,
            TASK_LEASE_SECONDS,
            allow_recover=True,
        ):
            return
        generation_task["lease_owner"] = lease_owner
        heartbeat = _start_generation_task_heartbeat(
            generation_task["id"],
            lease_owner,
        )
    try:
        last_query_error = ""
        while True:
            try:
                state = provider_client.check_video_task(provider, model, task_id)
                last_query_error = ""
            except Exception as exc:
                last_query_error = str(exc)
                error_kind = _provider_query_error_kind(exc)
                if error_kind != "retryable":
                    _mark_resumed_task_terminal_query_failure(
                        project_id,
                        node_id,
                        generation_task,
                        task_id,
                        exc,
                        error_kind,
                    )
                    return
                if not _mark_resumed_query_retry(
                    project_id,
                    node_id,
                    generation_task,
                    exc,
                ):
                    return
                time.sleep(15)
                continue
            progress = state.get("progress")
            if generation_task:
                updated = store.update_generation_task(
                    generation_task["id"], status="running",
                    phase=_provider_task_phase(state.get("rawStatus") or state.get("status"), progress),
                    progress=int(progress) if isinstance(progress, (int, float)) else None,
                    next_poll_at=(
                        store._now_iso()
                        if state.get("status") in {"completed", "failed"}
                        else (
                            datetime.now(timezone.utc) + timedelta(seconds=8)
                        ).isoformat()
                    ),
                    _lease_owner=lease_owner,
                )
                if not updated or updated.get("lease_owner") != lease_owner:
                    return
            current_project = store.get_project(project_id) or {}
            current_node = next(
                (
                    item
                    for item in current_project.get("payload", {}).get("nodes", [])
                    if item.get("id") == node_id
                ),
                {},
            )
            progress_meta = dict((current_node.get("metadata") or {}))
            _with_progress_changed_at(
                progress_meta,
                state.get("rawStatus") or state.get("status"),
                progress,
            )
            commit_node_metadata(project_id, node_id, {
                "providerTaskStatus": state.get("rawStatus") or state.get("status"),
                "providerTaskPhase": _provider_task_phase(state.get("rawStatus") or state.get("status"), progress),
                "providerTaskProgress": progress,
                "providerTaskProgressChangedAt": progress_meta.get("providerTaskProgressChangedAt"),
                "lastError": "" if state.get("status") != "failed" else str(state.get("error") or "生成失败"),
            })
            if state.get("status") == "completed":
                if generation_task:
                    terminal = store.finish_generation_task(
                        generation_task["id"],
                        status="running",
                        phase="正在落盘并回写画布",
                        lease_owner=lease_owner,
                    )
                    if not terminal:
                        return
                _finalize_resumed_video(project_id, node_id, state, provider, model)
                return
            if state.get("status") == "failed":
                failure_detail = str(state.get("error") or "视频生成失败")
                finished_at = store._now_iso()
                task_id_value = str(
                    state.get("taskId")
                    or (generation_task or {}).get("provider_task_id")
                    or task_id
                )
                updates = {
                    "status": "failed",
                    "lastError": failure_detail,
                    "providerTaskStatus": "failed",
                    "providerTaskPhase": "生成失败",
                    "generationFinishedAt": finished_at,
                }
                if generation_task:
                    commit_generation_terminal(
                        project_id,
                        node_id,
                        updates,
                        task_id=str(generation_task.get("id") or ""),
                        task_updates={
                            "status": "failed",
                            "phase": "生成失败",
                            "error": failure_detail,
                            "provider_task_id": task_id_value,
                            "delivery_status": "failed",
                            "finished_at": "",
                            "node_written_at": "",
                        },
                        lease_owner=lease_owner,
                    )
                else:
                    commit_node_metadata(project_id, node_id, updates)
                return
            time.sleep(8)
    finally:
        if generation_task:
            _finish_generation_task_lease(
                generation_task["id"],
                lease_owner,
                heartbeat,
            )
        with RESUMED_VIDEO_TASKS_LOCK:
            RESUMED_VIDEO_TASKS.discard(key)


def _finalize_resumed_image(project_id, node_id, state, provider, model):
    project = store.get_project(project_id)
    if not project:
        return False
    node = next(
        (item for item in project["payload"].get("nodes", [])
         if item.get("id") == node_id),
        None,
    )
    if not node:
        return False
    meta = node.get("metadata") or {}
    task_id = str(state.get("taskId") or "")
    generated_url = persist_generated_media(str(state.get("url") or ""), ".png")
    if not generated_url.startswith("/uploads/"):
        raise provider_client.ProviderOutcomeUncertain(
            "供应商图片已完成，但没有拿到本地新文件"
        )
    generated_at = store._now_iso()
    generation_task = _generation_task_for_provider(
        project_id,
        node_id,
        task_id,
    )
    _record_media_version(
        meta,
        "image",
        generated_url,
        task_id,
        generated_at,
        provider_result_url=str(state.get("url") or ""),
    )
    meta.update({
        "status": "done",
        "content": generated_url,
        "lastError": "",
        "taskId": task_id,
        "generatedAt": generated_at,
        "generationFinishedAt": generated_at,
        "providerResultUrl": str(state.get("url") or ""),
        "providerTaskStatus": "completed",
        "providerTaskPhase": "已完成",
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
    updates = {
        "status": "done",
        "content": generated_url,
        "lastError": "",
        "taskId": task_id,
        "generatedAt": generated_at,
        "generationFinishedAt": generated_at,
        "providerResultUrl": str(state.get("url") or ""),
        "providerTaskStatus": "completed",
        "providerTaskPhase": "已完成",
        "provider": provider.get("id"),
        "model": model.get("id"),
        "remoteModel": model.get("remoteModel"),
        "production": meta["production"],
        "_paramsPatch": {
            "mediaVersions": meta["params"].get("mediaVersions", []),
            "mediaVersionIndex": meta["params"].get("mediaVersionIndex", 0),
        },
    }
    committed = commit_generation_terminal(
        project_id,
        node_id,
        updates,
        task_id=str((generation_task or {}).get("id") or ""),
        task_updates=(
            {
                "status": "succeeded",
                "phase": "已完成",
                "progress": 100,
                "error": "",
                "delivery_status": "ready",
                "next_poll_at": "",
                "result_ready_at": generated_at,
                "local_result_url": generated_url,
                "provider_result_url": str(state.get("url") or ""),
                "provider_finished_at": generated_at,
                "finished_at": "",
                "node_written_at": "",
                "lease_owner": "",
                "lease_expires_at": None,
            }
            if generation_task
            else None
        ),
    )
    if not committed.get("committed"):
        raise RuntimeError(
            f"恢复图片终态写入失败：{committed.get('reason') or 'unknown'}"
        )
    return True


def _resume_image_task(project_id, node_id, task_id, provider, model):
    # The provider task id is part of the key. Two different provider tasks for
    # the same node must be able to be checked independently; only duplicate
    # consumers of the same paid task are forbidden.
    key = (project_id, node_id, str(task_id or "").strip())
    with RESUMED_VIDEO_TASKS_LOCK:
        if key in RESUMED_VIDEO_TASKS:
            return
        RESUMED_VIDEO_TASKS.add(key)
    task_id = str(task_id or "").strip()
    generation_task = _generation_task_for_provider(project_id, node_id, task_id)
    if not generation_task:
        with RESUMED_VIDEO_TASKS_LOCK:
            RESUMED_VIDEO_TASKS.discard(key)
        return
    if _recover_persisted_result_to_canvas(project_id, node_id, generation_task):
        with RESUMED_VIDEO_TASKS_LOCK:
            RESUMED_VIDEO_TASKS.discard(key)
        return
    current_task = store.get_generation_task(generation_task["id"]) or {}
    lease_expires_at = str(current_task.get("lease_expires_at") or "").strip()
    if (
        current_task.get("status") == "running"
        and current_task.get("lease_owner")
        and lease_expires_at
        and lease_expires_at > datetime.now(timezone.utc).isoformat()
    ):
        with RESUMED_VIDEO_TASKS_LOCK:
            RESUMED_VIDEO_TASKS.discard(key)
        return
    lease_owner = f"resume:{secrets.token_hex(12)}"
    if not store.claim_generation_task_for_recovery(
        generation_task["id"],
        task_id,
        lease_owner,
        TASK_LEASE_SECONDS,
    ):
        with RESUMED_VIDEO_TASKS_LOCK:
            RESUMED_VIDEO_TASKS.discard(key)
        return
    store.schedule_provider_task_poll(
        generation_task["id"],
        store._now_iso(),
    )
    generation_task = store.get_generation_task(generation_task["id"]) or generation_task
    generation_task["lease_owner"] = lease_owner
    heartbeat = _start_generation_task_heartbeat(generation_task["id"], lease_owner)
    try:
        while True:
            try:
                state = provider_client.check_image_task(provider, model, task_id)
            except Exception as exc:
                error_kind = _provider_query_error_kind(exc)
                if error_kind != "retryable":
                    _mark_resumed_task_terminal_query_failure(
                        project_id,
                        node_id,
                        generation_task,
                        task_id,
                        exc,
                        error_kind,
                    )
                    return
                if not _mark_resumed_query_retry(
                    project_id,
                    node_id,
                    generation_task,
                    exc,
                ):
                    return
                time.sleep(15)
                continue
            progress = state.get("progress")
            if generation_task:
                updated = store.update_generation_task(
                    generation_task["id"], status="running",
                    phase=_provider_task_phase(state.get("rawStatus") or state.get("status"), progress),
                    progress=int(progress) if isinstance(progress, (int, float)) else None,
                    next_poll_at=(
                        store._now_iso()
                        if state.get("status") in {"completed", "failed"}
                        else (
                            datetime.now(timezone.utc) + timedelta(seconds=8)
                        ).isoformat()
                    ),
                    _lease_owner=lease_owner,
                )
                if not updated or updated.get("lease_owner") != lease_owner:
                    return
            commit_node_metadata(project_id, node_id, {
                "providerTaskStatus": state.get("rawStatus") or state.get("status"),
                "providerTaskPhase": _provider_task_phase(state.get("rawStatus") or state.get("status"), progress),
                "providerTaskProgress": progress,
                "lastError": "" if state.get("status") != "failed" else str(state.get("error") or "生成失败"),
            })
            if state.get("status") == "completed":
                if generation_task:
                    claimed = store.finish_generation_task(
                        generation_task["id"],
                        status="running",
                        phase="正在落盘并回写画布",
                        lease_owner=lease_owner,
                    )
                    if not claimed:
                        return
                _finalize_resumed_image(project_id, node_id, state, provider, model)
                return
            if state.get("status") == "failed":
                failure_detail = str(state.get("error") or "图片生成失败")
                finished_at = store._now_iso()
                task_id_value = str(
                    state.get("taskId")
                    or (generation_task or {}).get("provider_task_id")
                    or task_id
                )
                updates = {
                    "status": "failed",
                    "lastError": failure_detail,
                    "providerTaskStatus": "failed",
                    "providerTaskPhase": "生成失败",
                    "generationFinishedAt": finished_at,
                }
                if generation_task:
                    commit_generation_terminal(
                        project_id,
                        node_id,
                        updates,
                        task_id=str(generation_task.get("id") or ""),
                        task_updates={
                            "status": "failed",
                            "phase": "生成失败",
                            "error": failure_detail,
                            "provider_task_id": task_id_value,
                            "delivery_status": "failed",
                            "finished_at": "",
                            "node_written_at": "",
                        },
                        lease_owner=lease_owner,
                    )
                else:
                    commit_node_metadata(project_id, node_id, updates)
                return
            time.sleep(8)
    finally:
        if generation_task:
            _finish_generation_task_lease(
                generation_task["id"],
                lease_owner,
                heartbeat,
            )
        with RESUMED_VIDEO_TASKS_LOCK:
            RESUMED_VIDEO_TASKS.discard(key)


def reconcile_generation_task_records():
    """Align persisted task rows with the authoritative canvas node state."""
    nodes = {}
    reconciled = 0
    for task in store.list_runtime_reconcile_tasks(200):
        project_id = str(task.get("project_id") or "")
        node_id = str(task.get("node_id") or "")
        if not project_id or not node_id:
            continue
        node = store.get_projection_node(project_id, node_id)
        if node:
            nodes[(project_id, node_id)] = node
        if task.get("status") not in {"queued", "submitting", "running"}:
            if task.get("status") in {"failed", "interrupted"}:
                node = nodes.get((project_id, node_id))
                meta = (node or {}).get("metadata") or {}
                if not node or str(meta.get("status") or "") != "generating":
                    continue
                provider_terminal_failure = (
                    generation_outcome.is_provider_terminal_failure(task)
                )
                task_provider_id = str(task.get("provider_task_id") or "")
                node_provider_id = str(meta.get("taskId") or "")
                if task_provider_id and node_provider_id and task_provider_id != node_provider_id:
                    continue
                provider_status = str(meta.get("providerTaskStatus") or "").strip().lower()
                if (
                    not provider_terminal_failure
                    and
                    task_provider_id
                    and node_provider_id == task_provider_id
                    and provider_status in {
                        "queued", "pending", "created", "submitted",
                        "running", "processing", "retrying", "in_progress", "uncertain",
                    }
                ):
                    # A stale worker may have marked the row failed after the paid
                    # provider task already existed. Keep it recoverable until the
                    # same provider task returns completed or failed.
                    if task.get("status") == "failed":
                        reopened = store.reopen_generation_task_for_provider(
                            task["id"],
                            task_provider_id,
                            message=(
                                "供应商任务仍在执行；系统会继续查询原任务，"
                                "不会自动重新提交。"
                            ),
                        )
                        if reopened:
                            reconciled += 1
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
        node = nodes.get((project_id, node_id))
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
    """Project the newest task outcome without reviving historical failures."""
    repaired = 0
    for project_row in store.list_projects():
        project = store.get_project(project_row.get("id"))
        if not project:
            continue
        tasks_by_node = {}
        for task in store.list_generation_tasks(
            project["id"],
            1000,
        ):
            node_id = str(task.get("node_id") or "")
            if node_id and node_id not in tasks_by_node:
                tasks_by_node[node_id] = task
        for node in project["payload"].get("nodes", []):
            if node.get("type") not in {"image", "audio", "video"}:
                continue
            meta = node.get("metadata") or {}
            task = tasks_by_node.get(str(node.get("id") or ""))
            if not task:
                continue
            projection = project_generation_node_state(node, task)
            projected_meta = projection.get("metadata") or {}
            updates = {
                key: projected_meta.get(key)
                for key in (
                    "status", "lastError", "taskId", "generatedAt",
                    "generationStartedAt", "generationFinishedAt",
                    "providerTaskStatus", "providerTaskPhase",
                    "providerTaskProgress", "providerTaskProgressChangedAt",
                )
                if projected_meta.get(key) != meta.get(key)
            }
            if not updates:
                continue
            commit_node_metadata(project["id"], node["id"], updates)
            repaired += 1
    return repaired


def reconcile_generation_runtime_once():
    """Repair persisted task state without starting provider I/O."""
    journal_recovered = _run_for_active_tenants(
        reconcile_provider_task_journal
    )
    expired = _collect_for_active_tenants(
        store.expire_stale_generation_tasks
    )
    unconfirmed_expired = _collect_for_active_tenants(
        store.expire_unconfirmed_generation_tasks
    )
    delivered = _run_for_active_tenants(
        reconcile_pending_generation_deliveries
    )
    reconciled = _run_for_active_tenants(reconcile_generation_task_records)
    # Scan a bounded set of due tasks, never every tenant and every project.
    resumed = _resume_expired_provider_tasks(expired)
    resumed += _dispatch_due_provider_tasks()
    return {
        "expired": expired,
        "unconfirmedExpired": len(unconfirmed_expired),
        "journalRecovered": int(journal_recovered or 0),
        "delivered": int(delivered or 0),
        "reconciled": int(reconciled or 0),
        "resumed": int(resumed or 0),
    }


def _dispatch_due_provider_tasks(limit=50):
    """Claim a bounded due set and give each unique provider task one poller."""
    started = 0
    for tenant in _active_tenants():
        tenant_id = tenant["id"] if tenant else None
        tenant_slug = tenant["slug"] if tenant else "local"
        try:
            with (tenant_context.use_tenant(tenant_id, tenant_slug) if tenant else tenant_context.use_context(None)):
                for task in store.list_due_provider_tasks(limit=limit):
                    project_id = str(task.get("project_id") or "").strip()
                    node_id = str(task.get("node_id") or "").strip()
                    provider_task_id = str(task.get("provider_task_id") or "").strip()
                    if not project_id or not node_id or not provider_task_id:
                        continue
                    if generation_outcome.is_provider_terminal_failure(task):
                        continue
                    try:
                        provider, model, _ = provider_catalog.resolve_model(
                            task.get("provider"),
                            task.get("model"),
                            str(task.get("kind") or ""),
                        )
                    except Exception:
                        continue
                    if task.get("kind") == "image" and str(model.get("adapter") or "") != "newapi_image":
                        continue
                    key = (tenant_id or "legacy", project_id, node_id, provider_task_id)
                    with RESUMED_VIDEO_TASKS_LOCK:
                        if key in RESUMED_VIDEO_TASKS:
                            continue
                        RESUMED_VIDEO_TASKS.add(key)

                    def run(
                        tenant_id=tenant_id,
                        tenant_slug=tenant_slug,
                        project_id=project_id,
                        node_id=node_id,
                        provider_task_id=provider_task_id,
                        provider=provider,
                        model=model,
                        kind=str(task.get("kind") or ""),
                        key=key,
                    ):
                        try:
                            with RESUMED_VIDEO_TASKS_LOCK:
                                RESUMED_VIDEO_TASKS.discard(key)
                            _run_in_tenant(
                                tenant_id,
                                tenant_slug,
                                _resume_image_task if kind == "image" else _resume_video_task,
                                project_id,
                                node_id,
                                provider_task_id,
                                provider,
                                model,
                            )
                        except Exception as exc:
                            print(
                                f"[generation-resume] due task failed "
                                f"node={node_id} task={provider_task_id} error={exc}",
                                file=sys.stderr,
                            )
                        finally:
                            with RESUMED_VIDEO_TASKS_LOCK:
                                RESUMED_VIDEO_TASKS.discard(key)

                    try:
                        threading.Thread(
                            target=run,
                            name=f"generation-due-{node_id}",
                            daemon=True,
                        ).start()
                        started += 1
                    except Exception:
                        with RESUMED_VIDEO_TASKS_LOCK:
                            RESUMED_VIDEO_TASKS.discard(key)
        except Exception as exc:
            print(
                f"[generation-resume] due scan failed tenant={tenant_slug} error={exc}",
                file=sys.stderr,
            )
    return started


def reconcile_recoverable_provider_tasks():
    """Attach durable provider ids to their nodes without provider I/O."""
    tasks = store.list_generation_tasks(None, 300)
    recovered = 0
    for task in tasks:
        provider_task_id = str(task.get("provider_task_id") or "").strip()
        if (
            not provider_task_id
            or task.get("kind") not in {"image", "video"}
            or task.get("status") not in {"queued", "submitting", "running", "interrupted", "failed"}
            or generation_outcome.is_provider_terminal_failure(task)
        ):
            continue
        project_id = str(task.get("project_id") or "")
        node_id = str(task.get("node_id") or "")
        if _recover_existing_local_task_result(
            project_id,
            node_id,
            task,
        ):
            continue
        project = store.get_project(project_id)
        if not project:
            continue
        node = next(
            (
                item
                for item in (project.get("payload") or {}).get("nodes", [])
                if item.get("id") == node_id
            ),
            None,
        )
        if not node:
            continue
        meta = node.get("metadata") or {}
        if (
            str(meta.get("taskId") or "") == provider_task_id
            and str(meta.get("status") or "") == "generating"
            and str(meta.get("providerTaskStatus") or "").lower()
            in {"queued", "pending", "created", "submitted", "running", "processing", "retrying", "in_progress", "uncertain"}
        ):
            continue
        commit_node_metadata(project_id, node_id, {
            "status": "generating",
            "taskId": provider_task_id,
            "providerTaskStatus": "queued",
            "providerTaskPhase": "等待恢复",
            "providerTaskProgress": 0,
            "lastError": "",
            "generationFinishedAt": "",
        })
        recovered += 1
    return recovered


def start_storage_maintenance():
    """空闲页回收：项目按整包 payload 覆盖写，SQLite 只涨不缩。

    启动后先等 90 秒（避开启动期导入/对账的写高峰），之后每 6 小时检查一次，
    只在空闲页占比 >35% 且空闲 >32MB 时才 VACUUM，避免为几 MB 碎片锁库。
    """
    def run():
        time.sleep(90)
        while True:
            try:
                freed = _run_for_active_tenants(store.maintain_storage)
                if freed:
                    print(
                        f"[storage] 回收数据库空闲页 {freed / (1024 * 1024):.1f}MB",
                        file=sys.stderr,
                    )
            except Exception as exc:
                print(f"[storage] 数据库整理失败：{exc}", file=sys.stderr)
            time.sleep(6 * 60 * 60)

    threading.Thread(target=run, name="storage-maintenance", daemon=True).start()


def start_audit_maintenance():
    """Rotate tenant audit history outside request and generation paths."""

    def run():
        time.sleep(120)
        while True:
            try:
                deleted = _run_for_active_tenants(store.rotate_audit_events)
                deleted += _run_for_active_tenants(
                    store.rotate_frontend_errors
                )
                if deleted:
                    print(
                        f"[audit] 轮转清理 {deleted} 条过期审计记录",
                        file=sys.stderr,
                    )
            except Exception as exc:
                print(f"[audit] 审计轮转失败：{exc}", file=sys.stderr)
            time.sleep(6 * 60 * 60)

    threading.Thread(target=run, name="audit-maintenance", daemon=True).start()


def start_wal_maintenance():
    """Checkpoint WAL away from paid generation commits.

    SQLite's default autocheckpoint runs inside whichever commit crosses the
    page threshold. On Windows that turns an otherwise ~20ms task update into a
    0.2-2s stall. Keep automatic checkpoints disabled at connection level and
    converge the WAL from this low-frequency maintenance thread instead.
    """
    backend = os.environ.get("CANVAS_DB_BACKEND", "").strip().lower()
    if backend in {"postgres", "postgresql", "pg"}:
        # PostgreSQL owns WAL checkpointing. Starting the SQLite maintenance
        # loop here only produced a periodic "transaction already in progress"
        # warning in PostgreSQL logs.
        return

    def run():
        time.sleep(20)
        while True:
            try:
                _run_for_active_tenants(
                    lambda: store.checkpoint_wal(mode="PASSIVE")
                )
            except Exception as exc:
                print(f"[storage] WAL checkpoint 失败：{exc}", file=sys.stderr)
            time.sleep(30)

    threading.Thread(target=run, name="wal-maintenance", daemon=True).start()


def start_generation_task_reconciler():
    def run():
        while True:
            try:
                result = reconcile_generation_runtime_once()
                for item in result["expired"]:
                    print(
                        f"[generation-tasks] stale task converged: "
                        f"task={item.get('id')} status={item.get('status')}",
                        file=sys.stderr,
                    )
                if result["delivered"]:
                    print(
                        f"[generation-delivery] projected {result['delivered']} persisted results",
                        file=sys.stderr,
                    )
                if result.get("journalRecovered"):
                    print(
                        f"[generation-journal] recovered "
                        f"{result['journalRecovered']} provider task receipts",
                        file=sys.stderr,
                    )
            except Exception as exc:
                print(f"[generation-tasks] 定时对账失败：{exc}", file=sys.stderr)
            time.sleep(30)

    threading.Thread(target=run, name="generation-task-reconciler", daemon=True).start()


def start_pending_video_resumers():
    for tenant in _active_tenants():
        if tenant is None:
            reconcile_recoverable_provider_tasks()
        else:
            with tenant_context.use_tenant(tenant["id"], tenant["slug"]):
                reconcile_recoverable_provider_tasks()
    thread_options = {}
    try:
        thread_options = inspect.signature(threading.Thread).parameters
    except (TypeError, ValueError):
        thread_options = {}
    supports_name = "name" in thread_options
    supports_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in thread_options.values()
    )

    def start_thread(target, name, args=()):
        kwargs = {"target": target, "daemon": True}
        if supports_name or supports_kwargs:
            kwargs["name"] = name
        if supports_kwargs:
            kwargs["args"] = args
        thread = threading.Thread(**kwargs)
        if args and not supports_kwargs:
            # Test doubles may only accept stable Thread constructor keys.
            return thread
        thread.start()

    started_tasks: set[tuple[str, str, str]] = set()
    for tenant in _active_tenants():
        def resume_tenant(tenant_record=tenant):
            known = set()

            def start_resumer(
                project_id,
                node_id,
                provider_task_id,
                provider,
                model,
                kind,
            ):
                tenant_id = tenant_record["id"] if tenant_record else None
                tenant_slug = tenant_record["slug"] if tenant_record else "local"
                key = (
                    tenant_id or "legacy",
                    project_id,
                    node_id,
                    str(provider_task_id),
                )
                with RESUMED_VIDEO_TASKS_LOCK:
                    if key in RESUMED_VIDEO_TASKS:
                        return
                    RESUMED_VIDEO_TASKS.add(key)

                def run_resumer():
                    try:
                        _run_in_tenant(
                            tenant_id,
                            tenant_slug,
                            _resume_image_task if kind == "image" else _resume_video_task,
                            project_id,
                            node_id,
                            provider_task_id,
                            provider,
                            model,
                        )
                    except Exception as exc:
                        print(
                            f"[generation-resume] poller failed "
                            f"node={node_id} task={provider_task_id} error={exc}",
                            file=sys.stderr,
                        )
                    finally:
                        with RESUMED_VIDEO_TASKS_LOCK:
                            RESUMED_VIDEO_TASKS.discard(key)

                try:
                    start_thread(
                        run_resumer,
                        f"generation-resume-{node_id}",
                    )
                    started_tasks.add(key)
                except Exception as exc:
                    with RESUMED_VIDEO_TASKS_LOCK:
                        RESUMED_VIDEO_TASKS.discard(key)
                    print(
                        f"[generation-resume] start failed "
                        f"node={node_id} task={provider_task_id} error={exc}",
                        file=sys.stderr,
                    )

            for project_row in store.list_projects():
                project = store.get_project(project_row.get("id"))
                if not project:
                    continue
                for node in project["payload"].get("nodes", []):
                    meta = node.get("metadata") or {}
                    task_id = str(meta.get("taskId") or "").strip()
                    provider_status = str(meta.get("providerTaskStatus") or "").strip().lower()
                    node_type = str(node.get("type") or "")
                    resumable_node = (
                        meta.get("status") == "generating"
                        and node_type in {"image", "video"}
                        and bool(task_id)
                        and provider_status in {
                            "queued", "pending", "created", "submitted",
                            "running", "processing", "retrying", "uncertain",
                        }
                    )
                    if not resumable_node:
                        continue
                    task_row = _find_generation_task(
                        project["id"],
                        node["id"],
                        task_id,
                    )
                    if task_row:
                        known.add(str(task_row.get("id") or ""))
                        if generation_outcome.is_provider_terminal_failure(task_row):
                            commit_node_metadata(project["id"], node["id"], {
                                "status": "failed",
                                "lastError": str(task_row.get("error") or "生成失败"),
                                "providerTaskStatus": "failed",
                                "providerTaskPhase": str(
                                    task_row.get("phase") or "生成失败"
                                ),
                                "generationFinishedAt": (
                                    task_row.get("finished_at")
                                    or store._now_iso()
                                ),
                            })
                            continue
                        if (
                            task_row.get("status") == "failed"
                            and not generation_outcome.is_provider_terminal_failure(
                                task_row
                            )
                        ):
                            provider_status_value = str(
                                (node.get("metadata") or {}).get(
                                    "providerTaskStatus"
                                ) or ""
                            ).strip().lower()
                            if provider_status_value not in {
                                "queued", "pending", "created", "submitted",
                                "running", "processing", "retrying",
                                "in_progress", "uncertain",
                            }:
                                continue
                        if generation_outcome.is_provider_terminal_failure(task_row):
                            commit_node_metadata(project["id"], node["id"], {
                                "status": "failed",
                                "lastError": str(task_row.get("error") or "生成失败"),
                                "providerTaskStatus": "failed",
                                "providerTaskPhase": str(
                                    task_row.get("phase") or "生成失败"
                                ),
                                "generationFinishedAt": (
                                    task_row.get("finished_at")
                                    or store._now_iso()
                                ),
                            })
                            continue
                    try:
                        params = meta.get("params") or {}
                        provider, model, _ = provider_catalog.resolve_model(
                            params.get("provider") or meta.get("provider"),
                            params.get("model") or meta.get("model"),
                            node_type,
                        )
                        if node_type == "image" and str(model.get("adapter") or "") != "newapi_image":
                            continue
                    except Exception as exc:
                        commit_node_metadata(project["id"], node["id"], {
                            "status": "failed",
                            "lastError": f"无法恢复{node_type}任务：{exc}",
                            "providerTaskPhase": "恢复失败",
                        })
                        continue
                    start_resumer(
                        project["id"],
                        node["id"],
                        task_id,
                        provider,
                        model,
                        node_type,
                    )

            # A browser timeout may have persisted the provider id but already
            # marked the node failed. The task row remains the source of truth.
            for task_row in store.list_generation_tasks(None, 300):
                local_id = str(task_row.get("id") or "")
                provider_task_id = str(task_row.get("provider_task_id") or "").strip()
                if (
                    local_id in known
                    or not provider_task_id
                    or task_row.get("kind") not in {"image", "video"}
                    or task_row.get("status") not in {"running", "interrupted", "failed"}
                ):
                    continue
                project = store.get_project(task_row.get("project_id"))
                node = next(
                    (
                        item for item in (project or {}).get("payload", {}).get("nodes", [])
                        if item.get("id") == task_row.get("node_id")
                    ),
                    None,
                )
                if not node:
                    continue
                if generation_outcome.is_provider_terminal_failure(task_row):
                    continue
                try:
                    provider, model, _ = provider_catalog.resolve_model(
                        task_row.get("provider"),
                        task_row.get("model"),
                        str(task_row.get("kind") or ""),
                    )
                except Exception:
                    continue
                provider_status = str(
                    (node.get("metadata") or {}).get("providerTaskStatus") or ""
                ).strip().lower()
                live_provider_statuses = {
                    "queued", "pending", "created", "submitted",
                    "running", "processing", "retrying", "in_progress", "uncertain",
                }
                if (
                    task_row.get("status") == "failed"
                    and str((node.get("metadata") or {}).get("taskId") or "")
                    != provider_task_id
                    and provider_status not in live_provider_statuses
                ):
                    continue
                if (
                    task_row.get("status") == "failed"
                    and provider_status not in live_provider_statuses
                ):
                    continue
                if task_row.get("status") == "failed":
                    reopened = store.reopen_generation_task_for_provider(
                        task_row["id"],
                        provider_task_id,
                        message=(
                            "供应商任务仍在执行；系统会继续查询原任务，"
                            "不会自动重新提交。"
                        ),
                    )
                    if not reopened:
                        continue
                store.schedule_provider_task_poll(
                    task_row["id"],
                    store._now_iso(),
                )
                commit_node_metadata(project["id"], node["id"], {
                    "status": "generating",
                    "taskId": provider_task_id,
                    "providerTaskStatus": "queued",
                    "providerTaskPhase": "等待恢复",
                    "lastError": "",
                    "generationFinishedAt": "",
                })
                start_resumer(
                    project["id"],
                    node["id"],
                    provider_task_id,
                    provider,
                    model,
                    str(task_row.get("kind") or ""),
                )

        if tenant is None:
            resume_tenant()
        else:
            with tenant_context.use_tenant(tenant["id"], tenant["slug"]):
                resume_tenant(tenant)
    return len(started_tasks)


def reconcile_provider_task_journal():
    """Restore task ids from the durable journal before provider task resume."""
    recovered = 0
    for entry in store.list_provider_task_journal():
        task_id = str(entry.get("taskId") or "").strip()
        provider_task_id = str(entry.get("providerTaskId") or "").strip()
        project_id = str(entry.get("projectId") or "").strip()
        node_id = str(entry.get("nodeId") or "").strip()
        if not provider_task_id:
            continue
        task = store.get_generation_task(task_id) if task_id else None
        if generation_outcome.is_provider_terminal_failure(task):
            continue
        task_updates = {}
        if task and not task.get("provider_task_id"):
            task_updates.update({
                "provider_task_id": provider_task_id,
                "status": "running",
                "phase": "等待恢复",
                "error": "",
                "finished_at": "",
            })
        if task and task.get("status") == "failed":
            reopened = store.reopen_generation_task_for_provider(
                task_id,
                provider_task_id,
                message=(
                    "供应商任务仍在执行；系统会继续查询原任务，"
                    "不会自动重新提交。"
                ),
            )
            if reopened:
                recovered += 1
        elif task_updates:
            store.update_generation_task(
                task_id,
                **task_updates,
            )
            recovered += 1
        if project_id and node_id:
            project = store.get_project(project_id)
            node = next(
                (
                    item for item in (project or {}).get("payload", {}).get("nodes", [])
                    if item.get("id") == node_id
                ),
                None,
            )
            meta = (node or {}).get("metadata") or {}
            if node and (
                str(meta.get("taskId") or "") != provider_task_id
                or str(meta.get("providerTaskStatus") or "").lower()
                in {"uncertain", "interrupted", "failed"}
            ):
                commit_node_metadata(project_id, node_id, {
                    "taskId": provider_task_id,
                    "status": "generating",
                    "lastError": "",
                    "providerTaskStatus": "queued",
                    "providerTaskPhase": "等待恢复",
                    "providerTaskProgress": 0,
                    "generationFinishedAt": "",
                })
                episode_id = str(meta.get("episodeId") or "").strip().upper()
                if (
                    task
                    and episode_id
                    and not str(task.get("episode_id") or "").strip()
                ):
                    store.update_generation_task(
                        task["id"],
                        episode_id=episode_id,
                    )
                recovered += 1
    return recovered


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
                        episode_id=str(meta.get("episodeId") or ""),
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
                terminal = commit_generation_terminal(
                    project["id"],
                    node["id"],
                    {
                        "status": "failed",
                        "providerTaskStatus": "interrupted",
                        "providerTaskPhase": task_phase,
                        "lastError": task_error,
                        "generationFinishedAt": store._now_iso(),
                    },
                    task_id=str(task.get("id") or ""),
                    task_updates={
                        "status": "interrupted",
                        "phase": task_phase,
                        "error": task_error,
                        "provider_task_id": provider_task_id,
                        "delivery_status": "pending",
                "next_poll_at": (
                    datetime.now(timezone.utc) + timedelta(seconds=8)
                ).isoformat(),
                        "finished_at": "",
                        "node_written_at": "",
                    },
                )
                if terminal.get("committed"):
                    reconciled += 1
                    continue
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


def backfill_media_version_ids():
    """Give historical media versions stable ids and active pointers.

    This is a compatibility migration for projects created before version ids
    existed. It preserves the version order and URL history. Repeated starts
    are harmless because already-migrated rows are left untouched.
    """
    updated = 0
    for project_row in store.list_projects():
        project_id = str(project_row.get("id") or "")
        project = store.get_project(project_id)
        if not project:
            continue
        payload = project["payload"]
        changed = 0
        for node in payload.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            kind = str(node.get("type") or "")
            if kind not in {"image", "audio", "video"}:
                continue
            meta = node.get("metadata")
            if not isinstance(meta, dict):
                continue
            params = meta.get("params")
            if not isinstance(params, dict):
                continue
            source = params.get("mediaVersions")
            if not source and kind == "video":
                source = params.get("videoVersions")
            if not isinstance(source, list) or not source:
                continue
            before = json.dumps(
                {
                    "mediaVersions": params.get("mediaVersions"),
                    "activeVersionId": params.get("activeVersionId"),
                    "videoVersions": params.get("videoVersions"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            _normalize_media_versions(meta, kind)
            after = json.dumps(
                {
                    "mediaVersions": params.get("mediaVersions"),
                    "activeVersionId": params.get("activeVersionId"),
                    "videoVersions": params.get("videoVersions"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            if before != after:
                changed += 1
        if changed:
            store.update_project(
                project_id,
                payload=payload,
                snapshot_mode="none",
            )
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


def backfill_local_media_storage():
    """Move legacy local-only uploads into the tenant's configured object store.

    Uploads made before the storage receipt reached node metadata still have a
    valid local file and source=local. This is idempotent: stored nodes are
    skipped, and a provider failure leaves the node untouched for the next boot.
    """
    storage_cfg = provider_catalog.storage_config()
    if not media_storage.is_ready(storage_cfg):
        return 0
    updated = 0
    for project_row in store.list_projects():
        project = store.get_project(project_row.get("id"))
        if not project:
            continue
        payload = project["payload"]
        changed = 0
        for node in payload.get("nodes", []):
            if not isinstance(node, dict):
                continue
            meta = node.get("metadata")
            if not isinstance(meta, dict):
                continue
            source = meta.get("source")
            content = str(meta.get("content") or "")
            storage = meta.get("storage")
            if (
                not isinstance(source, dict)
                or source.get("type") != "local"
                or not content.startswith("/uploads/")
                or (isinstance(storage, dict) and storage.get("status") == "stored")
            ):
                continue
            try:
                descriptor = media_storage.persist_local_url(
                    content,
                    kind=str(node.get("type") or "media"),
                    fallback_ext=os.path.splitext(content)[1],
                    source={
                        "type": "legacy-local-upload",
                        "fileName": source.get("fileName") or os.path.basename(content),
                        "mime": source.get("mime") or "",
                        "sha256": source.get("sha256") or "",
                    },
                )
            except Exception as exc:
                meta["storageError"] = str(exc)
                if str(model.get("adapter") or "") == "ark_video":
                    raise RuntimeError(
                        f"{label} 无法转存到火山 TOS：{exc}。"
                        "请先在设置中完成“测试 火山 TOS”"
                    ) from exc
                print(
                    f"[storage] legacy upload migration failed {content[:120]}: {exc}",
                    file=sys.stderr,
                )
                continue
            meta["storage"] = descriptor
            meta["storageError"] = ""
            params = meta.get("params")
            if isinstance(params, dict):
                for version in params.get("mediaVersions") or []:
                    if (
                        isinstance(version, dict)
                        and version.get("url") == content
                    ):
                        version["storage"] = descriptor
            changed += 1
        if changed:
            store.update_project(project["id"], payload=payload)
            updated += 1
    return updated


def repair_expiring_seedream_references():
    """Attach a durable TOS object to completed Seedream image versions.

    Older rows stored the provider's one-day URL beside a valid local original.
    Generation must prefer the local file/TOS object, and this repair keeps the
    Canvas projection consistent so clients do not need to resubmit for it.
    """
    if not tos_storage.is_verified(provider_catalog.storage_profile_config("tos")):
        return 0
    repaired = 0
    for project_row in store.list_projects():
        project = store.get_project(project_row.get("id"))
        if not project:
            continue
        payload = project["payload"]
        changed = 0
        for node in payload.get("nodes", []):
            if not isinstance(node, dict) or node.get("type") != "image":
                continue
            meta = node.get("metadata")
            if not isinstance(meta, dict) or meta.get("status") != "done":
                continue
            content = str(meta.get("content") or "").strip()
            if not content.startswith("/uploads/"):
                continue
            params = meta.setdefault("params", {})
            if not isinstance(params, dict):
                params = {}
                meta["params"] = params
            versions = params.get("mediaVersions")
            versions = versions if isinstance(versions, list) else []
            active_index = params.get("mediaVersionIndex")
            try:
                active_index = int(active_index)
            except (TypeError, ValueError):
                active_index = 0
            target = (
                versions[active_index]
                if 0 <= active_index < len(versions)
                else None
            )
            if not isinstance(target, dict):
                continue
            storage = target.get("storage") or meta.get("storage")
            if isinstance(storage, dict) and storage.get("provider") == "tos" and storage.get("key"):
                continue
            source_model = str(
                target.get("model") or meta.get("model") or ""
            ).lower()
            if not source_model.startswith("doubao-seedream-5-0-"):
                continue
            try:
                descriptor = tos_storage.ensure_local_image(
                    content,
                    source={
                        "provider": target.get("provider") or meta.get("provider"),
                        "model": target.get("model") or meta.get("model"),
                        "taskId": target.get("taskId"),
                        "generatedAt": target.get("generatedAt"),
                        "url": content,
                    },
                )
                if not descriptor:
                    continue
                tos_storage._client().head_object(
                    descriptor["bucket"],
                    descriptor["key"],
                )
            except Exception as exc:
                meta["storageError"] = str(exc)
                changed += 1
                continue
            target["storage"] = descriptor
            meta["storage"] = descriptor
            meta["storageError"] = ""
            changed += 1
        if changed:
            store.update_project(project["id"], payload=payload)
            repaired += changed
    return repaired


def backfill_image_provider_result_urls():
    """Restore a trusted Seedream short URL from the task ledger."""
    updated = 0
    for project_row in store.list_projects():
        project_id = str(project_row.get("id") or "")
        project = store.get_project(project_id)
        if not project:
            continue
        payload = project["payload"]
        receipts = {
            str(task.get("node_id") or ""): task
            for task in store.list_succeeded_generation_task_receipts(
                project_id,
                kind="image",
            )
        }
        if not receipts:
            continue
        changed = 0
        for node in payload.get("nodes", []):
            if (
                not isinstance(node, dict)
                or node.get("type") != "image"
                or not isinstance(node.get("metadata"), dict)
            ):
                continue
            meta = node["metadata"]
            existing = str(
                meta.get("providerResultUrl")
                or meta.get("providerUrl")
                or ""
            ).strip()
            task = receipts.get(str(node.get("id") or "")) or {}
            value = str(task.get("provider_result_url") or "").strip()
            if existing.startswith("https://") or not value.startswith("https://"):
                continue
            params = meta.setdefault("params", {})
            if not isinstance(params, dict):
                params = {}
                meta["params"] = params
            versions = params.get("mediaVersions")
            if not isinstance(versions, list) or not versions:
                versions = params.get("videoVersions")
            versions = versions if isinstance(versions, list) else []
            target = _active_media_version(meta)
            if not target:
                target = next(
                    (
                        item for item in reversed(versions)
                        if isinstance(item, dict)
                        and str(item.get("taskId") or "")
                        == str(task.get("provider_task_id") or "")
                    ),
                    {},
                )
            if not isinstance(target, dict):
                target = {}
            if not target and versions:
                active_index = params.get("mediaVersionIndex")
                try:
                    active_index = int(active_index)
                except (TypeError, ValueError):
                    active_index = -1
                if 0 <= active_index < len(versions) and isinstance(
                    versions[active_index], dict
                ):
                    target = versions[active_index]
            if target:
                target["providerResultUrl"] = value
                if not target.get("generatedAt"):
                    target["generatedAt"] = str(task.get("provider_finished_at") or "")
                if not target.get("model"):
                    target["model"] = str(task.get("model") or "")
                if not target.get("provider"):
                    target["provider"] = str(task.get("provider") or "")
            if str(meta.get("content") or "").strip() == str(target.get("url") or "").strip():
                meta["providerResultUrl"] = value
                if not meta.get("generatedAt"):
                    meta["generatedAt"] = str(task.get("provider_finished_at") or "")
                if not meta.get("model"):
                    meta["model"] = str(task.get("model") or "")
                if not meta.get("provider"):
                    meta["provider"] = str(task.get("provider") or "")
            changed += 1
        if changed:
            store.update_project(project_id, payload=payload)
            updated += changed
    return updated


def reconcile_image_provider_metadata():
    """
    Repair legacy nodes from the task ledger without inventing URLs.

    - Restore a provider result URL only from a successful image task.
    - Refresh the node-level storage receipt from the active media version.
    """
    updated = 0
    for project_row in store.list_projects():
        project_id = str(project_row.get("id") or "")
        project = store.get_project(project_id)
        if not project:
            continue
        payload = project["payload"]
        receipts = {
            str(task.get("node_id") or ""): task
            for task in store.list_succeeded_generation_task_receipts(
                project_id,
                kind="image",
            )
        }
        changed = 0
        for node in payload.get("nodes", []):
            if (
                not isinstance(node, dict)
                or node.get("type") != "image"
                or not isinstance(node.get("metadata"), dict)
            ):
                continue
            meta = node["metadata"]
            params = meta.setdefault("params", {})
            if not isinstance(params, dict):
                params = {}
                meta["params"] = params
            active_version = _active_media_version(meta)
            if (
                active_version
                and isinstance(active_version.get("storage"), dict)
                and active_version["storage"] != meta.get("storage")
            ):
                meta["storage"] = dict(active_version["storage"])
                changed += 1
            if active_version:
                version_model = str(active_version.get("model") or "").strip()
                version_generated_at = str(
                    active_version.get("generatedAt") or ""
                ).strip()
                if version_model and not meta.get("model"):
                    meta["model"] = version_model
                    changed += 1
                if version_generated_at and not meta.get("generatedAt"):
                    meta["generatedAt"] = version_generated_at
                    changed += 1
            task = receipts.get(str(node.get("id") or "")) or {}
            value = str(task.get("provider_result_url") or "").strip()
            existing = str(
                active_version.get("providerResultUrl")
                or meta.get("providerResultUrl")
                or meta.get("providerUrl")
                or ""
            ).strip()
            if value.startswith("https://") and not existing.startswith("https://"):
                if active_version:
                    active_version["providerResultUrl"] = value
                    if not active_version.get("generatedAt"):
                        active_version["generatedAt"] = str(
                            task.get("provider_finished_at") or ""
                        )
                    if not active_version.get("model"):
                        active_version["model"] = str(task.get("model") or "")
                    if not active_version.get("provider"):
                        active_version["provider"] = str(task.get("provider") or "")
                meta["providerResultUrl"] = value
                if not meta.get("generatedAt"):
                    meta["generatedAt"] = str(task.get("provider_finished_at") or "")
                if not meta.get("model"):
                    meta["model"] = str(task.get("model") or "")
                if not meta.get("provider"):
                    meta["provider"] = str(task.get("provider") or "")
                changed += 1
        if changed:
            store.update_project(project_id, payload=payload)
            updated += changed
    return updated


def repair_episode_node_ownership():
    """Backfill explicit node ownership and make episode membership exclusive."""
    repaired = 0
    for project_row in store.list_projects():
        project_id = str(project_row.get("id") or "")
        if not project_id:
            continue
        repaired += store.repair_episode_node_ownership(project_id)
    return repaired


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._audit_request_scope = None
        self._share_access = None
        self._response_status = 0

    def log_message(self, fmt, *args):
        if access_log_enabled(self._response_status):
            sys.stderr.write("%s - - [%s] %s\n" % (
                self.client_address[0],
                self.log_date_time_string(),
                fmt % args,
            ))

    def send_response(self, code, message=None):
        self._response_status = int(code)
        return super().send_response(code, message)

    # ---------- helpers ----------
    def parse_request(self):
        parsed = super().parse_request()
        if parsed:
            self._request_id = secrets.token_hex(8)
            self._audit_request_scope = store.use_audit_request(
                request_id=self._request_id,
                method=self.command,
                path=self.path,
                ip=self.client_address[0] if self.client_address else "",
                user_agent=self.headers.get("User-Agent", ""),
            )
            self._audit_request_scope.__enter__()
        return parsed

    def finish(self):
        try:
            super().finish()
        finally:
            scope = getattr(self, "_audit_request_scope", None)
            self._audit_request_scope = None
            if scope is not None:
                scope.__exit__(None, None, None)

    def end_headers(self):
        if not getattr(self, "_request_id", ""):
            self._request_id = secrets.token_hex(8)
        if not getattr(self, "_request_id_sent", False):
            self._request_id_sent = True
            self.send_header("X-Request-ID", self._request_id)
        super().end_headers()

    def _cors_headers(self):
        origin = str(self.headers.get("Origin", "")).strip().rstrip("/")
        allowed = _frontend_origins()
        origin_host = urllib.parse.urlparse(origin).netloc.lower()
        request_host = str(self.headers.get("Host", "")).strip().lower()
        local_origin = (
            _auth_mode() != "accounts"
            and _normalized_host(origin_host) in {"127.0.0.1", "localhost", "::1"}
            and _normalized_host(request_host) in {"127.0.0.1", "localhost", "::1"}
        )
        if not origin or (origin.lower() not in allowed and not local_origin):
            return {}
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
            "Access-Control-Allow-Headers": (
                "Content-Type,Authorization,X-File-Name,X-File-Type,"
                "X-Request-ID,X-Share-Token"
            ),
            "Access-Control-Expose-Headers": "Accept-Ranges,Content-Disposition,Content-Length,Content-Range,ETag,Last-Modified,X-Request-ID",
            "Access-Control-Max-Age": "600",
            "Vary": "Origin",
        }

    def do_OPTIONS(self):
        return self._send(204, {}, raw=b"")

    def _send(self, code, obj=None, raw=None, content_type="application/json", headers=None):
        if int(code or 0) >= 500:
            observability.log_event(
                "api",
                "http.error",
                severity="error",
                requestId=getattr(self, "_request_id", ""),
                method=self.command,
                path=urllib.parse.urlparse(self.path).path,
                status=int(code or 0),
                tenantId=tenant_context.current_tenant_id(),
            )
        if raw is not None:
            body = raw.encode("utf-8") if isinstance(raw, str) else raw
        else:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        # 画布读接口一次就吐 400~500KB JSON（127 个节点的那本实测 487KB），
        # 生成结束后的整份刷新还要再来一次。不压缩等于每次首屏都多下半个 MB。
        encoding = ""
        compressible = _compressible_content_type(content_type) and len(body) >= _STATIC_COMPRESS_MIN_BYTES
        if compressible and _accepts_gzip(self.headers.get("Accept-Encoding", "")):
            try:
                packed = gzip.compress(body, compresslevel=6)
            except (OSError, ValueError):
                packed = body
            if len(packed) < len(body):
                body = packed
                encoding = "gzip"
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        response_headers = {
            **_security_headers(),
            **self._cors_headers(),
            **(headers or {}),
        }
        if encoding:
            response_headers["Content-Encoding"] = encoding
        if compressible:
            # 同一 URL 会按 Accept-Encoding 给出两种表示，必须声明 Vary。
            # CORS 已经写过 Vary: Origin，合并成一条，避免重复头被代理误读。
            existing = str(response_headers.get("Vary") or "").strip()
            parts = [part.strip() for part in existing.split(",") if part.strip()]
            if "Accept-Encoding" not in parts:
                parts.append("Accept-Encoding")
            response_headers["Vary"] = ", ".join(parts)
        for key, value in response_headers.items():
            self.send_header(str(key), str(value))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _read_json(self):
        raw = self._read_raw_body(
            MAX_JSON_BODY_BYTES,
            too_large_code="JSON_BODY_TOO_LARGE",
        )
        if not raw:
            return {}
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

    def _read_raw_body(
        self,
        max_bytes: int,
        *,
        too_large_code: str = "REQUEST_BODY_TOO_LARGE",
    ) -> bytes:
        cached = getattr(self, "_raw_request_body", None)
        if isinstance(cached, bytes):
            return cached
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
            return b""
        if length > int(max_bytes):
            raise RequestBoundaryError(
                413,
                f"请求体超过 {int(max_bytes) // (1024 * 1024)}MB 上限",
                too_large_code,
            )
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise RequestBoundaryError(
                400,
                "请求体读取不完整",
                "JSON_BODY_TRUNCATED",
            )
        self._raw_request_body = raw
        return raw

    def _send_upload_file(self, fpath: str, name: str):
        """Serve tenant media with session auth, CORS and single-range support."""
        return self._send_upload_file_with_options(
            fpath,
            name,
            download_name="",
        )

    def _send_upload_file_with_options(
        self,
        fpath: str,
        name: str,
        *,
        download_name: str = "",
    ):
        """Serve tenant media with optional RFC 5987 attachment filename."""
        stat = os.stat(fpath)
        size = stat.st_size
        etag = f'"{size:x}-{stat.st_mtime_ns:x}"'
        last_modified = formatdate(stat.st_mtime, usegmt=True)
        share_path = urlparse(
            str(getattr(self, "path", "") or "")
        ).path.startswith("/api/share/")
        cors_headers = (
            {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Expose-Headers": (
                    "Accept-Ranges, Content-Length, Content-Range, "
                    "Content-Type, ETag, Last-Modified"
                ),
            }
            if share_path
            else self._cors_headers()
        )
        if share_path:
            cors_headers["Access-Control-Allow-Headers"] = (
                "If-None-Match, If-Range, Range"
            )
            cors_headers["Access-Control-Allow-Methods"] = "GET, HEAD, OPTIONS"
            cors_headers["Cross-Origin-Resource-Policy"] = "cross-origin"
        cache_headers = {
            "ETag": etag,
            "Last-Modified": last_modified,
            "Cache-Control": (
                "private, no-cache"
                if share_path
                else "private, max-age=31536000, immutable"
            ),
            **(
                {}
                if share_path
                else {
                    "Cross-Origin-Resource-Policy": "cross-origin",
                    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                    "Access-Control-Allow-Headers": (
                        "Range, If-Range, If-None-Match, Content-Type"
                    ),
                }
            ),
        }
        if self.command in {"GET", "HEAD"} and self.headers.get("If-None-Match", "").strip() == etag:
            self.send_response(304)
            for key, value in {
                **_security_headers(),
                **cache_headers,
                **cors_headers,
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
                    **cors_headers,
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
                    **cors_headers,
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
        if download_name:
            self.send_header(
                "Content-Disposition",
                _download_content_disposition(download_name),
            )
        for key, value in cache_headers.items():
            self.send_header(key, value)
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        for key, value in {
            **_security_headers(),
            **cors_headers,
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
                try:
                    self.wfile.write(chunk)
                except OSError as exc:
                    if not is_client_disconnect_error(exc):
                        raise
                    # Browsers cancel range requests while seeking or switching
                    # cards. Stop only this response; disk/protocol errors remain fatal.
                    self.close_connection = True
                    return
                remaining -= len(chunk)

    def _send_remote_download(
        self,
        url: str,
        *,
        download_name: str,
    ):
        """Proxy a trusted object-store download through the app origin."""
        request_headers = {}
        range_header = str(self.headers.get("Range") or "").strip()
        if_range = str(self.headers.get("If-Range") or "").strip()
        if range_header:
            request_headers["Range"] = range_header
        if if_range:
            request_headers["If-Range"] = if_range
        request = urllib.request.Request(
            url,
            headers=request_headers,
            method=self.command,
        )
        try:
            response = urllib.request.urlopen(
                request,
                timeout=60,
            )
        except urllib.error.HTTPError as exc:
            return self._send(
                exc.code,
                {"error": "对象存储下载失败"},
            )
        except Exception as exc:
            return self._send(
                502,
                {
                    "error": f"对象存储下载失败：{exc}",
                    "code": "REMOTE_DOWNLOAD_FAILED",
                },
            )
        with response:
            status = int(getattr(response, "status", 200))
            response_headers = getattr(response, "headers", {})
            content_type = str(
                response_headers.get("Content-Type")
                or "application/octet-stream"
            )
            content_length = str(
                response_headers.get("Content-Length")
                or ""
            )
            content_range = str(
                response_headers.get("Content-Range")
                or ""
            )
            accept_ranges = str(
                response_headers.get("Accept-Ranges")
                or ("bytes" if status == 206 else "")
            )
            etag = str(response_headers.get("ETag") or "")
            last_modified = str(
                response_headers.get("Last-Modified")
                or ""
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if content_length:
                self.send_header("Content-Length", content_length)
            if content_range:
                self.send_header("Content-Range", content_range)
            if accept_ranges:
                self.send_header("Accept-Ranges", accept_ranges)
            if etag:
                self.send_header("ETag", etag)
            if last_modified:
                self.send_header("Last-Modified", last_modified)
            self.send_header("Cache-Control", "private, no-store")
            self.send_header(
                "Content-Disposition",
                _download_content_disposition(download_name),
            )
            for key, value in {
                **_security_headers(),
                **self._cors_headers(),
                "Cross-Origin-Resource-Policy": "cross-origin",
            }.items():
                self.send_header(str(key), str(value))
            self.end_headers()
            if self.command == "HEAD":
                return
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except OSError as exc:
                    if not is_client_disconnect_error(exc):
                        raise
                    self.close_connection = True
                    return

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
        mime = m.group(1).split(";")[0].strip()
        result = {
            "ok": True,
            "url": "/uploads/" + fname,
            "name": raw_name or fname,
            "size": len(blob),
            "mime": mime,
            "assetId": f"{self._upload_kind_for_extension(ext) or 'media'}:upload:"
            + hashlib.sha256(blob).hexdigest()[:24],
            "variantKey": "default",
        }
        result["seriesId"] = result["assetId"]
        storage_cfg = provider_catalog.storage_config()
        if media_storage.is_ready(storage_cfg):
            try:
                storage = media_storage.persist_local_url(
                    result["url"],
                    kind=self._upload_kind_for_extension(ext) or "media",
                    fallback_ext=ext,
                    source={
                        "type": "local-upload",
                        "fileName": raw_name or fname,
                        "mime": mime,
                    },
                )
            except Exception as exc:
                return self._send(502, {
                    "error": f"文件已保存在本地，但对象存储上传失败：{exc}",
                    "code": "STORAGE_UPLOAD_FAILED",
                    "provider": (
                        storage_cfg.get("active_provider")
                        or storage_cfg.get("provider")
                        or ""
                    ),
                    "localUrl": result["url"],
                })
            result["storage"] = storage
            result["remoteUrl"] = media_storage.public_url(storage)
        return self._send(200, result)

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
        mime = declared_mime or mimetypes.guess_type(fname)[0] or "application/octet-stream"
        result = {
            "ok": True,
            "url": "/uploads/" + fname,
            "name": raw_name or fname,
            "size": content_length,
            "mime": mime,
            "sha256": digest.hexdigest(),
            "kind": kind,
            "assetId": (
                f"{kind}:upload:{digest.hexdigest()[:24]}"
            ),
            "seriesId": (
                f"{kind}:upload:{digest.hexdigest()[:24]}"
            ),
            "variantKey": "default",
        }
        storage_cfg = provider_catalog.storage_config()
        if media_storage.is_ready(storage_cfg):
            try:
                storage = media_storage.persist_local_url(
                    result["url"],
                    kind=kind,
                    fallback_ext=ext,
                    source={
                        "type": "local-upload",
                        "fileName": raw_name or fname,
                        "mime": mime,
                        "sha256": result["sha256"],
                    },
                )
            except Exception as exc:
                return self._send(502, {
                    "error": f"文件已保存在本地，但对象存储上传失败：{exc}",
                    "code": "STORAGE_UPLOAD_FAILED",
                    "provider": (
                        storage_cfg.get("active_provider")
                        or storage_cfg.get("provider")
                        or ""
                    ),
                    "localUrl": result["url"],
                })
            result["storage"] = storage
            result["remoteUrl"] = media_storage.public_url(storage)
        return self._send(200, result)

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
        try:
            stat = os.stat(target)
        except OSError:
            return False
        etag = f'"{stat.st_size:x}-{stat.st_mtime_ns:x}"'
        # assets 下都是带指纹的文件，可长缓存；index.html 必须每次回源，
        # 否则重建后旧 HTML 会继续引用已被删除的旧 chunk → 白屏。
        if rel.startswith("assets/"):
            cache_control = "public, max-age=31536000, immutable"
        else:
            cache_control = "no-cache, must-revalidate"

        try:
            with open(target, "rb") as f:
                body = f.read()
        except OSError:
            return False

        encoding = ""
        if (
            ext in _STATIC_COMPRESSIBLE_EXT
            and len(body) >= _STATIC_COMPRESS_MIN_BYTES
            and _accepts_gzip(self.headers.get("Accept-Encoding", ""))
        ):
            compressed = _static_gzip(target, stat.st_mtime_ns, stat.st_size, body)
            if compressed is not None:
                body = compressed
                encoding = "gzip"

        # no-cache 的 index.html 每次导航都要回源确认，用 ETag 换 304 可省掉整包
        # 重传；immutable 的 assets 正常不会走到这里，命中也不亏。
        if (
            self.command in {"GET", "HEAD"}
            and str(self.headers.get("If-None-Match") or "").strip() == etag
        ):
            self.send_response(304)
            for key, value in _security_headers().items():
                self.send_header(key, value)
                self.send_header("Content-Type", ctype)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", cache_control)
            self.send_header("Vary", "Accept-Encoding")
            self.end_headers()
            return True

        self.send_response(200)
        for key, value in _security_headers().items():
            self.send_header(key, value)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", cache_control)
        # 同一 URL 会按 Accept-Encoding 给出两种表示，必须声明 Vary，
        # 否则中间代理可能把 gzip 响应喂给不支持解压的客户端。
        self.send_header("Vary", "Accept-Encoding")
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.end_headers()
        if self.command != "HEAD":
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

    def _clear_share_cookie_header(self):
        cookie = (
            f"{CANVAS_SHARE_COOKIE}=; Path=/; Max-Age=0; "
            "HttpOnly; SameSite=Lax"
        )
        if _cookie_secure():
            cookie += "; Secure"
        return cookie

    def _share_request_token(self):
        # The token travels with each request from the current tab URL. A
        # global cookie lets an editor tab overwrite a viewer tab's authority.
        return str(self.headers.get(CANVAS_SHARE_HEADER, "") or "").strip()

    def _share_request_target(self, path, parsed):
        project_match = re.match(r"^/api/projects/([^/]+)(?:/|$)", path)
        if project_match:
            project_id = unquote(project_match.group(1))
            if re.match(
                r"^/api/projects/[^/]+/shares(?:/|$)",
                path,
            ):
                return project_id, "share_manage"
            return project_id, "project"
        if path == "/api/generation-tasks":
            query = urllib.parse.parse_qs(
                urllib.parse.urlsplit(self.path).query
            )
            project_id = str((query.get("projectId") or [""])[0]).strip()
            return project_id, "project"
        if path == "/api/model-catalog":
            return "", "tenant_read"
        if path == "/api/upload/stream":
            return "", "upload"
        return "", "unsupported"

    def _share_owner_only_request(self, path, command):
        if command in {"GET", "HEAD"}:
            return False
        project_match = re.match(
            r"^/api/projects/([^/]+)(?:/(.*))?$",
            path,
        )
        if not project_match:
            return False
        suffix = str(project_match.group(2) or "").strip("/")
        if not suffix:
            return True
        if suffix == "import" or suffix.startswith("import/"):
            return True
        if suffix == "duplicate":
            return True
        if suffix == "episodes" or suffix.startswith("episodes/"):
            return True
        if suffix == "versions" or suffix.startswith("versions/"):
            return True
        return False

    def _request_tenant_scope(self):
        self._share_access = None
        if self.command not in {"GET", "HEAD"} and not self._origin_allowed():
            self._send(403, {"error": "跨站请求已拒绝", "code": "ORIGIN_REJECTED"})
            return None
        parsed = urlparse(self.path)
        path = parsed.path
        if (
            path in {"/healthz", "/readyz", "/metrics"}
            and self.command in {"GET", "HEAD"}
        ):
            client_ip = (
                str(self.client_address[0])
                if self.client_address
                else ""
            )
            if client_ip in {"127.0.0.1", "::1"}:
                return nullcontext()
            self._send(404, {"error": "资源不存在"})
            return None
        if agent_gateway.is_agent_internal_path(path):
            if agent_gateway.allow_unsigned_local_agent():
                if not agent_gateway.loopback_client(self):
                    self._send(
                        403,
                        {
                            "error": "未签名的 Agent 请求只能来自本机",
                            "code": "AGENT_INTERNAL_AUTH_FAILED",
                        },
                    )
                    return None
                try:
                    context = agent_gateway.verify_unsigned_context_headers(
                        self.headers
                    )
                except ValueError as exc:
                    self._send(
                        401,
                        {
                            "error": str(exc),
                            "code": "AGENT_INTERNAL_AUTH_FAILED",
                        },
                    )
                    return None
                account = tenant_registry.get_account_by_id(context.account_id)
                tenant = tenant_registry.get_tenant_by_id(context.tenant_id)
                if _auth_mode() == "accounts" and (
                    not account
                    or not tenant
                    or str(account.get("tenant_id") or "") != context.tenant_id
                    or str(account.get("status") or "") != "active"
                    or str(tenant.get("status") or "") != "active"
                ):
                    self._send(
                        403,
                        {
                            "error": "Agent 内部账号上下文无效",
                            "code": "AGENT_PROJECT_FORBIDDEN",
                        },
                    )
                    return None
                self._internal_agent_body = self._read_raw_body(
                    agent_gateway.MAX_PROXY_BODY_BYTES
                )
                self._internal_agent_context = context
                return tenant_context.use_tenant(
                    tenant["id"] if tenant else context.tenant_id,
                    tenant["slug"] if tenant else "local",
                )
            try:
                raw_body = self._read_raw_body(agent_gateway.MAX_PROXY_BODY_BYTES)
                context = agent_gateway.verify_context_headers(
                    self.headers,
                    method=self.command,
                    path=self.path,
                    body=raw_body,
                )
            except (RequestBoundaryError, ValueError) as exc:
                status = exc.status if isinstance(exc, RequestBoundaryError) else 401
                self._send(
                    status,
                    {
                        "error": str(exc),
                        "code": "AGENT_INTERNAL_AUTH_FAILED",
                    },
                )
                return None
            account = tenant_registry.get_account_by_id(context.account_id)
            tenant = tenant_registry.get_tenant_by_id(context.tenant_id)
            if _auth_mode() == "accounts" and (
                not account
                or not tenant
                or str(account.get("tenant_id") or "") != context.tenant_id
                or str(account.get("status") or "") != "active"
                or str(tenant.get("status") or "") != "active"
            ):
                self._send(
                    403,
                    {
                        "error": "Agent 内部账号上下文无效",
                        "code": "AGENT_PROJECT_FORBIDDEN",
                    },
                )
                return None
            self._internal_agent_context = context
            self._internal_agent_body = raw_body
            if (
                _auth_mode() != "accounts"
                and context.tenant_id in {"legacy", "local"}
            ):
                return nullcontext()
            return tenant_context.use_tenant(
                tenant["id"] if tenant else context.tenant_id,
                tenant["slug"] if tenant else "local",
            )
        if path.startswith("/share/") or path.startswith("/api/share/"):
            if (
                path.startswith("/api/share/")
                and self.command not in {"GET", "HEAD"}
            ):
                self._send(
                    405,
                    {
                        "error": "分享链接是只读的，只接受 GET",
                        "code": "SHARE_READ_ONLY",
                    },
                )
                return None
            return nullcontext()
        share_token = self._share_request_token()
        if share_token:
            resolved_share = _resolve_share_scope(share_token)
            if resolved_share:
                share_scope, share = resolved_share
                target_project, target_kind = self._share_request_target(
                    path,
                    parsed,
                )
                share_project = str(share.get("projectId") or "")
                if (
                    target_kind == "project"
                    and target_project == share_project
                ) or target_kind == "tenant_read":
                    if target_kind == "tenant_read" and not share.get("canWrite"):
                        self._send(403, {
                            "error": "只读分享不能读取协作接口",
                            "code": "SHARE_READ_ONLY",
                        })
                        return None
                    if target_kind == "tenant_read" and self.command not in {
                        "GET",
                        "HEAD",
                    }:
                        self._send(405, {
                            "error": "该分享只允许读取",
                            "code": "SHARE_READ_ONLY",
                        })
                        return None
                    if (
                        self.command not in {"GET", "HEAD"}
                        and not share.get("canWrite")
                    ):
                        self._send(403, {
                            "error": "该分享链接为只读，不能修改画布",
                            "code": "SHARE_READ_ONLY",
                        })
                        return None
                    if (
                        target_kind == "project"
                        and self._share_owner_only_request(
                            path,
                            self.command,
                        )
                    ):
                        self._send(403, {
                            "error": "协作成员不能执行项目级管理操作",
                            "code": "SHARE_OWNER_REQUIRED",
                        })
                        return None
                    self._share_access = share
                    return share_scope
                if (
                    target_kind == "share_manage"
                    and target_project == share_project
                ):
                    self._send(403, {
                        "error": "协作分享不能创建或撤销分享链接",
                        "code": "SHARE_MANAGEMENT_FORBIDDEN",
                    })
                    return None
                if target_kind == "upload" and share.get("canWrite"):
                    self._share_access = share
                    return share_scope
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
            if not is_api_path and self.command in {"GET", "HEAD"}:
                # The login page and its own JS/CSS must be reachable before a
                # session exists. Only API and media paths carry tenant data.
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
                if share_token:
                    self._send(
                        410,
                        {
                            "error": "协作分享已撤销或过期，请重新打开分享链接",
                            "code": "SHARE_INVALID",
                        },
                        headers={"Set-Cookie": self._clear_share_cookie_header()},
                    )
                    return None
                self._send(401, {
                    "error": "请先登录。",
                    "code": "ACCOUNT_AUTH_REQUIRED",
                })
                return None
            if session.get("account_role") != "admin":
                if path.startswith("/api/admin/"):
                    self._send(403, {
                        "error": "只有管理员可以查看该数据",
                        "code": "ADMIN_REQUIRED",
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
                # 与 /api/auth/session 保持对称：登录响应也必须带偏好，否则前端
                # setPreferences(session.preferences || {}) 永远拿到空对象，
                # 画布底纹等偏好在登录后不会立即生效，要等下一次会话刷新。
                "preferences": tenant_registry.get_tenant_preferences(account["tenant_id"]),
            },
            headers={"Set-Cookie": self._session_cookie(session["token"])},
        )

    def _handle_project_events(self, project_id, parsed):
        """SSE：把生成进度和完成事件推给前台，代替"等下一次轮询"。

        连接建立后先把 since 之后的历史补发一遍（断线重连、刷新页面都能接上），
        然后阻塞等新事件；15 秒没有事件就发一个注释心跳，避免中间代理断开。
        """
        params = urllib.parse.parse_qs(parsed.query)
        raw_since = (params.get("since") or [""])[0] or self.headers.get("Last-Event-ID") or "0"
        try:
            since = max(0, int(str(raw_since).strip() or 0))
        except (TypeError, ValueError):
            since = 0

        subscriber, limit_code = _acquire_event_subscriber(project_id)
        if subscriber is None:
            return self._send(
                429,
                {
                    "error": "实时连接数已达上限，请关闭多余页面后重试。",
                    "code": limit_code,
                },
                headers={"Retry-After": "5"},
            )

        self.close_connection = True
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b"retry: 3000\n: connected\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            _release_event_subscriber(project_id, subscriber)
            return

        try:
            while True:
                events = store.list_events(project_id, since, 200)
                if not events:
                    try:
                        subscriber.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    events = store.list_events(project_id, since, 200)
                for event in events:
                    since = max(since, event["id"])
                    body = {
                        **event["payload"],
                        "id": event["id"],
                        "kind": event["kind"],
                        "nodeId": event["nodeId"],
                        "at": event["at"],
                    }
                    chunk = (
                        f"id: {event['id']}\n"
                        f"event: {event['kind']}\n"
                        f"data: {json.dumps(body, ensure_ascii=False)}\n\n"
                    )
                    self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        except Exception as exc:
            print(f"[events] 推送中断：{exc}", file=sys.stderr)
        finally:
            _release_event_subscriber(project_id, subscriber)

    def _handle_auth_session(self):
        if _auth_mode() != "accounts":
            context = tenant_context.current()
            preferences = tenant_registry.get_tenant_preferences(
                context.tenant_id if context else "legacy",
            )
            return self._send(200, {
                "authenticated": True,
                "username": "local",
                "tenantId": context.tenant_id if context else "legacy",
                "slug": context.slug if context else "local",
                "role": "admin",
                "isAdmin": True,
                "local": True,
                "preferences": preferences,
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
            "preferences": tenant_registry.get_tenant_preferences(
                session["tenant_id"],
            ),
        })

    def _handle_tenant_preferences_update(self, body):
        context = tenant_context.current()
        tenant_id = (
            str(context.tenant_id)
            if context
            else ("legacy" if _auth_mode() != "accounts" else "")
        )
        if not tenant_id:
            return self._send(401, {
                "error": "请先登录。",
                "code": "ACCOUNT_AUTH_REQUIRED",
            })
        background_mode = body.get("canvasBackgroundMode")
        if background_mode is not None and not isinstance(background_mode, str):
            return self._send(400, {
                "error": "画布底纹参数格式错误",
                "code": "CANVAS_BACKGROUND_INVALID",
            })
        theme = body.get("theme")
        if theme is not None and not isinstance(theme, str):
            return self._send(400, {
                "error": "主题参数格式错误",
                "code": "THEME_INVALID",
            })
        updates = {}
        if isinstance(background_mode, str):
            if background_mode not in {"dots", "lines", "blank"}:
                return self._send(400, {
                    "error": "画布底纹只能是 dots、lines 或 blank",
                    "code": "CANVAS_BACKGROUND_INVALID",
                })
            updates["canvasBackgroundMode"] = background_mode
        if isinstance(theme, str):
            normalized_theme = "immersive" if theme in {"gold", "tech"} else theme
            if normalized_theme not in {"light", "dark", "immersive", "forest", "copper"}:
                return self._send(400, {
                    "error": "主题值无效",
                    "code": "THEME_INVALID",
                })
            updates["theme"] = normalized_theme
        if not updates:
            return self._send(400, {
                "error": "没有可保存的偏好设置",
                "code": "PREFERENCES_EMPTY",
            })
        try:
            if (
                _auth_mode() != "accounts"
                and not tenant_registry.get_tenant_by_id(tenant_id)
            ):
                tenant_registry.create_tenant(
                    "local",
                    name="Local",
                    tenant_id=tenant_id,
                    legacy_source="local",
                )
            preferences = tenant_registry.update_tenant_preferences(
                tenant_id,
                updates,
            )
        except ValueError as exc:
            return self._send(404, {"error": str(exc)})
        try:
            store.record_audit_event(
                "preferences.update",
                actor=(self._current_session() or {}).get("account_username", ""),
                details={
                    "keys": sorted(updates),
                    "canvasBackgroundMode": updates.get("canvasBackgroundMode"),
                },
            )
        except Exception as exc:
            # The preference is already committed to control.db. A tenant
            # audit-row failure must not turn a successful save into an error.
            print(
                f"[preferences] audit write failed: {exc}",
                file=sys.stderr,
            )
        return self._send(200, {"ok": True, "preferences": preferences})

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
        if _auth_mode() != "accounts":
            # 本机单用户模式压根没有账号表，系统里只有一个隐式 local 身份。
            #
            # 这里以前走 _admin_session() → 403 ADMIN_REQUIRED：但前端 Shell 每 30 秒
            # 拉一次这个接口只为拿「在线人数」，于是控制台每 30 秒稳定刷一条红色 403，
            # 用户却没有任何账号可管 —— 既污染控制台（"0 报错"永远做不到），
            # 又让「账号管理」弹窗在本机模式下必然报错。
            # 现在按事实返回：零个可管理账号 + 本机那一个在线身份。
            context = tenant_context.current()
            now = store._now_iso()
            return self._send(200, {
                "accounts": [],
                "onlineCount": 1,
                "onlineUsers": [{
                    "id": "local",
                    "username": "local",
                    "lastSeenAt": now,
                }],
                "generatedAt": now,
                "local": True,
                "tenantId": context.tenant_id if context else "legacy",
            })
        if self._admin_session() is None:
            return
        rows = []
        online_accounts = []
        accounts = tenant_registry.list_accounts()
        activity = tenant_registry.account_activity([account["id"] for account in accounts])
        for account in accounts:
            tenant = tenant_registry.get_tenant_by_id(account["tenant_id"]) or {}
            state = activity.get(account["id"]) or {}
            online = bool(state.get("online")) and account["status"] == "active"
            row = {
                "id": account["id"],
                "username": account["username"],
                "role": account["role"],
                "status": account["status"],
                "tenantId": account["tenant_id"],
                "slug": tenant.get("slug") or "",
                "createdAt": account["created_at"],
                "lastLoginAt": account["last_login_at"],
                "lastSeenAt": state.get("lastSeenAt") or "",
                "online": online,
                "lockedUntil": account["locked_until"],
            }
            rows.append(row)
            if online:
                online_accounts.append({
                    "id": account["id"],
                    "username": account["username"],
                    "lastSeenAt": state.get("lastSeenAt") or "",
                })
        return self._send(200, {
            "accounts": rows,
            "onlineCount": len(online_accounts),
            "onlineUsers": online_accounts,
            "generatedAt": store._now_iso(),
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
        if agent_gateway.is_agent_browser_path(path):
            return agent_gateway.proxy_agent(self)
        if path == "/api/internal/agent/projects":
            return self._send(
                200,
                {"projects": agent_gateway.project_summaries_from_store(store)},
            )
        internal_match = re.match(
            r"^/api/internal/agent/projects/([^/]+)/(project|index|nodes|assets)$",
            path,
        )
        if internal_match:
            project_id = unquote(internal_match.group(1))
            resource = internal_match.group(2)
            project = store.get_project(project_id)
            if not project:
                return self._send(404, {"error": "项目不存在"})
            if resource == "project":
                return self._send(200, project)
            if resource == "index":
                return self._send(
                    200,
                    agent_gateway.project_index_from_project(project),
                )
            query = urllib.parse.parse_qs(parsed.query)
            if resource == "nodes":
                return self._send(
                    200,
                    agent_gateway.project_nodes_from_project(
                        project,
                        episode_id=str((query.get("episodeId") or [""])[0]),
                        node_type=str((query.get("type") or [""])[0]),
                        query=str((query.get("query") or [""])[0]),
                        limit=int((query.get("limit") or ["100"])[0]),
                    ),
                )
            return self._send(
                200,
                agent_gateway.project_assets_from_project(
                    project,
                    limit=int((query.get("limit") or ["300"])[0]),
                ),
            )
        download_media = re.match(
            r"^/api/downloads/media/([^/]+)$",
            path,
        )
        if download_media:
            try:
                ticket = verify_media_download_ticket(
                    unquote(download_media.group(1))
                )
            except ValueError as exc:
                return self._send(
                    403,
                    {"error": str(exc), "code": "DOWNLOAD_TICKET_INVALID"},
                )
            project_id = str(ticket.get("p") or "")
            node_id = str(ticket.get("node") or "")
            version_id = str(ticket.get("vid") or "")
            download_name = str(ticket.get("n") or "")
            try:
                source = resolve_media_download_source(
                    project_id,
                    node_id,
                    version_id,
                )
            except (KeyError, FileNotFoundError):
                return self._send(
                    404,
                    {"error": "文件不存在"},
                )
            if source["localFilename"]:
                return self._send_upload_file_with_options(
                    os.path.join(
                        _upload_dir(),
                        source["localFilename"],
                    ),
                    source["localFilename"],
                    download_name=download_name,
                )
            try:
                signed_url = tos_storage.public_presigned_url(
                    source["storage"],
                    expires=DOWNLOAD_TICKET_TTL_SECONDS,
                )
            except Exception as exc:
                return self._send(
                    502,
                    {
                        "error": f"生成对象存储下载地址失败：{exc}",
                        "code": "REMOTE_DOWNLOAD_SIGN_FAILED",
                    },
                )
            return self._send_remote_download(
                signed_url,
                download_name=download_name,
            )
        share_read = re.match(
            r"^/api/share/([^/]+)/"
            r"(project|workspace-index|workspace-nodes|notes|tasks|events)$",
            path,
        )
        share_media = re.match(
            r"^/api/share/([^/]+)/media/([^/]+)$",
            path,
        )
        if share_media:
            token = unquote(share_media.group(1))
            filename = unquote(share_media.group(2))
            resolved = _resolve_share_scope(token)
            if not resolved:
                return self._send(
                    410,
                    {"error": "分享链接已撤销或过期", "code": "SHARE_INVALID"},
                )
            share_scope, share = resolved
            with share_scope:
                if not store.project_references_upload(
                    share["projectId"],
                    filename,
                ):
                    return self._send(404, {"error": "文件不存在"})
                fpath = os.path.join(_upload_dir(), filename)
                if not os.path.isfile(fpath):
                    return self._send(404, {"error": "文件不存在"})
                return self._send_upload_file(fpath, filename)
        if share_read:
            token = unquote(share_read.group(1))
            resource = share_read.group(2)
            resolved = _resolve_share_scope(token)
            if not resolved:
                return self._send(
                    410,
                    {"error": "分享链接已撤销或过期", "code": "SHARE_INVALID"},
                )
            share_scope, share = resolved
            with share_scope:
                if resource == "project":
                    project = canvas_repository.repository.get_project(
                        share["projectId"]
                    )
                    payload = _public_share_payload(share, project)
                    if not payload:
                        return self._send(404, {"error": "项目不存在"})
                    return self._send(200, payload)
                if resource == "workspace-index":
                    workspace = store.get_project_workspace_index(
                        share["projectId"]
                    )
                    if not workspace:
                        return self._send(404, {"error": "项目不存在"})
                    return self._send(200, workspace)
                if resource == "workspace-nodes":
                    query = urllib.parse.parse_qs(parsed.query)
                    episode_id = str(
                        (query.get("episodeId") or [""])[0]
                    ).strip().upper()
                    workspace = store.get_project_workspace_nodes(
                        share["projectId"],
                        episode_id or None,
                    )
                    if not workspace:
                        return self._send(404, {"error": "项目不存在"})
                    workspace = project_share_workspace_nodes(
                        share["projectId"],
                        workspace,
                    )
                    return self._send(200, workspace)
                if resource == "notes":
                    query = urllib.parse.parse_qs(parsed.query)
                    episode_id = str(
                        (query.get("episodeId") or [""])[0]
                    ).strip().upper()
                    if not episode_id:
                        return self._send(400, {"error": "读取备注必须指定集号"})
                    notes = canvas_repository.repository.get_notes(
                        share["projectId"],
                        episode_id,
                    )
                    return self._send(
                        200,
                        {"episodeId": episode_id, "notes": notes},
                    )
                if resource == "tasks":
                    tasks = store.list_generation_tasks(
                        share["projectId"],
                        120,
                    )
                    return self._send(200, {"tasks": tasks})
                if resource == "events":
                    return self._handle_project_events(
                        share["projectId"],
                        parsed,
                    )
        if path == "/api/auth/session":
            return self._handle_auth_session()
        share_match = re.match(r"^/share/([^/]+)$", path)
        if share_match and self._serve_webapp("/index.html"):
            return
        if share_match:
            return self._send(
                404,
                {"error": "前端产物不存在，请先构建 web-vimax/dist"},
            )
        if path.startswith("/api/projects/") and path.endswith("/events"):
            project_id = path[len("/api/projects/"):-len("/events")].strip("/")
            if project_id and "/" not in project_id:
                return self._handle_project_events(project_id, parsed)
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
            projects = store.list_project_summaries()
            activity_by_project = store.project_activity_summaries()
            for project in projects:
                activity = activity_by_project.get(project["id"], {})
                project["running_count"] = int(activity.get("running_count") or 0)
                project["latest_task_status"] = activity.get("latest_task_status") or ""
                project["latest_task_phase"] = activity.get("latest_task_phase") or ""
                project["latest_task_error_kind"] = activity.get("latest_task_error_kind") or ""
            return self._send(200, {"projects": projects})
        m = re.match(r"^/api/projects/([^/]+)/workspace-index$", path)
        if m:
            project_id = unquote(m.group(1))
            workspace = store.get_project_workspace_index(project_id)
            if not workspace:
                return self._send(404, {"error": "项目不存在"})
            return self._send(200, workspace)
        m = re.match(r"^/api/projects/([^/]+)/workspace-nodes$", path)
        if m:
            project_id = unquote(m.group(1))
            query = urllib.parse.parse_qs(parsed.query)
            episode_id = str((query.get("episodeId") or [""])[0]).strip().upper()
            workspace = store.get_project_workspace_nodes(
                project_id,
                episode_id,
            )
            workspace = project_workspace_nodes(
                project_id,
                workspace,
            )
            return self._send(200, workspace)
        m = re.match(r"^/api/projects/([^/]+)/notes$", path)
        if m:
            project_id = unquote(m.group(1))
            query = urllib.parse.parse_qs(parsed.query)
            episode_id = str((query.get("episodeId") or [""])[0]).strip().upper()
            if not episode_id:
                return self._send(400, {"error": "读取备注必须指定集号"})
            if not store.project_exists(project_id):
                return self._send(404, {"error": "项目不存在"})
            try:
                notes = canvas_repository.repository.get_notes(
                    project_id,
                    episode_id,
                )
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            return self._send(200, {
                "episodeId": episode_id,
                "notes": notes,
            })
        if path == "/api/storage":
            return self._send(200, {"storage": provider_catalog.public_storage_config()})
        if path == "/api/generation-tasks":
            query = urllib.parse.parse_qs(parsed.query)
            project_id = str((query.get("projectId") or [""])[0]).strip() or None
            limit = int((query.get("limit") or ["80"])[0])
            episode_id = str((query.get("episodeId") or [""])[0]).strip().upper()
            if episode_id:
                node_ids = store.get_episode_node_ids(project_id, episode_id)
                if not node_ids:
                    tasks = []
                else:
                    tasks = store.list_generation_tasks_for_node_ids(
                        project_id,
                        node_ids,
                        limit=limit,
                    )
            else:
                tasks = store.list_generation_tasks(
                    project_id,
                    limit,
                )
            for task in tasks:
                if (
                    task.get("status") == "succeeded"
                    and not str(task.get("result_ready_at") or "").strip()
                    and not str(task.get("local_result_url") or "").strip()
                ):
                    # Historical rows predate delivery receipts. Their node state is
                    # already the authoritative completion evidence.
                    task["delivery_status"] = "ready"
                if task.get("status") == "failed":
                    task["error_kind"] = (
                        "local_validation"
                        if generation_outcome.is_local_validation_error(task.get("error"))
                        else "provider"
                    )
                task["outcome"] = generation_outcome.task_outcome(task)
                task["outcome"]["canResolve"] = (
                    generation_outcome.can_manually_resolve_task(
                        task,
                        task["outcome"],
                    )
                )
                task["leaseHealthy"] = bool(
                    task.get("status") in {"queued", "submitting", "running"}
                    and task.get("lease_expires_at")
                    and str(task.get("lease_expires_at")) > store._now_iso()
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
            return self._send(200, {
                "tasks": tasks,
                "episodeId": episode_id or None,
            })
        # 上传文件回读：/uploads/<name>
        # 只允许读取 UPLOAD_DIR 下的单层文件名，禁止 ../ 穿越
        if path.startswith("/uploads/"):
            name = unquote(path[len("/uploads/"):])
            if not name or "/" in name or "\\" in name or name.startswith("."):
                return self._send(400, {"error": "非法文件名"})
            if self._share_access and not store.project_references_upload(
                self._share_access["projectId"],
                name,
            ):
                return self._send(404, {"error": "文件不存在"})
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
        if path == "/healthz":
            return self._send(200, {
                "ok": True,
                "service": "drama-canvas-studio",
                "releaseCommit": release_commit(),
                "uptimeSeconds": round(
                    time.monotonic() - SERVER_STARTED_AT,
                    3,
                ),
            })
        if path == "/readyz":
            report = operational_metrics.readiness(
                "api",
                release_commit=release_commit(),
                execution_mode=GENERATION_EXECUTION_MODE,
            )
            return self._send(200 if report["ok"] else 503, report)
        if path == "/metrics":
            client_ip = (
                str(self.client_address[0])
                if self.client_address
                else ""
            )
            if client_ip not in {"127.0.0.1", "::1"}:
                return self._send(404, {"error": "资源不存在"})
            return self._send(
                200,
                raw=operational_metrics.render_prometheus(
                    release_commit()
                ),
                content_type="text/plain; version=0.0.4; charset=utf-8",
            )
        if path == "/api/config":
            return self._send(200, provider_catalog.config_status())
        if path == "/api/audit":
            try:
                filters = _audit_query_from_url(parsed)
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            return self._send(200, store.list_audit_events(**filters))
        if path == "/api/admin/audit":
            if self._admin_session() is None:
                return
            try:
                filters = _audit_query_from_url(parsed)
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            current = tenant_context.current()
            target = current.tenant_id if current else "legacy"
            try:
                with _admin_tenant_audit_context(target):
                    return self._send(
                        200,
                        store.list_audit_events(**filters),
                    )
            except ValueError as exc:
                return self._send(404, {"error": str(exc)})
        if path == "/api/admin/audit/health":
            if self._admin_session() is None:
                return
            return self._send(200, store.audit_storage_health())
        if path == "/api/admin/frontend-errors":
            if self._admin_session() is None:
                return
            params = urllib.parse.parse_qs(parsed.query)
            current = tenant_context.current()
            target = current.tenant_id if current else "legacy"
            try:
                with _admin_tenant_audit_context(target):
                    return self._send(200, {
                        "events": store.list_frontend_errors(
                            limit=(params.get("limit") or ["100"])[0],
                            project_id=(params.get("projectId") or [""])[0],
                            node_id=(params.get("nodeId") or [""])[0],
                            task_id=(params.get("taskId") or [""])[0],
                        ),
                    })
            except ValueError as exc:
                return self._send(404, {"error": str(exc)})
        m = re.match(r"^/api/admin/tenants/([^/]+)/audit$", path)
        if m:
            if self._admin_session() is None:
                return
            tenant_id = unquote(m.group(1))
            try:
                filters = _audit_query_from_url(parsed)
                with _admin_tenant_audit_context(tenant_id):
                    return self._send(
                        200,
                        store.list_audit_events(**filters),
                    )
            except ValueError as exc:
                return self._send(404, {"error": str(exc)})
        m = re.match(r"^/api/admin/tenants/([^/]+)/frontend-errors$", path)
        if m:
            if self._admin_session() is None:
                return
            tenant_id = unquote(m.group(1))
            params = urllib.parse.parse_qs(parsed.query)
            try:
                with _admin_tenant_audit_context(tenant_id):
                    return self._send(200, {
                        "events": store.list_frontend_errors(
                            limit=(params.get("limit") or ["100"])[0],
                            project_id=(params.get("projectId") or [""])[0],
                            node_id=(params.get("nodeId") or [""])[0],
                            task_id=(params.get("taskId") or [""])[0],
                        ),
                    })
            except ValueError as exc:
                return self._send(404, {"error": str(exc)})
        if path == "/api/metrics":
            tenant_key = tenant_context.current_tenant_id()
            metrics = {
                **store.runtime_metrics(),
                "databasePool": pg_compat.pool_stats(),
                "generationQueue": generation_queue.queue_metrics(),
                "releaseCommit": release_commit(),
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
            }
            if pg_compat.postgres_enabled():
                metrics["generationAdmissions"] = (
                    generation_queue.admission_metrics()
                )
                metrics["workerHeartbeats"] = (
                    generation_queue.worker_heartbeats(
                        active_within_seconds=60
                    )
                )
            return self._send(200, metrics)
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
            asset_aliases = [
                entry
                for entry in (payload.get("assetAliases") or [])
                if isinstance(entry, dict)
                and str(entry.get("assetId") or "").strip()
            ]
            assets = []
            active_asset_ids = set()
            for node in payload.get("nodes") or []:
                if node.get("type") not in {"image", "audio", "video"}:
                    continue
                meta = node.get("metadata") or {}
                asset_id = str(
                    meta.get("assetId")
                    or (meta.get("params") or {}).get("assetId")
                    or ""
                ).strip()
                if asset_id:
                    active_asset_ids.add(asset_id)
                params = meta.get("params") or {}
                code_match = re.search(r"图\s*(\d+)", str(node.get("title") or ""))
                versions = (
                    params.get("mediaVersions")
                    or params.get("videoVersions")
                    or []
                )
                node_episode = str(
                    node.get("episodeId")
                    or meta.get("episodeId")
                    or ""
                ).strip().upper()
                episode_ids = (
                    [node_episode]
                    if node_episode
                    and node_episode != store._UNASSIGNED_EPISODE_ID
                    else []
                )
                assets.append({
                    "id": node.get("id"),
                    "kind": node.get("type"),
                    "title": node.get("title") or "",
                    "code": f"图{code_match.group(1)}" if code_match else "",
                    "assetKey": meta.get("assetKey") or "",
                    "assetId": asset_id,
                    "seriesId": meta.get("seriesId") or params.get("seriesId") or "",
                    "variantKey": meta.get("variantKey") or params.get("variantKey") or "default",
                    "status": meta.get("status") or "idle",
                    "url": meta.get("content") or "",
                    "prompt": meta.get("prompt") or "",
                    "episodeIds": episode_ids,
                    "versions": versions,
                    "reusedAsset": meta.get("reusedAsset") or None,
                    "storage": meta.get("storage") or None,
                    "displayName": meta.get("displayName") or "",
                    "aliases": [
                        entry
                        for entry in asset_aliases
                        if entry.get("assetId") == asset_id
                    ],
                })
            reused_aliases = [
                {
                    "assetId": str(entry.get("assetId") or ""),
                    "aliasOf": str(entry.get("aliasOf") or ""),
                    "oldName": str(entry.get("oldName") or ""),
                    "newName": str(entry.get("newName") or ""),
                    "figure": str(entry.get("figure") or ""),
                }
                for entry in (payload.get("assetAliases") or [])
                if isinstance(entry, dict)
                and str(entry.get("assetId") or "") in active_asset_ids
            ]
            return self._send(200, {
                "schema": "drama-canvas-assets/v1",
                "projectId": pid,
                "title": proj.get("title") or payload.get("title") or "",
                "revision": proj.get("revision"),
                "exportedAt": store._now_iso(),
                "reusedAliases": reused_aliases,
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
        m = re.match(r"^/api/projects/([^/]+)/shares$", path)
        if m:
            current = self._current_session()
            if not current:
                return self._send(401, {"error": "请先登录"})
            project_id = unquote(m.group(1))
            if not store.project_exists(project_id):
                return self._send(404, {"error": "项目不存在"})
            return self._send(200, {
                "shares": store.list_project_shares(project_id),
            })
        m = re.match(r"^/api/projects/([^/]+)$", path)
        if m:
            pid = unquote(m.group(1))
            proj = canvas_repository.repository.get_project(pid)
            if not proj:
                return self._send(404, {"error": "项目不存在"})
            return self._send(200, project_project_media_urls(proj))
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
        except generation_budget_guard.BudgetGuardTimeout as exc:
            return self._send(503, {
                "error": str(exc),
                "code": exc.code,
            })
        except generation_budget_guard.BudgetGuardUnavailable as exc:
            return self._send(503, {
                "error": str(exc),
                "code": exc.code,
            })
        except media_delivery.MediaDeliveryUnavailable as exc:
            return self._send(503, {
                "error": str(exc),
                "code": exc.code,
            })

    def _do_post(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/telemetry/frontend-errors":
            session = self._current_session()
            if _auth_mode() == "accounts" and not session:
                return self._send(
                    401,
                    {
                        "error": "请先登录",
                        "code": "ACCOUNT_AUTH_REQUIRED",
                    },
                )
            try:
                body = self._read_json()
            except RequestBoundaryError:
                raise
            except Exception as exc:
                return self._send(
                    400,
                    {
                        "error": f"前端错误遥测格式无效：{exc}",
                        "code": "FRONTEND_TELEMETRY_INVALID",
                    },
                )
            event_id = store.record_frontend_error(
                body,
                account_id=str((session or {}).get("account_id") or ""),
            )
            if not event_id:
                return self._send(
                    400,
                    {
                        "error": "前端错误遥测缺少可记录的 message",
                        "code": "FRONTEND_TELEMETRY_INVALID",
                    },
                )
            return self._send(202, {"ok": True, "id": event_id})
        if agent_gateway.is_agent_browser_path(path):
            return agent_gateway.proxy_agent(self)
        internal_feed = re.match(
            r"^/api/internal/agent/projects/([^/]+)/feed/(preview|import)$",
            path,
        )
        if internal_feed:
            project_id = unquote(internal_feed.group(1))
            mode = internal_feed.group(2)
            try:
                body = json.loads(
                    getattr(self, "_internal_agent_body", b"{}").decode("utf-8")
                    or "{}"
                )
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._send(
                    400,
                    {"error": "Agent Feed 请求体不是有效 JSON"},
                )
            feed = body.get("feed_json")
            if not isinstance(feed, dict):
                return self._send(
                    400,
                    {"error": "feed_json 必须是对象"},
                )
            source = body.get("source") if isinstance(body.get("source"), dict) else {"name": "agent"}
            project = store.get_project(project_id)
            if not project:
                return self._send(404, {"error": "项目不存在"})
            if mode == "preview":
                plan = canvas_feed_contract.prepare_import(
                    project["payload"],
                    feed,
                    project_id,
                    int(project.get("revision") or 0),
                    source=source,
                )
                issues = [canvas_feed_contract.issue_dict(item) for item in plan.errors]
                warnings = [canvas_feed_contract.issue_dict(item) for item in plan.warnings]
                if plan.errors:
                    return self._send(
                        422,
                        {
                            "error": "导入校验失败，项目未被修改",
                            "code": "FEED_VALIDATION_FAILED",
                            "revision": plan.revision,
                            "issues": issues,
                            "warnings": warnings,
                        },
                    )
                return self._send(
                    200,
                    {
                        "dryRun": True,
                        "id": project_id,
                        "revision": plan.revision,
                        "episodeId": plan.episode_id,
                        "summary": plan.summary,
                        "warnings": warnings,
                        "provenance": plan.provenance,
                    },
                )
            try:
                plan, commit_result = canvas_feed_contract.prepare_import_locked(
                    feed,
                    project_id,
                    source=source,
                )
            except canvas_feed_contract.RevisionConflictError as exc:
                return self._send(
                    409,
                    {
                        "error": str(exc),
                        "code": "AGENT_FEED_REVISION_CONFLICT",
                    },
                )
            except ValueError as exc:
                return self._send(
                    400,
                    {
                        "error": str(exc),
                        "code": "AGENT_IMPORT_CONFLICT",
                    },
                )
            issues = [canvas_feed_contract.issue_dict(item) for item in plan.errors]
            warnings = [canvas_feed_contract.issue_dict(item) for item in plan.warnings]
            if plan.errors:
                return self._send(
                    422,
                    {
                        "error": "导入校验失败，项目未被修改",
                        "code": "FEED_VALIDATION_FAILED",
                        "revision": plan.revision,
                        "issues": issues,
                        "warnings": warnings,
                    },
                )
            return self._send(
                200,
                {
                    **commit_result,
                    "summary": plan.summary,
                    "warnings": warnings,
                    "provenance": plan.provenance,
                },
            )
        m = re.match(
            r"^/api/projects/([^/]+)/downloads$",
            path,
        )
        if m:
            body = self._read_json()
            project_id = unquote(m.group(1))
            node_id = str(body.get("nodeId") or "").strip()
            version_id = str(body.get("versionId") or "").strip()
            download_name = str(
                body.get("downloadName") or ""
            ).strip()
            if not node_id or not download_name:
                return self._send(400, {"error": "下载参数不完整"})
            try:
                resolve_media_download_source(
                    project_id,
                    node_id,
                    version_id,
                )
            except (KeyError, FileNotFoundError):
                return self._send(404, {"error": "文件不存在"})
            try:
                ticket = issue_media_download_ticket(
                    project_id=project_id,
                    node_id=node_id,
                    version_id=version_id,
                    download_name=download_name,
                )
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            return self._send(
                200,
                {
                    "url": (
                        "/api/downloads/media/"
                        + urllib.parse.quote(ticket, safe="")
                    ),
                    "expiresIn": DOWNLOAD_TICKET_TTL_SECONDS,
                    "downloadName": download_name,
                },
            )
        if path == "/api/upload/stream":
            return self._handle_stream_upload()
        body = self._read_json()
        m = re.match(
            r"^/api/projects/([^/]+)/nodes/([^/]+)/local-version$",
            path,
        )
        if m:
            pid, nid = unquote(m.group(1)), unquote(m.group(2))
            project = store.get_project(pid)
            if not project:
                return self._send(404, {"error": "项目不存在"})
            current = store.get_projection_node(pid, nid)
            if not current:
                return self._send(404, {"error": "节点不存在"})
            episode_id = str(
                body.get("episodeId")
                or current.get("episodeId")
                or (current.get("metadata") or {}).get("episodeId")
                or ""
            ).strip().upper()
            if not episode_id:
                return self._send(400, {
                    "error": "保存本地素材必须指定所属集",
                    "code": "CANVAS_EPISODE_REQUIRED",
                })
            upload = body.get("upload")
            if not isinstance(upload, dict):
                return self._send(400, {"error": "上传结果缺失"})
            appended = {}

            def merge_metadata(target, _incoming):
                version = _append_local_media_version(
                    target,
                    str(current.get("type") or ""),
                    upload,
                )
                appended.update(version)

            try:
                updated = store.patch_project_node_atomic(
                    pid,
                    episode_id,
                    nid,
                    {
                        # Appending a local version is a field-level command, not
                        # an editor snapshot. The atomic merge must read the
                        # latest node row; a stale card timestamp must not turn
                        # a successful upload into a false tab-conflict.
                        "metadata": {},
                        **(
                            {"title": str(body["title"]).strip()}
                            if str(body.get("title") or "").strip()
                            else {}
                        ),
                    },
                    merge_metadata=merge_metadata,
                )
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            except store.RevisionConflictError:
                return self._send(409, {
                    "error": "另一个标签页已更新此节点，请刷新后重试",
                    "code": "NODE_EDIT_CONFLICT",
                    "nodeId": nid,
                })
            if not updated:
                return self._send(409, {
                    "error": "保存本地素材版本冲突，请重试",
                    "code": "NODE_REVISION_CONFLICT",
                })
            media_storage.project_node_media_url(updated)
            updated_meta = updated.setdefault("metadata", {})
            updated_params = updated_meta.setdefault("params", {})
            _publish_node_metadata_event(pid, nid, {
                "status": "done",
                "content": str(updated_meta.get("content") or ""),
                "generatedAt": str(updated_meta.get("generatedAt") or ""),
                "source": updated_meta.get("source") or {},
                "providerResultUrl": str(updated_meta.get("providerResultUrl") or ""),
                "storage": updated_meta.get("storage") or {},
                "actualDuration": updated_meta.get("actualDuration"),
                "_paramsPatch": {
                    "mediaVersions": updated_params.get("mediaVersions"),
                    "mediaVersionIndex": updated_params.get("mediaVersionIndex"),
                    "activeVersionId": updated_params.get("activeVersionId"),
                    "videoVersions": updated_params.get("videoVersions"),
                    "videoVersionIndex": updated_params.get("videoVersionIndex"),
                    "thumbUrl": updated_params.get("thumbUrl"),
                },
            })
            return self._send(200, {
                "ok": True,
                "nodeId": nid,
                "node": updated,
                "versionId": str(appended.get("versionId") or ""),
                "updatedAt": str(updated.get("updatedAt") or ""),
            })
        m = re.match(
            r"^/api/projects/([^/]+)/nodes/([^/]+)/select-version$",
            path,
        )
        if m:
            pid, nid = unquote(m.group(1)), unquote(m.group(2))
            project = store.get_project(pid)
            if not project:
                return self._send(404, {"error": "项目不存在"})
            current = store.get_projection_node(pid, nid)
            if not current:
                return self._send(404, {"error": "节点不存在"})
            episode_id = str(
                body.get("episodeId")
                or current.get("episodeId")
                or (current.get("metadata") or {}).get("episodeId")
                or ""
            ).strip().upper()
            if not episode_id:
                return self._send(400, {
                    "error": "切换素材版本必须指定所属集",
                    "code": "CANVAS_EPISODE_REQUIRED",
                })
            version_id = str(body.get("versionId") or "").strip()
            version_url = str(
                body.get("versionUrl")
                or body.get("version_url")
                or ""
            ).strip()
            selected = {}

            def merge_metadata(target, _incoming):
                nonlocal selected
                selected = _select_media_version(
                    target,
                    str(current.get("type") or ""),
                    version_id,
                    version_url,
                )

            try:
                updated = store.patch_project_node_atomic(
                    pid,
                    episode_id,
                    nid,
                    {
                        # Selection is authoritative against the current node
                        # row. The version ID is validated inside the same
                        # transaction; the card's old updatedAt is irrelevant.
                        "metadata": {},
                    },
                    merge_metadata=merge_metadata,
                    write_payload=False,
                )
            except MediaVersionError as exc:
                return self._send(exc.status, {
                    "error": str(exc),
                    "code": exc.code,
                    "nodeId": nid,
                })
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            except store.RevisionConflictError:
                return self._send(409, {
                    "error": "另一个标签页已更新此节点，请刷新后重试",
                    "code": "NODE_EDIT_CONFLICT",
                    "nodeId": nid,
                })
            if not updated:
                return self._send(409, {
                    "error": "切换素材版本冲突，请重试",
                    "code": "NODE_REVISION_CONFLICT",
                })
            media_storage.project_node_media_url(updated)
            updated_meta = updated.setdefault("metadata", {})
            updated_params = updated_meta.setdefault("params", {})
            _publish_node_metadata_event(pid, nid, {
                "status": "done",
                "content": str(updated_meta.get("content") or ""),
                "generatedAt": str(updated_meta.get("generatedAt") or ""),
                "source": updated_meta.get("source") or {},
                "providerResultUrl": str(updated_meta.get("providerResultUrl") or ""),
                "storage": updated_meta.get("storage") or {},
                "actualDuration": updated_meta.get("actualDuration"),
                "_paramsPatch": {
                    "mediaVersions": updated_params.get("mediaVersions"),
                    "mediaVersionIndex": updated_params.get("mediaVersionIndex"),
                    "activeVersionId": updated_params.get("activeVersionId"),
                    "videoVersions": updated_params.get("videoVersions"),
                    "videoVersionIndex": updated_params.get("videoVersionIndex"),
                    "thumbUrl": updated_params.get("thumbUrl"),
                },
            })
            return self._send(200, {
                "ok": True,
                "nodeId": nid,
                "node": updated,
                "versionId": str(selected.get("versionId") or version_id),
                "versionIndex": int(selected.get("index") or 0),
                "updatedAt": str(updated.get("updatedAt") or ""),
            })
        m = re.match(r"^/api/projects/([^/]+)/shares$", path)
        if m:
            current = self._current_session()
            if not current:
                return self._send(401, {"error": "请先登录"})
            project_id = unquote(m.group(1))
            try:
                share = store.create_project_share(
                    project_id,
                    created_by=str(current.get("account_username") or ""),
                    ttl_days=int(body.get("ttlDays") or 7),
                    role=str(body.get("role") or "viewer"),
                    label=str(body.get("label") or ""),
                )
            except (TypeError, ValueError) as exc:
                return self._send(400, {"error": str(exc)})
            store.record_audit_event(
                "project.share_create",
                actor=str(current.get("account_username") or ""),
                target_type="project",
                target_id=project_id,
                details={
                    "shareId": share["id"],
                    "expiresAt": share["expiresAt"],
                    "role": share["role"],
                    "label": share["label"],
                },
            )
            return self._send(200, {
                **share,
                "url": f"/share/{share['token']}",
                "readOnly": share["role"] != "editor",
            })
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
        if path == "/api/storage/activate":
            try:
                provider = str((body or {}).get("provider") or "")
                storage = provider_catalog.activate_storage_provider(provider)
                store.record_audit_event(
                    "storage.activate",
                    actor=(self._current_session() or {}).get("account_username", ""),
                    target_type="storage",
                    details={"provider": storage.get("provider")},
                )
                return self._send(200, {"ok": True, "storage": storage})
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
        if path == "/api/storage/deactivate":
            try:
                storage = provider_catalog.deactivate_storage()
                store.record_audit_event(
                    "storage.deactivate",
                    actor=(self._current_session() or {}).get("account_username", ""),
                    target_type="storage",
                    details={"provider": storage.get("provider")},
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
            request_body = body if isinstance(body, dict) else {}
            try:
                provider = str(request_body.get("provider") or "").strip().lower()
                result = (
                    media_storage.test_connection_for(provider)
                    if provider
                    else media_storage.test_connection()
                )
                return self._send(200, {"ok": True, **result})
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
                    # Preview is read-only and may reuse the normal planner.
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
                    committed_plan, commit_result = canvas_feed_contract.prepare_import_locked(
                        feed_json,
                        pid,
                        source=source_meta,
                    )
                    if committed_plan.errors:
                        return self._send(422, {
                            "error": "导入校验失败，项目未被修改",
                            "code": "FEED_VALIDATION_FAILED",
                            "revision": committed_plan.revision,
                            "issues": [
                                canvas_feed_contract.issue_dict(item)
                                for item in committed_plan.errors
                            ],
                            "warnings": [
                                canvas_feed_contract.issue_dict(item)
                                for item in committed_plan.warnings
                            ],
                        })
                except canvas_feed_contract.RevisionConflictError as exc:
                    return self._send(409, {
                        "error": str(exc),
                        "code": canvas_feed_contract.ErrorCode.REVISION_CONFLICT,
                        "revision": plan.revision,
                        "warnings": warning_payload,
                    })
                except ValueError as exc:
                    return self._send(400, {
                        "error": str(exc),
                        "code": "CANVAS_IMPORT_OWNERSHIP_INVALID",
                    })
                plan = committed_plan
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
            body = body if isinstance(body, dict) else {}
            query = urllib.parse.parse_qs(parsed.query)
            episode_id = str(
                body.get("episodeId")
                or (query.get("episodeId") or [""])[0]
            ).strip().upper()
            if not episode_id:
                return self._send(400, {"error": "保存备注必须指定集号"})
            try:
                result = canvas_repository.repository.save_notes(
                    pid,
                    episode_id,
                    body.get("notes") or [],
                    if_revision=body.get("if_revision"),
                )
            except canvas_store.RevisionConflictError as exc:
                return self._send(409, {"error": str(exc)})
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            if result is None:
                return self._send(404, {"error": "项目不存在"})
            return self._send(200, {"ok": True, **result})
        m = re.match(r"^/api/projects/([^/]+)/nodes/([^/]+)/recover-media$", path)
        if m:
            pid, nid = unquote(m.group(1)), unquote(m.group(2))
            try:
                result = recover_missing_generated_media(pid, nid)
            except KeyError as exc:
                return self._send(404, {"error": str(exc)})
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            except Exception as exc:
                return self._send(502, {
                    "error": f"恢复生成结果失败：{exc}",
                    "code": "MEDIA_RECOVERY_FAILED",
                })
            return self._send(200 if result.get("restored") else 409, result)
        m = re.match(r"^/api/projects/([^/]+)/nodes/([^/]+)/generate$", path)
        if m:
            pid, nid = unquote(m.group(1)), unquote(m.group(2))
            proj = store.get_project(pid)
            if not proj:
                return self._send(404, {"error": "项目不存在"})
            node = next((n for n in proj["payload"].get("nodes", []) if n["id"] == nid), None)
            if not node:
                return self._send(404, {"error": "节点不存在"})
            requested_episode = str(body.get("episodeId") or "").strip().upper()
            node_episode = str(
                node.get("episodeId")
                or (node.get("metadata") or {}).get("episodeId")
                or ""
            ).strip().upper()
            if (
                requested_episode
                and node_episode
                and requested_episode != node_episode
            ):
                return self._send(409, {
                    "error": (
                        f"节点属于 {node_episode}，不能在 {requested_episode} 提交生成"
                    ),
                    "code": "NODE_EPISODE_MISMATCH",
                })
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
            media_delivery.assert_submission_ready(
                str(node.get("type") or "")
            )
            requested_operation_id = str(
                body.get("clientOperationId") or ""
            ).strip()
            request_entry = str(body.get("entry") or "single").strip()[:80]
            request_batch_id = str(body.get("batchId") or "").strip()[:120]
            # Reserve budget and create the task under one tenant-wide
            # PostgreSQL advisory lock. The lock is shared by every API process.
            with GENERATION_BUDGET_LOCK:
                budget_issue, budget_estimate = check_generation_budget(
                    node.get("type") or "",
                    params,
                    proj["payload"],
                )
                if budget_issue:
                    store.record_audit_event(
                        "generation.budget_blocked",
                        actor=(
                            self._current_session() or {}
                        ).get("account_username", ""),
                        target_type="project_node",
                        target_id=f"{pid}:{node['id']}",
                        status="blocked",
                        details={
                            "projectId": pid,
                            "nodeId": node["id"],
                            "kind": node.get("type"),
                            "code": budget_issue.get("code"),
                            "estimate": budget_estimate,
                            "entry": request_entry,
                            "batchId": request_batch_id,
                            "operationId": requested_operation_id,
                            "sessionId": str(
                                (self._current_session() or {}).get("id") or ""
                            ),
                            "userAgent": str(
                                self.headers.get("User-Agent", "")
                            )[:500],
                            "ip": str(
                                self.client_address[0]
                                if self.client_address
                                else ""
                            )[:128],
                        },
                    )
                    return self._send(402, {
                        **budget_issue,
                        "estimate": budget_estimate,
                    })
                reconcile_active_generation_task_for_node(pid, node["id"], node)
                if requested_operation_id:
                    existing_operation = next(
                        (
                            item
                            for item in store.list_generation_tasks(pid, 300)
                            if item.get("client_operation_id") == requested_operation_id
                        ),
                        None,
                    )
                    if existing_operation:
                        latest_task = store.get_generation_task(existing_operation["id"]) or existing_operation
                        current_status = str(latest_task.get("status") or "")
                        if current_status in {"queued", "submitting", "running", "interrupted"}:
                            return self._send(202, {
                                "accepted": True,
                                "replayed": True,
                                "taskId": latest_task.get("id"),
                                "nodeId": node["id"],
                                "status": "pending",
                                "code": "GENERATION_TASK_REPLAYED",
                            })
                        if current_status == "succeeded":
                            return self._send(200, {
                                "replayed": True,
                                "taskId": latest_task.get("id"),
                                "nodeId": node["id"],
                                "status": "done",
                            })
                        return self._send(409, {
                            "replayed": True,
                            "taskId": latest_task.get("id"),
                            "nodeId": node["id"],
                            "status": "failed",
                            "code": "GENERATION_TASK_REPLAYED_FAILED",
                            "error": str(latest_task.get("error") or "原生成任务已失败"),
                        })
                unresolved_provider_task = _unresolved_provider_task_for_node(
                    pid,
                    node["id"],
                )
                if unresolved_provider_task:
                    return self._send(202, {
                        "accepted": True,
                        "taskId": unresolved_provider_task.get("id"),
                        "nodeId": node["id"],
                        "status": "pending",
                        "code": "PROVIDER_OUTCOME_UNCERTAIN",
                        "error": (
                            "该节点仍有供应商任务结果待确认，"
                            "系统会继续查询原任务，不会自动重复提交。"
                        ),
                    })
                request_snapshot = {
                    "version": 1,
                    "projectId": pid,
                    "nodeId": node["id"],
                    "episodeId": node_episode or requested_episode,
                    "kind": node.get("type") or "",
                    "provider": params.get("provider") or meta.get("provider") or "",
                    "model": params.get("model") or meta.get("model") or "",
                    "prompt": str(body.get("prompt") if "prompt" in body else meta.get("prompt") or ""),
                    "params": dict(params),
                    "refs": list(meta.get("refs") or []),
                    "voiceRefs": list(params.get("voiceRefs") or meta.get("voiceRefs") or []),
                    "createdAt": store._now_iso(),
                }
                task = store.create_generation_task(
                    pid, node["id"], title=node.get("title", ""), kind=node.get("type", ""),
                    episode_id=node_episode or requested_episode or str(
                        meta.get("episodeId") or ""
                    ),
                    provider=params.get("provider") or meta.get("provider") or "",
                    model=params.get("model") or meta.get("model") or "",
                    estimated_cost=(budget_estimate or {}).get("amount") or 0,
                    cost_currency=(budget_estimate or {}).get("currency") or "",
                    request_snapshot=request_snapshot,
                    client_operation_id=requested_operation_id,
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
                if task and task.get("replayed"):
                    latest_task = store.get_generation_task(task["id"]) or task
                    status = str(latest_task.get("status") or "")
                    if status in {"queued", "submitting", "running", "interrupted"}:
                        return self._send(202, {
                            "accepted": True,
                            "replayed": True,
                            "taskId": latest_task.get("id"),
                            "nodeId": node["id"],
                            "status": "pending",
                            "code": "GENERATION_TASK_REPLAYED",
                        })
                    if status == "succeeded":
                        return self._send(200, {
                            "replayed": True,
                            "taskId": latest_task.get("id"),
                            "nodeId": node["id"],
                            "status": "done",
                        })
                    return self._send(409, {
                        "replayed": True,
                        "taskId": latest_task.get("id"),
                        "nodeId": node["id"],
                        "status": "failed",
                        "code": "GENERATION_TASK_REPLAYED_FAILED",
                        "error": str(latest_task.get("error") or "原生成任务已失败"),
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
                        "entry": request_entry,
                        "batchId": request_batch_id,
                        "operationId": requested_operation_id,
                        "sessionId": str(
                            (self._current_session() or {}).get("id") or ""
                        ),
                        "userAgent": str(
                            self.headers.get("User-Agent", "")
                        )[:500],
                        "ip": str(
                            self.client_address[0]
                            if self.client_address
                            else ""
                        )[:128],
                    },
                )
            try:
                context = tenant_context.current()
                tenant_id = context.tenant_id if context else None
                tenant_slug = context.slug if context else "local"
                dispatch_generation_task(
                    task["id"],
                    tenant_id,
                    tenant_slug,
                )
            except Exception as exc:
                store.finish_generation_task(
                    task["id"],
                    status="failed",
                    phase="任务启动失败",
                    error=str(exc),
                    delivery_status="failed",
                )
                return self._send(503, {
                    "error": str(exc),
                    "code": "GENERATION_WORKER_START_FAILED",
                    "taskId": task.get("id"),
                })
            return self._send(202, {
                "accepted": True,
                "taskId": task["id"],
                "nodeId": node["id"],
                "status": "pending",
                "code": "GENERATION_TASK_ACCEPTED",
            })
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
            store.finish_generation_task(
                task_id,
                status="interrupted",
                phase="已取消",
                error="用户取消了排队任务",
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
        m = re.match(r"^/api/generation-tasks/([^/]+)/resolve$", path)
        if m:
            task_id = unquote(m.group(1))
            task = store.get_generation_task(task_id)
            if not task:
                return self._send(404, {"error": "任务不存在"})
            outcome = generation_outcome.task_outcome(task)
            if not generation_outcome.can_manually_resolve_task(task, outcome):
                return self._send(409, {
                    "error": "该任务当前不能人工确认失败",
                    "code": "TASK_OUTCOME_NOT_RESOLVABLE",
                    "outcome": outcome,
                })
            finished_at = store._now_iso()
            node_updates = {
                "status": "failed",
                "lastError": (
                    "原任务结果待确认，已由用户确认失败；"
                    "可主动重新生成。"
                ),
                "providerTaskStatus": "failed",
                "providerTaskPhase": "人工确认失败",
                "generationFinishedAt": finished_at,
            }
            event_payload = _generation_terminal_event_payload(
                task["node_id"],
                node_updates,
                task={"id": task_id, "status": "failed", "phase": "人工确认失败"},
            )
            resolution = store.resolve_generation_task_failed_atomic(
                task_id,
                expected_updated_at=task.get("updated_at") or "",
                phase="人工确认失败",
                error=(
                    "用户已确认不再等待原任务结果；"
                    "该任务不会自动重提，可主动重新生成。"
                ),
                node_updates=node_updates,
                event_kind="media",
                event_payload=event_payload,
                audit_action="generation.resolve",
                audit_actor=(self._current_session() or {}).get(
                    "account_username",
                    "",
                ),
                audit_details={
                    "projectId": task.get("project_id"),
                    "nodeId": task.get("node_id"),
                    "kind": task.get("kind"),
                    "action": "confirm_failed",
                    "previousStatus": task.get("status"),
                    "previousPhase": task.get("phase"),
                    "providerTaskId": task.get("provider_task_id") or "",
                    "reason": outcome.get("reason") or "",
                },
            )
            if not resolution.get("resolved"):
                return self._send(409, {
                    "error": "任务状态已变化，请刷新后重试",
                    "code": "TASK_RESOLUTION_CONFLICT",
                    "reason": resolution.get("reason") or "",
                })
            resolved = resolution["task"]
            _notify_project_event_subscribers(
                task["project_id"],
                resolution.get("eventId"),
            )
            resolved_outcome = generation_outcome.task_outcome(resolved)
            resolved_outcome["canResolve"] = False
            resolved["outcome"] = resolved_outcome
            return self._send(200, {
                "ok": True,
                "taskId": task_id,
                "task": resolved,
            })
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
        if agent_gateway.is_agent_browser_path(path):
            return agent_gateway.proxy_agent(self)
        project_id = project_id_for_path(path)
        if project_id:
            with project_write_lock(project_id):
                return self._do_put_locked()
        with PROJECT_WRITE_LOCK:
            return self._do_put_locked()

    def _do_put_locked(self):
        path = urlparse(self.path).path
        body = self._read_json()
        if path == "/api/preferences":
            return self._handle_tenant_preferences_update(body)
        m = re.match(
            r"^/api/projects/([^/]+)/assets/([^/]+)/rename$",
            path,
        )
        if m:
            pid, nid = unquote(m.group(1)), unquote(m.group(2))
            if not isinstance(body, dict):
                return self._send(400, {
                    "error": "请求体必须是对象",
                    "code": "ASSET_RENAME_INVALID",
                })
            project = store.get_project(pid)
            if not project:
                return self._send(404, {"error": "项目不存在"})
            new_name = str(body.get("name") or "").strip()
            expected_asset_id = str(body.get("assetId") or "").strip()
            expected_revision = body.get("revision")
            try:
                result = store.rename_project_asset_atomic(
                    pid,
                    nid,
                    new_name,
                    expected_asset_id=expected_asset_id,
                    expected_revision=(
                        int(expected_revision)
                        if expected_revision is not None
                        else None
                    ),
                )
            except ValueError as exc:
                return self._send(400, {
                    "error": str(exc),
                    "code": "ASSET_RENAME_INVALID",
                })
            except store.RevisionConflictError:
                return self._send(409, {
                    "error": "项目已更新，请刷新后重试",
                    "code": "ASSET_RENAME_CONFLICT",
                })
            if not result:
                return self._send(404, {"error": "项目或资产不存在"})
            if result.get("error"):
                error_code = str(result["error"])
                status = 404 if error_code == "node_not_found" else (
                    409 if error_code in {
                        "asset_identity_conflict",
                        "asset_name_conflict",
                    } else 400
                )
                messages = {
                    "node_not_found": "资产节点不存在",
                    "non_asset_node": "只有图片或音频资产节点可以改名",
                    "asset_identity_conflict": "资产身份已经变化，请刷新后重试",
                    "asset_title_invalid": "资产标题不符合“类型｜名称（图N）”格式",
                    "asset_name_conflict": "该资产名称与同图号资产冲突",
                }
                return self._send(status, {
                    "error": messages.get(error_code, "资产改名失败"),
                    "code": error_code.upper(),
                })
            session = self._current_session() or {}
            store.record_audit_event(
                "asset.rename",
                actor=str(session.get("account_username") or ""),
                target_type="node",
                target_id=nid,
                details={
                    "projectId": pid,
                    "assetId": result.get("assetId") or "",
                    "figure": result.get("figure") or "",
                    "oldName": result.get("oldName") or "",
                    "newName": result.get("newName") or "",
                    "updatedNodes": result.get("updatedNodes") or 0,
                    "updatedReferences": result.get("updatedReferences") or 0,
                    "revision": result.get("revision"),
                },
            )
            result["ok"] = True
            return self._send(200, result)
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

                def pair_key(from_id: str, to_id: str) -> tuple[str, str]:
                    return (
                        (from_id, to_id)
                        if from_id < to_id
                        else (to_id, from_id)
                    )

                automatic_pair_keys = {
                    pair_key(from_id, to_id)
                    for from_id, to_id in automatic_pairs
                    if from_id and to_id
                }
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
                    pair = pair_key(from_id, to_id)
                    if pair in automatic_pair_keys or pair in manual_pairs:
                        continue
                    manual_pairs.add(pair)
                    manual.append({
                        "id": str(item.get("id") or f"e-user-{len(manual) + 1}"),
                        "fromNodeId": from_id,
                        "toNodeId": to_id,
                        "source": "user",
                    })
                old_manual_count = len(existing) - len(automatic)
                next_connections = [*automatic, *manual]
                if next_connections == existing:
                    return self._send(200, {
                        "ok": True,
                        "total": len(next_connections),
                        "manual": len(manual),
                        "removed": max(0, old_manual_count - len(manual)),
                        "droppedInvalid": len(invalid),
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
                    "droppedInvalid": len(invalid),
                })
            return self._send(409, {
                "error": "项目正在被其他操作更新，请稍后重试连线保存",
                "code": "CONNECTION_REVISION_CONFLICT",
            })
        # Position-only save: dragging/layout code must never write stale metadata
        # (status/content/refs) back over another tab's newer generation state.
        m = re.match(r"^/api/projects/([^/]+)/viewport$", path)
        if m:
            pid = unquote(m.group(1))
            proj = store.get_project(pid)
            if not proj:
                return self._send(404, {"error": "项目不存在"})
            incoming = body.get("viewport")
            if not isinstance(incoming, dict):
                return self._send(400, {"error": "viewport 字段缺失或不是对象"})
            try:
                viewport = {
                    "x": float(incoming.get("x")),
                    "y": float(incoming.get("y")),
                    "zoom": float(incoming.get("zoom")),
                }
            except (TypeError, ValueError):
                return self._send(400, {"error": "viewport 的 x、y、zoom 必须是数字"})
            if (
                not all(math.isfinite(value) for value in viewport.values())
                or not 0.12 <= viewport["zoom"] <= 8
            ):
                return self._send(400, {"error": "viewport 缩放范围无效"})
            query = urllib.parse.parse_qs(
                urllib.parse.urlsplit(self.path).query
            )
            normalized_episode = str(
                body.get("episodeId")
                or (query.get("episodeId") or [""])[0]
                or ""
            ).strip().upper()
            try:
                saved = canvas_repository.CanvasRepository().save_viewport(
                    pid,
                    viewport,
                    episode_id=normalized_episode or None,
                    if_revision=proj["revision"],
                )
            except KeyError:
                return self._send(404, {"error": "项目不存在"})
            except ValueError as exc:
                return self._send(400, {"error": str(exc)})
            return self._send(200, {
                "ok": True,
                "viewport": saved.get("viewport") if isinstance(saved, dict) else saved,
                "revision": saved.get("revision") if isinstance(saved, dict) else None,
                "unchanged": bool(
                    saved.get("unchanged")
                    if isinstance(saved, dict)
                    else False
                ),
                "episodeId": normalized_episode,
            })
        # Position-only save: dragging/layout code must never write stale metadata
        # (status/content/refs) back over another tab's newer generation state.
        m = re.match(r"^/api/projects/([^/]+)/nodes/positions$", path)
        if m:
            pid = unquote(m.group(1))
            incoming = body.get("positions")
            if not isinstance(incoming, list):
                return self._send(400, {"error": "positions 字段缺失或不是数组"})
            write_owner = str(body.get("writeOwner") or "").strip()[:120]
            episode_id = str(body.get("episodeId") or "").upper()
            updates = []
            unchanged_ids = []
            missing_ids = []
            saved = None
            for attempt in range(4):
                updates = []
                unchanged_ids = []
                missing_ids = []
                proj = store.get_project(pid)
                if not proj:
                    return self._send(404, {"error": "项目不存在"})
                by_id = {
                    node.get("id"): node
                    for node in proj["payload"].get("nodes", [])
                }
                episode_layouts = proj["payload"].get("episodeLayouts")
                if not isinstance(episode_layouts, dict):
                    episode_layouts = {}
                    proj["payload"]["episodeLayouts"] = episode_layouts
                episode_layout = (
                    episode_layouts.setdefault(episode_id, {})
                    if episode_id
                    else None
                )
                if episode_id and not isinstance(episode_layout, dict):
                    episode_layout = {}
                    episode_layouts[episode_id] = episode_layout
                for item in incoming:
                    if not isinstance(item, dict) or not item.get("id"):
                        continue
                    position = item.get("position")
                    node = by_id.get(item["id"])
                    if node is None or not isinstance(position, dict):
                        if node is None:
                            missing_ids.append(item["id"])
                        continue
                    try:
                        position = {
                            "x": float(position.get("x") or 0),
                            "y": float(position.get("y") or 0),
                        }
                        write_seq = int(item.get("seq") or 0)
                    except (TypeError, ValueError):
                        continue
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
                        if isinstance(episode_layout, dict)
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
                if not updates:
                    break
                try:
                    if episode_id:
                        saved = store.update_project_layout(
                            pid,
                            episode_id,
                            episode_layout or {},
                            if_revision=proj["revision"],
                        )
                    else:
                        saved = store.update_project_rows(
                            pid,
                            nodes=proj["payload"].get("nodes") or [],
                            if_revision=proj["revision"],
                        )
                    if saved:
                        break
                except store.RevisionConflictError:
                    if attempt == 3:
                        return self._send(409, {
                            "error": "画布位置保存冲突，请重试",
                            "code": "POSITION_REVISION_CONFLICT",
                        })
                    continue
            if updates and not saved:
                return self._send(409, {
                    "error": "画布位置保存冲突，请重试",
                    "code": "POSITION_REVISION_CONFLICT",
                })
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
            incoming = body.get("nodes")
            if not isinstance(incoming, list):
                return self._send(400, {"error": "nodes 字段缺失或不是数组"})
            episode_id = str(body.get("episodeId") or "").strip().upper()
            if not episode_id:
                return self._send(400, {
                    "error": "新建节点必须指定所属集",
                    "code": "CANVAS_EPISODE_REQUIRED",
                })
            active_task_nodes = {
                task.get("node_id")
                for task in store.list_generation_tasks(pid, 300)
                if task.get("status") in {"queued", "submitting", "running"}
            }
            conflicts = []
            save_updates = []
            shared_asset_ids = set()
            claim_shared_ids = set()
            created = 0
            persisted_node_count = 0
            current_revision = 0
            existing_created_items: list[dict] = []
            existing_updated_items: list[dict] = []
            for attempt in range(4):
                proj = store.get_project(pid)
                if not proj:
                    return self._send(404, {"error": "项目不存在"})
                current_revision = int(proj["revision"])
                nodes_list = proj["payload"].setdefault("nodes", [])
                project_node_ids = {
                    str(node.get("id"))
                    for node in nodes_list
                    if isinstance(node, dict) and node.get("id")
                }
                by_id = {
                    node.get("id"): node
                    for node in nodes_list
                    if isinstance(node, dict) and node.get("id")
                }
                episode_node_ids = set(
                    store.get_episode_node_ids(pid, episode_id) or []
                )
                conflicts = []
                shared_asset_ids = set()
                claim_shared_ids = set()
                for item in incoming:
                    if not isinstance(item, dict) or not item.get("id"):
                        continue
                    current = by_id.get(item["id"])
                    if current and item["id"] not in episode_node_ids:
                        # Provisional/imported assets may still have no owner.
                        # Adopt those into the requesting episode; real shared
                        # assets keep their original owner while their editable
                        # content is updated as a project-level asset.
                        current_episode = str(
                            current.get("episodeId")
                            or (current.get("metadata") or {}).get("episodeId")
                            or ""
                        ).strip().upper()
                        if not current_episode:
                            claim_shared_ids.add(item["id"])
                        else:
                            shared_asset_ids.add(item["id"])
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
                created_items = []
                save_updates = []
                try:
                    for item in incoming:
                        if not isinstance(item, dict) or not item.get("id"):
                            continue
                        nid = item.get("id")
                        node = by_id.get(nid)
                        if node is not None and nid not in episode_node_ids:
                            if (
                                nid not in shared_asset_ids
                                and nid not in claim_shared_ids
                            ):
                                continue
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
                            if not any(
                                existing.get("id") == nid
                                for existing in created_items
                            ):
                                created_items.append(node)
                            continue
                        if nid in claim_shared_ids:
                            metadata = dict(item.get("metadata") or {})
                            metadata["episodeId"] = episode_id
                            item["metadata"] = metadata
                        if isinstance(item.get("metadata"), dict):
                            meta = node.setdefault("metadata", {})
                            merge_node_metadata(
                                meta,
                                item["metadata"],
                                generation_active=nid in active_task_nodes,
                                node_type=str(item.get("type") or node.get("type") or ""),
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
                            "node": node,
                        })
                except Exception:
                    raise
                if not created_items and not save_updates:
                    break
                try:
                    if created_items:
                        created_items = [
                            item
                            for item in created_items
                            if str(item.get("id")) not in by_id
                        ]
                    if save_updates:
                        save_updates = [
                            update
                            for update in save_updates
                            if str(update.get("id")) in project_node_ids
                        ]
                    if created_items:
                        saved = store.insert_project_nodes(
                            pid,
                            created_items,
                            episode_id=episode_id,
                            if_revision=current_revision,
                        )
                        created += int(saved.get("created") or 0)
                        current_revision = int(
                            saved.get("revision") or current_revision
                        )
                        persisted_node_count += int(saved.get("created") or 0)
                        existing_created_items.extend(saved.get("nodes") or [])
                        project_node_ids.update(
                            str(item.get("id"))
                            for item in (saved.get("nodes") or [])
                            if item.get("id")
                        )
                    if save_updates:
                        remaining = [
                            update["node"]
                            for update in save_updates
                        ]
                        shared_updates = [
                            update["node"]
                            for update in save_updates
                            if update["id"] in shared_asset_ids
                        ]
                        local_updates = [
                            update["node"]
                            for update in save_updates
                            if update["id"] not in shared_asset_ids
                        ]
                        if shared_updates:
                            shared_saved = store.update_project_nodes(
                                pid,
                                shared_updates,
                                episode_id=episode_id,
                                if_revision=current_revision,
                                preserve_ownership=True,
                            )
                            current_revision = int(
                                shared_saved.get("revision")
                                or current_revision
                            )
                        if local_updates:
                            local_saved = store.update_project_nodes(
                                pid,
                                local_updates,
                                episode_id=episode_id,
                                if_revision=current_revision,
                            )
                            current_revision = int(
                                local_saved.get("revision")
                                or current_revision
                            )
                        persisted_node_count += len(remaining)
                        existing_updated_items.extend(remaining)
                    break
                except store.RevisionConflictError:
                    if attempt == 3:
                        return self._send(409, {
                            "error": "项目正在被其他操作更新，请重新保存",
                            "code": "NODE_REVISION_CONFLICT",
                        })
            save_updates = [
                {
                    "id": update["id"],
                    "updatedAt": update["updatedAt"],
                    "positionUpdatedAt": update["positionUpdatedAt"],
                }
                for update in save_updates
            ]
            return self._send(200, {
                "ok": True,
                "nodes": persisted_node_count,
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
        node = store.get_projection_node(pid, nid)
        if not node:
            # upsert：前端新建的卡片 id 形如 m-<timestamp>，载荷里本就不存在，
            # 直接 404 会让"＋添加"添加的一切节点永远存不进去 —— 表现为"加了卡片，刷新就没了"。
            # 这里按 body 建新节点后再走同一套合并逻辑。
            episode_id = str(body.get("episodeId") or "").strip().upper()
            if not episode_id:
                return self._send(400, {
                    "error": "新建节点必须指定所属集",
                    "code": "CANVAS_EPISODE_REQUIRED",
                })
            node = {
                "id": nid,
                "type": body.get("type") if body.get("type") in ("text", "image", "audio", "video") else "text",
                "title": body.get("title") or "未命名节点",
                "position": body.get("position") if isinstance(body.get("position"), dict) else {"x": 0, "y": 0},
                "metadata": {
                    **dict(body.get("metadata") or {}),
                    "episodeId": episode_id,
                },
                "createdAt": store._now_iso(),
            }
            node["episodeId"] = episode_id
            node["updatedAt"] = store._now_iso()
            saved = store.insert_project_nodes(
                pid,
                [node],
                episode_id=episode_id,
                if_revision=proj["revision"],
            )
            if not saved or not saved.get("revision"):
                return self._send(409, {
                    "error": "项目正在被其他操作更新，请重新保存",
                    "code": "NODE_REVISION_CONFLICT",
                })
            return self._send(200, {
                "ok": True,
                "nodeId": nid,
                "created": bool(saved.get("created")),
                "updatedAt": saved["nodes"][0].get("updatedAt") if saved.get("nodes") else "",
                "positionUpdatedAt": (
                    saved["nodes"][0].get("positionUpdatedAt")
                    if saved.get("nodes")
                    else ""
                ),
            })
        requested_episode = str(body.get("episodeId") or "").strip().upper()
        node_episode = str(
            node.get("episodeId")
            or (node.get("metadata") or {}).get("episodeId")
            or ""
        ).strip().upper()
        active_task_nodes = {
            task.get("node_id")
            for task in store.list_generation_tasks(pid, 300)
            if task.get("status") in {"queued", "submitting", "running"}
        }

        def merge_metadata(target, incoming):
            merge_node_metadata(
                target,
                incoming,
                generation_active=nid in active_task_nodes,
                node_type=str(node.get("type") or ""),
            )

        try:
            update_result = store.patch_project_node_atomic(
                pid,
                node_episode or requested_episode,
                nid,
                {
                    "_baseUpdatedAt": body.get("baseUpdatedAt"),
                    "metadata": (
                        body.get("metadata")
                        if isinstance(body.get("metadata"), dict)
                        else {}
                    ),
                    **(
                        {"title": body["title"]}
                        if "title" in body
                        else {}
                    ),
                    "position": (
                        body.get("position")
                        if isinstance(body.get("position"), dict)
                        else None
                    ),
                },
                merge_metadata=merge_metadata,
            )
        except ValueError as exc:
            return self._send(409, {
                "error": str(exc),
                "code": "NODE_EPISODE_MISMATCH",
            })
        except store.RevisionConflictError:
            return self._send(409, {
                "error": "另一个标签页已更新此节点，已拒绝覆盖。请刷新后再编辑。",
                "code": "NODE_EDIT_CONFLICT",
                "nodeId": nid,
            })
        if not update_result:
            return self._send(409, {
                "error": "项目正在被其他操作更新，请重新保存",
                "code": "NODE_REVISION_CONFLICT",
            })
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
        m = re.match(r"^/api/projects/([^/]+)/shares/([^/]+)$", path)
        if m:
            current = self._current_session()
            if not current:
                return self._send(401, {"error": "请先登录"})
            pid, share_id = unquote(m.group(1)), unquote(m.group(2))
            revoked = store.revoke_project_share(pid, share_id)
            if not revoked:
                return self._send(404, {"error": "分享记录不存在或已撤销"})
            store.record_audit_event(
                "project.share_revoke",
                actor=str(current.get("account_username") or ""),
                target_type="project",
                target_id=pid,
                details={"shareId": share_id},
            )
            return self._send(200, {"ok": True, "shareId": share_id})
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
            node_ids = store.get_episode_node_ids(pid, target["id"])
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
            active_task = store.get_active_generation_task_for_node(pid, nid)
            if active_task:
                return self._send(409, {
                    "error": "节点仍有生成任务在运行，请完成或取消后再删除",
                    "code": "NODE_GENERATION_ACTIVE",
                    "taskId": active_task.get("id") or "",
                })
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
            active_tasks = store.list_generation_tasks_that_block_project_delete(
                deleted_id,
            )
            if active_tasks:
                return self._send(409, {
                    "error": "项目仍有生成任务在运行，请完成或取消后再删除",
                    "code": "PROJECT_GENERATION_ACTIVE",
                    "activeTasks": len(active_tasks),
                })
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
                        task_id, phase="不支持的类型",
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

    def _generate(self, pid, proj, node, task_id=None, lease_owner=""):
        key = (str(pid), str(node.get("id") or ""))
        # The SQLite partial unique index already guarantees one active task
        # per node. Do not layer a process-local boolean lock on top: a worker
        # can otherwise reject its own freshly persisted task as a duplicate.
        if task_id:
            current_task = store.get_generation_task(task_id) or {}
            current_owner = str(current_task.get("lease_owner") or "").strip()
            current_status = str(current_task.get("status") or "").strip()
            if not current_task:
                current_task = {
                    "id": task_id,
                    "status": "queued",
                    "lease_owner": "",
                }
            if lease_owner and current_owner and current_owner != lease_owner:
                latest_task = store.get_generation_task(task_id) or {}
                return self._send(409, {
                    "error": "生成任务租约已被其他执行线程持有，本次未提交供应商",
                    "code": "GENERATION_LEASE_LOST",
                    "taskId": task_id,
                })
            if not lease_owner and not current_owner:
                lease_owner = f"generation:{secrets.token_hex(12)}"
                if current_task.get("id") == task_id and current_status not in {"queued", "submitting"}:
                    lease_owner = ""
                elif not store.claim_generation_task(
                    task_id,
                    lease_owner,
                    TASK_LEASE_SECONDS,
                ):
                    if not store.get_generation_task(task_id):
                        current_task = {
                            "id": task_id,
                            "status": "running",
                            "lease_owner": lease_owner,
                        }
                    else:
                        latest_task = store.get_generation_task(task_id) or {}
                        latest_owner = str(
                            latest_task.get("lease_owner") or ""
                        ).strip()
                        if latest_owner and latest_owner != lease_owner:
                            return self._send(409, {
                                "error": "生成任务租约已被其他执行线程持有，本次未提交供应商",
                                "code": "GENERATION_LEASE_LOST",
                                "taskId": task_id,
                            })
                        lease_owner = latest_owner
            if (
                str(current_task.get("status") or "") not in {"queued", "submitting", "running"}
                or (
                    lease_owner
                    and str(current_task.get("lease_owner") or "") not in {"", str(lease_owner)}
                )
                or (not lease_owner and current_owner)
            ):
                latest_task = store.get_generation_task(task_id) or {}
                if latest_task.get("status") == "succeeded":
                    return self._send(200, {
                        "taskId": task_id,
                        "nodeId": node["id"],
                        "status": "done",
                        "replayed": True,
                    })
                if latest_task.get("status") in {"failed", "interrupted"}:
                    return self._send(202, {
                        "taskId": task_id,
                        "nodeId": node["id"],
                        "status": "pending",
                        "code": "PROVIDER_OUTCOME_UNCERTAIN"
                        if latest_task.get("provider_task_id")
                        else "GENERATION_TASK_TERMINAL",
                        "error": str(latest_task.get("error") or "原任务已结束"),
                        "replayed": True,
                    })
                return self._send(409, {
                    "error": "生成任务执行租约已失效，本次未提交供应商",
                    "code": "GENERATION_LEASE_LOST",
                    "taskId": task_id,
                })
            store.update_generation_task(
                task_id,
                phase="正在连接供应商",
                progress=0,
                error="",
                finished_at="",
                **({"_lease_owner": lease_owner} if lease_owner else {}),
            )
        if task_id:
            # Resolve only after the lease gate. A stale worker must receive
            # GENERATION_LEASE_LOST without touching model secrets/configuration.
            metadata = node.get("metadata") or {}
            params = metadata.get("params") or {}
            provider_catalog.resolve_model(
                str(params.get("provider") or metadata.get("provider") or "").strip()
                or None,
                str(params.get("model") or metadata.get("model") or "").strip()
                or None,
                str(node.get("type") or ""),
                require_api_key=False,
            )
        try:
            return self._generate_once(
                pid,
                proj,
                node,
                task_id=task_id,
                lease_owner=lease_owner,
            )
        except Exception as exc:
            if preserve_generation_outcome(task_id, exc):
                print(
                    f"[generation] client disconnected after result was persisted; "
                    f"task={task_id or '-'} error={exc}",
                    file=sys.stderr,
                )
                return None
            if isinstance(exc, provider_client.ProviderTaskFailed):
                provider_task_id = str(
                    getattr(exc, "provider_task_id", "")
                    or _submitted_provider_task_id(task_id)
                    or ""
                )
                if task_id:
                    store.finish_generation_task(
                        task_id,
                        status="failed",
                        phase="生成失败",
                        error=str(exc),
                        provider_task_id=provider_task_id,
                        delivery_status="failed",
                        lease_owner=lease_owner,
                    )
                commit_node_metadata(pid, node["id"], {
                    "status": "failed",
                    "lastError": str(exc),
                    "providerTaskStatus": "failed",
                    "providerTaskPhase": "生成失败",
                    "generationFinishedAt": store._now_iso(),
                })
                publish_project_event(pid, "media", node["id"], {
                    "status": "failed",
                    "error": str(exc),
                    "providerTaskStatus": "failed",
                    "providerTaskPhase": "生成失败",
                })
                return self._send(
                    _generation_error_status(exc),
                    {
                        "error": str(exc),
                        "code": "GENERATION_PROVIDER_FAILED",
                        "taskId": task_id or "",
                        "nodeId": node["id"],
                    },
                )
            if isinstance(exc, provider_client.ProviderOutcomeUncertain):
                submitted_at = str(getattr(exc, "submitted_at", "") or "")
                provider_response = getattr(exc, "response", {}) or {}
                provider_task_id = str(
                    provider_response.get("id")
                    or provider_response.get("task_id")
                    or provider_response.get("taskId")
                    or _submitted_provider_task_id(task_id)
                    or ""
                ).strip()
                if task_id:
                    store.finish_generation_task(
                        task_id,
                        status="interrupted",
                        phase="结果待确认",
                        error=str(exc),
                        provider_task_id=provider_task_id,
                        delivery_status="pending",
                        lease_owner=lease_owner,
                    )
                _mark_generation_outcome_uncertain(
                    task_id, pid, node["id"], exc, provider_task_id
                )
                if submitted_at:
                    commit_node_metadata(pid, node["id"], {
                        "providerSubmittedAt": submitted_at,
                        "providerTaskId": provider_task_id,
                    })
                return self._send(202, {
                    "accepted": True,
                    "taskId": task_id or "",
                    "nodeId": node["id"],
                    "status": "pending",
                    "code": "PROVIDER_OUTCOME_UNCERTAIN",
                    "error": str(exc),
                    "providerSubmittedAt": submitted_at,
                })
            submitted_provider_task_id = _submitted_provider_task_id(task_id)
            if (
                submitted_provider_task_id
            ):
                if isinstance(exc, provider_client.ProviderTaskFailed):
                    provider_task_id = str(
                        getattr(exc, "provider_task_id", "")
                        or submitted_provider_task_id
                    )
                    if task_id:
                        store.finish_generation_task(
                            task_id,
                            status="failed",
                            phase="生成失败",
                            error=str(exc),
                            provider_task_id=provider_task_id,
                            delivery_status="failed",
                            lease_owner=lease_owner,
                        )
                    commit_node_metadata(pid, node["id"], {
                        "status": "failed",
                        "lastError": str(exc),
                        "providerTaskStatus": "failed",
                        "providerTaskPhase": "生成失败",
                        "generationFinishedAt": store._now_iso(),
                    })
                    publish_project_event(pid, "media", node["id"], {
                        "status": "failed",
                        "error": str(exc),
                        "providerTaskStatus": "failed",
                        "providerTaskPhase": "生成失败",
                    })
                    return self._send(
                        _generation_error_status(exc),
                        {
                            "error": str(exc),
                            "code": "GENERATION_PROVIDER_FAILED",
                            "taskId": task_id or "",
                            "nodeId": node["id"],
                        },
                    )
                _mark_generation_outcome_uncertain(
                    task_id, pid, node["id"], exc, submitted_provider_task_id
                )
                return self._send(202, {
                    "accepted": True,
                    "taskId": task_id or "",
                    "nodeId": node["id"],
                    "status": "pending",
                    "code": "PROVIDER_OUTCOME_UNCERTAIN",
                    "error": str(exc),
                })
            if isinstance(exc, provider_client.ProviderTaskFailed):
                provider_task_id = str(
                    getattr(exc, "provider_task_id", "")
                    or _submitted_provider_task_id(task_id)
                    or ""
                )
                if task_id:
                    store.finish_generation_task(
                        task_id,
                        status="failed",
                        phase="生成失败",
                        error=str(exc),
                        provider_task_id=provider_task_id,
                        delivery_status="failed",
                        lease_owner=lease_owner,
                    )
                commit_node_metadata(pid, node["id"], {
                    "status": "failed",
                    "lastError": str(exc),
                    "providerTaskStatus": "failed",
                    "providerTaskPhase": "生成失败",
                    "generationFinishedAt": store._now_iso(),
                })
                publish_project_event(pid, "media", node["id"], {
                    "status": "failed",
                    "error": str(exc),
                    "providerTaskStatus": "failed",
                    "providerTaskPhase": "生成失败",
                })
                return self._send(
                    _generation_error_status(exc),
                    {
                        "error": str(exc),
                        "code": "GENERATION_PROVIDER_FAILED",
                        "taskId": task_id or "",
                        "nodeId": node["id"],
                    },
                )
            if task_id:
                store.finish_generation_task(
                    task_id,
                    status="interrupted",
                    phase="结果待确认",
                    error=(
                        "供应商请求可能已经发出，但本地未取得确认回执；"
                        "系统不会自动重提，请先查询原任务或由用户确认后重新生成。"
                    ),
                    delivery_status="pending",
                    lease_owner=lease_owner,
                )
                commit_node_metadata(pid, node["id"], {
                    "status": "error",
                    "lastError": (
                        "供应商请求可能已经发出，但本地未取得确认回执；"
                        "系统不会自动重提。"
                    ),
                    "providerTaskStatus": "uncertain",
                    "providerTaskPhase": "结果待确认",
                    "generationFinishedAt": "",
                })
            raise
        finally:
            pass

    def _generate_once(self, pid, proj, node, task_id=None, lease_owner=""):
        ntype = str(node.get("type") or "")
        meta = node.get("metadata", {}) or {}
        params = meta.get("params") or {}
        generic_labels = {"", "资产图", "角色音色", "图片", "音频", "视频", "文本", "素材"}
        provider = None
        model = None
        deferred_image_archive_args = None
        deferred_video_finalize_args = None

        def local_reference_issue(url, label):
            raw_url = str(url or "").strip()
            if not raw_url.startswith("/uploads/"):
                return ""
            local_name = urllib.parse.unquote(raw_url[len("/uploads/"):])
            local_path = os.path.join(_upload_dir(), local_name)
            if (
                not local_name
                or "/" in local_name
                or "\\" in local_name
                or not os.path.isfile(local_path)
            ):
                return "参考媒体文件已丢失，请重新生成或导入本地文件"
            return ""

        def resolve_refs(raw_refs):
            resolved = []
            missing = []
            seen_refs = set()
            nodes = [
                item for item in proj["payload"].get("nodes", [])
                if isinstance(item, dict)
            ]
            by_node_id = {
                str(item.get("id") or ""): item
                for item in nodes
                if item.get("id")
            }
            by_asset_id = {}
            by_series_variant = {}
            for item in nodes:
                source_meta = item.get("metadata") or {}
                source_asset_id = str(source_meta.get("assetId") or "").strip()
                if source_asset_id:
                    by_asset_id.setdefault(source_asset_id, []).append(item)
                source_series_id = str(source_meta.get("seriesId") or "").strip()
                if source_series_id:
                    variant = str(source_meta.get("variantKey") or "default").strip() or "default"
                    by_series_variant.setdefault(
                        f"{source_series_id}|{variant}",
                        [],
                    ).append(item)

            for raw in raw_refs or []:
                if not isinstance(raw, dict):
                    continue
                requested_node_id = str(raw.get("nodeId") or "").strip()
                requested_label = str(raw.get("label") or "").strip()
                requested_asset_id = str(raw.get("assetId") or "").strip()

                # The frontend may only have a canonical @ label (for example
                # `角色｜贾富贵（图3）`). Resolve that label to the server-side
                # node/asset identity before matching the asset ID. Browser
                # ordering never determines identity.
                if not requested_node_id and requested_label:
                    matching_nodes = [
                        item for item in nodes
                        if str(item.get("title") or "").strip() == requested_label
                    ]
                    if len(matching_nodes) == 1:
                        requested_node_id = str(
                            matching_nodes[0].get("id") or ""
                        )
                    elif len(matching_nodes) > 1:
                        missing.append({
                            "nodeId": "",
                            "label": requested_label,
                            "reason": "提示词 @ 名称对应多个资产，无法确定唯一资产 ID",
                        })
                        continue

                ref_id = requested_node_id
                requested_series_id = str(raw.get("seriesId") or "").strip()
                requested_variant = (
                    str(raw.get("variantKey") or "default").strip() or "default"
                )
                identity_key = (
                    requested_asset_id
                    or (
                        f"{requested_series_id}|{requested_variant}"
                        if requested_series_id else ""
                    )
                )
                dedupe_key = identity_key or ref_id
                if dedupe_key and dedupe_key in seen_refs:
                    continue
                if dedupe_key:
                    seen_refs.add(dedupe_key)

                source = by_node_id.get(ref_id)
                if identity_key:
                    identity_candidates = (
                        by_asset_id.get(requested_asset_id)
                        if requested_asset_id
                        else by_series_variant.get(identity_key)
                    ) or []
                    if len(identity_candidates) > 1:
                        identity_candidates = sorted(
                            identity_candidates,
                            key=lambda item: (
                                str((item.get("metadata") or {}).get("status") or "") == "done",
                                bool(str((item.get("metadata") or {}).get("content") or "").strip()),
                                str(item.get("updatedAt") or ""),
                            ),
                            reverse=True,
                        )
                    identity_source = identity_candidates[0] if identity_candidates else None
                    if source and identity_candidates:
                        source_meta = source.get("metadata") or {}
                        source_asset_id = str(source_meta.get("assetId") or "").strip()
                        source_series_id = str(source_meta.get("seriesId") or "").strip()
                        source_variant = (
                            str(source_meta.get("variantKey") or "default").strip()
                            or "default"
                        )
                        source_identity = (
                            source_asset_id
                            or (
                                f"{source_series_id}|{source_variant}"
                                if source_series_id else ""
                            )
                        )
                        if source_identity and source_identity != identity_key:
                            missing.append({
                                "nodeId": ref_id,
                                "assetId": requested_asset_id,
                                "label": str(
                                    raw.get("label")
                                    or (source or {}).get("title")
                                    or ref_id
                                ),
                                "reason": (
                                    "引用节点与资产唯一 ID 不一致"
                                    f"（请求 {identity_key}，节点 {source_identity}）"
                                ),
                            })
                            continue
                    if identity_source and (
                        not source
                        or source.get("id") != identity_source.get("id")
                    ):
                        source = identity_source
                        ref_id = str(identity_source.get("id") or ref_id)
                title = str((source or {}).get("title") or "").strip()
                claimed = str(raw.get("label") or "").strip()
                label = title or (claimed if claimed not in generic_labels else "") or ref_id or "未知引用"
                if not source:
                    missing.append({"nodeId": ref_id, "label": label, "reason": "引用的节点已不存在"})
                    continue
                source_meta = source.get("metadata", {}) or {}
                source_type = str(source.get("type") or "")
                source_asset_id = str(source_meta.get("assetId") or "").strip()
                source_series_id = str(source_meta.get("seriesId") or "").strip()
                source_variant_key = str(
                    source_meta.get("variantKey") or "default"
                ).strip()
                if (
                    source_asset_id
                    and requested_asset_id
                    and source_asset_id != requested_asset_id
                ):
                    missing.append({
                        "nodeId": ref_id,
                        "assetId": requested_asset_id,
                        "label": label,
                        "reason": (
                            f"资产唯一 ID 不一致"
                            f"（请求 {requested_asset_id}，数据库 {source_asset_id}）"
                        ),
                    })
                    continue
                resolved_ref = {
                    "nodeId": ref_id,
                    "label": label,
                    **(
                        {
                            "assetId": source_asset_id,
                            "seriesId": source_series_id,
                            "variantKey": source_variant_key,
                        }
                        if source_asset_id else {}
                    ),
                }
                if source_type == "text":
                    text = str(source_meta.get("prompt") or source_meta.get("content") or "").strip()
                    if not text:
                        missing.append({"nodeId": ref_id, "label": label, "reason": "引用文本为空"})
                        continue
                    resolved.append({
                        **resolved_ref,
                        "kind": "text",
                        "text": text,
                    })
                    continue
                # The source node is authoritative. params.refs[].url is only a
                # snapshot from the moment the reference was created and can be
                # stale after the asset was regenerated or replaced.
                url = _active_reference_url(source_meta, raw)
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
                local_issue = local_reference_issue(url, label)
                if local_issue:
                    missing.append({
                        "nodeId": ref_id,
                        "label": label,
                        "reason": local_issue,
                    })
                    continue
                kind = "audio" if source_type == "audio" else "video" if source_type == "video" else "image"
                active_version = dict(_active_media_version(source_meta))
                source_model = str(
                    active_version.get("model")
                    or source_meta.get("model")
                    or source_meta.get("remoteModel")
                    or ""
                ).strip()
                source_provider = str(
                    active_version.get("provider")
                    or source_meta.get("provider")
                    or ""
                ).strip()
                version_storage = active_version.get("storage")
                storage = (
                    version_storage
                    if isinstance(version_storage, dict)
                    else source_meta.get("storage")
                )
                provider_result_url = str(
                    active_version.get("providerResultUrl")
                    or source_meta.get("providerResultUrl")
                    or source_meta.get("providerUrl")
                    or ""
                ).strip()
                if (
                    kind == "image"
                    and not provider_result_url
                ):
                    provider_result_url = _latest_provider_result_url(pid, ref_id)
                    if provider_result_url:
                        source_meta["providerResultUrl"] = provider_result_url
                        try:
                            commit_node_metadata(pid, ref_id, {
                                "providerResultUrl": provider_result_url,
                            })
                        except Exception:
                            pass
                    active_version["providerResultUrl"] = provider_result_url
                resolved.append({
                    **resolved_ref,
                    "kind": kind,
                    "url": url,
                    "prompt": str(source_meta.get("prompt") or ""),
                    "storage": storage if isinstance(storage, dict) else None,
                    "sourceModel": source_model,
                    "sourceProvider": source_provider,
                    "providerResultUrl": provider_result_url,
                    "generatedAt": str(
                        active_version.get("generatedAt")
                        or source_meta.get("generatedAt")
                        or ""
                    ),
                    "taskId": str(
                        active_version.get("taskId")
                        or source_meta.get("taskId")
                        or ""
                    ),
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
            prompt = str(meta.get("prompt") or "")
            explicit = explicit_references.resolve_explicit_references(
                prompt=prompt,
                nodes=[
                    item for item in proj["payload"].get("nodes", [])
                    if isinstance(item, dict)
                ],
                candidate_refs=(
                    params.get("refs")
                    if isinstance(params.get("refs"), list)
                    else meta.get("refs")
                    if isinstance(meta.get("refs"), list)
                    else []
                ),
                candidate_voices=[
                    *(
                        params.get("voiceRefs")
                        if isinstance(params.get("voiceRefs"), list)
                        else []
                    ),
                    *(
                        [params.get("voiceRef")]
                        if isinstance(params.get("voiceRef"), dict)
                        else []
                    ),
                ],
            )
            has_asset_at_syntax = bool(re.search(
                r"@(?:角色|群像|场景|道具|音频)｜",
                prompt,
            ))
            if (
                ntype == "video"
                and has_asset_at_syntax
                and not explicit.has_explicit_mention
            ):
                raise RuntimeError(
                    "视频提示词包含 @ 资产引用，但没有解析出完整资产身份。"
                    "请从素材菜单重新选择资产后再提交。"
                )
            if ntype == "video" and explicit.issues:
                raise RuntimeError(explicit.issues[0].message)
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
            effective = {**(model.get("defaultParams") or {}), **params}
            mention_identities = (
                explicit.reference_identity_map
                if explicit.has_explicit_mention
                else {}
            )
            effective["_mentionIdentities"] = mention_identities
            if task_id:
                store.update_generation_task(
                    task_id,
                    provider=provider.get("id", ""),
                    model=model.get("id", ""),
                    phase="提交供应商",
                    progress=15,
                    error="", finished_at="",
                )
            ensure_provider_auth(provider)
            for key in ("ratio", "resolution", "duration", "guidance", "size", "system", "temperature", "maxTokens"):
                if effective.get(key) in (None, "") and meta.get(key) not in (None, ""):
                    effective[key] = meta.get(key)
            max_prompt = int((model.get("limits") or {}).get("maxPromptChars") or 40000)
            if len(prompt) > max_prompt:
                raise RuntimeError(f"提示词超过当前模型上限（{max_prompt} 字符）")
            if not prompt.strip():
                raise RuntimeError("提示词为空，不能提交生成")
            connections = (
                proj["payload"].get("connections")
                if isinstance(proj["payload"].get("connections"), list)
                else []
            )
            candidate_refs, _explicit_reference_list = (
                request_reference_candidates(
                    params=params,
                    meta=meta,
                    node_type=ntype,
                    node_id=str(node.get("id") or ""),
                    connections=connections,
                    nodes=[
                        item
                        for item in proj["payload"].get("nodes", [])
                        if isinstance(item, dict)
                    ],
                )
            )
            # An explicit list, including an empty one, is the authoritative
            # slot set for this request. Legacy nodes without either field can
            # still derive slots from links. Explicit @ mentions may add a
            # declared asset, but links must never resurrect a user-removed one.
            raw_refs = candidate_refs
            if ntype == "video":
                seen_ref_ids = {
                    str(ref.get("nodeId") or "")
                    for ref in raw_refs
                    if isinstance(ref, dict)
                }
                for ref in explicit.refs:
                    node_id = str(ref.get("nodeId") or "")
                    if node_id and node_id not in seen_ref_ids:
                        raw_refs.append(ref)
                        seen_ref_ids.add(node_id)
            refs, missing = resolve_refs(raw_refs)
            result = {}
            if ntype == "image":
                refs = [item for item in refs if item.get("kind") in ("image", "text")]
                if missing:
                    raise RuntimeError(missing_note(missing))
                validate_refs(refs)
                generation_started_at = store._now_iso()
                generation_task_id = task_id

                def on_image_task_created(task):
                    accepted = generation_acceptance.accept_provider_task(
                        task,
                        task_id=generation_task_id,
                        project_id=pid,
                        node_id=node["id"],
                        provider=provider.get("id"),
                        model=model.get("id"),
                        request={
                            "prompt": prompt,
                            "params": effective,
                            "refs": refs,
                            "voices": voices,
                            "nodeAssetId": str(meta.get("assetId") or ""),
                            "episodeId": str(meta.get("episodeId") or ""),
                        },
                        next_poll_at=store._now_iso(),
                        lease_owner=lease_owner,
                        remember=store.remember_provider_task,
                        update_task=store.update_generation_task,
                    )
                    provider_task_id = accepted.provider_task_id
                    meta.update({
                        "taskId": provider_task_id,
                        "providerTaskStatus": "queued",
                        "providerTaskPhase": "排队中",
                        "providerTaskProgress": 0,
                        "generationStartedAt": generation_started_at,
                    })
                    commit_node_metadata(pid, node["id"], {
                        "taskId": provider_task_id,
                        "providerTaskStatus": "queued",
                        "providerTaskPhase": "排队中",
                        "providerTaskProgress": 0,
                        "generationStartedAt": generation_started_at,
                    })

                # New API image tasks are asynchronous and can take longer than
                # a browser request. Persist the provider task id, release this
                # worker, and let the single reconciler own polling/finalization.
                # This prevents two workers from polling and racing the same
                # terminal receipt.
                if str(model.get("adapter") or "") == "newapi_image":
                    submitted = provider_client.submit_image_task(
                        provider,
                        model,
                        prompt,
                        effective,
                        refs,
                        on_task_created=on_image_task_created,
                    )
                    provider_task_id = str(submitted.get("taskId") or "").strip()
                    if not provider_task_id:
                        raise RuntimeError("New API 图片任务未返回任务号")
                    if task_id:
                        store.finish_generation_task(
                            task_id,
                            status="interrupted",
                            phase="供应商任务已提交",
                            progress=20,
                            error="",
                            provider_task_id=provider_task_id,
                            delivery_status="pending",
                            lease_owner=lease_owner,
                        )
                        store.schedule_provider_task_poll(
                            task_id,
                            store._now_iso(),
                        )
                    _start_image_resumer(
                        pid,
                        node["id"],
                        provider_task_id,
                        provider,
                        model,
                    )
                    return self._send(202, {
                        "accepted": True,
                        "taskId": task_id or "",
                        "nodeId": node["id"],
                        "status": "pending",
                        "code": "PROVIDER_TASK_ACCEPTED",
                        "providerTaskId": provider_task_id,
                        "message": "供应商已受理，后台会查询并回写画布",
                    })
                result = provider_client.generate_image(
                    provider,
                    model,
                    prompt,
                    effective,
                    refs,
                    on_task_created=on_image_task_created,
                )
                provider_ready_at = store._now_iso()
                provider_url = str(result.get("url") or "")
                if not provider_url:
                    raise RuntimeError("供应商未返回图片地址，本次生成没有产生新图片")
                if task_id:
                    # The paid result is already available. Persist its URL
                    # before downloading so a local transfer failure can be
                    # recovered from the same result, never by re-submitting.
                    store.update_generation_task(
                        task_id,
                        _lease_owner=lease_owner,
                        provider_result_url=provider_url,
                        provider_finished_at=provider_ready_at,
                        delivery_status="materializing",
                        result_ready_at=provider_ready_at,
                    )
                try:
                    local_generated_url = persist_generated_media(
                        provider_url,
                        ".png",
                        provider=provider,
                        model=model,
                    )
                except Exception as exc:
                    raise provider_client.ProviderOutcomeUncertain(
                        f"供应商已生成图片，但下载新图失败：{exc}"
                    ) from exc
                if not local_generated_url.startswith("/uploads/"):
                    raise provider_client.ProviderOutcomeUncertain(
                        "供应商已生成图片，但没有拿到本地新文件"
                    )
                if task_id:
                    store.update_generation_task(
                        task_id,
                        _lease_owner=lease_owner,
                        provider_result_url=provider_url,
                        local_result_url=local_generated_url,
                        provider_finished_at=provider_ready_at,
                        delivery_status="materializing",
                        result_ready_at=provider_ready_at,
                    )
                generated_at = provider_ready_at
                generated_url = local_generated_url
                _record_media_version(
                    meta,
                    "image",
                    generated_url,
                    str(result.get("taskId") or ""),
                    generated_at,
                    provider_result_url=provider_url,
                )
                meta["content"] = generated_url
                meta["status"] = "done"
                meta["generatedAt"] = generated_at
                meta["providerResultUrl"] = provider_url
                meta["finalizePending"] = False
                meta["storage"] = {}
                meta["storageError"] = ""
                meta["lastNote"] = missing_note(missing)
                meta["promptStale"] = False
                meta["promptStaleReason"] = ""
                meta.pop("sourcePromptUpdatedAt", None)
                if local_generated_url.startswith("/uploads/"):
                    deferred_image_archive_args = (
                        pid,
                        node["id"],
                        local_generated_url,
                        provider,
                        model,
                        str(result.get("taskId") or ""),
                        str(generated_at or ""),
                    )
            elif ntype == "video":
                # Explicit @ mentions are authoritative. Legacy videos without
                # any @ mention retain their already-persisted refs so old
                # projects continue to validate and generate.
                voice_refs = (
                    explicit.voices
                    if explicit.has_explicit_mention
                    else (
                        params.get("voiceRefs")
                        if isinstance(params.get("voiceRefs"), list)
                        else meta.get("voiceRefs")
                        if isinstance(meta.get("voiceRefs"), list)
                        else []
                    )
                )
                if not explicit.has_explicit_mention and not voice_refs:
                    single = params.get("voiceRef") or meta.get("voiceRef")
                    voice_refs = [single] if isinstance(single, dict) else []
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
                        missing.append({
                            "nodeId": voice_ref.get("nodeId"),
                            "label": voice_ref.get("label") or "角色音色",
                            "reason": "音色节点已不存在",
                        })
                        continue
                    voice_meta = voice_node.get("metadata", {}) or {}
                    voice_url = str(voice_meta.get("content") or "").strip()
                    voice_label = str(voice_node.get("title") or voice_ref.get("label") or "角色音色").strip()
                    voice_asset_id = str(voice_meta.get("assetId") or "").strip()
                    requested_voice_asset_id = str(
                        voice_ref.get("assetId") or ""
                    ).strip()
                    if (
                        voice_asset_id
                        and requested_voice_asset_id
                        and voice_asset_id != requested_voice_asset_id
                    ):
                        missing.append({
                            "nodeId": voice_ref.get("nodeId"),
                            "assetId": requested_voice_asset_id,
                            "label": voice_label,
                            "reason": (
                                "音色资产唯一 ID 不一致"
                                f"（请求 {requested_voice_asset_id}，"
                                f"数据库 {voice_asset_id}）"
                            ),
                        })
                        continue
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
                    local_issue = local_reference_issue(voice_url, voice_label)
                    if local_issue:
                        missing.append({
                            "nodeId": voice_ref.get("nodeId"),
                            "label": voice_label,
                            "reason": local_issue,
                        })
                        continue
                    voices.append({
                        "kind": "audio",
                        "prompt": str(voice_meta.get("prompt") or ""),
                        "url": voice_url,
                        "label": voice_label,
                        **(
                            {
                                "nodeId": voice_node.get("id"),
                                "assetId": voice_asset_id,
                                "seriesId": str(
                                    voice_meta.get("seriesId") or ""
                                ).strip(),
                                "variantKey": str(
                                    voice_meta.get("variantKey") or "default"
                                ).strip(),
                            }
                            if voice_asset_id else {}
                        ),
                    })
                refs = [item for item in refs if item.get("kind") in ("image", "video", "text")]
                if missing:
                    raise RuntimeError(missing_note(missing))
                validate_refs(refs, voices)
                generation_started_at = store._now_iso()
                generation_task_id = task_id

                def on_task_created(task):
                    reference_plan = {
                        "providerPromptSha256": str(
                            task.get("providerPromptSha256") or ""
                        ),
                        "referenceMapping": task.get(
                            "referenceMapping"
                        ) or [],
                        "voiceMapping": task.get("voiceMapping") or [],
                    }
                    meta["referencePlan"] = reference_plan
                    commit_node_metadata(pid, node["id"], {
                        "referencePlan": reference_plan,
                    })
                    accepted = generation_acceptance.accept_provider_task(
                        task,
                        task_id=generation_task_id,
                        project_id=pid,
                        node_id=node["id"],
                        provider=provider.get("id"),
                        model=model.get("id"),
                        request={
                            "prompt": prompt,
                            "params": effective,
                            "refs": refs,
                            "providerPromptSha256": str(
                                reference_plan["providerPromptSha256"]
                            ),
                            "referenceMapping": reference_plan[
                                "referenceMapping"
                            ],
                            "voiceMapping": reference_plan["voiceMapping"],
                            "nodeAssetId": str(meta.get("assetId") or ""),
                            "episodeId": str(meta.get("episodeId") or ""),
                        },
                        lease_owner=lease_owner,
                        remember=store.remember_provider_task,
                        update_task=store.update_generation_task,
                    )
                    provider_task_id = accepted.provider_task_id
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

                # 轮询已经加密到 1.5s，若每次都重写整份 payload，一个 5 分钟的视频
                # 会多出上百次全量写。进度文字晚 3 秒无所谓，状态机不能因此抖动。
                progress_write = {"at": 0.0, "phase": "", "progress": None}

                def on_task_update(task):
                    raw_status = str(task.get("status") or "running")
                    progress = task.get("progress")
                    phase = str(task.get("phase") or "").strip() or _provider_task_phase(raw_status, progress)
                    _with_progress_changed_at(meta, raw_status, progress)
                    meta.update({
                        "providerTaskStatus": raw_status,
                        "providerTaskPhase": phase,
                        "providerTaskProgress": progress,
                        "providerTaskProgressChangedAt": meta.get("providerTaskProgressChangedAt"),
                    })
                    if task_id:
                        store.update_generation_task(
                            task_id, status="running", phase=phase,
                            progress=int(progress) if isinstance(progress, (int, float)) else None,
                            error="", finished_at="",
                            _lease_owner=lease_owner,
                        )
                    now = time.time()
                    if (
                        phase == progress_write["phase"]
                        and progress == progress_write["progress"]
                        and now - progress_write["at"] < 3.0
                    ):
                        return
                    progress_write.update({"at": now, "phase": phase, "progress": progress})
                    commit_node_metadata(pid, node["id"], {
                        "providerTaskStatus": raw_status,
                        "providerTaskPhase": phase,
                        "providerTaskProgress": progress,
                        "providerTaskProgressChangedAt": meta.get("providerTaskProgressChangedAt"),
                    })
                    publish_project_event(pid, "media.progress", node["id"], {
                        "status": "generating",
                        "providerTaskStatus": raw_status,
                        "providerTaskPhase": phase,
                        "providerTaskProgress": progress,
                    })

                result = provider_client.generate_video(
                    provider, model, prompt, effective, refs, voices,
                    on_task_created=on_task_created,
                    on_task_update=on_task_update,
                )
                if result.get("pending"):
                    if task_id:
                        store.finish_generation_task(
                            task_id,
                            status="interrupted",
                            phase="结果待确认",
                            progress=20,
                            error=str(result.get("message") or "供应商仍在生成"),
                            provider_task_id=str(result.get("taskId") or ""),
                            delivery_status="pending",
                            lease_owner=lease_owner,
                        )
                    meta.update({
                        "status": "error",
                        "lastError": str(result.get("message") or "供应商仍在生成"),
                        "taskId": str(result.get("taskId") or ""),
                        "providerTaskStatus": "uncertain",
                        "providerTaskPhase": "结果待确认",
                        "generationFinishedAt": "",
                    })
                    return self._send(202, {
                        "accepted": True,
                        "taskId": task_id or "",
                        "nodeId": node["id"],
                        "status": "pending",
                        "code": "PROVIDER_OUTCOME_UNCERTAIN",
                        "error": str(result.get("message") or "供应商仍在生成"),
                        "referencePlan": meta.get("referencePlan") or {},
                    })
                provider_ready_at = store._now_iso()
                provider_url = str(result.get("url") or "")
                if not provider_url:
                    raise RuntimeError("供应商未返回视频地址，本次生成没有产生新视频")
                try:
                    local_generated_url = persist_generated_media(provider_url, ".mp4")
                except Exception as exc:
                    raise provider_client.ProviderOutcomeUncertain(
                        f"供应商已生成视频，但下载新视频失败：{exc}"
                    ) from exc
                if not local_generated_url.startswith("/uploads/"):
                    raise provider_client.ProviderOutcomeUncertain(
                        "供应商已生成视频，但没有拿到本地新文件"
                    )
                if task_id:
                    materialized = store.update_generation_task(
                        task_id,
                        _lease_owner=lease_owner,
                        provider_result_url=provider_url,
                        local_result_url=local_generated_url,
                        delivery_status="materializing",
                        result_ready_at=provider_ready_at,
                    )
                    if not materialized or materialized.get("lease_owner") != lease_owner:
                        return self._send(409, {
                            "code": "GENERATION_LEASE_LOST",
                            "error": "生成结果已落盘，但执行租约已失效，系统将从原任务恢复",
                            "taskId": task_id,
                        })
                generated_at = provider_ready_at
                generated_url = local_generated_url
                _record_video_version(
                    meta,
                    generated_url,
                    str(result.get("taskId") or ""),
                    generated_at,
                )
                recorded_version_id = str(
                    (meta.get("params") or {}).get("activeVersionId") or ""
                )
                if recorded_version_id:
                    for version in (
                        (meta.get("params") or {}).get("mediaVersions") or []
                    ) + (
                        (meta.get("params") or {}).get("videoVersions") or []
                    ):
                        if (
                            isinstance(version, dict)
                            and str(version.get("versionId") or "")
                            == recorded_version_id
                        ):
                            version["providerResultUrl"] = str(
                                provider_url or ""
                            )
                video_thumb_url = ensure_video_thumbnail(generated_url)
                if video_thumb_url:
                    meta.setdefault("params", {})["thumbUrl"] = video_thumb_url
                    for version_key in ("mediaVersions", "videoVersions"):
                        for version in (meta.get("params") or {}).get(version_key) or []:
                            if (
                                isinstance(version, dict)
                                and str(version.get("versionId") or "")
                                == recorded_version_id
                            ):
                                version["thumbUrl"] = video_thumb_url
                meta["content"] = generated_url
                meta["status"] = "done"
                meta["lastNote"] = missing_note(missing)
                meta["taskId"] = result.get("taskId", "")
                meta["generatedAt"] = generated_at
                meta["generationFinishedAt"] = generated_at
                meta["providerTaskStatus"] = "completed"
                meta["providerTaskPhase"] = "已完成，后台缓存中"
                meta["providerTaskProgress"] = 100
                meta["finalizePending"] = False
                deferred_video_finalize_args = (
                    pid,
                    node["id"],
                    local_generated_url,
                    provider,
                    model,
                    str(result.get("taskId") or ""),
                    generated_at,
                )
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
            if ntype != "video":
                meta["finalizePending"] = False
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
            if isinstance(exc, provider_client.ProviderOutcomeUncertain):
                _mark_generation_outcome_uncertain(
                    task_id, pid, node["id"], exc
                )
                raise
            if (
                _submitted_provider_task_id(task_id)
                and not isinstance(exc, provider_client.ProviderTaskFailed)
            ):
                _mark_generation_outcome_uncertain(
                    task_id, pid, node["id"], exc
                )
                raise provider_client.ProviderOutcomeUncertain(
                    f"供应商已受理任务，但本地收尾失败：{exc}。系统会继续查询原任务，不会自动重新提交。"
                ) from exc
            meta["status"] = "failed"
            meta["lastError"] = str(exc)
            explicit_provider_task_id = str(
                getattr(exc, "provider_task_id", "") or ""
            )
            failed_updates = {
                "status": "failed",
                "lastError": str(exc),
                "providerTaskStatus": "failed",
                "providerTaskPhase": "生成失败",
                "generationFinishedAt": store._now_iso(),
            }
            if explicit_provider_task_id:
                failed_updates["taskId"] = explicit_provider_task_id
            commit_node_metadata(pid, node["id"], failed_updates)
            publish_project_event(pid, "media", node["id"], {
                "status": "failed",
                "error": str(exc),
                "finalizePending": False,
                "providerTaskStatus": "failed",
                "providerTaskPhase": "生成失败",
            })
            if task_id:
                task_updates = {
                    "status": "failed",
                    "phase": "生成失败",
                    "error": str(exc),
                    "delivery_status": "failed",
                    "lease_owner": lease_owner,
                }
                if explicit_provider_task_id:
                    task_updates["provider_task_id"] = explicit_provider_task_id
                store.finish_generation_task(
                    task_id,
                    **task_updates,
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
                "referencePlan",
                "videoValidation", "actualDuration", "storage", "storageError",
                "source", "providerUrl", "providerResultUrl", "finalizePending",
                "promptStale", "promptStaleReason", "sourcePromptUpdatedAt",
            )
        }
        if ntype == "text":
            generated_updates["prompt"] = meta.get("prompt")
        if ntype in ("image", "audio", "video"):
            generated_updates["_paramsPatch"] = {
                "mediaVersions": meta.get("params", {}).get("mediaVersions", []),
                "mediaVersionIndex": meta.get("params", {}).get("mediaVersionIndex", 0),
                "activeVersionId": meta.get("params", {}).get("activeVersionId", ""),
                **(
                    {
                        "videoVersions": meta.get("params", {}).get("videoVersions", []),
                        "videoVersionIndex": meta.get("params", {}).get("videoVersionIndex", 0),
                    }
                    if ntype == "video"
                    else {}
                ),
            }
        if ntype == "text":
            generated_updates["text"] = meta.get("prompt")
        terminal_result = commit_generation_terminal(
            pid,
            node["id"],
            generated_updates,
            task_id=task_id,
            task_updates=(
                {
                    "status": "succeeded",
                    "phase": "已完成",
                    "progress": 100,
                    "error": "",
                    "provider_task_id": str(result.get("taskId") or ""),
                    "delivery_status": "ready",
                    "next_poll_at": "",
                    "result_ready_at": str(meta.get("generationFinishedAt") or ""),
                    "finished_at": "",
                    "node_written_at": "",
                }
                if task_id
                else None
            ),
            lease_owner=lease_owner if task_id else None,
            accept_durable_late_result=bool(
                task_id
                and ntype == "image"
                and (
                    str(meta.get("content") or "").startswith("/uploads/")
                    or str(meta.get("providerResultUrl") or "").startswith("https://")
                )
            ),
        )
        if not terminal_result.get("committed"):
            reason = str(terminal_result.get("reason") or "")
            if reason == "lease_lost":
                return self._send(409, {
                    "code": "GENERATION_LEASE_LOST",
                    "error": "生成结果已落盘，但执行租约已失效，系统将从原任务恢复",
                    "taskId": task_id,
                })
            raise RuntimeError(f"生成终态提交失败：{reason or 'unknown'}")
        node_written_at = str(terminal_result.get("nodeWrittenAt") or "")
        # Async validation/object-storage projection must start only after the
        # terminal transaction. Otherwise it can write first and then be
        # overwritten by the terminal metadata snapshot.
        if deferred_image_archive_args:
            queue_generated_image_tos_archive(*deferred_image_archive_args)
        if deferred_video_finalize_args:
            queue_generated_video_finalize(*deferred_video_finalize_args)
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
        response = {
            "nodeId": node["id"],
            "url": meta.get("content"),
            "status": "done",
            "taskId": task_id or "",
            "providerTaskStatus": str(meta.get("providerTaskStatus") or "completed"),
            "providerTaskPhase": str(meta.get("providerTaskPhase") or "已完成"),
            "providerTaskProgress": 100,
            "storage": meta.get("storage") or {},
            "storageError": str(meta.get("storageError") or ""),
            "source": meta.get("source") if isinstance(meta.get("source"), dict) else {},
            "providerResultUrl": str(meta.get("providerResultUrl") or ""),
            "finalizePending": False,
            "generationFinishedAt": str(
                meta.get("generationFinishedAt") or node_written_at
            ),
            "referencePlan": meta.get("referencePlan") or {},
        }
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

    def handle_error(self, request, client_address):
        """Keep expected client cancellations out of operational error logs."""
        exc = sys.exc_info()[1]
        if is_client_disconnect_error(exc):
            return
        super().handle_error(request, client_address)


def _execute_claimed_generation_task(task_id, lease_owner, claimed_task=None):
    """Run one task whose lease is owned by the current process."""
    task = dict(claimed_task) if isinstance(claimed_task, dict) else (
        store.get_generation_task(task_id)
    )
    if not task or task.get("status") not in {
        "queued",
        "submitting",
        "running",
    }:
        return
    if claimed_task:
        task["status"] = "running"
        task["lease_owner"] = lease_owner
    current_owner = str(task.get("lease_owner") or "").strip()
    if lease_owner and current_owner and current_owner != lease_owner:
        return
    project = store.get_project(task["project_id"])
    if not project:
        store.finish_generation_task(
            task_id,
            status="failed",
            phase="节点已不存在",
            error="对应画布项目已删除，任务已结束",
            delivery_status="failed",
        )
        return
    node = next(
        (
            item
            for item in project["payload"].get("nodes", [])
            if item.get("id") == task.get("node_id")
        ),
        None,
    )
    if not node:
        store.finish_generation_task(
            task_id,
            status="failed",
            phase="节点已不存在",
            error="对应画布节点已删除，任务已结束",
            delivery_status="failed",
        )
        return
    heartbeat = _start_generation_task_heartbeat(task_id, lease_owner)
    try:
        store.renew_generation_task_lease(
            task_id,
            lease_owner,
            TASK_LEASE_SECONDS,
        )
        meta = node.setdefault("metadata", {})
        params = meta.get("params") or {}
        generation_started_at = store._now_iso()
        commit_node_metadata(
            task["project_id"],
            task["node_id"],
            {
                "status": "generating",
                "lastError": None,
                "taskId": None,
                "generationStartedAt": generation_started_at,
                "generationFinishedAt": None,
                "providerTaskStatus": "queued",
                "providerTaskPhase": "正在连接供应商",
                "providerTaskProgress": 0,
                "_paramsPatch": params,
            },
        )
        worker = Handler.__new__(Handler)
        worker._send = lambda *_args, **_kwargs: None
        worker._generate(
            task["project_id"],
            project,
            node,
            task_id=task_id,
            lease_owner=lease_owner,
        )
        latest = store.get_generation_task(task_id) or {}
        if latest.get("status") in {"queued", "submitting", "running"}:
            store.finish_generation_task(
                task_id,
                status="failed",
                phase="任务异常结束",
                error="生成执行结束但未形成终态，本次未更新画布",
                delivery_status="failed",
            )
    except GenerationQueueCancelled as exc:
        store.finish_generation_task(
            task_id,
            status="failed",
            phase="排队已取消",
            error=str(exc),
            delivery_status="failed",
        )
    except Exception as exc:
        latest = store.get_generation_task(task_id) or {}
        if latest.get("status") in {"queued", "submitting", "running"}:
            stored_provider_id = str(
                latest.get("provider_task_id") or ""
            ).strip()
            store.finish_generation_task(
                task_id,
                status=(
                    "interrupted"
                    if stored_provider_id
                    else "failed"
                ),
                phase=(
                    "结果待确认"
                    if stored_provider_id
                    else "任务异常"
                ),
                error=str(exc),
                delivery_status=(
                    "pending" if stored_provider_id else "failed"
                ),
            )
        print(
            f"[generation-worker] task={task_id} error={exc}",
            file=sys.stderr,
        )
    finally:
        _finish_generation_task_lease(
            task_id,
            lease_owner,
            heartbeat,
        )


def _mark_generation_worker_start_failure(task_id, tenant_id, tenant_slug, exc):
    latest = _run_in_tenant(
        tenant_id,
        tenant_slug,
        store.get_generation_task,
        task_id,
    ) or {}
    if latest.get("status") in {"queued", "submitting", "running"}:
        _run_in_tenant(
            tenant_id,
            tenant_slug,
            store.finish_generation_task,
            task_id,
            status="failed",
            phase="任务启动失败",
            error=str(exc),
            delivery_status="failed",
        )
    print(
        f"[generation-worker] start failed task={task_id} error={exc}",
        file=sys.stderr,
    )


def execute_admitted_generation_task(
    task_id,
    tenant_id,
    tenant_slug,
    claim_token,
    task=None,
):
    """Execute a task already claimed and admitted by generation_worker."""
    try:
        _run_in_tenant(
            tenant_id,
            tenant_slug,
            _execute_claimed_generation_task,
            task_id,
            claim_token,
            task,
        )
    except Exception as exc:
        _mark_generation_worker_start_failure(
            task_id,
            tenant_id,
            tenant_slug,
            exc,
        )


def _run_generation_task_worker(task_id, tenant_id, tenant_slug):
    """Execute one persisted task with the legacy inline admission path."""
    try:
        def run():
            task = store.get_generation_task(task_id)
            if not task or task.get("status") != "queued":
                return
            project = store.get_project(task["project_id"])
            if not project:
                store.finish_generation_task(
                    task_id,
                    status="failed",
                    phase="节点已不存在",
                    error="对应画布项目已删除，任务已结束",
                    delivery_status="failed",
                )
                return
            node = next(
                (
                    item
                    for item in project["payload"].get("nodes", [])
                    if item.get("id") == task.get("node_id")
                ),
                None,
            )
            if not node:
                store.finish_generation_task(
                    task_id,
                    status="failed",
                    phase="节点已不存在",
                    error="对应画布节点已删除，任务已结束",
                    delivery_status="failed",
                )
                return
            provider_key = str(
                task.get("provider")
                or (node.get("metadata") or {}).get("provider")
                or "__default__"
            )
            slot = None
            try:
                slot = acquire_generation_slot(
                    task_id,
                    provider_key,
                    task.get("model"),
                    task.get("kind"),
                )
                lease_owner = f"generation:{secrets.token_hex(12)}"
                if not store.claim_generation_task(
                    task_id,
                    lease_owner,
                    TASK_LEASE_SECONDS,
                ):
                    return
                _execute_claimed_generation_task(
                    task_id,
                    lease_owner,
                    task,
                )
            finally:
                if slot:
                    release_generation_slot(slot)

        _run_in_tenant(tenant_id, tenant_slug, run)
    except Exception as exc:
        _mark_generation_worker_start_failure(
            task_id,
            tenant_id,
            tenant_slug,
            exc,
        )


def start_generation_task_worker(task_id, tenant_id, tenant_slug):
    if SERVER_STOPPING.is_set():
        raise RuntimeError("服务正在停止，暂不接收新的生成任务")
    thread = threading.Thread(
        target=_run_generation_task_worker,
        args=(task_id, tenant_id, tenant_slug),
        name=f"generation-worker-{task_id}",
        daemon=False,
    )
    with GENERATION_WORKERS_LOCK:
        GENERATION_WORKERS.add(thread)

    def discard():
        with GENERATION_WORKERS_LOCK:
            GENERATION_WORKERS.discard(thread)

    original_run = thread.run

    def run_and_discard():
        try:
            original_run()
        finally:
            discard()

    thread.run = run_and_discard  # type: ignore[method-assign]
    thread.start()
    return thread


def dispatch_generation_task(task_id, tenant_id, tenant_slug):
    if GENERATION_EXECUTION_MODE == "worker":
        return False
    start_generation_task_worker(
        task_id,
        tenant_id,
        tenant_slug,
    )
    return True


def stop_generation_workers_for_shutdown():
    SERVER_STOPPING.set()
    deadline = time.monotonic() + SHUTDOWN_DRAIN_SECONDS
    while time.monotonic() < deadline:
        with GENERATION_WORKERS_LOCK:
            workers = [
                thread for thread in GENERATION_WORKERS
                if thread.is_alive()
            ]
        if not workers:
            return True
        for thread in workers:
            thread.join(timeout=min(1.0, max(0.05, deadline - time.monotonic())))
    with GENERATION_WORKERS_LOCK:
        return not any(thread.is_alive() for thread in GENERATION_WORKERS)


def main():
    received_signal = {"value": False}

    def handle_stop_signal(signum, frame):
        if received_signal["value"]:
            return
        received_signal["value"] = True
        SERVER_STOPPING.set()

    signal.signal(signal.SIGTERM, handle_stop_signal)
    signal.signal(signal.SIGINT, handle_stop_signal)
    print(
        f"drama-canvas-studio server: {__file__} | "
        f"{getattr(import_feed, 'REIMPORT_IDENTITY_POLICY', 'missing')}",
        flush=True,
    )
    os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"), exist_ok=True)
    try:
        tenant_bootstrap.bootstrap()
        if not SKIP_STARTUP_BACKFILLS:
            backfilled_task_episodes = _run_for_active_tenants(
                store.backfill_generation_task_episode_ids
            )
            if backfilled_task_episodes:
                print(
                    f"已补齐 {backfilled_task_episodes} 条生成任务的集数归属"
                )
        if tenant_context.multi_tenant_enabled():
            print(
                f"多租户模式已启用，基础域名："
                f"{os.environ.get('CANVAS_BASE_DOMAIN', '').strip() or '未配置'}"
            )
    except Exception as exc:
        print(f"[tenant] 初始化失败：{exc}", file=sys.stderr)
        raise
    try:
        if not SKIP_STARTUP_BACKFILLS:
            migrated_tenants = store.migrate_active_tenant_schemas()
            if migrated_tenants:
                print(
                    f"[schema] migrated {len(migrated_tenants)} tenant databases",
                    file=sys.stderr,
                )
        if not SKIP_STARTUP_BACKFILLS:
            delivery_backfilled = _run_for_active_tenants(
                store.backfill_generation_delivery_status
            )
            if delivery_backfilled:
                print(
                    f"[schema] backfilled {delivery_backfilled} task delivery receipts",
                    file=sys.stderr,
                )
            ignored_duplicates = _run_for_active_tenants(
                store.backfill_ignored_duplicate_generation_tasks
            )
            if ignored_duplicates:
                print(
                    f"[schema] normalized {ignored_duplicates} ignored duplicate tasks",
                    file=sys.stderr,
                )
            evidenced_delivery = _run_for_active_tenants(
                store.backfill_succeeded_delivery_evidence
            )
            if evidenced_delivery:
                print(
                    f"[schema] closed {evidenced_delivery} evidenced delivery receipts",
                    file=sys.stderr,
                )
            pruned_snapshots = _run_for_active_tenants(
                store.prune_auto_snapshots
            )
            if pruned_snapshots:
                print(
                    f"[schema] pruned {pruned_snapshots} excess auto snapshots",
                    file=sys.stderr,
                )
    except Exception as exc:
        print(f"[schema] tenant migration failed: {exc}", file=sys.stderr)
    verify_required_schema_migrations()

    # Bind the API before historical backfills. Those jobs can touch every
    # project in a large tenant and previously kept port 8890 closed for
    # minutes, which made the frontend retry saves against a dead socket.
    server = CanvasHTTPServer(("127.0.0.1", PORT), Handler)
    http_thread = threading.Thread(
        target=server.serve_forever,
        name="canvas-http-server",
        daemon=True,
    )
    http_thread.start()
    print(f"戏剧画布工作流系统已启动： http://127.0.0.1:{PORT}", flush=True)
    print("关闭：Ctrl+C", flush=True)

    if SAFE_LOCAL_READONLY:
        print(
            "[safe-local] provider I/O and startup backfills disabled",
            flush=True,
        )
        while not received_signal["value"] and http_thread.is_alive():
            http_thread.join(timeout=1)
        SERVER_STOPPING.set()
        server.shutdown()
        return

    try:
        recovered = _run_for_active_tenants(store.backfill_video_version_history)
        if recovered:
            print(f"已恢复 {recovered} 个项目的媒体版本历史")
    except Exception as exc:
        print(f"[media-history] 回填失败：{exc}", file=sys.stderr)
    try:
        versioned_projects = _run_for_active_tenants(
            backfill_media_version_ids
        )
        if versioned_projects:
            print(
                f"已为 {versioned_projects} 个项目补齐媒体版本唯一 ID"
            )
    except Exception as exc:
        print(f"[media-version-ids] 回填失败：{exc}", file=sys.stderr)
    try:
        recovered_episodes = _run_for_active_tenants(backfill_multi_episode_payloads)
        if recovered_episodes:
            print(f"已恢复 {recovered_episodes} 个项目的多集导入数据")
    except Exception as exc:
        print(f"[episode-import] 多集恢复失败：{exc}", file=sys.stderr)
    try:
        backfilled_nodes = _run_for_active_tenants(
            lambda: store.backfill_asset_identities().get("nodes", 0)
        )
        if backfilled_nodes:
            print(f"已为 {backfilled_nodes} 个资产节点补齐唯一 ID")
    except Exception as exc:
        print(f"[asset-identity] 资产唯一 ID 回填失败：{exc}", file=sys.stderr)
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
        archived_projects = _run_for_active_tenants(
            backfill_local_media_storage
        )
        if archived_projects:
            print(f"已转存 {archived_projects} 个项目的本地素材到对象存储")
    except Exception as exc:
        print(f"[storage] 本地素材转存失败：{exc}", file=sys.stderr)
    try:
        restored_urls = _run_for_active_tenants(
            reconcile_image_provider_metadata
        )
        if restored_urls:
            print(
                f"已恢复 {restored_urls} 个图片节点的供应商原始链接"
            )
    except Exception as exc:
        print(f"[provider-result] 图片元数据对账失败：{exc}", file=sys.stderr)
    try:
        repaired_ownership = _run_for_active_tenants(
            repair_episode_node_ownership
        )
        if repaired_ownership:
            print(
                f"已修复 {repaired_ownership} 个节点的集数归属"
            )
    except Exception as exc:
        print(f"[episode-ownership] 节点集数归属修复失败：{exc}", file=sys.stderr)
    try:
        validated = _run_for_active_tenants(backfill_generated_video_validation)
        if validated:
            print(f"已校验 {validated} 个历史视频")
    except Exception as exc:
        print(f"[video-validation] 回填失败：{exc}", file=sys.stderr)
    try:
        repaired_references = _run_for_active_tenants(
            repair_expiring_seedream_references
        )
        if repaired_references:
            print(
                f"已修复 {repaired_references} 个 Seedream 临时引用"
            )
    except Exception as exc:
        print(
            f"[seedream-reference] 历史引用修复失败：{exc}",
            file=sys.stderr,
        )
    try:
        covers = _run_for_active_tenants(backfill_generated_video_thumbnails)
        if covers:
            print(f"已补齐 {covers} 个历史视频封面")
    except Exception as exc:
        print(f"[video-thumbnail] 回填失败：{exc}", file=sys.stderr)
    start_image_thumbnail_backfill()
    try:
        _run_for_active_tenants(store.interrupt_orphan_generation_tasks)
    except Exception as exc:
        print(f"[generation-tasks] 清理中断任务失败：{exc}", file=sys.stderr)
    try:
        recovered_provider_tasks = _run_for_active_tenants(
            reconcile_provider_task_journal
        )
        if recovered_provider_tasks:
            print(f"已恢复 {recovered_provider_tasks} 条供应商任务号记录")
    except Exception as exc:
        print(f"[generation-tasks] 恢复供应商任务号失败：{exc}", file=sys.stderr)
    try:
        materialized_images = _run_for_active_tenants(
            materialize_saved_image_results
        )
        if materialized_images:
            print(f"已恢复 {materialized_images} 个图片生成终态")
    except Exception as exc:
        print(f"[generation-tasks] 图片结果续传失败：{exc}", file=sys.stderr)
    try:
        reconciled = _run_for_active_tenants(reconcile_interrupted_generation_nodes)
        if reconciled:
            print(f"已标记 {reconciled} 个中断的生成节点")
    except Exception as exc:
        print(f"[generation-tasks] 对账中断节点失败：{exc}", file=sys.stderr)
    try:
        repaired_failures = _run_for_active_tenants(reconcile_failed_generation_task_nodes)
        if repaired_failures:
            print(f"已对齐 {repaired_failures} 个生成任务的节点终态")
    except Exception as exc:
        print(f"[generation-tasks] 恢复失败节点状态失败：{exc}", file=sys.stderr)
    try:
        reconciled_tasks = _run_for_active_tenants(reconcile_generation_task_records)
        if reconciled_tasks:
            print(f"已对账 {reconciled_tasks} 条生成任务")
    except Exception as exc:
        print(f"[generation-tasks] 对账任务记录失败：{exc}", file=sys.stderr)
    start_generation_task_reconciler()
    start_storage_maintenance()
    start_audit_maintenance()
    start_wal_maintenance()
    try:
        start_pending_video_resumers()
    except Exception as exc:
        print(f"[video-resume] 恢复进行中任务失败：{exc}", file=sys.stderr)
    while not received_signal["value"] and http_thread.is_alive():
        http_thread.join(timeout=1)
    SERVER_STOPPING.set()
    server.shutdown()
    drained = stop_generation_workers_for_shutdown()
    print(
        "\n已停止（生成任务已排空）"
        if drained
        else "\n已停止（仍有生成任务保留为待确认状态）"
    )


if __name__ == "__main__":
    main()
