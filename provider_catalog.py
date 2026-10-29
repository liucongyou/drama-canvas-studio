# -*- coding: utf-8 -*-
"""Provider and model catalog for the canvas studio.

Configuration is intentionally local-only. Public API responses never expose
API keys; callers receive only a boolean and a short hint.
"""
from __future__ import annotations

import copy
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import network_security
import tenant_context
import tenant_security


BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
LOCAL_CONFIG_PATH = os.path.join(BASE, "config.local.json")


def _local_config_path() -> str:
    context = tenant_context.current()
    return str(context.config_path) if context is not None else LOCAL_CONFIG_PATH


def _tenant_mode() -> bool:
    return tenant_context.current() is not None


def _read_json_file(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"配置文件必须是 JSON 对象：{path}")
    return loaded


def _decrypt_config_secrets(cfg: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(cfg)
    for provider in result.get("providers", []):
        if isinstance(provider, dict) and provider.get("api_key"):
            provider["api_key"] = tenant_security.decrypt_secret(provider["api_key"])
    storage = result.get("storage")
    if isinstance(storage, dict):
        for key in ("access_key", "secret_key"):
            if storage.get(key):
                storage[key] = tenant_security.decrypt_secret(storage[key])
        imgbb = storage.get("imgbb")
        if isinstance(imgbb, dict) and imgbb.get("api_key"):
            imgbb["api_key"] = tenant_security.decrypt_secret(imgbb["api_key"])
    return result


def _encrypt_config_secrets(cfg: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(cfg)
    # Legacy flat keys were the migration source for provider api_key fields.
    # Never write them back after the provider-level values have been resolved.
    for legacy_key in (
        "ark_api_key",
        "ARK_API_KEY",
        "team_router_api_key",
        "teamorouter_api_key",
        "dashscope_api_key",
        "dashscope_workspace_id",
        "workspace_id",
        "tos_access_key",
        "tos_secret_key",
    ):
        result.pop(legacy_key, None)
    for provider in result.get("providers", []):
        if isinstance(provider, dict) and provider.get("api_key"):
            provider["api_key"] = tenant_security.encrypt_secret(provider["api_key"])
    storage = result.get("storage")
    if isinstance(storage, dict):
        for key in ("access_key", "secret_key"):
            if storage.get(key):
                storage[key] = tenant_security.encrypt_secret(storage[key])
        imgbb = storage.get("imgbb")
        if isinstance(imgbb, dict) and imgbb.get("api_key"):
            imgbb["api_key"] = tenant_security.encrypt_secret(imgbb["api_key"])
    return result


def _select(options: list[str], *, multiple: bool = False) -> dict[str, Any]:
    return {"type": "select", "options": options, "multiple": multiple}


def _model(
    model_id: str,
    provider_id: str,
    capability: str,
    label: str,
    remote_model: str,
    adapter: str,
    description: str,
    *,
    badge: str = "M",
    default_params: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    limits: dict[str, Any] | None = None,
    pricing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": model_id,
        "providerId": provider_id,
        "capability": capability,
        "label": label,
        "remoteModel": remote_model,
        "adapter": adapter,
        "description": description,
        "badge": badge,
        "enabled": True,
        "defaultParams": default_params or {},
        "params": params or {},
        "limits": limits or {},
        "pricing": pricing or {},
    }


DEFAULT_PROVIDERS = [
    {
        "id": "ark",
        "name": "火山方舟",
        "protocol": "ark",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key": "",
        "enabled": True,
    },
    {
        "id": "teamorouter",
        "name": "TeamRouter",
        "protocol": "openai",
        "base_url": "https://api.teamorouter.cn/v1",
        "api_key": "",
        "enabled": True,
    },
    {
        "id": "agnes-ai",
        "name": "Agnes AI",
        "protocol": "openai",
        "base_url": "https://api.agnes-ai.cn/v1",
        "api_key": "",
        "enabled": True,
        "test_path": "/models",
    },
    {
        "id": "aliyun_bailian",
        "name": "阿里云百炼 TTS",
        "protocol": "dashscope",
        "base_url": "https://dashscope.aliyuncs.com/api/v1",
        "api_key": "",
        "workspace_id": "",
        "enabled": True,
    },
]


DEFAULT_MODELS = [
    _model(
        "doubao-seedream-5-0-pro",
        "ark",
        "image",
        "Seedream 5.0 Pro",
        "doubao-seedream-5-0-pro-260628",
        "ark_image",
        "顶级画质，多参考图与图文生成",
        badge="Pro",
        default_params={"ratio": "1:1", "clarity": "1.5K", "size": "1536x1536", "guidance": "中等"},
        params={
            "ratio": _select(["1:1", "16:9", "9:16", "4:3", "3:4", "21:9"]),
            "clarity": _select(["1.5K", "2K", "4K"]),
            "guidance": _select(["轻度", "中等", "高", "极高", "最高"]),
        },
        limits={"maxRefs": 10, "maxImages": 10, "maxTextRefs": 10, "maxPromptChars": 4000},
    ),
    _model(
        "doubao-seedance-2-0",
        "ark",
        "video",
        "Seedance 2.0",
        "doubao-seedance-2-0-260128",
        "ark_video",
        "标准视频生成，最高 1080P / 15 秒",
        badge="2.0",
        default_params={"ratio": "9:16", "resolution": "480P", "duration": 5, "guidance": "中等"},
        params={
            "ratio": _select(["1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "adaptive"]),
            "resolution": _select(["480P", "720P", "1080P"]),
            "duration": {"type": "range", "min": 4, "max": 15, "step": 1, "options": [4, 5, 10, 15]},
            "guidance": _select(["轻度", "中等", "高", "极高", "最高"]),
        },
        limits={"maxRefs": 9, "maxImages": 9, "maxVideos": 3, "maxAudios": 3, "maxPromptChars": 8000},
        pricing={
            "currency": "CNY",
            "video": {
                "unit": "second",
                "rates": {"480P": 0.462, "720P": 0.994, "1080P": 2.478},
                "ratesWithVideo": {"480P": 0.506, "720P": 1.088, "1080P": 2.712},
                "note": "按火山方舟官方 Seedance 2.0 无视频输入 5 秒示例换算，实际以 usage.completion_tokens 和账单为准",
                "noteWithVideo": "按火山方舟官方 Seedance 2.0 含视频输入 5 秒示例换算；2~15 秒存在最低 token 限制，实际以 usage.completion_tokens 和账单为准",
            },
        },
    ),
    _model(
        "doubao-seedance-2-0-mini",
        "ark",
        "video",
        "Seedance 2.0 mini",
        "doubao-seedance-2-0-mini-260615",
        "ark_video",
        "轻量快速，最高 720P / 15 秒",
        badge="mini",
        default_params={"ratio": "9:16", "resolution": "480P", "duration": 5, "guidance": "中等"},
        params={
            "ratio": _select(["1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "adaptive"]),
            "resolution": _select(["480P", "720P"]),
            "duration": {"type": "range", "min": 4, "max": 15, "step": 1, "options": [4, 5, 10, 15]},
            "guidance": _select(["轻度", "中等", "高", "极高", "最高"]),
        },
        limits={"maxRefs": 9, "maxImages": 9, "maxVideos": 3, "maxAudios": 3, "maxPromptChars": 8000},
    ),
    _model(
        "doubao-seedance-2-0-fast",
        "ark",
        "video",
        "Seedance 2.0 fast",
        "doubao-seedance-2-0-fast-260128",
        "ark_video",
        "极速出片，最高 720P / 15 秒",
        badge="fast",
        default_params={"ratio": "9:16", "resolution": "480P", "duration": 5, "guidance": "中等"},
        params={
            "ratio": _select(["1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "adaptive"]),
            "resolution": _select(["480P", "720P"]),
            "duration": {"type": "range", "min": 4, "max": 15, "step": 1, "options": [4, 5, 10, 15]},
            "guidance": _select(["轻度", "中等", "高", "极高", "最高"]),
        },
        limits={"maxRefs": 9, "maxImages": 9, "maxVideos": 3, "maxAudios": 3, "maxPromptChars": 8000},
        pricing={
            "currency": "CNY",
            "video": {
                "unit": "second",
                "rates": {"480P": 0.372, "720P": 0.8},
                "ratesWithVideo": {"480P": 0.398, "720P": 0.856},
                "note": "按火山方舟官方 Seedance 2.0 fast 无视频输入 5 秒示例换算，实际以 usage.completion_tokens 和账单为准",
                "noteWithVideo": "按火山方舟官方 Seedance 2.0 fast 含视频输入 5 秒示例换算；2~15 秒存在最低 token 限制，实际以 usage.completion_tokens 和账单为准",
            },
        },
    ),
    _model(
        "doubao-seedance-2-5",
        "ark",
        "video",
        "Seedance 2.5",
        "doubao-seedance-2-5-260628",
        "ark_video",
        "新一代视频模型，最高 1080P / 30 秒",
        badge="2.5",
        default_params={"ratio": "9:16", "resolution": "480P", "duration": 5, "guidance": "中等"},
        params={
            "ratio": _select(["1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "adaptive"]),
            "resolution": _select(["480P", "720P", "1080P"]),
            "duration": {"type": "range", "min": 4, "max": 30, "step": 1, "options": [4, 5, 10, 15, 20, 30]},
            "guidance": _select(["轻度", "中等", "高", "极高", "最高"]),
        },
        limits={"maxRefs": 30, "maxImages": 30, "maxVideos": 10, "maxAudios": 10, "maxPromptChars": 12000},
    ),
    _model(
        "agnes-image-2.5-flash",
        "agnes-ai",
        "image",
        "Agnes Image 2.5 Flash",
        "agnes-image-2.5-flash",
        "agnes_image",
        "高信息密度图像、多参考图合成与图生图编辑",
        badge="25F",
        default_params={
            "ratio": "16:9",
            "clarity": "2K",
            "size": "2K",
            "responseFormat": "url",
        },
        params={
            "ratio": _select(["1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9"]),
            "clarity": _select(["1K", "2K", "3K", "4K"]),
            "responseFormat": {
                **_select(["url", "b64_json"]),
                "api": "extra_body.response_format",
            },
        },
        limits={"maxRefs": 10, "maxImages": 10, "maxTextRefs": 10, "maxPromptChars": 8000},
    ),
    _model(
        "agnes-video-2.5-flash",
        "agnes-ai",
        "video",
        "Agnes Video 2.5 Flash",
        "agnes-video-2.5-flash",
        "agnes_video",
        "文生视频、首尾帧控制与图片/音频参考",
        badge="25F",
        default_params={
            "ratio": "16:9",
            "resolution": "720P",
            "duration": 5,
            "count": 1,
            "mode": "auto",
        },
        params={
            "ratio": _select(["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"]),
            "resolution": _select(["720P"]),
            "duration": {"type": "range", "min": 4, "max": 12, "step": 1, "options": [4, 5, 8, 10, 12]},
            "count": {
                **_select([1]),
                "default": 1,
                "api": "n",
            },
            "seed": {"type": "range", "min": 0, "max": 2147483647, "step": 1},
            "mode": _select(["auto", "text", "reference", "keyframe"]),
            "firstFrame": {"type": "text"},
            "lastFrame": {"type": "text"},
        },
        limits={
            "maxRefs": 8,
            "maxImages": 5,
            "maxVideos": 0,
            "maxAudios": 3,
            "maxTextRefs": 3,
            "maxPromptChars": 8000,
        },
    ),
    _model(
        "deepseek-flash-1017",
        "teamorouter",
        "text",
        "DeepSeek V4.1 Flash 1017",
        "deepseek-flash-1017",
        "openai_chat",
        "OpenAI 兼容对话接口，适合剧本改写与结构化输出",
        badge="Text",
        default_params={"maxTokens": 3000, "temperature": 0.7},
        params={
            "maxTokens": {"type": "range", "min": 256, "max": 32000, "step": 256},
            "temperature": {"type": "range", "min": 0, "max": 2, "step": 0.1},
        },
        limits={"maxRefs": 20, "maxTextRefs": 20, "maxPromptChars": 32000},
    ),
    _model(
        "cosyvoice-v3-5-flash",
        "aliyun_bailian",
        "audio",
        "CosyVoice v3.5 Flash",
        "cosyvoice-v3.5-flash",
        "dashscope_tts",
        "阿里云百炼音色设计与非实时语音合成",
        badge="TTS",
        default_params={
            "ttsMode": "design",
            "ttsVoice": "",
            "voiceId": "",
            "outputFormat": "mp3",
            "sampleRate": 24000,
            "previewDuration": 4.5,
        },
        params={
            "ttsMode": _select(["design", "speech"]),
            "outputFormat": _select(["mp3", "wav"]),
            "sampleRate": _select(["16000", "22050", "24000", "44100", "48000"]),
            "previewDuration": {"type": "range", "min": 0.5, "max": 4.5, "step": 0.5},
        },
        limits={"maxRefs": 10, "maxImages": 3, "maxTextRefs": 10, "maxPromptChars": 4000},
    ),
]


DEFAULT_STORAGE = {
    "enabled": False,
    "provider": "tos",
    "bucket": "",
    "region": "cn-beijing",
    "endpoint": "tos-cn-beijing.volces.com",
    "internal_endpoint": "tos-cn-beijing.ivolces.com",
    "prefix": "drama-canvas/",
    "access_key": "",
    "secret_key": "",
    "url_ttl_seconds": 3600,
    "auto_persist_images": True,
    "require_for_ark_portrait": True,
    "imgbb": {
        "api_key": "",
        "auto_persist_images": True,
    },
}

DEFAULT_BUDGET = {
    "enabled": False,
    "currency": "CNY",
    "dailyLimit": 0,
    "monthlyLimit": 0,
    "maxRequestCost": 0,
    "allowUnknownCost": False,
}


DEFAULTS = {
    "providers": DEFAULT_PROVIDERS,
    "models": DEFAULT_MODELS,
    "storage": DEFAULT_STORAGE,
    "budget": DEFAULT_BUDGET,
}


def _deep_merge(defaults: Any, override: Any) -> Any:
    if isinstance(defaults, list) and isinstance(override, list):
        return copy.deepcopy(override)
    if isinstance(defaults, dict) and isinstance(override, dict):
        out = copy.deepcopy(defaults)
        for key, value in override.items():
            out[key] = _deep_merge(defaults.get(key), value)
        return out
    return copy.deepcopy(override)


def load_config(*, migrate: bool = True) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    local_path = _local_config_path()
    if _tenant_mode():
        cfg = _read_json_file(local_path) if os.path.exists(local_path) else _read_json_file(CONFIG_PATH)
    else:
        cfg = _read_json_file(CONFIG_PATH)
        if os.path.exists(local_path):
            cfg = _deep_merge(cfg, _read_json_file(local_path))
    cfg = _decrypt_config_secrets(cfg)
    deleted_providers = {
        str(item).strip()
        for item in (cfg.get("deletedProviders") or [])
        if str(item).strip()
    }
    deleted_models = {
        str(item).strip()
        for item in (cfg.get("deletedModels") or [])
        if str(item).strip()
    }
    merged = copy.deepcopy(cfg)
    existing_providers = cfg.get("providers") if isinstance(cfg.get("providers"), list) else []
    provider_map = {item["id"]: copy.deepcopy(item) for item in DEFAULT_PROVIDERS}
    for item in existing_providers:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        current = provider_map.get(item["id"], {})
        provider_map[item["id"]] = _deep_merge(current, item)
    for provider_id in deleted_providers:
        provider_map.pop(provider_id, None)
    merged["providers"] = list(provider_map.values())

    existing_models = cfg.get("models") if isinstance(cfg.get("models"), list) else []
    model_map = {item["id"]: copy.deepcopy(item) for item in DEFAULT_MODELS}
    for item in existing_models:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        current = model_map.get(item["id"], {})
        model_map[item["id"]] = _deep_merge(current, item)
    for model_id in deleted_models:
        model_map.pop(model_id, None)
    merged["models"] = list(model_map.values())
    merged["storage"] = _deep_merge(
        DEFAULT_STORAGE,
        cfg.get("storage") if isinstance(cfg.get("storage"), dict) else {},
    )
    merged["budget"] = _deep_merge(
        DEFAULT_BUDGET,
        cfg.get("budget") if isinstance(cfg.get("budget"), dict) else {},
    )
    merged["deletedProviders"] = sorted(deleted_providers)
    merged["deletedModels"] = sorted(deleted_models)

    # One-time compatibility with the original flat config.
    providers = merged.setdefault("providers", [])
    models = merged.setdefault("models", [])
    provider_by_id = {p["id"]: p for p in providers if isinstance(p, dict) and p.get("id")}
    ark = provider_by_id.get("ark")
    if ark and not ark.get("api_key"):
        ark["api_key"] = str(cfg.get("ark_api_key") or cfg.get("ARK_API_KEY") or "")
    team = provider_by_id.get("teamorouter")
    if team and not team.get("api_key"):
        team["api_key"] = str(cfg.get("team_router_api_key") or cfg.get("teamorouter_api_key") or "")
    ali = provider_by_id.get("aliyun_bailian")
    if ali and not ali.get("api_key"):
        ali["api_key"] = str(cfg.get("dashscope_api_key") or "")
    if ali and not ali.get("workspace_id"):
        ali["workspace_id"] = str(cfg.get("dashscope_workspace_id") or cfg.get("workspace_id") or "")
    if ali and ali.get("workspace_id") and (
        not ali.get("base_url") or "dashscope.aliyuncs.com" in str(ali.get("base_url"))
    ):
        ali["base_url"] = (
            f"https://{ali['workspace_id']}.cn-beijing.maas.aliyuncs.com/api/v1"
        )

    # Ensure each model has the expected top-level fields.
    for model in models:
        if not isinstance(model, dict):
            continue
        model.setdefault("enabled", True)
        model.setdefault("defaultParams", {})
        model.setdefault("params", {})
        model.setdefault("limits", {})
        model.setdefault("pricing", {})
        if model.get("capability") == "video" and model.get("adapter") == "ark_video":
            model["defaultParams"].setdefault("generateAudio", True)
            model["defaultParams"].setdefault("watermark", False)
            model["params"].setdefault("generateAudio", {"type": "boolean"})
            model["params"].setdefault("watermark", {"type": "boolean"})
            model["params"].setdefault("seed", {"type": "range", "min": -1, "max": 2147483647, "step": 1})
        if model.get("capability") == "image" and model.get("adapter") == "ark_image":
            model["defaultParams"].setdefault("watermark", False)
            model["params"].setdefault("watermark", {"type": "boolean"})
        if model.get("id") == "agnes-image-2.5-flash":
            model.update({
                "providerId": "agnes-ai",
                "capability": "image",
                "adapter": "agnes_image",
            })
            if not model.get("description"):
                model["description"] = "高信息密度图像、多参考图合成与图生图编辑"
            if str(model.get("badge") or "").strip() in {"", "M"}:
                model["badge"] = "25F"
        if model.get("id") == "agnes-video-2.5-flash":
            model.update({
                "providerId": "agnes-ai",
                "capability": "video",
                "adapter": "agnes_video",
            })
            if not model.get("description"):
                model["description"] = "文生视频、首尾帧控制与图片/音频参考"
            if str(model.get("badge") or "").strip() in {"", "M"}:
                model["badge"] = "25F"
        if model.get("id") == "cosyvoice-v3-5-flash":
            preview_spec = model.setdefault("params", {}).setdefault("previewDuration", {})
            preview_spec.update({"type": "range", "min": 0.5, "max": 4.5, "step": 0.5})
            model.setdefault("defaultParams", {})["previewDuration"] = min(
                4.5,
                max(0.5, float(model["defaultParams"].get("previewDuration") or 4.5)),
            )
        if model.get("id") == "doubao-seedream-5-0-pro":
            # Seedream 5.0 Pro rejects images below 921600 pixels. 1K 宽幅尺寸
            # (1024x576, 1024x438) are below that hard floor.
            model["params"]["clarity"]["options"] = ["1.5K", "2K", "4K"]
            if model["defaultParams"].get("clarity") == "1K":
                model["defaultParams"]["clarity"] = "1.5K"
    if migrate and merged != cfg:
        save_config(merged)
    return merged


def save_config(cfg: dict[str, Any]) -> None:
    payload = _encrypt_config_secrets(cfg)
    path = _local_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _hint(key: str) -> str:
    if len(key) <= 8:
        return "••••••"
    return f"••••••{key[-4:]}"


def _public_provider(provider: dict[str, Any]) -> dict[str, Any]:
    key = str(provider.get("api_key") or "")
    return {
        "id": provider.get("id", ""),
        "name": provider.get("name", ""),
        "protocol": provider.get("protocol", "openai"),
        "base_url": provider.get("base_url", ""),
        "workspace_id": provider.get("workspace_id", ""),
        "enabled": provider.get("enabled", True) is not False,
        "has_api_key": bool(key.strip()),
        "api_key_hint": _hint(key) if key.strip() else "",
        "builtin": provider.get("id") in {item["id"] for item in DEFAULT_PROVIDERS},
    }


def _public_storage(storage: dict[str, Any]) -> dict[str, Any]:
    access_key = str(storage.get("access_key") or "")
    secret_key = str(storage.get("secret_key") or "")
    imgbb = storage.get("imgbb") if isinstance(storage.get("imgbb"), dict) else {}
    imgbb_key = str(imgbb.get("api_key") or "")
    enabled = bool(storage.get("enabled"))
    provider = str(storage.get("provider") or "tos")
    tos_ready = bool(
        enabled
        and provider == "tos"
        and storage.get("bucket")
        and storage.get("region") == "cn-beijing"
        and storage.get("endpoint")
        and storage.get("internal_endpoint")
        and access_key.strip()
        and secret_key.strip()
    )
    imgbb_ready = bool(enabled and provider == "imgbb" and imgbb_key.strip())
    ready = tos_ready or imgbb_ready
    return {
        "enabled": enabled,
        "provider": provider,
        "bucket": str(storage.get("bucket") or ""),
        "region": str(storage.get("region") or "cn-beijing"),
        "endpoint": str(storage.get("endpoint") or ""),
        "internal_endpoint": str(storage.get("internal_endpoint") or ""),
        "prefix": str(storage.get("prefix") or ""),
        "url_ttl_seconds": int(storage.get("url_ttl_seconds") or 3600),
        "auto_persist_images": storage.get("auto_persist_images", True) is not False,
        "require_for_ark_portrait": storage.get("require_for_ark_portrait", True) is not False,
        "has_access_key": bool(access_key.strip()),
        "access_key_hint": _hint(access_key) if access_key.strip() else "",
        "has_secret_key": bool(secret_key.strip()),
        "secret_key_hint": _hint(secret_key) if secret_key.strip() else "",
        "ready": ready,
        "tos_ready": tos_ready,
        "imgbb": {
            "enabled": bool(enabled and provider == "imgbb"),
            "has_api_key": bool(imgbb_key.strip()),
            "api_key_hint": _hint(imgbb_key) if imgbb_key.strip() else "",
            "auto_persist_images": imgbb.get("auto_persist_images", True) is not False,
            "ready": imgbb_ready,
        },
    }


def _public_budget(budget: dict[str, Any]) -> dict[str, Any]:
    def number(value: Any) -> float:
        try:
            return max(0.0, float(value or 0))
        except (TypeError, ValueError):
            return 0.0
    return {
        "enabled": bool(budget.get("enabled")),
        "currency": str(budget.get("currency") or "CNY").upper(),
        "dailyLimit": number(budget.get("dailyLimit")),
        "monthlyLimit": number(budget.get("monthlyLimit")),
        "maxRequestCost": number(budget.get("maxRequestCost")),
        "allowUnknownCost": bool(budget.get("allowUnknownCost")),
    }


def budget_config() -> dict[str, Any]:
    cfg = load_config()
    return _public_budget(
        cfg.get("budget") if isinstance(cfg.get("budget"), dict) else DEFAULT_BUDGET,
    )


def save_budget_config(input_data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(input_data, dict):
        raise ValueError("预算配置必须是对象")
    source = input_data.get("budget") if isinstance(input_data.get("budget"), dict) else input_data
    budget = _public_budget({**DEFAULT_BUDGET, **source})
    if budget["currency"] not in {"CNY", "USD"}:
        raise ValueError("预算币种仅支持 CNY 或 USD")
    cfg = load_config()
    cfg["budget"] = budget
    save_config(cfg)
    return budget


def storage_config() -> dict[str, Any]:
    cfg = load_config()
    storage = cfg.get("storage")
    return copy.deepcopy(storage if isinstance(storage, dict) else DEFAULT_STORAGE)


def public_storage_config() -> dict[str, Any]:
    return _public_storage(storage_config())


def _normalize_endpoint(value: Any, default: str) -> str:
    raw = str(value or default).strip()
    if "://" in raw:
        raw = urllib.parse.urlsplit(raw).netloc
    return raw.split("/", 1)[0].strip().lower()


def save_storage_config(input_data: dict[str, Any]) -> dict[str, Any]:
    cfg = load_config()
    source = input_data.get("storage") if isinstance(input_data.get("storage"), dict) else input_data
    current = cfg.setdefault("storage", copy.deepcopy(DEFAULT_STORAGE))
    provider = str(source.get("provider", current.get("provider") or "tos")).strip().lower()
    if provider not in {"tos", "imgbb"}:
        raise ValueError("存储类型只能是 TOS 或 ImgBB")
    bucket = str(source.get("bucket", current.get("bucket") or "")).strip()
    region = str(source.get("region", current.get("region") or "cn-beijing")).strip()
    endpoint = _normalize_endpoint(
        source.get("endpoint", current.get("endpoint")),
        "tos-cn-beijing.volces.com",
    )
    internal_endpoint = _normalize_endpoint(
        source.get("internal_endpoint", current.get("internal_endpoint")),
        "tos-cn-beijing.ivolces.com",
    )
    prefix = str(source.get("prefix", current.get("prefix") or "")).strip().strip("/")
    if prefix:
        prefix += "/"
    try:
        url_ttl = int(source.get("url_ttl_seconds", current.get("url_ttl_seconds") or 3600))
    except (TypeError, ValueError):
        raise ValueError("预签名有效期必须是整数秒")
    url_ttl = max(60, min(604800, url_ttl))
    enabled = bool(source.get("enabled", current.get("enabled", False)))
    access_key = str(current.get("access_key") or "")
    secret_key = str(current.get("secret_key") or "")
    imgbb_current = current.get("imgbb") if isinstance(current.get("imgbb"), dict) else {}
    imgbb_api_key = str(imgbb_current.get("api_key") or "")
    if isinstance(source.get("imgbb_api_key"), str) and source["imgbb_api_key"].strip():
        imgbb_api_key = source["imgbb_api_key"].strip()
    if source.get("imgbb_api_key_clear") is True:
        imgbb_api_key = ""
    imgbb_auto_persist = bool(source.get(
        "imgbb_auto_persist_images",
        imgbb_current.get("auto_persist_images", True),
    ))
    if isinstance(source.get("access_key"), str) and source["access_key"].strip():
        access_key = source["access_key"].strip()
    if isinstance(source.get("secret_key"), str) and source["secret_key"].strip():
        secret_key = source["secret_key"].strip()
    if enabled and provider == "tos":
        missing = [
            label for label, value in (
                ("Bucket", bucket),
                ("Region", region),
                ("Endpoint", endpoint),
                ("内网 Endpoint", internal_endpoint),
                ("Access Key", access_key),
                ("Secret Key", secret_key),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"启用 TOS 前请填写：{'、'.join(missing)}")
        if region != "cn-beijing":
            raise ValueError("方舟可信产物要求 TOS 使用 cn-beijing，内网 Endpoint 也必须位于该地域")
        if not internal_endpoint.endswith("ivolces.com"):
            raise ValueError("内网 Endpoint 必须使用 TOS 内网域名（通常以 .ivolces.com 结尾）")
    if enabled and provider == "imgbb" and not imgbb_api_key:
        raise ValueError("启用 ImgBB 前请填写 API Key")
    current.update({
        "enabled": enabled,
        "provider": provider,
        "bucket": bucket[:128],
        "region": region[:64],
        "endpoint": endpoint[:255],
        "internal_endpoint": internal_endpoint[:255],
        "prefix": prefix[:180],
        "access_key": access_key[:2048],
        "secret_key": secret_key[:4096],
        "url_ttl_seconds": url_ttl,
        "auto_persist_images": bool(source.get(
            "auto_persist_images",
            current.get("auto_persist_images", True),
        )),
        "require_for_ark_portrait": bool(source.get(
            "require_for_ark_portrait",
            current.get("require_for_ark_portrait", True),
        )),
        "imgbb": {
            "api_key": imgbb_api_key[:2048],
            "auto_persist_images": imgbb_auto_persist,
        },
    })
    context = tenant_context.current()
    if context is not None:
        import tenant_registry

        if enabled and provider == "tos":
            tenant_registry.claim_storage_binding(
                context.tenant_id,
                bucket=current["bucket"],
                region=current["region"],
                prefix=current["prefix"],
                access_key=current["access_key"],
            )
        else:
            tenant_registry.release_storage_binding(context.tenant_id)
    save_config(cfg)
    return public_storage_config()


def _public_model(model: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: copy.deepcopy(model.get(key))
        for key in (
            "id", "providerId", "capability", "label", "remoteModel", "adapter",
            "description", "badge", "enabled", "defaultParams", "params", "limits",
            "pricing",
        )
    }
    result["limits"] = _normalize_model_limits(result.get("limits") or {})
    result["builtin"] = model.get("id") in {item["id"] for item in DEFAULT_MODELS}
    return result


def public_catalog() -> dict[str, Any]:
    cfg = load_config()
    providers = [_public_provider(p) for p in cfg.get("providers", []) if isinstance(p, dict)]
    provider_ids = {p["id"] for p in providers if p.get("enabled", True)}
    models = [
        _public_model(m)
        for m in cfg.get("models", [])
        if isinstance(m, dict) and m.get("enabled", True) and m.get("providerId") in provider_ids
    ]
    return {
        "providers": providers,
        "models": models,
        "storage": _public_storage(
            cfg.get("storage") if isinstance(cfg.get("storage"), dict) else DEFAULT_STORAGE,
        ),
        "budget": _public_budget(
            cfg.get("budget") if isinstance(cfg.get("budget"), dict) else DEFAULT_BUDGET,
        ),
    }


def config_status() -> dict[str, Any]:
    cfg = load_config()
    catalog = public_catalog()
    providers = {p["id"]: p for p in catalog["providers"]}
    ark = providers.get("ark", {})
    team = providers.get("teamorouter", {})
    ali = providers.get("aliyun_bailian", {})
    return {
        "ark_api_key": ark.get("has_api_key", False),
        "seedream_model": "doubao-seedream-5-0-pro-260628",
        "seedream_size": "1536x1536",
        "seedance_model": "doubao-seedance-2-0-mini-260615",
        "seedance_ratio": "9:16",
        "tts_model": "cosyvoice-v3.5-flash",
        "text_model": "deepseek-flash-1017",
        "team_router_api_key": team.get("has_api_key", False),
        "dashscope_api_key": ali.get("has_api_key", False),
        "providers": catalog["providers"],
        "models": catalog["models"],
        "storage": catalog["storage"],
        "budget": catalog["budget"],
    }


def normalize_provider_id(value: str) -> str:
    raw = str(value or "").strip().lower()
    slug = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-_")
    return slug[:48]


def upsert_provider(input_data: dict[str, Any]) -> dict[str, Any]:
    cfg = load_config()
    source = input_data.get("provider") if isinstance(input_data.get("provider"), dict) else input_data
    provider_id = normalize_provider_id(str(source.get("id") or source.get("name") or ""))
    if not provider_id:
        raise ValueError("供应商 ID 不能为空")
    providers = cfg.setdefault("providers", [])
    cfg["deletedProviders"] = [
        item for item in (cfg.get("deletedProviders") or []) if item != provider_id
    ]
    current = next((p for p in providers if p.get("id") == provider_id), None)
    if current is None:
        current = {"id": provider_id, "enabled": True, "api_key": ""}
        providers.append(current)
    name = str(source.get("name") or current.get("name") or provider_id).strip()
    base_url = str(source.get("base_url") or current.get("base_url") or "").strip().rstrip("/")
    protocol = str(source.get("protocol") or current.get("protocol") or "openai").strip()
    if protocol not in {"ark", "openai", "dashscope"}:
        raise ValueError("protocol 仅支持 ark / openai / dashscope")
    if not base_url or not re.match(r"^https?://", base_url):
        raise ValueError("base_url 必须是 http:// 或 https:// 地址")
    current.update({
        "name": name[:80],
        "protocol": protocol,
        "base_url": base_url[:500],
        "enabled": bool(source.get("enabled", current.get("enabled", True))),
    })
    if "workspace_id" in source:
        current["workspace_id"] = str(source.get("workspace_id") or "").strip()[:128]
    if isinstance(source.get("api_key"), str) and source["api_key"].strip():
        current["api_key"] = source["api_key"].strip()[:8192]
    save_config(cfg)
    return public_catalog()


def delete_provider(provider_id: str) -> dict[str, Any]:
    cfg = load_config()
    providers = cfg.setdefault("providers", [])
    if not any(p.get("id") == provider_id for p in providers):
        raise ValueError("供应商不存在")
    cfg["providers"] = [p for p in providers if p.get("id") != provider_id]
    cfg["models"] = [m for m in cfg.get("models", []) if m.get("providerId") != provider_id]
    deleted = set(cfg.get("deletedProviders") or [])
    deleted.add(provider_id)
    cfg["deletedProviders"] = sorted(deleted)
    save_config(cfg)
    return public_catalog()


ADAPTER_CAPABILITIES = {
    "ark_image": "image",
    "openai_image": "image",
    "agnes_image": "image",
    "ark_video": "video",
    "agnes_video": "video",
    "newapi_video": "video",
    "openai_chat": "text",
    "dashscope_tts": "audio",
}
VALID_ADAPTERS = set(ADAPTER_CAPABILITIES)


def normalize_model_id(value: str) -> str:
    raw = str(value or "").strip().lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-")
    return slug[:96]


MODEL_LIMIT_KEYS = (
    "maxRefs",
    "maxImages",
    "maxVideos",
    "maxAudios",
    "maxTextRefs",
    "maxPromptChars",
    "maxConcurrency",
)


def _normalize_model_limits(value: dict[str, Any]) -> dict[str, Any]:
    limits = copy.deepcopy(value)
    for key in MODEL_LIMIT_KEYS:
        if key not in limits:
            continue
        try:
            limits[key] = max(0, int(limits[key]))
        except (TypeError, ValueError):
            limits.pop(key, None)
    reference_keys = ("maxImages", "maxVideos", "maxAudios", "maxTextRefs")
    if any(key in limits for key in reference_keys):
        limits["maxRefs"] = sum(int(limits.get(key) or 0) for key in reference_keys)
    return limits


def upsert_model(input_data: dict[str, Any]) -> dict[str, Any]:
    cfg = load_config()
    source = input_data.get("model") if isinstance(input_data.get("model"), dict) else input_data
    provider_id = str(source.get("providerId") or source.get("provider_id") or "").strip()
    providers = {p.get("id") for p in cfg.get("providers", []) if isinstance(p, dict)}
    if provider_id not in providers:
        raise ValueError("providerId 不存在")
    capability = str(source.get("capability") or "").strip()
    if capability not in {"image", "video", "audio", "text"}:
        raise ValueError("capability 必须是 image / video / audio / text")
    adapter = str(source.get("adapter") or "").strip()
    if adapter not in VALID_ADAPTERS:
        raise ValueError(f"adapter 仅支持 {', '.join(sorted(VALID_ADAPTERS))}")
    adapter_capability = ADAPTER_CAPABILITIES[adapter]
    if capability != adapter_capability:
        raise ValueError(f"适配器 {adapter} 只能用于 {adapter_capability} 模型")
    model_id = normalize_model_id(
        str(source.get("id") or source.get("remoteModel") or source.get("label") or "")
    )
    if not model_id:
        raise ValueError("模型 ID 不能为空")
    remote_model = str(source.get("remoteModel") or source.get("remote_model") or model_id).strip()
    if not remote_model:
        raise ValueError("远程模型 ID 不能为空")
    models = cfg.setdefault("models", [])
    cfg["deletedModels"] = [
        item for item in (cfg.get("deletedModels") or []) if item != model_id
    ]
    current = next((m for m in models if m.get("id") == model_id), None)
    if current is None:
        current = {"id": model_id, "enabled": True}
        models.append(current)
    current.update({
        "providerId": provider_id,
        "capability": capability,
        "adapter": adapter,
        "label": str(source.get("label") or remote_model).strip()[:120],
        "remoteModel": remote_model[:240],
        "description": str(source.get("description") or "").strip()[:500],
        "badge": str(source.get("badge") or capability[:4]).strip()[:16],
        "enabled": bool(source.get("enabled", current.get("enabled", True))),
    })
    defaults = source.get("defaultParams")
    schema = source.get("params")
    limits = source.get("limits")
    pricing = source.get("pricing")
    if isinstance(defaults, dict):
        current["defaultParams"] = defaults
    elif "defaultParams" not in current:
        current["defaultParams"] = {}
    if isinstance(schema, dict):
        current["params"] = schema
    elif "params" not in current:
        current["params"] = {}
    if isinstance(limits, dict):
        current["limits"] = _normalize_model_limits(limits)
    elif "limits" not in current:
        current["limits"] = {}
    if isinstance(pricing, dict):
        current["pricing"] = copy.deepcopy(pricing)
    elif "pricing" not in current:
        current["pricing"] = {}
    save_config(cfg)
    return public_catalog()


def delete_model(model_id: str) -> dict[str, Any]:
    cfg = load_config()
    models = cfg.setdefault("models", [])
    if not any(m.get("id") == model_id for m in models):
        raise ValueError("模型不存在")
    cfg["models"] = [m for m in models if m.get("id") != model_id]
    deleted = set(cfg.get("deletedModels") or [])
    deleted.add(model_id)
    cfg["deletedModels"] = sorted(deleted)
    save_config(cfg)
    return public_catalog()


def resolve_model(
    provider_id: str | None,
    model_ref: str | None,
    capability: str,
    *,
    require_api_key: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    cfg = load_config()
    providers = {
        p.get("id"): p for p in cfg.get("providers", [])
        if isinstance(p, dict) and p.get("enabled", True) is not False
    }
    candidates = [
        m for m in cfg.get("models", [])
        if isinstance(m, dict)
        and m.get("enabled", True) is not False
        and m.get("capability") == capability
        and m.get("providerId") in providers
    ]
    raw = str(model_ref or "").strip()
    legacy_aliases = {
        "doubao-seedream-5-0-pro-260628": "doubao-seedream-5-0-pro",
        "doubao-seedance-2-0-260128": "doubao-seedance-2-0",
        "doubao-seedance-2-0-mini": "doubao-seedance-2-0-mini",
        "doubao-seedance-2-0-mini-260615": "doubao-seedance-2-0-mini",
        "doubao-seedance-2-0-fast": "doubao-seedance-2-0-fast",
        "doubao-seedance-2-0-fast-260128": "doubao-seedance-2-0-fast",
        "doubao-seedance-2-5": "doubao-seedance-2-5",
        "doubao-seedance-2-5-260628": "doubao-seedance-2-5",
        "cosyvoice-v3.5-flash": "cosyvoice-v3-5-flash",
    }
    canonical = legacy_aliases.get(raw, raw)
    model = None
    if raw:
        if provider_id:
            model = next(
                (m for m in candidates if m.get("providerId") == provider_id
                 and canonical in {m.get("id"), m.get("remoteModel"), m.get("label")}),
                None,
            )
        if model is None:
            model = next(
                (m for m in candidates
                 if canonical in {m.get("id"), m.get("remoteModel"), m.get("label")}),
                None,
            )
    if raw and model is None:
        # Legacy inference keeps old nodes usable without mutating their payload.
        if capability == "video" and raw.startswith("doubao-seedance"):
            target_provider = provider_id or "ark"
            model = next(
                (m for m in candidates if m.get("providerId") == target_provider
                 and m.get("remoteModel") == raw),
                None,
            )
            if model is None:
                model = next(
                    (m for m in candidates if m.get("providerId") == target_provider),
                    None,
                )
        elif capability == "image" and raw.startswith("doubao-seedream"):
            model = next(
                (m for m in candidates if m.get("providerId") == (provider_id or "ark")),
                None,
            )
    if model is None:
        if raw:
            raise RuntimeError(f"模型 {raw} 不可用、已禁用或不属于当前能力，请重新选择")
        model = next(
            (m for m in candidates if not provider_id or m.get("providerId") == provider_id),
            None,
        )
    if model is None:
        raise RuntimeError(f"没有可用的 {capability} 模型，请在「模型接口」中启用或添加")
    provider = providers.get(model.get("providerId"))
    if not provider:
        raise RuntimeError(f"供应商 {model.get('providerId')} 未启用")
    key = str(provider.get("api_key") or "").strip()
    if require_api_key and not key:
        raise RuntimeError(f"{provider.get('name') or provider.get('id')} 尚未配置 API Key")
    resolved_model = copy.deepcopy(model)
    resolved_model["limits"] = _normalize_model_limits(resolved_model.get("limits") or {})
    return copy.deepcopy(provider), resolved_model, cfg


def provider_key(provider: dict[str, Any]) -> str:
    return str(provider.get("api_key") or "").strip()


class ProviderRequestTimeoutError(RuntimeError):
    """Raised when a provider request exceeds the configured probe timeout."""


def _request_status(
    url: str,
    key: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    *,
    timeout: float = 15,
    read_limit: int | None = None,
) -> tuple[int, str]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Connection": "close",
            "Content-Type": "application/json",
        },
    )
    try:
        with network_security.safe_urlopen(request, timeout=timeout) as response:
            body_bytes = (
                response.read(read_limit)
                if read_limit is not None
                else response.read()
            )
            return response.status, body_bytes.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body_bytes = (
            exc.read(read_limit)
            if read_limit is not None
            else exc.read()
        )
        return exc.code, body_bytes.decode("utf-8", errors="replace")
    except TimeoutError as exc:
        raise ProviderRequestTimeoutError(
            "供应商接口响应超时，请检查网络、接口地址或稍后重试"
        ) from exc
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc


def test_provider(provider_id: str) -> dict[str, Any]:
    cfg = load_config()
    provider = next(
        (p for p in cfg.get("providers", []) if p.get("id") == provider_id),
        None,
    )
    if not provider:
        return {"ok": False, "message": "供应商不存在"}
    key = provider_key(provider)
    if not key:
        return {"ok": False, "message": "尚未配置 API Key"}
    base = str(provider.get("base_url") or "").rstrip("/")
    protocol = provider.get("protocol")
    started = time.time()
    # Connection tests must never submit a generation request. A paid TTS/image/video
    # probe would violate the "test means non-billing" contract and can charge users
    # just for clicking a diagnostics button.
    test_path = str(provider.get("test_path") or "/models").strip()
    if not test_path.startswith("/"):
        test_path = "/" + test_path
    try:
        # Probe only the response headers and a few KB of body. Some providers
        # expose very large /models payloads; a connectivity test must not
        # download the whole catalogue or it will look like a timeout.
        status, text = _request_status(f"{base}{test_path}", key, read_limit=4096)
    except ProviderRequestTimeoutError as exc:
        return {
            "ok": False,
            "message": str(exc),
            "elapsedMs": round((time.time() - started) * 1000),
            "timeout": True,
        }
    except RuntimeError as exc:
        return {
            "ok": False,
            "message": f"连接失败：{exc}",
            "elapsedMs": round((time.time() - started) * 1000),
        }
    ok = status == 200
    elapsed = round((time.time() - started) * 1000)
    if ok:
        return {"ok": True, "message": f"连接成功（{elapsed}ms）", "elapsedMs": elapsed}
    detail = ""
    try:
        parsed = json.loads(text)
        detail = (
            (parsed.get("error") or {}).get("message")
            if isinstance(parsed.get("error"), dict)
            else parsed.get("message")
        ) or ""
    except Exception:
        detail = text[:240]
    if status in (404, 405) and protocol in {"ark", "dashscope"}:
        return {
            "ok": False,
            "message": (
                f"接口可达（HTTP {status}），但当前地址没有 {test_path} 探针；"
                "密钥未提交生成请求，实际权限将在首次生成时验证"
            ),
            "elapsedMs": elapsed,
            "probeMissing": True,
        }
    return {"ok": False, "message": f"连接失败 HTTP {status}{('：' + detail) if detail else ''}"}


def _model_ids_from_response(payload: Any) -> list[str]:
    if isinstance(payload, list):
        raw_items = payload
    elif isinstance(payload, dict):
        candidates = (
            payload.get("data")
            or payload.get("models")
            or payload.get("result")
            or payload.get("items")
        )
        raw_items = candidates if isinstance(candidates, list) else []
    else:
        raw_items = []
    ids: list[str] = []
    for item in raw_items:
        value = item.get("id") if isinstance(item, dict) else item
        text = str(value or "").strip()
        if text:
            ids.append(text)
    return list(dict.fromkeys(ids))


def _model_endpoint_types_from_response(payload: Any) -> dict[str, list[str]]:
    if isinstance(payload, dict):
        candidates = (
            payload.get("data")
            or payload.get("models")
            or payload.get("result")
            or payload.get("items")
        )
        raw_items = candidates if isinstance(candidates, list) else []
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []
    endpoint_types: dict[str, list[str]] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        values = (
            item.get("supported_endpoint_types")
            or item.get("supportedEndpointTypes")
            or item.get("endpoint_types")
            or []
        )
        if isinstance(values, list):
            endpoint_types[model_id] = [str(value) for value in values if str(value).strip()]
    return endpoint_types


def _parse_model_payload(prefix: str) -> dict[str, Any]:
    """Parse a truncated OpenAI model-list prefix by keeping complete items."""
    text = str(prefix or "").strip()
    data_start = text.find("[")
    if data_start < 0:
        raise RuntimeError("供应商返回的模型列表不是 JSON")
    # Trim back to the last complete object before the truncation point.
    object_end = text.rfind("}")
    if object_end < data_start:
        raise RuntimeError("供应商返回的模型列表不是 JSON")
    candidate = text[: object_end + 1] + "]}"
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        # Try each earlier object boundary; relay payloads may contain nested
        # objects, so the first complete top-level row is still useful.
        cursor = object_end
        while cursor > data_start:
            cursor = text.rfind("}", 0, cursor)
            if cursor < data_start:
                break
            try:
                payload = json.loads(text[: cursor + 1] + "]}")
            except json.JSONDecodeError:
                continue
            if _model_ids_from_response(payload):
                return payload
        raise RuntimeError("供应商返回的模型列表不是 JSON")
    if not _model_ids_from_response(payload):
        raise RuntimeError("供应商返回成功，但没有识别到模型 id 字段")
    return payload


def _infer_discovered_model(
    provider: dict[str, Any],
    remote_model: str,
    index: int,
    supported_endpoint_types: list[str] | None = None,
) -> dict[str, Any]:
    """Create a conservative editable draft from a provider /models row."""
    name = remote_model.lower()
    endpoint_marker = " ".join(
        str(item).lower() for item in (supported_endpoint_types or [])
    )
    marker = " ".join([
        name,
        str(provider.get("name") or "").lower(),
        str(provider.get("id") or "").lower(),
        endpoint_marker,
    ])
    if "openai_video" in endpoint_marker:
        capability = "video"
        adapter = "newapi_video"
    elif any(word in endpoint_marker for word in (
        "seedance", "suanli-seedance", "video",
    )) or any(word in marker for word in (
        "seedance", "seedream", "kling", "wan", "video", "veo", "sora",
        "hailuo", "pika", "runway", "minimax", "cogvideo", "luma",
    )):
        capability = "video"
    elif any(word in marker for word in (
        "tts", "speech", "voice", "audio", "sound", "cosyvoice",
    )):
        capability = "audio"
    elif any(word in marker for word in (
        "embedding", "embed", "rerank",
    )):
        capability = "text"
    elif any(word in marker for word in (
        "chat", "text", "deepseek", "qwen", "gpt", "claude", "glm",
        "moonshot", "doubao", "llama", "mistral",
    )):
        capability = "text"
    else:
        capability = "image"
    if "adapter" not in locals():
        adapter = {
            "video": "ark_video" if provider.get("protocol") == "ark" else "agnes_video",
            "image": "ark_image" if provider.get("protocol") == "ark" else "openai_image",
            "audio": "dashscope_tts" if provider.get("protocol") == "dashscope" else "dashscope_tts",
            "text": "openai_chat",
        }[capability]
    label = remote_model
    model_id = normalize_model_id(remote_model)
    if capability == "video":
        default_params = {"ratio": "9:16", "resolution": "480P", "duration": 5}
        params = {
            "ratio": _select(["1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "adaptive"]),
            "resolution": _select(["480P", "720P", "1080P"]),
            "duration": {"type": "range", "min": 1, "max": 30, "step": 1, "options": [4, 5, 10, 15, 20, 30]},
        }
    elif capability == "image":
        default_params = {"ratio": "1:1", "size": "1024x1024"}
        params = {
            "ratio": _select(["1:1", "16:9", "9:16", "4:3", "3:4", "21:9"]),
            "size": {"type": "text"},
        }
    elif capability == "audio":
        default_params = {"format": "mp3", "duration": 4.5}
        params = {
            "format": _select(["mp3", "wav"]),
            "duration": {"type": "range", "min": 0.5, "max": 30, "step": 0.5},
        }
    else:
        default_params = {"temperature": 0.7}
        params = {
            "temperature": {"type": "range", "min": 0, "max": 2, "step": 0.1},
        }
    return {
        "id": model_id,
        "providerId": provider.get("id"),
        "capability": capability,
        "adapter": adapter,
        "remoteModel": remote_model,
        "label": label,
        "description": f"从 {provider.get('name') or provider.get('id')} 模型列表自动发现",
        "badge": str(index + 1),
        "enabled": True,
        "defaultParams": default_params,
        "params": params,
        "limits": {},
        "pricing": {},
        "discovered": True,
    }


def discover_provider_models(provider_id: str) -> dict[str, Any]:
    """Fetch a provider's model list and return drafts without persisting them."""
    cfg = load_config()
    provider = next(
        (p for p in cfg.get("providers", []) if p.get("id") == provider_id),
        None,
    )
    if not provider:
        raise ValueError("供应商不存在")
    key = provider_key(provider)
    if not key:
        raise ValueError("尚未配置 API Key")
    base = str(provider.get("base_url") or "").rstrip("/")
    protocol = str(provider.get("protocol") or "openai")
    test_path = str(provider.get("test_path") or "/models").strip()
    if not test_path.startswith("/"):
        test_path = f"/{test_path}"
    candidates = [f"{base}{test_path}"]
    status = 0
    text = ""
    last_timeout: ProviderRequestTimeoutError | None = None
    for url in dict.fromkeys(candidates):
        try:
            status, text = _request_status(
                url,
                key,
                timeout=30,
                read_limit=1024 * 1024,
            )
            if status == 200:
                break
        except ProviderRequestTimeoutError as exc:
            last_timeout = exc
            continue
    if last_timeout is not None and status != 200:
        raise last_timeout
    if status != 200:
        detail = text[:240]
        raise RuntimeError(f"模型列表拉取失败 HTTP {status}{('：' + detail) if detail else ''}")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = _parse_model_payload(text)
    ids = _model_ids_from_response(payload)
    if not ids:
        raise RuntimeError("供应商返回成功，但没有识别到模型 id 字段")
    endpoint_types = _model_endpoint_types_from_response(payload)
    existing = {
        str(model.get("remoteModel") or model.get("id") or "")
        for model in cfg.get("models", [])
        if isinstance(model, dict) and model.get("providerId") == provider_id
    }
    drafts = [
        _infer_discovered_model(
            provider,
            remote_model,
            index,
            endpoint_types.get(remote_model),
        )
        for index, remote_model in enumerate(ids)
    ]
    for draft in drafts:
        draft["alreadyConfigured"] = draft["remoteModel"] in existing or draft["id"] in existing
    return {
        "ok": True,
        "providerId": provider_id,
        "models": drafts,
        "count": len(drafts),
    }
