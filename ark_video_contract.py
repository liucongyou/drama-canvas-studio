from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ArkVideoContract:
    version: str
    max_images: int
    max_videos: int
    max_audios: int
    max_total_refs: int
    duration_min: int
    duration_max: int
    recommended_prompt_chars: int
    max_prompt_chars: int
    supports_audio_only: bool
    supports_audio: bool
    supports_seed: bool
    supports_omni_reference_type: bool
    supports_output_format: bool
    default_ratio: str
    default_resolution: str


_CONTRACTS = {
    "2.5": ArkVideoContract(
        version="2.5",
        max_images=30,
        max_videos=10,
        max_audios=10,
        max_total_refs=50,
        duration_min=4,
        duration_max=30,
        recommended_prompt_chars=500,
        max_prompt_chars=4000,
        supports_audio_only=True,
        supports_audio=True,
        supports_seed=False,
        supports_omni_reference_type=True,
        supports_output_format=True,
        default_ratio="adaptive",
        default_resolution="720p",
    ),
    "2.0": ArkVideoContract(
        version="2.0",
        max_images=9,
        max_videos=3,
        max_audios=3,
        max_total_refs=15,
        duration_min=4,
        duration_max=15,
        recommended_prompt_chars=500,
        max_prompt_chars=4000,
        supports_audio_only=False,
        supports_audio=True,
        supports_seed=False,
        supports_omni_reference_type=False,
        supports_output_format=False,
        default_ratio="adaptive",
        default_resolution="720p",
    ),
    "1.0": ArkVideoContract(
        version="1.0",
        max_images=1,
        max_videos=0,
        max_audios=0,
        max_total_refs=1,
        duration_min=2,
        duration_max=12,
        recommended_prompt_chars=500,
        max_prompt_chars=4000,
        supports_audio_only=False,
        supports_audio=False,
        supports_seed=True,
        supports_omni_reference_type=False,
        supports_output_format=False,
        default_ratio="16:9",
        default_resolution="1080p",
    ),
}


def classify_model(remote_model: Any) -> ArkVideoContract:
    name = str(remote_model or "").lower()
    match = re.search(r"seedance[-_ ]?(\d+)[._-](\d+)", name)
    if match:
        family = f"{int(match.group(1))}.{int(match.group(2))}"
        if family in _CONTRACTS:
            return _CONTRACTS[family]
    if "seedance-2-5" in name or "seedance_2_5" in name:
        return _CONTRACTS["2.5"]
    if "seedance-2-0" in name or "seedance_2_0" in name:
        return _CONTRACTS["2.0"]
    if "seedance-1-0" in name or "seedance_1_0" in name:
        return _CONTRACTS["1.0"]
    raise ValueError(f"无法确认 Seedance 模型能力：{remote_model or 'unknown'}")


def _text_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [ref for ref in refs if str(ref.get("kind") or "image") == "text"]


def validate_submission(
    *,
    remote_model: Any,
    prompt: str,
    refs: list[dict[str, Any]],
    voices: list[dict[str, Any]],
    params: dict[str, Any],
) -> ArkVideoContract:
    contract = classify_model(remote_model)
    if not str(prompt or "").strip():
        raise ValueError("提示词为空，不能提交视频生成")
    if len(str(prompt)) > contract.max_prompt_chars:
        raise ValueError(
            f"Seedance {contract.version} 单次提示词超过安全上限 "
            f"{contract.max_prompt_chars} 字符，当前 {len(str(prompt))} 字符；"
            "请拆分镜头或精简重复的画面复述"
        )
    image_count = sum(
        1 for ref in refs if str(ref.get("kind") or "image") == "image"
    )
    video_count = sum(1 for ref in refs if str(ref.get("kind")) == "video")
    audio_count = len(voices) + sum(
        1 for ref in refs if str(ref.get("kind")) == "audio"
    )
    if image_count > contract.max_images:
        raise ValueError(
            f"Seedance {contract.version} 最多 {contract.max_images} 张参考图，"
            f"当前 {image_count} 张"
        )
    if video_count > contract.max_videos:
        raise ValueError(
            f"Seedance {contract.version} 最多 {contract.max_videos} 个参考视频，"
            f"当前 {video_count} 个"
        )
    if audio_count and not contract.supports_audio:
        raise ValueError(f"Seedance {contract.version} 不支持参考音频")
    if audio_count > contract.max_audios:
        raise ValueError(
            f"Seedance {contract.version} 最多 {contract.max_audios} 段参考音频，"
            f"当前 {audio_count} 段"
        )
    total = image_count + video_count + audio_count
    if total > contract.max_total_refs:
        raise ValueError(
            f"Seedance {contract.version} 最多 {contract.max_total_refs} 个参考素材，"
            f"当前 {total} 个"
        )
    if audio_count and not image_count and not video_count and not contract.supports_audio_only:
        raise ValueError(f"Seedance {contract.version} 不能只传音频，至少需要 1 张图或 1 个视频")
    raw_duration = params.get("duration")
    if raw_duration not in (None, "", -1):
        try:
            duration = int(raw_duration)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"视频时长必须是整数秒：{raw_duration}") from exc
        if duration < contract.duration_min:
            # Keep the established UX: a too-short request is raised to the
            # documented minimum instead of failing after the user clicks.
            duration = contract.duration_min
        elif duration > contract.duration_max:
            raise ValueError(
                f"Seedance {contract.version} 时长必须在 "
                f"{contract.duration_min}-{contract.duration_max} 秒，当前 {duration}"
            )
    return contract


def normalize_params(
    contract: ArkVideoContract,
    params: dict[str, Any],
) -> dict[str, Any]:
    raw_duration = params.get("duration")
    if raw_duration in (None, ""):
        duration = contract.duration_min
    else:
        try:
            duration = int(raw_duration)
        except (TypeError, ValueError):
            duration = contract.duration_min
        if duration != -1:
            duration = max(contract.duration_min, min(contract.duration_max, duration))
    resolution = str(params.get("resolution") or contract.default_resolution).upper()
    ratio = str(params.get("ratio") or contract.default_ratio)
    return {**params, "duration": duration, "resolution": resolution, "ratio": ratio}
