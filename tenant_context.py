# -*- coding: utf-8 -*-
"""Request-scoped tenant paths and context helpers."""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = BASE_DIR / "data"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "canvas.db"
DEFAULT_UPLOAD_DIR = DEFAULT_DATA_DIR / "uploads"
DEFAULT_CONFIG_PATH = BASE_DIR / "config.local.json"

_CURRENT_TENANT: ContextVar["TenantContext | None"] = ContextVar(
    "canvas_current_tenant",
    default=None,
)

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    slug: str
    data_root: Path
    db_path: Path
    upload_dir: Path
    config_path: Path
    log_dir: Path


def multi_tenant_enabled() -> bool:
    value = os.environ.get("CANVAS_MULTI_TENANT", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def data_root() -> Path:
    configured = os.environ.get("CANVAS_TENANT_DATA_ROOT", "").strip()
    return Path(configured).expanduser().resolve() if configured else DEFAULT_DATA_DIR


def tenants_root() -> Path:
    return data_root() / "tenants"


def normalize_slug(value: str) -> str:
    slug = str(value or "").strip().lower()
    if not _SLUG_RE.fullmatch(slug):
        raise ValueError(
            "租户标识只能包含小写字母、数字和中间连字符，长度 1~63。"
        )
    return slug


def make_context(tenant_id: str, slug: str) -> TenantContext:
    safe_id = str(tenant_id or "").strip()
    if not safe_id or not re.fullmatch(r"[a-z0-9_-]{1,64}", safe_id):
        raise ValueError("租户 ID 非法")
    safe_slug = normalize_slug(slug)
    root = tenants_root() / safe_id
    return TenantContext(
        tenant_id=safe_id,
        slug=safe_slug,
        data_root=root,
        db_path=root / "canvas.db",
        upload_dir=root / "uploads",
        config_path=root / "config.local.json",
        log_dir=root / "logs",
    )


def ensure_context_dirs(context: TenantContext) -> TenantContext:
    context.data_root.mkdir(parents=True, exist_ok=True)
    context.upload_dir.mkdir(parents=True, exist_ok=True)
    context.log_dir.mkdir(parents=True, exist_ok=True)
    return context


def current() -> TenantContext | None:
    return _CURRENT_TENANT.get()


def current_tenant_id() -> str:
    context = current()
    return context.tenant_id if context else "legacy"


def current_slug() -> str:
    context = current()
    return context.slug if context else "local"


def current_db_path() -> Path:
    context = current()
    return context.db_path if context else Path(DEFAULT_DB_PATH)


def current_upload_dir() -> Path:
    context = current()
    return context.upload_dir if context else Path(DEFAULT_UPLOAD_DIR)


def current_config_path() -> Path:
    context = current()
    return context.config_path if context else Path(DEFAULT_CONFIG_PATH)


def set_tenant(context: TenantContext | None):
    return _CURRENT_TENANT.set(context)


def reset_tenant(token) -> None:
    _CURRENT_TENANT.reset(token)


@contextmanager
def use_tenant(tenant_id: str, slug: str | None = None) -> Iterator[TenantContext]:
    context = make_context(tenant_id, slug or tenant_id)
    ensure_context_dirs(context)
    token = set_tenant(context)
    try:
        yield context
    finally:
        reset_tenant(token)


@contextmanager
def use_context(context: TenantContext | None) -> Iterator[TenantContext | None]:
    token = set_tenant(context)
    try:
        yield context
    finally:
        reset_tenant(token)


def classify_host(host_header: str) -> tuple[str, str]:
    """Return (kind, slug). kind is tenant, base or invalid."""
    base_domain = os.environ.get("CANVAS_BASE_DOMAIN", "").strip().lower().rstrip(".")
    if not base_domain:
        return "invalid", ""
    raw = str(host_header or "").strip()
    if not raw:
        return "invalid", ""
    hostname = raw.split(",", 1)[0].strip()
    if "://" in hostname:
        hostname = hostname.split("://", 1)[1]
    hostname = hostname.split("/", 1)[0]
    if hostname.startswith("["):
        hostname = hostname.split("]", 1)[0].lstrip("[")
    elif ":" in hostname:
        hostname = hostname.rsplit(":", 1)[0]
    hostname = hostname.strip().lower().rstrip(".")
    if hostname == base_domain or hostname == f"www.{base_domain}":
        return "base", ""
    suffix = f".{base_domain}"
    if not hostname.endswith(suffix):
        return "invalid", ""
    slug = hostname[:-len(suffix)]
    if not slug or "." in slug:
        return "invalid", ""
    try:
        return "tenant", normalize_slug(slug)
    except ValueError:
        return "invalid", ""


def owner_context() -> TenantContext:
    return make_context("owner", os.environ.get("CANVAS_OWNER_SLUG", "owner"))

