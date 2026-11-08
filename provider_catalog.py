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
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

import network_security
import tenant_context
import tenant_security


BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
LOCAL_CONFIG_PATH = os.path.join(BASE, "config.local.json")
MODEL_PROFILES_PATH = os.path.join(BASE, "provider_model_profiles.json")
_MODEL_PROFILES: dict[str, dict[str, Any]] | None = None
MODEL_DISCOVERY_TIMEOUT_SECONDS = 90

VIDEO_ROUTE_TEMPLATES: dict[str, dict[str, Any]] = {
    "suanli-video": {
        "submitPath": "/suanli/v1/videos",
        "queryPath": "/suanli/v1/videos/{task_id}",
        "promptField": "prompt",
        "durationField": "seconds",
        "ratioField": "aspect_ratio",
        "resolutionCase": "lower",
        "imageField": "images",
        "videoField": "video_refs",
        "audioField": "audio_refs",
        "supportsMode": False,
        "taskFields": ["id", "task_id"],
        "successStatuses": ["completed"],
        "failureStatuses": ["failed"],
    },
    "aistars-video": {
        "submitPath": "/aistars/v1/videos",
        "queryPath": "/aistars/v1/tasks/{task_id}",
        "promptField": "prompt",
        "durationField": "seconds",
        "ratioField": "aspect_ratio",
        "resolutionCase": "lower",
        "imageField": "images",
        "videoField": "videos",
        "audioField": "audios",
        "supportsMode": True,
        "taskFields": ["id", "task_id"],
        "successStatuses": ["completed"],
        "failureStatuses": ["failed"],
    },
    "hc-video": {
        "submitPath": "/hc-cn/v1/video/tasks",
        "queryPath": "/hc-cn/v1/video/tasks/{task_id}",
        "promptField": "content",
        "durationField": "duration",
        "ratioField": "ratio",
        "resolutionCase": "lower",
        "imageField": "image_url",
        "videoField": "video_url",
        "audioField": "audio_url",
        "supportsMode": False,
        "taskFields": ["task_id", "id"],
        "successStatuses": ["succeeded"],
        "failureStatuses": ["failed"],
    },
    "openai-video": {
        "submitPath": "/videos",
        "queryPath": "/videos/{task_id}",
        "promptField": "prompt",
        "durationField": "seconds",
        "ratioField": "size",
        "resolutionCase": "upper",
        "imageField": "images",
        "videoField": "videos",
        "audioField": "audios",
        "supportsMode": False,
        "taskFields": ["id", "task_id"],
        "successStatuses": ["completed", "succeeded", "success", "done"],
        "failureStatuses": ["failed", "error", "cancelled", "canceled"],
    },
}


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
        for profile in (storage.get("providers") or {}).values():
            if not isinstance(profile, dict):
                continue
            for key in ("access_key", "secret_key"):
                if profile.get(key):
                    profile[key] = tenant_security.decrypt_secret(profile[key])
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
        for profile in (storage.get("providers") or {}).values():
            if not isinstance(profile, dict):
                continue
            for key in ("access_key", "secret_key"):
                if profile.get(key):
                    profile[key] = tenant_security.encrypt_secret(profile[key])
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
        "id": "4stoken",
        "name": "4SToken",
        "protocol": "openai",
        "base_url": "https://api.4stoken.cn/v1",
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
        default_params={"ratio": "1:1", "clarity": "1.5K", "guidance": "中等"},
        params={
            "ratio": _select(["1:1", "16:9", "9:16", "4:3", "3:4", "21:9"]),
            "clarity": _select(["1K", "1.5K", "2K"]),
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
        default_params={"ratio": "9:16", "resolution": "720P", "duration": 4, "guidance": "中等"},
        params={
            "ratio": _select(["1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "adaptive"]),
            "resolution": _select(["480P", "720P", "1080P"]),
            "duration": {"type": "range", "min": 4, "max": 15, "step": 1},
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
        default_params={"ratio": "9:16", "resolution": "720P", "duration": 4, "guidance": "中等"},
        params={
            "ratio": _select(["1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "adaptive"]),
            "resolution": _select(["480P", "720P"]),
            "duration": {"type": "range", "min": 4, "max": 15, "step": 1},
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
        default_params={"ratio": "9:16", "resolution": "720P", "duration": 4, "guidance": "中等"},
        params={
            "ratio": _select(["1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "adaptive"]),
            "resolution": _select(["480P", "720P"]),
            "duration": {"type": "range", "min": 4, "max": 15, "step": 1},
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
        default_params={"ratio": "9:16", "resolution": "720P", "duration": 4, "guidance": "中等"},
        params={
            "ratio": _select(["1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "adaptive"]),
            "resolution": _select(["480P", "720P", "1080P"]),
            "duration": {"type": "range", "min": 4, "max": 30, "step": 1},
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
            "duration": {"type": "range", "min": 4, "max": 12, "step": 1},
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
    "public_domain": "",
    "prefix": "drama-canvas/",
    "access_key": "",
    "secret_key": "",
    "url_ttl_seconds": 3600,
    "auto_persist_images": True,
    "auto_persist_videos": True,
    "auto_persist_audio": True,
    "require_for_ark_portrait": True,
    "providers": {},
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


def _is_newapi_relay(provider: dict[str, Any]) -> bool:
    base_url = str(provider.get("base_url") or "").strip().lower()
    provider_id = str(provider.get("id") or "").strip().lower()
    return (
        provider_id in {"new-api", "aijusheng"}
        or "canvasproai.com" in base_url
        or "aijusheng.com" in base_url
    )


def _is_fourstoken_relay(provider: dict[str, Any]) -> bool:
    base_url = str(provider.get("base_url") or "").strip().lower()
    provider_id = str(provider.get("id") or "").strip().lower()
    return (
        provider_id in {"4stoken", "4stoken.cn"}
        or "4stoken.cn" in base_url
    )


def _is_openai_video_relay(provider: dict[str, Any]) -> bool:
    return _is_newapi_relay(provider) or _is_fourstoken_relay(provider)


def _fourstoken_model_name(value: Any) -> str | None:
    marker = str(value or "").strip().lower()
    if not marker:
        return None
    if any(
        token in marker
        for token in (
            "embedding",
            "embed-",
            "rerank",
            "whisper",
            "tts",
            "speech",
            "moderation",
        )
    ):
        return None
    if any(token in marker for token in ("seedance", "4sdance", "video")):
        return "video"
    if _looks_like_image_model(marker):
        return "image"
    return None


def _fourstoken_video_contract(remote_model: Any) -> dict[str, Any]:
    """Conservatively documented capability gate for 4SToken video models.

    The model list is the source of model IDs; OpenAPI documents the request and
    task protocol but does not expose a stable public model registry. Unknown
    contracts stay importable and are validated on the first paid request.
    """
    marker = str(remote_model or "").strip().lower()
    image_limit = 9 if "933" in marker else 4
    audio_limit = 3 if "933" in marker else 1
    if "seedance-2" in marker or "seedance-2.5" in marker:
        return {
            "durations": list(range(4, 31)),
            "resolutions": ["480P", "720P", "1080P"],
            "ratios": ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
            "images": image_limit,
            "videos": 3,
            "audios": audio_limit,
        }
    return {
        "durations": list(range(1, 31)),
        "resolutions": ["480P", "720P", "1080P"],
        "ratios": ["16:9", "9:16", "1:1"],
        "images": 4,
        "videos": 0,
        "audios": 0,
    }


def _model_profile_catalog() -> dict[str, dict[str, Any]]:
    global _MODEL_PROFILES
    if _MODEL_PROFILES is None:
        with open(MODEL_PROFILES_PATH, encoding="utf-8") as handle:
            loaded = json.load(handle)
        _MODEL_PROFILES = (
            loaded if isinstance(loaded, dict) else {}
        )
    return _MODEL_PROFILES


def _documented_model_profile(remote_model: Any) -> str:
    """Read the exact protocol profile from the generated API catalog."""
    entry = _model_profile_catalog().get(str(remote_model or "").strip())
    return str((entry or {}).get("profile") or "").strip()


def _documented_model_entry(remote_model: Any) -> dict[str, Any] | None:
    entry = _model_profile_catalog().get(str(remote_model or "").strip())
    return entry if isinstance(entry, dict) else None


def _documented_video_profile_templates() -> dict[str, dict[str, Any]]:
    return copy.deepcopy(VIDEO_ROUTE_TEMPLATES)


def documented_video_contract(remote_model: Any) -> dict[str, Any] | None:
    """Return the normalized video contract used by every bridge route."""
    return _documented_video_spec(remote_model)


def _documented_video_spec(remote_model: Any) -> dict[str, Any] | None:
    """Return the exact parameter contract documented for a video model."""
    entry = _documented_model_entry(remote_model)
    if not entry or entry.get("kind") != "video":
        return None
    profiles = _documented_video_profile_templates()
    profile_name = str(entry.get("profile") or "").strip()
    profile = profiles.get(profile_name) or {}
    raw_variants = entry.get("variants")
    variants = [
        item
        for item in raw_variants
        if isinstance(item, dict)
    ] if isinstance(raw_variants, list) else []

    # Some relay routes publish a flat per-model contract instead of variants.
    # Normalize it here so every model reaches the same validation/compiler path.
    if not variants:
        resolutions = [
            str(value).strip().upper()
            for value in entry.get("resolutions") or profile.get("resolutions") or []
            if str(value).strip()
        ]
        durations = []
        for value in entry.get("durations") or profile.get("durations") or []:
            try:
                duration = int(value)
            except (TypeError, ValueError):
                continue
            if duration not in durations:
                durations.append(duration)
        ratios = [
            str(value).strip()
            for value in entry.get("ratios") or profile.get("ratios") or []
            if str(value).strip()
        ]
        media = entry.get("media") if isinstance(entry.get("media"), dict) else profile.get("media")
        variants = [{
            "resolution": resolution,
            "seconds": durations,
            "aspectRatios": ratios,
            "maxReferences": media or {},
            "referenceTotalMax": (
                entry.get("referenceTotalMax")
                or profile.get("referenceTotalMax")
            ),
            "modes": entry.get("modes") or profile.get("modes") or [],
            "audioOnly": entry.get("audio_only", profile.get("audioOnly")),
        } for resolution in resolutions]
    if not variants:
        return None

    durations: list[int] = []
    resolutions: list[str] = []
    aspect_ratios: list[str] = []
    reference_limits = {
        "images": 0,
        "videos": 0,
        "audios": 0,
        "texts": 0,
    }
    reference_combinations: list[str] = []
    modes: list[str] = []
    reference_total_max = 0
    audio_only = False
    duration_default: int | None = None
    for variant in variants:
        for value in variant.get("seconds") or []:
            try:
                duration = int(value)
            except (TypeError, ValueError):
                continue
            if duration not in durations:
                durations.append(duration)
        resolution = str(variant.get("resolution") or "").strip()
        if resolution and resolution.upper() not in resolutions:
            resolutions.append(resolution.upper())
        for value in variant.get("aspectRatios") or []:
            ratio = str(value or "").strip()
            if ratio and ratio not in aspect_ratios:
                aspect_ratios.append(ratio)
        max_refs = variant.get("maxReferences")
        if isinstance(max_refs, dict):
            for key in reference_limits:
                try:
                    reference_limits[key] = max(
                        reference_limits[key],
                        int(max_refs.get(key) or 0),
                    )
                except (TypeError, ValueError):
                    continue
        for value in variant.get("referenceCombinations") or []:
            combination = str(value or "").strip()
            if combination and combination not in reference_combinations:
                reference_combinations.append(combination)
        for value in variant.get("modes") or []:
            mode = str(value or "").strip()
            if mode and mode not in modes:
                modes.append(mode)
        try:
            reference_total_max = max(
                reference_total_max,
                int(variant.get("referenceTotalMax") or 0),
            )
        except (TypeError, ValueError):
            pass
        audio_only = audio_only or bool(variant.get("audioOnly"))
        candidate_default = variant.get("durationDefault", variant.get("duration_default"))
        if duration_default is None and candidate_default is not None:
            try:
                duration_default = int(candidate_default)
            except (TypeError, ValueError):
                pass
    if not durations:
        durations = [int(value) for value in profile.get("durations") or []]
    if not resolutions:
        resolutions = [
            str(value).upper()
            for value in profile.get("resolutions") or []
        ]
    if not aspect_ratios:
        aspect_ratios = [
            str(value)
            for value in profile.get("ratios") or []
        ]
    if not modes:
        modes = [
            str(value)
            for value in profile.get("modes") or []
        ]
    if not reference_combinations:
        reference_combinations = [
            str(value)
            for value in profile.get("referenceCombinations") or []
        ]
    if not any(reference_limits.values()):
        reference_limits = {
            str(key): int(value)
            for key, value in (profile.get("maxReferences") or {}).items()
            if str(key) in {"images", "videos", "audios", "texts"}
        }
    if duration_default is None and durations:
        duration_default = durations[0]
    durations.sort()
    return {
        "durations": durations,
        "durationDefault": duration_default,
        "resolutions": resolutions,
        "aspectRatios": aspect_ratios,
        "maxReferences": reference_limits,
        "referenceCombinations": reference_combinations,
        "modes": modes,
        "referenceTotalMax": reference_total_max,
        "audioOnly": audio_only,
        "profile": profile_name,
    }


def _documented_video_contract(remote_model: Any) -> dict[str, Any] | None:
    """Backward-compatible name used by the video duration contract tests."""
    return _documented_video_spec(remote_model)


def _apply_documented_video_contract(
    model: dict[str, Any],
    documented: dict[str, Any],
) -> None:
    """Make saved model parameters match the provider's published API."""
    durations = [
        int(value)
        for value in documented.get("durations") or []
    ]
    resolutions = [
        str(value).upper()
        for value in documented.get("resolutions") or []
    ]
    ratios = [
        str(value)
        for value in documented.get("aspectRatios") or []
    ]
    if not durations:
        return

    defaults = copy.deepcopy(model.get("defaultParams") or {})
    schema = copy.deepcopy(model.get("params") or {})
    default_duration = _safe_int_value(
        documented.get("durationDefault"),
        durations[0],
    )
    current_duration = _safe_int_value(defaults.get("duration"), default_duration)
    defaults["duration"] = (
        current_duration if current_duration in durations else default_duration
    )
    duration_spec: dict[str, Any] = {
        "type": "range",
        "min": min(durations),
        "max": max(durations),
        "step": 1,
    }
    if durations != list(range(min(durations), max(durations) + 1)):
        duration_spec["options"] = durations
    if len(durations) == 1:
        duration_spec = {
            "type": "number",
            "default": default_duration,
            "min": durations[0],
            "max": durations[0],
        }
    schema["duration"] = duration_spec

    if resolutions:
        current_resolution = str(defaults.get("resolution") or "").upper()
        defaults["resolution"] = (
            current_resolution
            if current_resolution in resolutions
            else resolutions[0]
        )
        schema["resolution"] = _select(resolutions)
    if ratios:
        current_ratio = str(defaults.get("ratio") or "")
        defaults["ratio"] = current_ratio if current_ratio in ratios else ratios[0]
        schema["ratio"] = _select(ratios)
    modes = [
        str(value)
        for value in documented.get("modes") or []
        if str(value).strip()
    ]
    if modes:
        # A static text2video default is wrong as soon as a node has image,
        # video or audio references. Keep the documented concrete modes in
        # the schema, but use auto as the default and let the request compiler
        # resolve it from the actual reference payload.
        if "text2video" in modes and "image2video" in modes:
            current_mode = str(defaults.get("mode") or "").strip()
            defaults["mode"] = "auto" if current_mode == "text2video" else (
                current_mode if current_mode in modes else "auto"
            )
            schema["mode"] = _select(["auto", *modes])
        else:
            defaults["mode"] = (
                str(defaults.get("mode") or "") if str(defaults.get("mode") or "") in modes
                else modes[0]
            )
            schema["mode"] = _select(modes)
    else:
        defaults.pop("mode", None)
        schema.pop("mode", None)
    model["defaultParams"] = defaults
    model["params"] = schema


def _safe_int_value(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _video_profile_for_model(model: dict[str, Any], provider: dict[str, Any]) -> str:
    explicit = str(model.get("profile") or "").strip()
    if explicit:
        return explicit
    if _is_newapi_relay(provider):
        return _documented_model_profile(
            model.get("remoteModel") or model.get("id")
        )
    return ""


def _uses_newapi_image_route(remote_model: Any) -> bool:
    """Accept only image models declared by the API machine catalog."""
    entry = _documented_model_entry(remote_model)
    return bool(entry and entry.get("kind") == "image" and entry.get("profile"))


def _looks_like_image_model(value: Any) -> bool:
    marker = str(value or "").lower()
    return any(
        word in marker
        for word in (
            "gpt-image",
            "seedream",
            "nano-banana",
            "stable-diffusion",
            "sdxl",
            "qwen-image",
            "z-image",
            "dall-e",
            "dalle",
            "imagen",
            "flux",
            "image generation",
        )
    )


def _looks_like_video_model(value: Any) -> bool:
    marker = str(value or "").lower()
    return any(
        token in marker
        for token in (
            "视频",
            "video",
            "seedance",
            "sd2.",
            "sd2-",
            "wan3",
            "kling",
            "grok-imagine-video",
            "happyhorse",
        )
    )


def _capability_from_endpoint_types(values: list[str] | None) -> str | None:
    """Read the model capability from the provider's declared endpoint types."""
    tokens = {
        str(item).strip().lower()
        for item in (values or [])
        if str(item).strip()
    }
    if not tokens:
        return None
    if any(
        "video" in token
        or "seedance" in token
        or token.endswith("-video-submit")
        for token in tokens
    ):
        return "video"
    if any(
        "image" in token
        or token in {"openai_image", "image-generation-async"}
        for token in tokens
    ):
        return "image"
    if any(
        token in {"audio", "speech", "tts", "audio_speech"}
        or "audio" in token
        or "speech" in token
        for token in tokens
    ):
        return "audio"
    if any(
        token in {
            "embedding", "embeddings", "rerank",
            "openai-response", "chat", "responses", "text",
        }
        or "chat" in token
        or "embedding" in token
        or "response" in token
        for token in tokens
    ):
        return "text"
    return None


def _capability_from_ark_model(item: dict[str, Any]) -> str | None:
    task_types = {
        str(value).strip().lower()
        for value in (item.get("task_type") or [])
        if str(value).strip()
    }
    modalities = item.get("modalities")
    outputs = set()
    if isinstance(modalities, dict):
        outputs = {
            str(value).strip().lower()
            for value in (modalities.get("output_modalities") or [])
            if str(value).strip()
        }
    if outputs & {"video"} or any("video" in value for value in task_types):
        return "video"
    if outputs & {"image"} or any("imageto" in value or "texttoimage" in value for value in task_types):
        return "image"
    if outputs & {"text"} or any(
        value in {"textgeneration", "visualquestionanswering"}
        for value in task_types
    ):
        return "text"
    if "audio" in outputs or any("audio" in value for value in task_types):
        return "audio"
    return None


def _documented_provider_capability(
    provider: dict[str, Any],
    remote_model: str,
) -> str | None:
    """Capabilities from the provider's published model catalogue."""
    provider_id = str(provider.get("id") or "").strip().lower()
    model_id = str(remote_model or "").strip().lower()
    if provider_id != "agnes-ai":
        return None
    text_models = {
        "agnes-2.5-flash",
        "agnes-2.5-pro",
        "agnes-2.5-pro-beta",
        "agnes-3.0-flash",
        "agnes-3.0-pro",
    }
    image_models = {
        "agnes-image-2.0-flash",
        "agnes-image-2.1-flash",
        "agnes-image-2.5-flash",
    }
    video_models = {
        "agnes-video-2.5",
        "agnes-video-2.5-flash",
    }
    if model_id in text_models:
        return "text"
    if model_id in image_models:
        return "image"
    if model_id in video_models:
        return "video"
    return None


def _fourstoken_documented_video_spec(remote_model: Any) -> dict[str, Any] | None:
    marker = str(remote_model or "").strip().lower()
    if "video" not in marker and "seedance" not in marker:
        return None
    contract = _fourstoken_video_contract(remote_model)
    return {
        "durations": list(contract["durations"]),
        "durationDefault": contract["durations"][0],
        "resolutions": list(contract["resolutions"]),
        "ratios": list(contract["ratios"]),
        "maxReferences": {
            "images": int(contract["images"]),
            "videos": int(contract["videos"]),
            "audios": int(contract["audios"]),
            "texts": 4,
        },
        "referenceTotalMax": 0,
        "referenceCombinations": [],
        "modes": [],
        "audioOnly": False,
        "profile": "fourstoken-video",
    }


def documented_fourstoken_video_contract(remote_model: Any) -> dict[str, Any] | None:
    return _fourstoken_documented_video_spec(remote_model)


def fourstoken_video_contract(remote_model: Any) -> dict[str, Any]:
    return _fourstoken_video_contract(remote_model)


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
    if str((merged["storage"] or {}).get("provider") or "") == "imgbb":
        # ImgBB was retired after repeated maintenance failures. Preserve the
        # TOS credentials already present in the config and migrate the active
        # backend instead of leaving the only storage in a dead state.
        merged["storage"]["provider"] = "tos"
        merged["storage"].pop("imgbb", None)
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
            model["params"]["clarity"]["options"] = ["1K", "1.5K", "2K"]
            clarity = str(model["defaultParams"].get("clarity") or "").upper()
            model["defaultParams"]["clarity"] = (
                clarity if clarity in {"1K", "1.5K", "2K"} else "1.5K"
            )
        if (
            str(model.get("remoteModel") or "") == "即梦-65-seedream-5-pro"
            and model.get("capability") == "image"
        ):
            # New API relay's documented 65-pro image contract:
            # aspect_ratio + clarity=1K|2K. This is not Volcengine Ark.
            model["defaultParams"].pop("duration", None)
            model["defaultParams"].pop("watermark", None)
            model["defaultParams"]["ratio"] = str(model["defaultParams"].get("ratio") or "1:1")
            clarity = str(
                model["defaultParams"].get("clarity")
                or model["defaultParams"].get("resolution")
                or "2K"
            ).upper()
            model["defaultParams"]["clarity"] = clarity if clarity in {"1K", "2K", "4K"} else "2K"
            model["defaultParams"].pop("resolution", None)
            params = model.setdefault("params", {})
            params["ratio"] = {
                "type": "select",
                "options": ["1:1", "16:9", "9:16", "4:3", "3:4"],
            }
            params["clarity"] = {"type": "select", "options": ["1K", "2K"]}
            params.pop("resolution", None)
            params.pop("duration", None)
            params.pop("watermark", None)
    provider_by_id = {
        p.get("id"): p for p in providers if isinstance(p, dict) and p.get("id")
    }
    for model in models:
        if not isinstance(model, dict) or model.get("capability") != "video":
            continue
        provider = provider_by_id.get(model.get("providerId"))
        if provider and _is_openai_video_relay(provider):
            remote_model = model.get("remoteModel") or model.get("id")
            profile = _documented_model_profile(remote_model)
            if profile:
                model["profile"] = profile
            else:
                model.pop("profile", None)
            if model.get("adapter") in {
                "agnes_video",
                "ark_video",
                "newapi_video",
                "canvaspro_video",
                "openai_video",
                "fourstoken_video",
                "fourstoken_newapi_video",
            }:
                model["adapter"] = (
                    "canvaspro_video"
                    if _documented_model_entry(remote_model)
                    else "openai_video"
                )
            documented = _documented_video_spec(remote_model)
            if documented:
                _apply_documented_video_contract(model, documented)
                limits_spec = documented.get("maxReferences") or {}
                model["limits"] = _normalize_model_limits({
                    "maxImages": int(limits_spec.get("images") or 0),
                    "maxVideos": int(limits_spec.get("videos") or 0),
                    "maxAudios": int(limits_spec.get("audios") or 0),
                    "maxTextRefs": int(limits_spec.get("texts") or 0),
                    **(
                        {"maxPromptChars": 10000}
                        if documented.get("profile") == "aistars-video"
                        else {}
                    ),
                })
    for model in models:
        if not isinstance(model, dict):
            continue
        provider = provider_by_id.get(model.get("providerId"))
        if not provider:
            continue
        if (
            provider.get("protocol") == "ark"
            and model.get("capability") == "video"
        ):
            contract = _ark_video_contract(
                str(model.get("remoteModel") or model.get("id") or "")
            )
            params = model.setdefault("params", {})
            duration = params.get("duration")
            if not isinstance(duration, dict):
                duration = {}
            duration.update({
                "type": "range",
                "min": contract["duration"]["min"],
                "max": contract["duration"]["max"],
                "step": 1,
            })
            duration.pop("options", None)
            params["duration"] = duration
            defaults = model.setdefault("defaultParams", {})
            default_duration = _safe_int_value(
                defaults.get("duration"),
                contract["duration"]["min"],
            )
            defaults["duration"] = max(
                contract["duration"]["min"],
                min(contract["duration"]["max"], default_duration),
            )
            available = contract["resolutions"]
            resolution = str(defaults.get("resolution") or "").upper()
            defaults["resolution"] = (
                resolution if resolution in available else available[0]
            )
            params["resolution"] = _select(available)
        if (
            _is_fourstoken_relay(provider)
            and model.get("capability") == "image"
            and model.get("adapter")
            not in {"newapi_image", "ark_image", "agnes_image"}
        ):
            model["adapter"] = "fourstoken_image"
            model["profile"] = "fourstoken-image"
        if (
            _is_fourstoken_relay(provider)
            and model.get("capability") == "video"
            and model.get("adapter") not in {
                "fourstoken_video",
                "fourstoken_newapi_video",
            }
        ):
            model["adapter"] = "fourstoken_video"
            model["profile"] = "fourstoken-video"
        if (
            provider.get("protocol") == "openai"
            and model.get("capability") == "image"
        ):
            params = model.setdefault("params", {})
            if "temperature" in params:
                model["defaultParams"] = {"ratio": "1:1", "size": "1024x1024"}
                model["params"] = {
                    "ratio": _select(["1:1", "16:9", "9:16", "4:3", "3:4", "21:9"]),
                    "size": {"type": "text"},
                }
        if (
            _is_newapi_relay(provider)
            and model.get("capability") == "image"
            and model.get("adapter") in {"openai_image", "ark_image", "agnes_image"}
            and _uses_newapi_image_route(model.get("remoteModel") or model.get("id"))
        ):
            model["adapter"] = "newapi_image"
        if _is_newapi_relay(provider):
            documented = _documented_model_entry(model.get("remoteModel"))
            if documented and documented.get("profile"):
                model["profile"] = str(documented["profile"])
        if _is_fourstoken_relay(provider):
            if model.get("capability") == "image":
                if model.get("adapter") in {"", "openai_image"}:
                    model["adapter"] = "fourstoken_image"
                    model["profile"] = "fourstoken-image"
                defaults = model.setdefault("defaultParams", {})
                defaults.setdefault("imageSize", "1K")
                defaults.pop("clarity", None)
                defaults.pop("resolution", None)
                schema = model.setdefault("params", {})
                schema["imageSize"] = _select(["1K", "2K", "4K"])
                schema.pop("clarity", None)
            elif model.get("capability") == "video":
                if model.get("adapter") not in {
                    "fourstoken_video",
                    "fourstoken_newapi_video",
                }:
                    model["adapter"] = "fourstoken_video"
                model["profile"] = (
                    "fourstoken-newapi-video"
                    if model.get("adapter") == "fourstoken_newapi_video"
                    else "fourstoken-video"
                )
        if model.get("capability") == "video" and model.get("adapter") == "newapi_video":
            model["adapter"] = (
                "canvaspro_video"
                if _documented_model_entry(model.get("remoteModel") or model.get("id"))
                else "openai_video"
            )
    if migrate and merged != cfg:
        save_config(merged)
    return merged


def save_config(cfg: dict[str, Any]) -> None:
    payload = _encrypt_config_secrets(cfg)
    path = _local_config_path()
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
        dir=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        # Windows can briefly deny replacement while antivirus, indexing or a
        # reader still has the destination open. Retry only that transient
        # sharing violation; a real ACL/read-only failure still fails fast.
        for attempt in range(6):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.02 * (2 ** attempt))
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


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
        "icon": str(provider.get("icon") or "").strip(),
        "enabled": provider.get("enabled", True) is not False,
        "has_api_key": bool(key.strip()),
        "api_key_hint": _hint(key) if key.strip() else "",
        "builtin": provider.get("id") in {item["id"] for item in DEFAULT_PROVIDERS},
    }


def _public_storage(storage: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(storage.get("enabled"))
    provider = str(storage.get("active_provider") or storage.get("provider") or "tos")
    profiles = storage.get("providers") if isinstance(storage.get("providers"), dict) else {}
    def profile_ready(name: str, profile: dict[str, Any]) -> bool:
        if not profile:
            return False
        if name == "tos":
            return bool(
                profile.get("bucket")
                and profile.get("region") == "cn-beijing"
                and profile.get("endpoint")
                and profile.get("internal_endpoint")
                and profile.get("access_key")
                and profile.get("secret_key")
            )
        if name == "aliyun_oss":
            return bool(
                profile.get("bucket")
                and profile.get("endpoint")
                and profile.get("access_key")
                and profile.get("secret_key")
            )
        if name == "qiniu":
            return bool(
                profile.get("bucket")
                and profile.get("public_domain")
                and profile.get("access_key")
                and profile.get("secret_key")
            )
        return False
    selected = profiles.get(provider) if isinstance(profiles.get(provider), dict) else {}
    if not selected:
        selected = storage
    legacy_test = {
        "last_test_ok": None,
        "last_test_error": "",
        "last_test_at": "",
    }
    selected_test = {
        "last_test_ok": selected.get("last_test_ok", legacy_test["last_test_ok"]),
        "last_test_error": str(
            selected.get("last_test_error", legacy_test["last_test_error"]) or ""
        ),
        "last_test_at": str(
            selected.get("last_test_at", legacy_test["last_test_at"]) or ""
        ),
    }
    access_key = str(selected.get("access_key") or "")
    secret_key = str(selected.get("secret_key") or "")
    bucket = str(selected.get("bucket") or "")
    region = str(selected.get("region") or "cn-beijing")
    endpoint = str(selected.get("endpoint") or "")
    internal_endpoint = str(selected.get("internal_endpoint") or "")
    public_domain = str(selected.get("public_domain") or "")
    tos_ready = profile_ready("tos", profiles.get("tos") if profiles else (
        storage if provider == "tos" else {}
    ))
    oss_ready = profile_ready("aliyun_oss", profiles.get("aliyun_oss") if profiles else (
        storage if provider == "aliyun_oss" else {}
    ))
    qiniu_ready = profile_ready("qiniu", profiles.get("qiniu") if profiles else (
        storage if provider == "qiniu" else {}
    ))
    tested_ok = storage.get("last_test_ok")
    selected_ready = {
        "tos": tos_ready,
        "aliyun_oss": oss_ready,
        "qiniu": qiniu_ready,
    }.get(provider, False)
    ready = (
        enabled
        and selected_ready
        and tested_ok is not False
    )
    return {
        "enabled": enabled,
        "provider": provider,
        "active_provider": provider,
        "bucket": bucket,
        "region": region,
        "endpoint": endpoint,
        "internal_endpoint": internal_endpoint,
        "public_domain": public_domain,
        "prefix": str(selected.get("prefix") or storage.get("prefix") or ""),
        "url_ttl_seconds": int(selected.get("url_ttl_seconds") or storage.get("url_ttl_seconds") or 3600),
        "auto_persist_images": storage.get("auto_persist_images", True) is not False,
        "auto_persist_videos": storage.get("auto_persist_videos", True) is not False,
        "auto_persist_audio": storage.get("auto_persist_audio", True) is not False,
        "require_for_ark_portrait": storage.get("require_for_ark_portrait", True) is not False,
        "has_access_key": bool(access_key.strip()),
        "access_key_hint": _hint(access_key) if access_key.strip() else "",
        "has_secret_key": bool(secret_key.strip()),
        "secret_key_hint": _hint(secret_key) if secret_key.strip() else "",
        "ready": ready,
        "active": enabled and selected_ready,
        "configured": selected_ready,
        "tos_ready": tos_ready,
        "oss_ready": oss_ready,
        "qiniu_ready": qiniu_ready,
        "enabled": enabled,
        **selected_test,
        "profiles": {
            name: {
                "bucket": str(profile.get("bucket") or ""),
                "region": str(profile.get("region") or "cn-beijing"),
                "endpoint": str(profile.get("endpoint") or ""),
                "internal_endpoint": str(profile.get("internal_endpoint") or ""),
                "public_domain": str(profile.get("public_domain") or ""),
                "prefix": str(profile.get("prefix") or storage.get("prefix") or ""),
                "url_ttl_seconds": int(
                    profile.get("url_ttl_seconds")
                    or storage.get("url_ttl_seconds")
                    or 3600
                ),
                "has_access_key": bool(str(profile.get("access_key") or "").strip()),
                "access_key_hint": _hint(str(profile.get("access_key") or "")),
                "has_secret_key": bool(str(profile.get("secret_key") or "").strip()),
                "secret_key_hint": _hint(str(profile.get("secret_key") or "")),
                "last_test_ok": profile.get("last_test_ok"),
                "last_test_error": str(profile.get("last_test_error") or ""),
                "last_test_at": str(profile.get("last_test_at") or ""),
            }
            for name, profile in profiles.items()
            if isinstance(profile, dict)
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
    resolved = copy.deepcopy(storage if isinstance(storage, dict) else DEFAULT_STORAGE)
    provider = str(
        resolved.get("active_provider") or resolved.get("provider") or "tos"
    ).strip().lower()
    profiles = resolved.get("providers")
    profile = (
        profiles.get(provider)
        if isinstance(profiles, dict)
        and isinstance(profiles.get(provider), dict)
        else None
    )
    if profile:
        # The flat fields are a runtime projection, not the source of truth.
        # Older configurations kept stale top-level fields after switching
        # providers while the selected provider profile remained complete.
        for key in (
            "bucket",
            "region",
            "endpoint",
            "internal_endpoint",
            "public_domain",
            "prefix",
            "access_key",
            "secret_key",
            "url_ttl_seconds",
        ):
            if key in profile:
                resolved[key] = copy.deepcopy(profile[key])
        resolved["provider"] = provider
        resolved["active_provider"] = provider
    return resolved


def storage_profile_config(provider: str) -> dict[str, Any]:
    """Resolve one saved storage profile without changing the active backend."""
    selected = str(provider or "").strip().lower()
    if selected not in {"tos", "aliyun_oss", "qiniu"}:
        return {}
    cfg = load_config()
    storage = cfg.get("storage")
    resolved = copy.deepcopy(storage if isinstance(storage, dict) else DEFAULT_STORAGE)
    profiles = resolved.get("providers")
    profile = (
        profiles.get(selected)
        if isinstance(profiles, dict)
        and isinstance(profiles.get(selected), dict)
        else None
    )
    if not profile:
        return {}
    merged = {**resolved, **copy.deepcopy(profile)}
    merged["provider"] = selected
    merged["active_provider"] = selected
    merged["enabled"] = bool(profile)
    return merged


def public_storage_config() -> dict[str, Any]:
    return _public_storage(storage_config())


def activate_storage_provider(provider: str) -> dict[str, Any]:
    """Switch the active storage backend without changing saved credentials."""
    provider = str(provider or "").strip().lower()
    if provider not in {"tos", "aliyun_oss", "qiniu"}:
        raise ValueError("存储类型只能是火山 TOS、阿里云 OSS 或七牛云")
    cfg = load_config()
    storage = cfg.setdefault("storage", copy.deepcopy(DEFAULT_STORAGE))
    profiles = storage.get("providers") if isinstance(storage.get("providers"), dict) else {}
    profile = profiles.get(provider) if isinstance(profiles.get(provider), dict) else {}
    if not profile:
        raise ValueError("请先保存该服务商的配置，再启用")
    configured = bool(
        profile.get("bucket")
        and profile.get("access_key")
        and profile.get("secret_key")
        and (
            profile.get("public_domain")
            if provider == "qiniu"
            else profile.get("endpoint")
        )
    )
    if not configured:
        raise ValueError("该服务商配置不完整，请先补全并保存")
    storage.update({
        "enabled": True,
        "provider": provider,
        "active_provider": provider,
    })
    context = tenant_context.current()
    if context is not None:
        import tenant_registry
        if provider == "tos":
            tenant_registry.claim_storage_binding(
                context.tenant_id,
                bucket=profile["bucket"],
                region=profile.get("region") or "cn-beijing",
                prefix=profile.get("prefix") or "",
                access_key=profile["access_key"],
            )
        else:
            tenant_registry.release_storage_binding(context.tenant_id)
    save_config(cfg)
    return public_storage_config()


def deactivate_storage() -> dict[str, Any]:
    cfg = load_config()
    storage = cfg.setdefault("storage", copy.deepcopy(DEFAULT_STORAGE))
    storage["enabled"] = False
    context = tenant_context.current()
    if context is not None:
        import tenant_registry
        tenant_registry.release_storage_binding(context.tenant_id)
    save_config(cfg)
    return public_storage_config()


def record_storage_test_failure(message: str, *, provider: str = "") -> None:
    cfg = load_config()
    storage = cfg.setdefault("storage", copy.deepcopy(DEFAULT_STORAGE))
    provider = str(provider or storage.get("provider") or "tos").strip().lower()
    profiles = storage.setdefault("providers", {})
    profile = profiles.setdefault(provider, {})
    tested_at = datetime.now(timezone.utc).isoformat()
    if provider == str(storage.get("active_provider") or storage.get("provider") or "tos"):
        storage["last_test_ok"] = False
        storage["last_test_error"] = str(message or "")[:1200]
        storage["last_test_at"] = tested_at
    profile["last_test_ok"] = False
    profile["last_test_error"] = str(message or "")[:1200]
    profile["last_test_at"] = tested_at
    save_config(cfg)


def record_storage_test_success(*, provider: str = "") -> None:
    cfg = load_config()
    storage = cfg.setdefault("storage", copy.deepcopy(DEFAULT_STORAGE))
    provider = str(provider or storage.get("provider") or "tos").strip().lower()
    profiles = storage.setdefault("providers", {})
    profile = profiles.setdefault(provider, {})
    tested_at = datetime.now(timezone.utc).isoformat()
    if provider == str(storage.get("active_provider") or storage.get("provider") or "tos"):
        storage["last_test_ok"] = True
        storage["last_test_error"] = ""
        storage["last_test_at"] = tested_at
    profile["last_test_ok"] = True
    profile["last_test_error"] = ""
    profile["last_test_at"] = tested_at
    save_config(cfg)


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
    if provider not in {"tos", "aliyun_oss", "qiniu"}:
        raise ValueError("存储类型只能是火山 TOS、阿里云 OSS 或七牛云")
    profiles = current.get("providers") if isinstance(current.get("providers"), dict) else {}
    target_profile = profiles.get(provider) if isinstance(profiles.get(provider), dict) else {}
    fallback = target_profile or current
    bucket = str(source.get("bucket", fallback.get("bucket") or "")).strip()
    region = str(source.get("region", fallback.get("region") or "cn-beijing")).strip()
    endpoint = _normalize_endpoint(
        source.get("endpoint", fallback.get("endpoint")),
        "tos-cn-beijing.volces.com",
    )
    internal_endpoint = _normalize_endpoint(
        source.get("internal_endpoint", fallback.get("internal_endpoint")),
        "tos-cn-beijing.ivolces.com",
    )
    public_domain = str(
        source.get("public_domain", fallback.get("public_domain") or "")
    ).strip().rstrip("/")
    prefix = str(source.get("prefix", fallback.get("prefix") or "")).strip().strip("/")
    if prefix:
        prefix += "/"
    try:
        url_ttl = int(source.get("url_ttl_seconds", fallback.get("url_ttl_seconds") or 3600))
    except (TypeError, ValueError):
        raise ValueError("预签名有效期必须是整数秒")
    url_ttl = max(60, min(604800, url_ttl))
    previous_provider = str(current.get("provider") or "tos").strip().lower()
    active_provider = str(
        current.get("active_provider") or previous_provider or "tos"
    ).strip().lower()
    provider_changed = provider != previous_provider
    # Saving a provider profile never activates it or changes the active
    # backend. Activation is an explicit user action.
    enabled = bool(current.get("enabled", False))
    previous_profile = target_profile
    if not previous_profile and provider == previous_provider:
        previous_profile = current
    access_key = str(previous_profile.get("access_key") or "") if previous_profile else ""
    secret_key = str(previous_profile.get("secret_key") or "") if previous_profile else ""
    if isinstance(source.get("access_key"), str) and source["access_key"].strip():
        access_key = source["access_key"].strip()
    if isinstance(source.get("secret_key"), str) and source["secret_key"].strip():
        secret_key = source["secret_key"].strip()
    if enabled and provider == active_provider and provider == "tos":
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
    if enabled and provider == active_provider and provider == "aliyun_oss":
        missing = [
            label for label, value in (
                ("Bucket", bucket),
                ("Endpoint", endpoint),
                ("AccessKeyId", access_key),
                ("AccessKeySecret", secret_key),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"启用阿里云 OSS 前请填写：{'、'.join(missing)}")
    if enabled and provider == active_provider and provider == "qiniu":
        missing = [
            label for label, value in (
                ("Bucket", bucket),
                ("公网访问域名", public_domain),
                ("AccessKey", access_key),
                ("SecretKey", secret_key),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"启用七牛云前请填写：{'、'.join(missing)}")
    provider_profile = {
        "bucket": bucket[:128],
        "region": region[:64],
        "endpoint": endpoint[:255],
        "internal_endpoint": internal_endpoint[:255],
        "public_domain": public_domain[:255],
        "prefix": prefix[:180],
        "access_key": access_key[:2048],
        "secret_key": secret_key[:4096],
        "url_ttl_seconds": url_ttl,
    }
    profiles[provider] = provider_profile
    current.update({
        "enabled": enabled,
        "provider": active_provider,
        "active_provider": active_provider,
        "providers": profiles,
        "auto_persist_images": bool(source.get(
            "auto_persist_images",
            current.get("auto_persist_images", True),
        )),
        "auto_persist_videos": bool(source.get(
            "auto_persist_videos",
            current.get("auto_persist_videos", True),
        )),
        "auto_persist_audio": bool(source.get(
            "auto_persist_audio",
            current.get("auto_persist_audio", True),
        )),
        "require_for_ark_portrait": bool(source.get(
            "require_for_ark_portrait",
            current.get("require_for_ark_portrait", True),
        )),
        "last_test_ok": current.get("last_test_ok"),
        "last_test_error": str(current.get("last_test_error") or ""),
        "last_test_at": str(current.get("last_test_at") or ""),
    })
    context = tenant_context.current()
    if context and provider == "tos" and any((bucket, region, access_key)):
        import tenant_registry
        tenant_registry.claim_storage_binding(
            context.tenant_id,
            bucket=bucket,
            region=region,
            prefix=prefix,
            access_key=access_key,
        )
    save_config(cfg)
    return public_storage_config()


def _public_model(model: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: copy.deepcopy(model.get(key))
        for key in (
            "id", "providerId", "capability", "label", "remoteModel", "adapter",
            "description", "badge", "enabled", "defaultParams", "params", "limits",
            "pricing", "profile",
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


_PROVIDER_ICON_PATH_RE = re.compile(
    r"^/uploads/[A-Za-z0-9][A-Za-z0-9._-]*\.(?:png|jpe?g|webp|gif)$",
    re.IGNORECASE,
)


def normalize_provider_icon(value: Any) -> str:
    """Validate the provider icon reference accepted by the public catalog.

    The browser uploads an icon to this deployment first, so new values are
    either a single local image path or a remote HTTPS image. Reject data,
    blob, javascript and executable paths: this field is rendered by <img> and
    persisted in tenant configuration.
    """
    icon = str(value or "").strip()
    if not icon:
        return ""
    if len(icon) > 2048:
        raise ValueError("供应商图标地址过长")
    if _PROVIDER_ICON_PATH_RE.fullmatch(icon):
        return icon
    parsed = urllib.parse.urlparse(icon)
    if (
        parsed.scheme.lower() == "https"
        and parsed.netloc
        and not parsed.username
        and not parsed.password
        and parsed.path.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
    ):
        return icon
    raise ValueError("供应商图标必须是本地上传的 PNG/JPG/WebP/GIF 或 HTTPS 图片地址")


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
    if "icon" in source:
        current["icon"] = normalize_provider_icon(source.get("icon"))
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
    "newapi_image": "image",
    "agnes_image": "image",
    "ark_video": "video",
    "agnes_video": "video",
    "newapi_video": "video",
    "canvaspro_video": "video",
    "openai_video": "video",
    "fourstoken_image": "image",
    "fourstoken_video": "video",
    "fourstoken_newapi_video": "video",
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
    provider_record = next(
        p for p in cfg.get("providers", [])
        if isinstance(p, dict) and p.get("id") == provider_id
    )
    documented_entry = (
        _documented_model_entry(remote_model)
        if _is_newapi_relay(provider_record)
        else None
    )
    fourstoken = _is_fourstoken_relay(provider_record)
    if adapter == "newapi_video":
        adapter = "canvaspro_video" if documented_entry else "openai_video"
    if fourstoken:
        expected_adapter = {
            "image": "fourstoken_image",
            "video": "fourstoken_video",
        }.get(capability)
        if expected_adapter:
            adapter = expected_adapter
    adapter_capability = ADAPTER_CAPABILITIES[adapter]
    if capability != adapter_capability:
        raise ValueError(f"适配器 {adapter} 只能用于 {adapter_capability} 模型")
    if _is_newapi_relay(provider_record) and not documented_entry:
        raise ValueError(
            f"模型 {remote_model} 不在当前接口文档目录中，"
            "无法确定 profile、提交字段和查询路径；请先更新接口文档目录"
        )
    if (
        documented_entry
        and documented_entry.get("kind")
        and str(documented_entry["kind"]) != capability
    ):
        raise ValueError(
            f"模型 {remote_model} 在接口文档中属于 {documented_entry['kind']}，"
            f"不能保存为 {capability}"
        )
    current.update({
        "providerId": provider_id,
        "capability": capability,
        "adapter": adapter,
        "label": str(source.get("label") or remote_model).strip()[:120],
        "remoteModel": remote_model[:240],
        "description": str(source.get("description") or "").strip()[:500],
        "badge": str(source.get("badge") or capability[:4]).strip()[:16],
        "enabled": bool(source.get("enabled", current.get("enabled", True))),
        "profile": str(
            source.get("profile")
            or current.get("profile")
            or (
                _video_profile_for_model(
                    {"remoteModel": remote_model},
                    provider_record,
                )
                if capability == "video"
                else ""
            )
        ).strip(),
    })
    # The provider catalog is authoritative for saved defaults, parameter
    # schemas and reference limits. Client-side drafts must not reintroduce
    # generic video values that are invalid for the exact model.
    defaults = (
        None
        if (capability == "video" and _is_newapi_relay(provider_record))
        else source.get("defaultParams")
    )
    schema = (
        None
        if (capability == "video" and _is_newapi_relay(provider_record))
        else source.get("params")
    )
    limits = (
        None
        if (capability == "video" and _is_newapi_relay(provider_record))
        else source.get("limits")
    )
    pricing = source.get("pricing")
    if isinstance(defaults, dict):
        current["defaultParams"] = copy.deepcopy(defaults)
    elif "defaultParams" not in current:
        current["defaultParams"] = {}
    if isinstance(schema, dict):
        current["params"] = copy.deepcopy(schema)
    elif "params" not in current:
        current["params"] = {}
    if remote_model == "即梦-65-seedream-5-pro" and capability == "image":
        normalized_defaults = current["defaultParams"]
        normalized_schema = current["params"]
        clarity = str(
            normalized_defaults.get("clarity")
            or normalized_defaults.get("resolution")
            or "2K"
        ).upper()
        normalized_defaults["clarity"] = clarity if clarity in {"1K", "2K", "4K"} else "2K"
        for key in ("resolution", "duration", "watermark"):
            normalized_defaults.pop(key, None)
        normalized_schema["ratio"] = {
            "type": "select",
            "options": ["1:1", "16:9", "9:16", "4:3", "3:4"],
        }
        normalized_schema["clarity"] = {
            "type": "select",
            "options": ["1K", "2K"],
        }
        for key in ("resolution", "duration", "watermark"):
            normalized_schema.pop(key, None)
    if isinstance(limits, dict):
        current["limits"] = _normalize_model_limits(limits)
    elif "limits" not in current:
        current["limits"] = {}
    if isinstance(pricing, dict):
        current["pricing"] = copy.deepcopy(pricing)
    elif "pricing" not in current:
        current["pricing"] = {}
    documented_video = (
        _documented_video_spec(remote_model)
        if (
            capability == "video"
            and _is_newapi_relay(provider_record)
        )
        else None
    )
    if documented_video:
        _apply_documented_video_contract(current, documented_video)
        limits_spec = documented_video.get("maxReferences") or {}
        current["limits"] = _normalize_model_limits({
            "maxImages": int(limits_spec.get("images") or 0),
            "maxVideos": int(limits_spec.get("videos") or 0),
            "maxAudios": int(limits_spec.get("audios") or 0),
            "maxTextRefs": int(limits_spec.get("texts") or 0),
            **({"maxPromptChars": 10000} if documented_video.get("profile") == "aistars-video" else {}),
        })
    if fourstoken and capability == "video":
        contract = _fourstoken_video_contract(remote_model)
        current["adapter"] = "fourstoken_video"
        current["profile"] = "fourstoken-video"
        current["defaultParams"] = {
            "ratio": "9:16",
            "resolution": "480P",
            "duration": 5,
        }
        current["params"] = {
            "ratio": _select(contract["ratios"]),
            "resolution": _select(contract["resolutions"]),
            "duration": {
                "type": "range",
                "min": contract["durations"][0],
                "max": contract["durations"][-1],
                "step": 1,
            },
        }
        current["limits"] = _normalize_model_limits({
            "maxImages": contract["images"],
            "maxVideos": contract["videos"],
            "maxAudios": contract["audios"],
        })
    elif fourstoken and capability == "image":
        current["adapter"] = "fourstoken_image"
        current["profile"] = "fourstoken-image"
        current["defaultParams"] = {
            "ratio": "1:1",
            "size": "1024x1024",
            "imageSize": "1K",
        }
        current["params"] = {
            "ratio": _select(["1:1", "16:9", "9:16", "4:3", "3:4", "21:9"]),
            "size": {"type": "text"},
            "imageSize": _select(["1K", "2K", "4K"]),
        }
        current["limits"] = _normalize_model_limits({
            "maxImages": 4,
            "maxTextRefs": 4,
        })

    save_config(cfg)
    return public_catalog()


def delete_model(model_id: str) -> dict[str, Any]:
    cfg = load_config()
    models = cfg.setdefault("models", [])
    known = any(m.get("id") == model_id for m in models)
    builtin = any(str(item.get("id")) == model_id for item in DEFAULT_MODELS)
    if not known and not builtin:
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
    model = None
    if raw:
        # Model identity comes only from persisted catalog fields. Never infer
        # from a display label, strip a date suffix, normalize a model name, or
        # substitute another model with the same capability.
        if provider_id:
            model = next(
                (m for m in candidates if m.get("providerId") == provider_id
                 and raw in {m.get("id"), m.get("remoteModel")}),
                None,
            )
        if model is None:
            model = next(
                (m for m in candidates
                 if raw in {m.get("id"), m.get("remoteModel")}),
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


def classify_provider_failure(status: int, detail: str) -> str:
    """Return the shared contract failure kind for any provider response."""
    text = str(detail or "")
    lowered = text.lower()
    balance_markers = ("overdue", "balance", "insufficient", "quota", "欠费", "余额", "额度")
    auth_markers = ("invalid api key", "invalid token", "unauthorized", "无效的令牌", "鉴权", "令牌")
    if status == 402:
        return "balance"
    if status == 401:
        return "auth"
    if status == 403:
        if any(marker in lowered or marker in text for marker in balance_markers):
            return "balance"
        if any(marker in lowered or marker in text for marker in auth_markers):
            return "auth"
    if status == 429:
        return "rate_limit"
    if status in (404, 405):
        return "probe_missing"
    if status >= 500:
        return "upstream"
    return "unknown"


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
    failure_kind = classify_provider_failure(status, detail)
    if failure_kind == "balance":
        reason = detail or "账户余额或额度不足"
        return {
            "ok": False,
            "message": (
                f"账户余额或额度不足（HTTP {status}）：{reason}。"
                "请先核对供应商账户余额与 Key 权限，再重新生成。"
            ),
            "elapsedMs": elapsed,
            "balanceFailed": True,
        }
    if failure_kind == "auth":
        reason = detail or "无效的令牌"
        return {
            "ok": False,
            "message": (
                f"鉴权失败（HTTP {status}）：{reason}。"
                "请在「模型接口」中更新该供应商的 API Key 后重新测试。"
            ),
            "elapsedMs": elapsed,
            "authFailed": True,
        }
    if failure_kind == "probe_missing" and protocol in {"ark", "dashscope"}:
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
        output = payload.get("output")
        candidates = (
            payload.get("data")
            or payload.get("models")
            or payload.get("result")
            or payload.get("items")
            or (output.get("models") if isinstance(output, dict) else None)
        )
        raw_items = candidates if isinstance(candidates, list) else []
    else:
        raw_items = []
    ids: list[str] = []
    for item in raw_items:
        value = (
            (item.get("id") or item.get("model"))
            if isinstance(item, dict)
            else item
        )
        text = str(value or "").strip()
        if text:
            ids.append(text)
    return list(dict.fromkeys(ids))


def _model_type_metadata_from_response(
    payload: Any,
) -> tuple[dict[str, list[str]], dict[str, str]]:
    if isinstance(payload, dict):
        output = payload.get("output")
        candidates = (
            payload.get("data")
            or payload.get("models")
            or payload.get("result")
            or payload.get("items")
            or (output.get("models") if isinstance(output, dict) else None)
        )
        raw_items = candidates if isinstance(candidates, list) else []
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []
    endpoint_types: dict[str, list[str]] = {}
    ark_capabilities: dict[str, str] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or item.get("model") or "").strip()
        if not model_id:
            continue
        values = (
            item.get("supported_endpoint_types")
            or item.get("supportedEndpointTypes")
            or item.get("endpoint_types")
            or item.get("endpointTypes")
            or []
        )
        if isinstance(values, list):
            endpoint_types[model_id] = [str(value) for value in values if str(value).strip()]
        ark_capability = _capability_from_ark_model(item)
        if ark_capability:
            ark_capabilities[model_id] = ark_capability
    return endpoint_types, ark_capabilities


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
    declared_capability: str | None = None,
) -> dict[str, Any]:
    """Create a conservative editable draft from a provider /models row."""
    fourstoken = _is_fourstoken_relay(provider)
    capability = (
        _capability_from_endpoint_types(supported_endpoint_types)
        or declared_capability
        or _documented_provider_capability(provider, remote_model)
    )
    if capability is None:
        return {
            "id": normalize_model_id(remote_model),
            "providerId": provider.get("id", ""),
            "remoteModel": remote_model,
            "label": remote_model,
            "capability": "unknown",
            "adapter": "",
            "enabled": False,
            "discovered": True,
            "index": index,
        }
    if "adapter" not in locals():
        if provider.get("protocol") == "ark":
            video_adapter = "ark_video"
        elif str(provider.get("id") or "").strip().lower() == "agnes-ai":
            video_adapter = "agnes_video"
        elif fourstoken:
            video_adapter = "fourstoken_video"
        else:
            video_adapter = (
                "canvaspro_video"
                if (
                    _documented_model_entry(remote_model)
                    or _is_newapi_relay(provider)
                )
                else "openai_video"
            )
        image_adapter = (
            "newapi_image"
            if (
                _is_newapi_relay(provider)
                and _uses_newapi_image_route(remote_model)
            )
            else ("ark_image" if provider.get("protocol") == "ark" else "openai_image")
        )
        if fourstoken:
            image_adapter = "fourstoken_image"
        adapter = {
            "video": video_adapter,
            "image": image_adapter,
            "audio": "dashscope_tts" if provider.get("protocol") == "dashscope" else "dashscope_tts",
            "text": "openai_chat",
        }[capability]
    if capability == "image" and adapter == "openai_image":
        existing_params = params if "params" in locals() else {}
        if set(existing_params) <= {"temperature"}:
            default_params = {"ratio": "1:1", "size": "1024x1024"}
            params = {
                "ratio": _select(["1:1", "16:9", "9:16", "4:3", "3:4", "21:9"]),
                "size": {"type": "text"},
            }
    label = remote_model
    model_id = normalize_model_id(remote_model)
    if capability == "video":
        default_params = {"ratio": "9:16", "resolution": "480P", "duration": 5}
        if fourstoken:
            contract = _fourstoken_video_contract(remote_model)
            params = {
                "ratio": _select(contract["ratios"]),
                "resolution": _select(contract["resolutions"]),
                "duration": {
                    "type": "range",
                    "min": contract["durations"][0],
                    "max": contract["durations"][-1],
                    "step": 1,
                },
            }
            default_params["resolution"] = contract["resolutions"][0]
        elif provider.get("protocol") == "ark":
            ark_video = _ark_video_contract(remote_model)
            params = {
                "ratio": _select(["16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive"]),
                "resolution": _select(ark_video["resolutions"]),
                "duration": {
                    "type": "range",
                    "min": ark_video["duration"]["min"],
                    "max": ark_video["duration"]["max"],
                    "step": 1,
                },
            }
            default_params["resolution"] = ark_video["resolutions"][0]
        else:
            params = {
                "ratio": _select(["1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "adaptive"]),
                "resolution": _select(["480P", "720P", "1080P"]),
                "duration": {"type": "range", "min": 1, "max": 30, "step": 1},
            }
    elif capability == "image":
        default_params = (
            {
                "ratio": "1:1",
                "size": "1024x1024",
                "imageSize": "1K",
            }
            if fourstoken
            else {"ratio": "1:1", "size": "1024x1024"}
        )
        params = (
            {
                "ratio": _select(["1:1", "16:9", "9:16", "4:3", "3:4", "21:9"]),
                "size": {"type": "text"},
                "imageSize": _select(["1K", "2K", "4K"]),
            }
            if fourstoken
            else {
                "ratio": _select(["1:1", "16:9", "9:16", "4:3", "3:4", "21:9"]),
                "size": {"type": "text"},
            }
        )
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
    result = {
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
        "profile": (
            "fourstoken-image"
            if capability == "image" and fourstoken
            else "fourstoken-video"
            if capability == "video" and fourstoken
            else _video_profile_for_model(
                {"remoteModel": remote_model},
                provider,
            )
            if capability == "video" and _is_newapi_relay(provider)
            else ""
        ),
    }
    documented_video = (
        _documented_video_spec(remote_model)
        if (
            capability == "video"
            and _is_newapi_relay(provider)
        )
        else None
    )
    if documented_video:
        _apply_documented_video_contract(result, documented_video)
        limits_spec = documented_video.get("maxReferences") or {}
        result["limits"] = _normalize_model_limits({
            "maxImages": int(limits_spec.get("images") or 0),
            "maxVideos": int(limits_spec.get("videos") or 0),
            "maxAudios": int(limits_spec.get("audios") or 0),
            "maxTextRefs": int(limits_spec.get("texts") or 0),
            **(
                {"maxPromptChars": 10000}
                if documented_video.get("profile") == "aistars-video"
                else {}
            ),
        })
    elif capability == "video" and fourstoken:
        contract = _fourstoken_video_contract(remote_model)
        result["limits"] = _normalize_model_limits({
            "maxImages": contract["images"],
            "maxVideos": contract["videos"],
            "maxAudios": contract["audios"],
        })
        if not declared_capability and not (
            _capability_from_endpoint_types(supported_endpoint_types) == "video"
        ):
            result["description"] += "；参数合同待首次生成确认"
    elif capability == "video":
        documented = _documented_model_entry(remote_model)
        if (
            provider.get("protocol") != "ark"
            and not documented
        ):
            result["profile"] = "openai-video"
            result["adapter"] = "openai_video"
            result["description"] += "；兼容参数合同，实际能力以首次生成为准"
    return result


def _ark_video_contract(remote_model: str) -> dict[str, Any]:
    """Official Ark Seedance request constraints, not UI shortcut presets."""
    name = str(remote_model or "").lower()
    if "seedance-2-5" in name or "seedance-2.5" in name:
        rates = ["480P", "720P", "1080P"]
        minimum, maximum = 4, 30
    elif any(token in name for token in ("seedance-2-0-fast", "seedance-2.0-fast")):
        rates = ["480P", "720P"]
        minimum, maximum = 4, 15
    elif any(token in name for token in ("seedance-2-0-mini", "seedance-2.0-mini")):
        rates = ["480P", "720P"]
        minimum, maximum = 4, 15
    elif any(token in name for token in ("seedance-2-0", "seedance-2.0")):
        rates = ["480P", "720P", "1080P", "4K"]
        minimum, maximum = 4, 15
    else:
        rates = ["480P", "720P", "1080P"]
        minimum, maximum = 1, 30
    return {
        "resolutions": rates,
        "duration": {"min": minimum, "max": maximum},
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
                timeout=MODEL_DISCOVERY_TIMEOUT_SECONDS,
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
    endpoint_types, declared_capabilities = _model_type_metadata_from_response(payload)
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
            declared_capabilities.get(remote_model),
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
