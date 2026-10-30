from __future__ import annotations

import copy
import re
import urllib.parse
from typing import Any


def _normalize_resolution(value: Any) -> str:
    return str(value or "").strip().lower()


def _safe_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def compile_video_request(
    *,
    model: dict[str, Any],
    contract: dict[str, Any],
    template: dict[str, Any],
    prompt: str,
    params: dict[str, Any],
    image_urls: list[str],
    video_urls: list[str],
    audio_urls: list[str],
) -> dict[str, Any]:
    """Compile one exact-model video request from its documented contract."""
    profile = str(contract.get("profile") or model.get("profile") or "").strip()
    durations = [int(value) for value in contract.get("durations") or []]
    resolutions = [
        _normalize_resolution(value)
        for value in contract.get("resolutions") or []
    ]
    ratios = [str(value) for value in contract.get("aspectRatios") or []]
    max_refs = contract.get("maxReferences") or {}

    raw_duration = params.get("duration", params.get("seconds"))
    duration_default = _safe_int(
        params.get("defaultDuration"),
        contract.get("durationDefault")
        if contract.get("durationDefault") is not None
        else (durations[0] if durations else 5),
    )
    duration = _safe_int(raw_duration, duration_default)
    if durations and duration not in durations:
        raise ValueError(
            f"{model.get('remoteModel')} 只支持 {durations} 秒，实际 {duration} 秒"
        )

    resolution = _normalize_resolution(
        params.get("resolution")
        or (resolutions[0] if resolutions else "720p")
    )
    if resolutions and resolution not in resolutions:
        raise ValueError(
            f"{model.get('remoteModel')} 只支持 {resolutions}，实际 {resolution}"
        )

    ratio = str(
        params.get("ratio")
        or params.get("aspect_ratio")
        or (ratios[0] if ratios else "16:9")
    )
    if ratios and ratio not in ratios:
        raise ValueError(
            f"{model.get('remoteModel')} 只支持 {ratios}，实际 {ratio}"
        )

    limits = {
        "images": int(max_refs.get("images") or 0),
        "videos": int(max_refs.get("videos") or 0),
        "audios": int(max_refs.get("audios") or 0),
    }
    actual = {
        "images": len(image_urls),
        "videos": len(video_urls),
        "audios": len(audio_urls),
    }
    for kind, count in actual.items():
        if count > limits[kind]:
            raise ValueError(
                f"{model.get('remoteModel')} 最多 {limits[kind]} 个{kind}，实际 {count}"
            )
    total_max = int(contract.get("referenceTotalMax") or 0)
    if total_max and sum(actual.values()) > total_max:
        raise ValueError(
            f"{model.get('remoteModel')} 参考素材合计最多 {total_max} 个"
        )

    mode = str(params.get("mode") or "auto").strip().lower()
    supported_modes = [str(value) for value in contract.get("modes") or []]
    if template.get("supportsMode"):
        if mode == "auto":
            if not any(actual.values()):
                mode = (
                    "text2video"
                    if "text2video" in supported_modes or not supported_modes
                    else "image2video"
                )
            elif (
                actual["images"] == 2
                and not actual["videos"]
                and not actual["audios"]
                and "frames2video" in supported_modes
            ):
                mode = "frames2video"
            elif (
                actual["images"] == 1
                and not actual["videos"]
                and not actual["audios"]
                and "first-frame-to-video" in supported_modes
                and "image2video" not in supported_modes
            ):
                mode = "first-frame-to-video"
            elif "image2video" in supported_modes:
                mode = "image2video"
            elif "first-frame-to-video" in supported_modes:
                mode = "first-frame-to-video"
            elif "frames2video" in supported_modes:
                mode = "frames2video"
        aliases = {
            "reference": "image2video",
            "keyframe": "image2video",
            "text": "text2video",
        }
        mode = aliases.get(mode, mode)
        if supported_modes and mode not in supported_modes:
            raise ValueError(
                f"{model.get('remoteModel')} 不支持模式 {mode}，可用：{supported_modes}"
            )
    else:
        mode = ""

    body_resolution = (
        resolution.upper()
        if template.get("resolutionCase") == "upper"
        else resolution
    )
    body: dict[str, Any] = {
        "model": model.get("remoteModel"),
        template["durationField"]: duration,
        "resolution": body_resolution,
        template["ratioField"]: ratio,
    }
    prompt_field = str(template.get("promptField") or "prompt")
    if prompt_field == "content":
        media_items: list[dict[str, Any]] = [{"type": "text", "text": str(prompt or "")}]
        for kind, urls in (
            ("image", image_urls),
            ("video", video_urls),
            ("audio", audio_urls),
        ):
            field = {
                "image": template.get("imageField") or "image_url",
                "video": template.get("videoField") or "video_url",
                "audio": template.get("audioField") or "audio_url",
            }[kind]
            role = {
                "image": "reference_image",
                "video": "reference_video",
                "audio": "reference_audio",
            }[kind]
            for url in urls:
                media_items.append({
                    "type": field,
                    field: {"url": url},
                    "role": role,
                })
        if mode == "image2video" and not any(actual.values()):
            raise ValueError(
                f"{model.get('remoteModel')} 仅支持参考素材模式，"
                "至少需要 1 张图片"
            )
        body["content"] = media_items
    else:
        body[prompt_field] = str(prompt or "")
        image_field = str(template.get("imageField") or "images")
        video_field = str(template.get("videoField") or "videos")
        audio_field = str(template.get("audioField") or "audios")
        if image_urls:
            body[image_field] = list(image_urls)
        if video_urls:
            body[video_field] = list(video_urls)
        if audio_urls:
            body[audio_field] = list(audio_urls)
    if template.get("supportsMode"):
        body["mode"] = mode
        if not actual["images"] and not actual["videos"] and not actual["audios"]:
            if mode != "text2video":
                raise ValueError(
                    f"{model.get('remoteModel')} 仅支持参考素材模式，"
                    "至少需要 1 张图片"
                )
    return {
        "profile": profile,
        "path": str(template["submitPath"]),
        "body": body,
        "duration": duration,
        "resolution": resolution,
        "ratio": ratio,
        "mode": mode,
        "taskFields": list(template.get("taskFields") or ["id", "task_id"]),
        "successStatuses": list(template.get("successStatuses") or ["completed"]),
        "failureStatuses": list(template.get("failureStatuses") or ["failed"]),
    }


def build_query_path(template: dict[str, Any], task_id: str) -> str:
    encoded = urllib.parse.quote(str(task_id or ""), safe="")
    return str(template.get("queryPath") or "").replace("{task_id}", encoded)


def task_id_from_response(response: dict[str, Any], fields: list[str]) -> str:
    for field in fields:
        value = response.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""
