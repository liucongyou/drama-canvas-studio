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
    """Compile one exact-model video request through its route compiler."""
    context = _validate_video_request(
        model=model,
        contract=contract,
        template=template,
        prompt=prompt,
        params=params,
        image_urls=image_urls,
        video_urls=video_urls,
        audio_urls=audio_urls,
    )
    compiler = _VIDEO_REQUEST_COMPILERS.get(context["profile"])
    if compiler is None:
        raise ValueError(
            f"New API 视频线路编译器未实现：{context['profile']}"
        )
    return compiler(context)


def _validate_video_request(
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
    """Validate fields shared by every documented New API video route."""
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
    return {
        "profile": profile,
        "model": model,
        "contract": contract,
        "template": template,
        "prompt": str(prompt or ""),
        "params": params,
        "image_urls": list(image_urls),
        "video_urls": list(video_urls),
        "audio_urls": list(audio_urls),
        "duration": duration,
        "resolution": resolution,
        "ratio": ratio,
        "actual": actual,
    }


def _video_result(
    context: dict[str, Any],
    body: dict[str, Any],
    *,
    mode: str = "",
) -> dict[str, Any]:
    template = context["template"]
    return {
        "profile": context["profile"],
        "path": str(template["submitPath"]),
        "body": body,
        "duration": context["duration"],
        "resolution": context["resolution"],
        "ratio": context["ratio"],
        "mode": mode,
        "taskFields": list(template.get("taskFields") or ["id", "task_id"]),
        "successStatuses": list(template.get("successStatuses") or ["completed"]),
        "failureStatuses": list(template.get("failureStatuses") or ["failed"]),
    }


def _compile_suanli_video_request(context: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": context["model"].get("remoteModel"),
        "prompt": context["prompt"],
        "seconds": context["duration"],
        "resolution": context["resolution"],
        "aspect_ratio": context["ratio"],
    }
    if context["image_urls"]:
        body["images"] = list(context["image_urls"])
    if context["video_urls"]:
        body["video_refs"] = list(context["video_urls"])
    if context["audio_urls"]:
        body["audio_refs"] = list(context["audio_urls"])
    return _video_result(context, body)


def _aistars_mode(context: dict[str, Any]) -> str:
    params = context["params"]
    actual = context["actual"]
    supported_modes = [
        str(value)
        for value in context["contract"].get("modes") or []
    ]
    mode = str(params.get("mode") or "auto").strip().lower()
    aliases = {
        "reference": "image2video",
        "keyframe": "image2video",
        "text": "text2video",
    }
    mode = aliases.get(mode, mode)
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

    # Aistars rejects text2video when reference media is present. Older saved
    # nodes can still carry text2video after references were attached, so this
    # route resolves the mode from the actual payload before submission.
    if mode == "text2video" and any(actual.values()):
        if "image2video" in supported_modes:
            mode = "image2video"
        elif "first-frame-to-video" in supported_modes:
            mode = "first-frame-to-video"
        elif "frames2video" in supported_modes:
            mode = "frames2video"
        else:
            raise ValueError(
                f"{context['model'].get('remoteModel')} 仅支持 text2video，"
                "不能提交参考素材"
            )
    if supported_modes and mode not in supported_modes:
        raise ValueError(
            f"{context['model'].get('remoteModel')} 不支持模式 {mode}，"
            f"可用：{supported_modes}"
        )
    if not any(actual.values()) and mode != "text2video":
        raise ValueError(
            f"{context['model'].get('remoteModel')} 仅支持参考素材模式，"
            "至少需要 1 张图片"
        )
    return mode


def _compile_aistars_video_request(context: dict[str, Any]) -> dict[str, Any]:
    mode = _aistars_mode(context)
    body: dict[str, Any] = {
        "model": context["model"].get("remoteModel"),
        "prompt": context["prompt"],
        "aspect_ratio": context["ratio"],
        "resolution": context["resolution"],
        "seconds": context["duration"],
        "mode": mode,
    }
    if context["image_urls"]:
        body["images"] = list(context["image_urls"])
    if context["video_urls"]:
        body["videos"] = list(context["video_urls"])
    if context["audio_urls"]:
        body["audios"] = list(context["audio_urls"])
    return _video_result(context, body, mode=mode)


def _compile_hc_video_request(context: dict[str, Any]) -> dict[str, Any]:
    content: list[dict[str, Any]] = [
        {"type": "text", "text": context["prompt"]}
    ]
    for kind, urls in (
        ("image", context["image_urls"]),
        ("video", context["video_urls"]),
        ("audio", context["audio_urls"]),
    ):
        field = f"{kind}_url"
        role = f"reference_{kind}"
        for url in urls:
            content.append({
                "type": field,
                field: {"url": url},
                "role": role,
            })
    body: dict[str, Any] = {
        "model": context["model"].get("remoteModel"),
        "content": content,
        "duration": context["duration"],
        "resolution": context["resolution"],
        "ratio": context["ratio"],
    }
    return _video_result(context, body)


def _compile_openai_video_request(context: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": context["model"].get("remoteModel"),
        "prompt": context["prompt"],
        "seconds": context["duration"],
        "resolution": context["resolution"].upper(),
        "size": context["ratio"],
    }
    if context["image_urls"]:
        body["images"] = list(context["image_urls"])
    if context["video_urls"]:
        body["videos"] = list(context["video_urls"])
    if context["audio_urls"]:
        body["audios"] = list(context["audio_urls"])
    return _video_result(context, body)


_VIDEO_REQUEST_COMPILERS = {
    "suanli-video": _compile_suanli_video_request,
    "aistars-video": _compile_aistars_video_request,
    "hc-video": _compile_hc_video_request,
    "openai-video": _compile_openai_video_request,
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
