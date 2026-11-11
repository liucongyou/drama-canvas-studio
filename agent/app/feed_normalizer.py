"""Schema-aware feed normalization before the canvas preview gate.

Models are good at content, bad at exact contract shapes. This module fixes only
mechanical, unambiguous issues (图号 format, generator metadata, audio format,
missing optional arrays); it never invents story facts or voice attributes.
"""
from __future__ import annotations

import copy
import re
from typing import Any

AUDIO_SUFFIX = (
    "short drama dubbing reference clip, 4.5 seconds clean voice, no background noise."
)
_CODE_RE = re.compile(r"^(?:图\s*)?0*(\d{1,3})$")
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def canonical_code(value: Any, index: int = 0) -> str:
    text = str(value or "").strip()
    match = _CODE_RE.fullmatch(text)
    if match:
        return f"图{int(match.group(1))}"
    if text in _CN_NUM:
        return f"图{_CN_NUM[text]}"
    return f"图{index + 1}"


def normalize_feed(
    feed: dict[str, Any],
    *,
    skill_id: str = "",
    skill_version: str = "",
    package_revision: str = "",
) -> dict[str, Any]:
    if not isinstance(feed, dict):
        return {}
    out = copy.deepcopy(feed)
    contract = out.get("canvas_contract")
    if not isinstance(contract, dict):
        contract = {}
        out["canvas_contract"] = contract
    contract.setdefault("schema", "drama-canvas-feed/v2")
    contract.setdefault("video_model", "doubao-seedance-2-0")
    contract.setdefault("video_aspect", "9:16")
    generator = contract.get("generator")
    if not isinstance(generator, dict) or not generator.get("name"):
        contract["generator"] = {
            "name": skill_id or "canvas-agent",
            "skillVersion": skill_version or "0.0.0",
            "packageRevision": package_revision or skill_version or "agent",
        }

    assets = out.get("asset_prompts")
    if not isinstance(assets, list):
        assets = []
    normalized_assets: list[dict[str, Any]] = []
    code_map: dict[str, str] = {}
    for index, item in enumerate(assets):
        if not isinstance(item, dict):
            continue
        current = dict(item)
        old = str(current.get("code") or "").strip()
        new = canonical_code(old, index)
        current["code"] = new
        if not str(current.get("raw") or "").strip():
            current["raw"] = str(current.get("description") or current.get("name") or new)
        raw = str(current.get("raw") or "")
        name = str(current.get("name") or "").strip()
        is_character = (
            "@//chr/" in raw
            or any(marker in raw for marker in ("角色设定板", "角色立绘", "人物设定", "人物立绘", "角色形象"))
        )
        if is_character and "角色立绘" not in name:
            current["name"] = (name or new) + "（角色立绘）"
        code_map[old] = new
        normalized_assets.append(current)
    out["asset_prompts"] = normalized_assets

    raw_segments = out.get("segments") if isinstance(out.get("segments"), list) else []
    speakers = set()
    for segment in raw_segments:
        if isinstance(segment, dict):
            for match in re.finditer(r"台词[:：]\s*([^（(：:\n]+)", str(segment.get("raw") or "")):
                speakers.add(match.group(1).strip())

    voices = out.get("voice_prompts")
    if not isinstance(voices, list):
        voices = []
    normalized_voices: list[dict[str, Any]] = []
    for index, item in enumerate(voices):
        if not isinstance(item, dict):
            continue
        current = dict(item)
        old = str(current.get("code") or "").strip()
        # Match by name to an asset when the model omitted the code.
        matched = next(
            (asset["code"] for asset in normalized_assets
             if asset.get("name") and asset.get("name") == current.get("name")),
            "",
        )
        current["code"] = canonical_code(old, index) if old else (matched or f"图{index + 1}")
        voice_name = str(current.get("name") or "").strip()
        if voice_name and voice_name not in speakers:
            role_match = next(
                (speaker for speaker in speakers
                 if voice_name.startswith(speaker + "｜") or voice_name.startswith(speaker + "|")),
                "",
            )
            if role_match:
                current["name"] = role_match
        current["format"] = "audio-prompt-v1"
        prompt = str(current.get("prompt") or "").strip()
        age_raw = current.get("age")
        age = ""
        if age_raw not in (None, ""):
            try:
                age = str(int(float(age_raw)))
            except (TypeError, ValueError):
                age = str(age_raw).strip()
            current["age"] = age
        gender = str(current.get("gender") or "").strip()
        if age and gender and f"A {gender} aged {age}" not in prompt:
            prompt = f"A {gender} aged {age}. " + prompt
        if AUDIO_SUFFIX not in prompt:
            current["prompt"] = (prompt + " " + AUDIO_SUFFIX).strip()
        if not str(current.get("label") or "").strip():
            current["label"] = str(current.get("name") or "voice")
        if not str(current.get("description") or "").strip():
            current["description"] = str(current.get("label") or current.get("name") or "voice")
        normalized_voices.append(current)
    out["voice_prompts"] = normalized_voices

    segments = out.get("segments")
    if not isinstance(segments, list):
        segments = []
    voice_by_name = {
        str(item.get("name") or "").strip(): str(item.get("code") or "").strip()
        for item in normalized_voices
        if str(item.get("name") or "").strip()
    }
    normalized_segments = []
    for index, item in enumerate(segments):
        if not isinstance(item, dict):
            continue
        current = dict(item)
        current.setdefault("no", index + 1)
        current.setdefault("name", f"段{index + 1}")
        raw = str(current.get("raw") or "")
        for old, new in code_map.items():
            if old and old != new and old in raw:
                raw = raw.replace(old, new)
        speakers: list[str] = []
        for match in re.finditer(r"台词[:：]\s*([^（(：:\n]+)", raw):
            name = match.group(1).strip()
            if name and name not in speakers:
                speakers.append(name)
        raw = re.sub(r"(?m)^【音色引用】[^\n]*(?:\n|$)", "", raw)
        references = [
            f"@音频｜{name}（{voice_by_name[name]}）"
            for name in speakers
            if voice_by_name.get(name)
        ]
        if references:
            section = "【音色引用】" + "；".join(references)
            core = re.search(r"(?m)^【核心人物】[^\n]*(?:\n|$)", raw)
            if core:
                raw = raw[:core.end()] + section + "\n" + raw[core.end():]
            else:
                raw = section + "\n" + raw.lstrip("\n")
        current["raw"] = raw
        normalized_segments.append(current)
    out["segments"] = normalized_segments
    return out


