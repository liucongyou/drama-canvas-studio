"""Resolve the text model from the canvas backend's provider configuration.

The Agent must not own a second model list. The canvas backend already stores
providers, model ids, pricing and (encrypted) keys. This module loads that same
configuration, decrypts it in-process via the backend modules, and exposes only
non-secret model metadata to the panel.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
from typing import Any

from app.config import get_settings

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class BackendConfigError(RuntimeError):
    pass


def _load(tenant_id: str = "") -> dict[str, Any]:
    settings = get_settings()
    try:
        import tenant_context
        import provider_catalog

        selected_tenant = str(tenant_id or settings.canvas_tenant_id).strip()
        slug = (
            settings.canvas_tenant_slug
            if selected_tenant == settings.canvas_tenant_id
            else selected_tenant
        )
        with tenant_context.use_tenant(selected_tenant, slug):
            return provider_catalog.load_config()
    except Exception as exc:
        raise BackendConfigError(f"读取画布后台模型配置失败：{exc}") from exc


def _canvas_catalog() -> dict[str, Any] | None:
    try:
        settings = get_settings()
        with httpx.Client(timeout=10, trust_env=False) as client:
            response = client.get(settings.canvas_base_url.rstrip("/") + "/api/model-catalog")
            if response.status_code != 200:
                return None
            data = response.json()
            return data if isinstance(data, dict) else None
    except Exception:
        return None


def _providers(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id")): item
        for item in config.get("providers", [])
        if isinstance(item, dict) and item.get("id")
    }


def list_text_models(tenant_id: str = "") -> list[dict[str, Any]]:
    catalog = _canvas_catalog()
    if catalog:
        providers = {
            str(item.get("id")): item
            for item in catalog.get("providers", [])
            if isinstance(item, dict) and item.get("id")
        }
        result = []
        for model in catalog.get("models", []):
            if not isinstance(model, dict) or model.get("capability") != "text" or not model.get("enabled", True):
                continue
            provider = providers.get(str(model.get("providerId") or "")) or {}
            result.append({
                "id": str(model.get("id") or ""),
                "label": str(model.get("label") or model.get("name") or model.get("id") or ""),
                "providerId": str(model.get("providerId") or ""),
                "providerName": str(provider.get("name") or model.get("providerId") or ""),
                "model": str(model.get("remoteModel") or model.get("model") or model.get("id") or ""),
                "description": str(model.get("description") or ""),
                "badge": str(model.get("badge") or "Text"),
                "hasApiKey": bool(provider.get("has_api_key")),
            })
        if result:
            return result
    # Fallback: tenant config when the canvas is not reachable.
    config = _load(tenant_id)
    providers = _providers(config)
    result = []
    for model in config.get("models", []):
        if not isinstance(model, dict) or model.get("capability") != "text" or not model.get("enabled", True):
            continue
        provider = providers.get(str(model.get("providerId") or "")) or {}
        result.append({
            "id": str(model.get("id") or ""),
            "label": str(model.get("label") or model.get("name") or model.get("id") or ""),
            "providerId": str(model.get("providerId") or ""),
            "providerName": str(provider.get("name") or model.get("providerId") or ""),
            "model": str(model.get("remoteModel") or model.get("model") or model.get("id") or ""),
            "description": str(model.get("description") or ""),
            "badge": str(model.get("badge") or "Text"),
            "hasApiKey": bool(str(provider.get("api_key") or "").strip()),
        })
    return result


def resolve_text_model(
    model_id: str = "",
    tenant_id: str = "",
) -> dict[str, Any] | None:
    catalog = _canvas_catalog()
    config = _load(tenant_id)
    providers = _providers(config)
    catalog_models = [
        item for item in (catalog or {}).get("models", [])
        if isinstance(item, dict) and item.get("capability") == "text" and item.get("enabled", True)
    ]
    selected = None
    if model_id:
        selected = next((item for item in catalog_models if str(item.get("id")) == model_id), None)
    if selected is None and catalog_models:
        selected = catalog_models[0]
    if selected is not None:
        provider_id = str(selected.get("providerId") or "")
        provider = providers.get(provider_id) or {}
        catalog_providers = {
            str(item.get("id")): item
            for item in (catalog or {}).get("providers", [])
            if isinstance(item, dict) and item.get("id")
        }
        public_provider = catalog_providers.get(provider_id) or {}
        api_key = str(provider.get("api_key") or "").strip()
        base_url = str(public_provider.get("base_url") or provider.get("base_url") or "").strip().rstrip("/")
        remote_model = str(selected.get("remoteModel") or selected.get("model") or selected.get("id") or "").strip()
        if api_key and base_url and remote_model:
            return {
                "id": str(selected.get("id") or remote_model),
                "label": str(selected.get("label") or remote_model),
                "providerId": provider_id,
                "baseUrl": base_url,
                "apiKey": api_key,
                "model": remote_model,
            }
    # Fallback: tenant config.
    models = [
        item for item in config.get("models", [])
        if isinstance(item, dict) and item.get("capability") == "text" and item.get("enabled", True)
    ]
    selected = None
    if model_id:
        selected = next((item for item in models if str(item.get("id")) == model_id), None)
    if selected is None and models:
        selected = models[0]
    if selected is None:
        return None
    provider = providers.get(str(selected.get("providerId") or "")) or {}
    api_key = str(provider.get("api_key") or "").strip()
    base_url = str(provider.get("base_url") or "").strip().rstrip("/")
    remote_model = str(selected.get("remoteModel") or selected.get("model") or selected.get("id") or "").strip()
    if not (api_key and base_url and remote_model):
        return None
    return {
        "id": str(selected.get("id") or remote_model),
        "label": str(selected.get("label") or remote_model),
        "providerId": str(selected.get("providerId") or ""),
        "baseUrl": base_url,
        "apiKey": api_key,
        "model": remote_model,
    }
