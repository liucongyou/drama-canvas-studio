# -*- coding: utf-8 -*-
"""Tenant provisioning and one-time migration of the legacy single-tenant data."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Iterator

import tenant_context
import tenant_registry


def _legacy_config() -> dict:
    path = tenant_context.BASE_DIR / "config.json"
    if not path.exists():
        return {"providers": [], "models": [], "storage": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {"providers": [], "models": [], "storage": {}}


def _empty_secret_template() -> dict:
    data = json.loads(json.dumps(_legacy_config(), ensure_ascii=False))
    providers = data.get("providers")
    if isinstance(providers, list):
        for provider in providers:
            if isinstance(provider, dict):
                provider["api_key"] = ""
    storage = data.get("storage")
    if isinstance(storage, dict):
        storage["access_key"] = ""
        storage["secret_key"] = ""
        storage["bucket"] = ""
        storage["prefix"] = ""
        storage["enabled"] = False
    data["deletedProviders"] = []
    data["deletedModels"] = []
    return data


def ensure_tenant_layout(tenant_id: str, slug: str) -> tenant_context.TenantContext:
    context = tenant_context.make_context(tenant_id, slug)
    tenant_context.ensure_context_dirs(context)
    if not context.config_path.exists():
        # Write through provider_catalog so the tenant gets a full, independent
        # snapshot instead of inheriting later changes from the platform template.
        import provider_catalog

        with tenant_context.use_context(context):
            provider_catalog.save_config(_empty_secret_template())
    return context


def create_tenant(slug: str, *, name: str = "") -> dict:
    tenant = tenant_registry.create_tenant(slug, name=name)
    ensure_tenant_layout(tenant["id"], tenant["slug"])
    return tenant


def ensure_owner_tenant() -> dict:
    owner_slug = os.environ.get("CANVAS_OWNER_SLUG", "owner")
    tenant = tenant_registry.get_tenant_by_id("owner") or tenant_registry.get_tenant_by_slug(
        owner_slug
    )
    if not tenant:
        tenant = tenant_registry.create_tenant(
            owner_slug,
            name="Owner",
            tenant_id="owner",
            legacy_source="legacy",
        )
    ensure_tenant_layout(tenant["id"], tenant["slug"])
    migrate_legacy_to_owner(tenant["id"], tenant["slug"])
    return tenant


def migrate_legacy_to_owner(tenant_id: str, slug: str) -> bool:
    context = tenant_context.make_context(tenant_id, slug)
    marker = context.data_root / ".legacy-migrated"
    if marker.exists():
        return False

    root = tenant_context.BASE_DIR
    legacy_db = root / "data" / "canvas.db"
    legacy_uploads = root / "data" / "uploads"
    legacy_config = root / "config.local.json"

    context.data_root.mkdir(parents=True, exist_ok=True)
    if legacy_db.exists() and not context.db_path.exists():
        shutil.copy2(legacy_db, context.db_path)
    if legacy_uploads.exists():
        context.upload_dir.mkdir(parents=True, exist_ok=True)
        if not any(context.upload_dir.iterdir()):
            shutil.copytree(legacy_uploads, context.upload_dir, dirs_exist_ok=True)
    if legacy_config.exists() and (
        not context.config_path.exists() or _looks_like_empty_template(context.config_path)
    ):
        shutil.copy2(legacy_config, context.config_path)

    # The copied legacy JSON may contain plaintext secrets. Load and save it
    # through the normal encrypted path, then leave the original untouched.
    import provider_catalog

    with tenant_context.use_context(context):
        cfg = provider_catalog.load_config(migrate=False)
        provider_catalog.save_config(cfg)
        storage = cfg.get("storage") if isinstance(cfg.get("storage"), dict) else {}
        if (
            storage.get("enabled")
            and storage.get("bucket")
            and storage.get("region")
            and storage.get("access_key")
        ):
            tenant_registry.claim_storage_binding(
                tenant_id,
                bucket=storage.get("bucket"),
                region=storage.get("region"),
                prefix=storage.get("prefix") or "",
                access_key=storage.get("access_key"),
            )

    marker.write_text("1\n", encoding="ascii")
    return True


def _looks_like_empty_template(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    providers = data.get("providers") if isinstance(data.get("providers"), list) else []
    if any(
        isinstance(provider, dict) and str(provider.get("api_key") or "").strip()
        for provider in providers
    ):
        return False
    storage = data.get("storage") if isinstance(data.get("storage"), dict) else {}
    return not any(
        str(storage.get(key) or "").strip()
        for key in ("access_key", "secret_key", "bucket")
    )


def ensure_all_tenants() -> None:
    for tenant in tenant_registry.list_tenants(include_disabled=True):
        ensure_tenant_layout(tenant["id"], tenant["slug"])


def active_contexts() -> Iterator[tenant_context.TenantContext]:
    if not tenant_context.multi_tenant_enabled():
        yield tenant_context.make_context("owner", os.environ.get("CANVAS_OWNER_SLUG", "owner"))
        return
    for tenant in tenant_registry.list_tenants(include_disabled=False):
        yield tenant_context.make_context(tenant["id"], tenant["slug"])


def bootstrap() -> None:
    if not tenant_context.multi_tenant_enabled():
        return
    ensure_owner_tenant()
    ensure_all_tenants()