def heal_voice_prompts(feed: dict[str, Any]) -> dict[str, Any]:
    """Create draft voice entries for dialogue speakers with character assets.

    This is a bounded recovery path: it only runs after the LLM repair loop still
    reports voice errors, and only when a matching character asset can provide
    age/gender clues. It never changes dialogue or story facts.
    """
    out = copy.deepcopy(feed)
    assets = [item for item in out.get("asset_prompts") or [] if isinstance(item, dict)]
    voices = [item for item in out.get("voice_prompts") or [] if isinstance(item, dict)]
    existing = {str(item.get("name") or "").strip() for item in voices}
    speakers = set()
    for segment in out.get("segments") or []:
        if isinstance(segment, dict):
            for match in re.finditer(r"台词[:：]\s*([^（(：:\n]+)", str(segment.get("raw") or "")):
                speakers.add(match.group(1).strip())
    changed = False
    for speaker in sorted(speakers):
        if not speaker or speaker in existing:
            continue
        asset = next(
            (
                item for item in assets
                if str(item.get("name") or "").strip() == speaker
                or str(item.get("name") or "").strip().startswith(speaker + "｜")
                or str(item.get("name") or "").strip().startswith(speaker + "（")
            ),
            None,
        )
        if not asset:
            continue
        raw = str(asset.get("raw") or "")
        age_match = re.search(r"(\d{1,2})\s*岁", raw)
        age = age_match.group(1) if age_match else "30"
        if re.search(r"女性|女|female", raw, re.IGNORECASE):
            gender = "female"
        elif re.search(r"男性|男|male", raw, re.IGNORECASE):
            gender = "male"
        else:
            continue
        code = str(asset.get("code") or "").strip()
        voices.append({
            "code": code,
            "name": speaker,
            "age": age,
            "gender": gender,
            "dialect": "standard",
            "format": "audio-prompt-v1",
            "prompt": f"A {gender} aged {age}, speaking standard Mandarin. {AUDIO_SUFFIX}",
            "label": speaker + " 声线（自动补全，待确认）",
            "description": speaker + " 的音色档案由角色资产自动补全，建议人工确认。",
        })
        existing.add(speaker)
        changed = True
    out["voice_prompts"] = voices
    return out if changed else feed
