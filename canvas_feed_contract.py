# -*- coding: utf-8 -*-
"""Shared contract codes and TDD seam for canvas-feed import validation."""
from __future__ import annotations

import re
import copy
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    FEED_PARSE_ERROR = "FEED_PARSE_ERROR"
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    SCHEMA_VERSION_UNSUPPORTED = "SCHEMA_VERSION_UNSUPPORTED"
    FEED_PATH_FORBIDDEN = "FEED_PATH_FORBIDDEN"
    SEGMENT_INVALID = "SEGMENT_INVALID"
    SEGMENT_NO_DUPLICATE = "SEGMENT_NO_DUPLICATE"
    ASSET_CODE_INVALID = "ASSET_CODE_INVALID"
    ASSET_CODE_DUPLICATE = "ASSET_CODE_DUPLICATE"
    ASSET_DECLARED_MISSING = "ASSET_DECLARED_MISSING"
    ASSET_EDGE_MISMATCH = "ASSET_EDGE_MISMATCH"
    VOICE_PROMPT_MISSING = "VOICE_PROMPT_MISSING"
    VOICE_PROMPT_INCOMPLETE = "VOICE_PROMPT_INCOMPLETE"
    VOICE_DIALECT_MISSING = "VOICE_DIALECT_MISSING"
    VOICE_FIELD_CONFLICT = "VOICE_FIELD_CONFLICT"
    VOICE_CODE_CONFLICT = "VOICE_CODE_CONFLICT"
    VOICE_ROLE_DUPLICATE = "VOICE_ROLE_DUPLICATE"
    VOICE_DECLARED_MISSING = "VOICE_DECLARED_MISSING"
    VOICE_REF_MISMATCH = "VOICE_REF_MISMATCH"
    VOICE_EDGE_MISMATCH = "VOICE_EDGE_MISMATCH"
    REVISION_CONFLICT = "REVISION_CONFLICT"


class WarningCode(StrEnum):
    WARN_DEPRECATED_VOICE_FIELD = "WARN_DEPRECATED_VOICE_FIELD"
    WARN_DERIVED_DIALECT = "WARN_DERIVED_DIALECT"
    WARN_GENERATOR_METADATA_MISSING = "WARN_GENERATOR_METADATA_MISSING"
    WARN_CODE_CANONICALIZED = "WARN_CODE_CANONICALIZED"
    WARN_RETRIED_AFTER_REVISION = "WARN_RETRIED_AFTER_REVISION"
    WARN_ASSET_IDENTITY_AUTO_CORRECTED = "WARN_ASSET_IDENTITY_AUTO_CORRECTED"


@dataclass(frozen=True)
class ValidationIssue:
    code: ErrorCode | WarningCode
    message: str
    path: str = ""
    segment: int | None = None
    role: str = ""
    node_code: str = ""


@dataclass(frozen=True)
class SegmentExpectation:
    segment_index: int
    video_no: int
    raw: str
    speakers: frozenset[str]
    audio_codes: frozenset[str]
    asset_codes: frozenset[str]


@dataclass(frozen=True)
class DeclaredExpectations:
    segments: tuple[SegmentExpectation, ...]
    warnings: tuple[ValidationIssue, ...]
    has_incoming_assets: bool = False


@dataclass(frozen=True)
class GateResult:
    issues: tuple[ValidationIssue, ...]
    expectations: DeclaredExpectations

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if isinstance(issue.code, ErrorCode)]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if isinstance(issue.code, WarningCode)]


class RevisionConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImportPlan:
    project_id: str
    revision: int
    episode_id: str
    merged_payload: dict | None
    errors: tuple[ValidationIssue, ...]
    warnings: tuple[ValidationIssue, ...]
    summary: dict
    provenance: dict

    @property
    def ok(self) -> bool:
        return not self.errors and isinstance(self.merged_payload, dict)


def issue_dict(issue: ValidationIssue) -> dict:
    return {
        "code": str(issue.code),
        "message": issue.message,
        "path": issue.path,
        "segment": issue.segment,
        "role": issue.role,
        "nodeCode": issue.node_code,
    }


SCHEMA_VERSION = "drama-canvas-feed/v2"
AUDIO_PROMPT_FORMAT = "audio-prompt-v1"
AUDIO_PROMPT_SUFFIX = (
    "short drama dubbing reference clip, 4.5 seconds clean voice, no background noise."
)
LABELED_DIALOG_RE = re.compile(
    r"台词[:：]\s*"
    r"(?:[【\[［]\s*)?"
    r"([^（(：:\n【】\[\]［］]{1,12}?)"
    r"\s*(?:[】\]］]\s*)?"
    r"[（(][^）)\n]*[）)]\s*"
    r"[:：]\s*[\"'“”‘’『「]"
)
BARE_DIALOG_RE = re.compile(
    r"^[ \t]*(?:[-*][ \t]+)?"
    r"(?:[【\[［]\s*)?"
    r"([^（(：:\n【】\[\]［］]{1,12}?)"
    r"\s*(?:[】\]］]\s*)?"
    r"[（(][^）)\n]*[）)]\s*"
    r"[:：]\s*[\"'“”‘’『「]",
    re.MULTILINE,
)
AUDIO_REF_RE = re.compile(r"@音频｜([^（(\n]+)[（(]图\s*(\d+)[）)]")
ASSET_REF_RE = re.compile(r"@(?:角色|场景|道具)｜[^（(\n]+[（(](?:参考)?图\s*(\d+)[）)]")
LEGACY_ASSET_REF_RE = re.compile(
    r"@//(?:chr|scn|prp)\s*/\s*-\s*[^（(\n]+[（(](?:参考)?图\s*(\d+)[）)]"
)
CODE_RE = re.compile(r"图\s*(\d+)")
SEMANTIC_CODE_RE = re.compile(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*")
TITLE_CODE_RE = re.compile(r"[（(]图\s*(\d+)[）)]")
TITLE_ROLE_RE = re.compile(r"^(?:音频|角色|场景|道具)｜([^（(\n]+)")
BAD_AGE_RE = re.compile(
    r"A \d+(?:-\d+)?-year-old (?:male|female)|A (?:male|female) in his ",
    re.IGNORECASE,
)
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "canvas_feed.schema.json"


def canonical_code(value: Any) -> str:
    """Normalize asset codes so 图9, 图09, and 图 9 share one identity."""
    match = CODE_RE.fullmatch(str(value or "").strip())
    if not match:
        return ""
    return f"图{int(match.group(1))}"


def canonical_code_from_text(value: Any) -> str:
    match = CODE_RE.search(str(value or ""))
    return canonical_code(match.group(0)) if match else ""


def canonical_semantic_code(value: Any) -> str:
    """Return a stable asset code such as CHR-001 when one is present."""
    text = str(value or "").strip().upper()
    return text if SEMANTIC_CODE_RE.fullmatch(text) else ""


def dialogue_speakers(text: str) -> set[str]:
    speakers = {
        match.group(1).strip()
        for match in LABELED_DIALOG_RE.finditer(str(text or ""))
        if match.group(1).strip()
    }
    speakers.update(
        match.group(1).strip()
        for match in BARE_DIALOG_RE.finditer(str(text or ""))
        if match.group(1).strip()
    )
    return speakers


def asset_ref_codes(text: str) -> set[str]:
    codes = {
        canonical_code(f"图{code}")
        for code in ASSET_REF_RE.findall(str(text or ""))
    }
    codes.update(
        canonical_code(f"图{code}")
        for code in LEGACY_ASSET_REF_RE.findall(str(text or ""))
    )
    codes.discard("")
    return codes


def audio_ref_entries(text: str) -> set[tuple[str, str]]:
    entries = set()
    for match in AUDIO_REF_RE.finditer(str(text or "")):
        code = canonical_code(f"图{match.group(2)}")
        if code:
            entries.add((match.group(1).strip(), code))
    return entries


def _issue(
    code: ErrorCode | WarningCode,
    message: str,
    *,
    path: str = "",
    segment: int | None = None,
    role: str = "",
    node_code: str = "",
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        message=message,
        path=path,
        segment=segment,
        role=role,
        node_code=node_code,
    )


@lru_cache(maxsize=1)
def load_feed_schema() -> dict:
    try:
        payload = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法加载画布载荷 Schema：{exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("画布载荷 Schema 根节点必须是对象")
    return payload


def _schema_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _schema_validate(
    value: Any,
    schema: dict,
    path: str,
    issues: list[ValidationIssue],
) -> None:
    if not isinstance(schema, dict):
        return
    expected_type = schema.get("type")
    if expected_type:
        expected_types = (
            expected_type if isinstance(expected_type, list) else [expected_type]
        )
        if not any(_schema_type_matches(value, str(item)) for item in expected_types):
            issues.append(_issue(
                ErrorCode.SCHEMA_VALIDATION_FAILED,
                f"字段类型应为 {'/'.join(map(str, expected_types))}",
                path=path or "$",
            ))
            return
    if "const" in schema and value != schema["const"]:
        issues.append(_issue(
            ErrorCode.SCHEMA_VALIDATION_FAILED,
            f"字段必须等于 {schema['const']!r}",
            path=path or "$",
        ))
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and value not in enum_values:
        issues.append(_issue(
            ErrorCode.SCHEMA_VALIDATION_FAILED,
            f"字段值必须是 {enum_values!r} 之一",
            path=path or "$",
        ))
    if isinstance(value, str):
        minimum_length = schema.get("minLength")
        if isinstance(minimum_length, int) and len(value) < minimum_length:
            issues.append(_issue(
                ErrorCode.SCHEMA_VALIDATION_FAILED,
                f"字符串长度不能小于 {minimum_length}",
                path=path or "$",
            ))
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                matches = re.fullmatch(pattern, value) is not None
            except re.error:
                matches = True
            if not matches:
                issues.append(_issue(
                    ErrorCode.SCHEMA_VALIDATION_FAILED,
                    f"字符串格式不匹配：{pattern}",
                    path=path or "$",
                ))
    if isinstance(value, dict):
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if key not in value:
                    child = f"{path}.{key}" if path else str(key)
                    issues.append(_issue(
                        ErrorCode.SCHEMA_VALIDATION_FAILED,
                        f"缺少必填字段：{key}",
                        path=child,
                    ))
        properties = schema.get("properties")
        properties = properties if isinstance(properties, dict) else {}
        for key, child_schema in properties.items():
            if key in value:
                child = f"{path}.{key}" if path else key
                _schema_validate(value[key], child_schema, child, issues)
        if schema.get("additionalProperties") is False:
            for key in sorted(set(value) - set(properties)):
                child = f"{path}.{key}" if path else str(key)
                issues.append(_issue(
                    ErrorCode.SCHEMA_VALIDATION_FAILED,
                    f"不允许的字段：{key}",
                    path=child,
                ))
    if isinstance(value, list):
        minimum_items = schema.get("minItems")
        if isinstance(minimum_items, int) and len(value) < minimum_items:
            issues.append(_issue(
                ErrorCode.SCHEMA_VALIDATION_FAILED,
                f"数组至少需要 {minimum_items} 项",
                path=path or "$",
            ))
        if schema.get("uniqueItems") is True:
            serialized = [
                json.dumps(item, ensure_ascii=False, sort_keys=True)
                for item in value
            ]
            if len(serialized) != len(set(serialized)):
                issues.append(_issue(
                    ErrorCode.SCHEMA_VALIDATION_FAILED,
                    "数组项不能重复",
                    path=path or "$",
                ))
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _schema_validate(
                    item,
                    item_schema,
                    f"{path}[{index}]" if path else f"[{index}]",
                    issues,
                )


def validate_feed_schema(payload: Any) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    try:
        schema = load_feed_schema()
    except RuntimeError as exc:
        return [_issue(ErrorCode.SCHEMA_VALIDATION_FAILED, str(exc), path="$")]
    _schema_validate(payload, schema, "", issues)
    return issues


def _canonical_field(
    item: dict,
    canonical: str,
    aliases: tuple[str, ...],
    path: str,
    issues: list[ValidationIssue],
) -> Any:
    canonical_value = item.get(canonical)
    alias_values = [
        item.get(alias)
        for alias in aliases
        if item.get(alias) not in (None, "")
    ]
    if canonical_value not in (None, "") and any(
        value not in (None, "") and value != canonical_value
        for value in alias_values
    ):
        issues.append(_issue(
            ErrorCode.VOICE_FIELD_CONFLICT,
            f"{canonical} 与旧字段值冲突",
            path=path,
        ))
    if canonical_value not in (None, ""):
        return canonical_value
    if alias_values:
        issues.append(_issue(
            WarningCode.WARN_DEPRECATED_VOICE_FIELD,
            f"请将 {aliases[0]} 改为 {canonical}",
            path=path,
        ))
        return alias_values[0]
    return ""


def _normalize_age(value: Any) -> str:
    return re.sub(r"\s*[~～—–-]\s*", "-", str(value or "").strip())


def analyze_declared_feed(payload: dict) -> GateResult:
    """Gate 1: validate declarations without touching store or derived edges."""
    issues: list[ValidationIssue] = validate_feed_schema(payload)
    if not isinstance(payload, dict):
        return GateResult(
            issues=tuple(issues),
            expectations=DeclaredExpectations(segments=(), warnings=()),
        )
    contract = payload.get("canvas_contract") if isinstance(payload, dict) else None
    schema = str((contract or {}).get("schema") or "") if isinstance(contract, dict) else ""
    if schema != SCHEMA_VERSION:
        issues.append(_issue(
            ErrorCode.SCHEMA_VERSION_UNSUPPORTED,
            f"仅支持 {SCHEMA_VERSION}，收到 {schema or '<missing>'}",
            path="canvas_contract.schema",
        ))
    generator = contract.get("generator") if isinstance(contract, dict) else None
    if not isinstance(generator, dict) or not any(
        str(generator.get(key) or "").strip()
        for key in ("name", "skillVersion", "packageRevision")
    ):
        issues.append(_issue(
            WarningCode.WARN_GENERATOR_METADATA_MISSING,
            "缺少 canvas_contract.generator，无法追踪技能名称和版本",
            path="canvas_contract.generator",
        ))

    assets = payload.get("asset_prompts") if isinstance(payload, dict) else None
    voices = payload.get("voice_prompts") if isinstance(payload, dict) else None
    segments = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(assets, list):
        issues.append(_issue(ErrorCode.FEED_PARSE_ERROR, "asset_prompts 必须是数组", path="asset_prompts"))
        assets = []
    if not isinstance(voices, list):
        issues.append(_issue(ErrorCode.VOICE_PROMPT_MISSING, "voice_prompts 必须是数组", path="voice_prompts"))
        voices = []
    if not isinstance(segments, list) or not segments:
        issues.append(_issue(ErrorCode.SEGMENT_INVALID, "segments 必须是非空数组", path="segments"))
        segments = []

    asset_code_role: dict[str, str] = {}
    seen_asset_codes: set[str] = set()
    for index, asset in enumerate(assets):
        path = f"asset_prompts[{index}]"
        if not isinstance(asset, dict):
            issues.append(_issue(ErrorCode.FEED_PARSE_ERROR, "资产项必须是对象", path=path))
            continue
        code_match = CODE_RE.fullmatch(str(asset.get("code") or "").strip())
        if not code_match:
            issues.append(_issue(
                ErrorCode.ASSET_CODE_INVALID,
                f"资产图号非法：{asset.get('code')!r}",
                path=f"{path}.code",
            ))
            continue
        code = canonical_code(code_match.group(0))
        if code in seen_asset_codes:
            issues.append(_issue(
                ErrorCode.ASSET_CODE_DUPLICATE,
                f"资产图号重复：{code}",
                path=path,
                node_code=code,
            ))
        seen_asset_codes.add(code)
        role = TITLE_ROLE_RE.match(str(asset.get("name") or "")).group(1) if TITLE_ROLE_RE.match(str(asset.get("name") or "")) else str(asset.get("name") or "").strip()
        role = re.sub(r"[（(].*?[)）]", "", role).strip()
        if role:
            asset_code_role[code] = role
        if not str(asset.get("raw") or "").strip():
            issues.append(_issue(ErrorCode.FEED_PARSE_ERROR, "资产 raw 为空", path=f"{path}.raw"))

    voice_by_name: dict[str, dict] = {}
    voice_by_code: dict[str, dict] = {}
    for index, voice in enumerate(voices):
        path = f"voice_prompts[{index}]"
        if not isinstance(voice, dict):
            issues.append(_issue(ErrorCode.FEED_PARSE_ERROR, "音色项必须是对象", path=path))
            continue
        name = str(voice.get("name") or "").strip()
        if not name:
            name = str(voice.get("title") or "").split("｜", 1)[-1]
            name = re.sub(r"[（(]图.*$", "", name).strip()
        if not name:
            issues.append(_issue(ErrorCode.VOICE_PROMPT_INCOMPLETE, "音色角色名为空", path=path))
            continue
        if name in voice_by_name:
            issues.append(_issue(ErrorCode.VOICE_ROLE_DUPLICATE, f"音色角色重复：{name}", path=path, role=name))
        code_value = str(voice.get("code") or "").strip()
        code_match = CODE_RE.fullmatch(code_value)
        if code_match:
            code = canonical_code(code_match.group(0))
        else:
            code = ""
            issues.append(_issue(ErrorCode.VOICE_PROMPT_INCOMPLETE, "音色图号非法", path=f"{path}.code", role=name))
        prompt = str(_canonical_field(voice, "prompt", ("voicePrompt",), f"{path}.prompt", issues) or "").strip()
        description = str(_canonical_field(voice, "description", ("voiceDescription",), f"{path}.description", issues) or "").strip()
        label = str(_canonical_field(voice, "label", ("voiceLabel",), f"{path}.label", issues) or "").strip()
        age = _normalize_age(_canonical_field(voice, "age", ("voiceAge",), f"{path}.age", issues))
        gender = str(_canonical_field(voice, "gender", ("voiceGender",), f"{path}.gender", issues) or "").strip().lower()
        dialect = str(_canonical_field(voice, "dialect", ("voiceDialect",), f"{path}.dialect", issues) or "").strip()
        fmt = str(voice.get("format") or "").strip()
        if not dialect:
            evidence = f"{description}\n{prompt}".casefold()
            if "northeastern" in evidence:
                dialect = "northeastern-accented"
                issues.append(_issue(
                    WarningCode.WARN_DERIVED_DIALECT,
                    "dialect 从文本明确证据派生为 northeastern-accented",
                    path=f"{path}.dialect",
                    role=name,
                ))
            elif "standard mandarin" in evidence:
                dialect = "standard"
                issues.append(_issue(
                    WarningCode.WARN_DERIVED_DIALECT,
                    "dialect 从文本明确证据派生为 standard",
                    path=f"{path}.dialect",
                    role=name,
                ))
        if not description or not label:
            issues.append(_issue(ErrorCode.VOICE_PROMPT_INCOMPLETE, "音色 description 或 label 为空", path=path, role=name))
        if not age or not gender or not dialect:
            missing = []
            if not age:
                missing.append("age")
            if not gender:
                missing.append("gender")
            if not dialect:
                missing.append("dialect")
            code = ErrorCode.VOICE_DIALECT_MISSING if missing == ["dialect"] else ErrorCode.VOICE_PROMPT_INCOMPLETE
            issues.append(_issue(code, f"音色缺字段：{', '.join(missing)}", path=path, role=name))
        if gender and gender not in {"male", "female"}:
            issues.append(_issue(ErrorCode.VOICE_PROMPT_INCOMPLETE, f"gender 非法：{gender}", path=f"{path}.gender", role=name))
        if fmt != AUDIO_PROMPT_FORMAT:
            issues.append(_issue(
                ErrorCode.VOICE_PROMPT_INCOMPLETE,
                f"format 必须是 {AUDIO_PROMPT_FORMAT}",
                path=f"{path}.format",
                role=name,
            ))
        if not prompt.casefold().endswith(AUDIO_PROMPT_SUFFIX.casefold()):
            issues.append(_issue(ErrorCode.VOICE_PROMPT_INCOMPLETE, "prompt 缺固定音频结尾", path=f"{path}.prompt", role=name))
        if BAD_AGE_RE.search(prompt):
            issues.append(_issue(ErrorCode.VOICE_PROMPT_INCOMPLETE, "prompt 使用了旧年龄句式", path=f"{path}.prompt", role=name))
        if age and gender and f"A {gender} aged {age}".casefold() not in prompt.casefold():
            issues.append(_issue(ErrorCode.VOICE_PROMPT_INCOMPLETE, f"prompt 缺身份格式 A {gender} aged {age}", path=f"{path}.prompt", role=name))
        if code and code in asset_code_role and asset_code_role[code] != name:
            issues.append(_issue(
                ErrorCode.VOICE_CODE_CONFLICT,
                f"音色图号 {code} 属于 {asset_code_role[code]}，不是 {name}",
                path=f"{path}.code",
                role=name,
                node_code=code,
            ))
        if code and code in voice_by_code and voice_by_code[code].get("name") != name:
            issues.append(_issue(
                ErrorCode.VOICE_CODE_CONFLICT,
                f"多个角色共用音色图号 {code}",
                path=f"{path}.code",
                role=name,
                node_code=code,
            ))
        normalized_voice = {
            **voice,
            "name": name,
            "code": code,
            "prompt": prompt,
            "description": description,
            "label": label,
            "age": age,
            "gender": gender,
            "dialect": dialect,
        }
        voice_by_name[name] = normalized_voice
        if code:
            voice_by_code[code] = normalized_voice

    segment_expectations: list[SegmentExpectation] = []
    speaker_names: set[str] = set()
    seen_video_numbers: dict[int, int] = {}
    for index, segment in enumerate(segments):
        path = f"segments[{index}]"
        if not isinstance(segment, dict):
            issues.append(_issue(ErrorCode.SEGMENT_INVALID, "分段必须是对象", path=path))
            continue
        raw = str(segment.get("raw") or "")
        if not raw.strip():
            issues.append(_issue(ErrorCode.SEGMENT_INVALID, "分段 raw 为空", path=f"{path}.raw", segment=index))
            continue
        speakers = dialogue_speakers(raw)
        speaker_names |= speakers
        audio_refs = audio_ref_entries(raw)
        audio_roles = {role for role, _code in audio_refs}
        if speakers != audio_roles:
            missing = sorted(speakers - audio_roles)
            extra = sorted(audio_roles - speakers)
            detail = []
            if missing:
                detail.append(f"缺 {', '.join(missing)}")
            if extra:
                detail.append(f"多 {', '.join(extra)}")
            issues.append(_issue(
                ErrorCode.VOICE_DECLARED_MISSING,
                f"对白角色与【音色引用】不一致：{'; '.join(detail)}",
                path=f"{path}.raw",
                segment=index,
            ))
        for role, code in audio_refs:
            voice = voice_by_name.get(role)
            if not voice:
                issues.append(_issue(
                    ErrorCode.VOICE_PROMPT_MISSING,
                    f"{role} 缺 voice_prompts",
                    path=f"{path}.raw",
                    segment=index,
                    role=role,
                    node_code=code,
                ))
            elif voice.get("code") and voice.get("code") != code:
                issues.append(_issue(
                    ErrorCode.VOICE_CODE_CONFLICT,
                    f"{role} 声明 {code}，voice_prompts 是 {voice.get('code')}",
                    path=f"{path}.raw",
                    segment=index,
                    role=role,
                    node_code=code,
                ))
        try:
            video_no = int(segment.get("no"))
        except (TypeError, ValueError):
            video_no = index + 1
        if video_no in seen_video_numbers:
            issues.append(_issue(
                ErrorCode.SEGMENT_NO_DUPLICATE,
                f"段号 {video_no} 重复，首次出现在 segments[{seen_video_numbers[video_no]}]",
                path=f"{path}.no",
                segment=index,
            ))
        else:
            seen_video_numbers[video_no] = index
        asset_codes = asset_ref_codes(raw)
        for role, voice in voice_by_name.items():
            code = str(voice.get("code") or "")
            if not code or not role:
                continue
            plain_ref = re.compile(
                rf"(?<!@){re.escape(role)}\s*[（(]\s*(?:参考)?图\s*"
                rf"{re.escape(code[1:] if code.startswith('图') else code)}\s*[）)]"
            )
            if plain_ref.search(raw):
                asset_codes.add(code)
        canvas = segment.get("canvas")
        if canvas is not None and not isinstance(canvas, dict):
            issues.append(_issue(
                ErrorCode.ASSET_CODE_INVALID,
                "segments[].canvas 必须是对象",
                path=f"{path}.canvas",
                segment=index,
            ))
            canvas = {}
        canvas = canvas if isinstance(canvas, dict) else {}
        raw_canvas_codes = {
            canonical_code(f"图{code}")
            for code in re.findall(r"图\s*(\d+)", raw)
        }
        raw_canvas_codes.discard("")
        canvas_codes: set[str] | None = None
        if "asset_codes" in canvas:
            values = canvas.get("asset_codes")
            if not isinstance(values, list):
                issues.append(_issue(
                    ErrorCode.ASSET_CODE_INVALID,
                    "canvas.asset_codes 必须是数组",
                    path=f"{path}.canvas.asset_codes",
                    segment=index,
                ))
            else:
                canvas_codes = set()
                for value_index, value in enumerate(values):
                    match = CODE_RE.fullmatch(str(value or "").strip())
                    if not match:
                        issues.append(_issue(
                            ErrorCode.ASSET_CODE_INVALID,
                            f"canvas.asset_codes 图号非法：{value!r}",
                            path=f"{path}.canvas.asset_codes[{value_index}]",
                            segment=index,
                        ))
                        continue
                    code = canonical_code(match.group(0))
                    if code in canvas_codes:
                        issues.append(_issue(
                            ErrorCode.ASSET_CODE_DUPLICATE,
                            f"canvas.asset_codes 图号重复：{code}",
                            path=f"{path}.canvas.asset_codes[{value_index}]",
                            segment=index,
                            node_code=code,
                        ))
                    canvas_codes.add(code)
        ref_codes: set[str] | None = None
        if "asset_refs" in canvas:
            values = canvas.get("asset_refs")
            if not isinstance(values, list):
                issues.append(_issue(
                    ErrorCode.ASSET_CODE_INVALID,
                    "canvas.asset_refs 必须是数组",
                    path=f"{path}.canvas.asset_refs",
                    segment=index,
                ))
            else:
                ref_codes = set()
                for value_index, value in enumerate(values):
                    if not isinstance(value, dict):
                        issues.append(_issue(
                            ErrorCode.ASSET_CODE_INVALID,
                            "canvas.asset_refs 项必须是对象",
                            path=f"{path}.canvas.asset_refs[{value_index}]",
                            segment=index,
                        ))
                        continue
                    match = CODE_RE.fullmatch(str(value.get("code") or "").strip())
                    if not match:
                        issues.append(_issue(
                            ErrorCode.ASSET_CODE_INVALID,
                            f"canvas.asset_refs 图号非法：{value.get('code')!r}",
                            path=f"{path}.canvas.asset_refs[{value_index}].code",
                            segment=index,
                        ))
                        continue
                    ref_code = canonical_code(match.group(0))
                    if ref_code:
                        ref_codes.add(ref_code)
        for field_name, declared_codes in (
            ("asset_codes", canvas_codes),
            ("asset_refs", ref_codes),
        ):
            if declared_codes is None or declared_codes == raw_canvas_codes:
                continue
            difference = sorted(declared_codes ^ raw_canvas_codes)
            issues.append(_issue(
                ErrorCode.ASSET_DECLARED_MISSING,
                f"canvas.{field_name} 与正文图号不一致：{', '.join(difference)}",
                path=f"{path}.canvas.{field_name}",
                segment=index,
                node_code=difference[0] if difference else "",
            ))
        if canvas_codes is not None and ref_codes is not None and canvas_codes != ref_codes:
            difference = sorted(canvas_codes ^ ref_codes)
            issues.append(_issue(
                ErrorCode.ASSET_DECLARED_MISSING,
                f"canvas.asset_codes 与 asset_refs 不一致：{', '.join(difference)}",
                path=f"{path}.canvas",
                segment=index,
                node_code=difference[0] if difference else "",
            ))
        segment_expectations.append(SegmentExpectation(
            segment_index=index,
            video_no=video_no,
            raw=raw,
            speakers=frozenset(speakers),
            audio_codes=frozenset(code for _role, code in audio_refs),
            asset_codes=frozenset(asset_codes),
        ))

    for role in sorted(speaker_names):
        if role not in voice_by_name:
            issues.append(_issue(
                ErrorCode.VOICE_PROMPT_MISSING,
                f"对白角色缺音色定义：{role}",
                path="voice_prompts",
                role=role,
            ))

    warnings = tuple(issue for issue in issues if isinstance(issue.code, WarningCode))
    return GateResult(
        issues=tuple(issues),
        expectations=DeclaredExpectations(
            segments=tuple(segment_expectations),
            warnings=warnings,
        ),
    )


def validate_declared_feed(payload: dict) -> list[ValidationIssue]:
    """Return Gate 1 errors and warnings."""
    normalized, normalization_issues = _prepare_feed_for_import(payload)
    issues = list(analyze_declared_feed(normalized).issues)
    if normalization_issues:
        issues = [*normalization_issues, *issues]
    return issues


def _prepare_feed_for_import(payload: Any) -> tuple[Any, list[ValidationIssue]]:
    """Normalize an import-only copy without weakening the strict feed schema.

    ``assets[]`` is the authoritative bridge between stable semantic identities
    (CHR-001) and canvas slot identities (图1). Early v7.4.29 exports put the
    semantic identity in ``asset_prompts[].code`` and left segment canvas
    projections empty. Normalize those projections here; again, the source
    payload is never mutated.
    """
    if not isinstance(payload, dict):
        return payload, []

    normalized = copy.deepcopy(payload)
    issues: list[ValidationIssue] = []
    semantic_to_figure: dict[str, str] = {}
    figure_to_semantic: dict[str, str] = {}
    top_assets = payload.get("assets")
    if isinstance(top_assets, list):
        for index, asset in enumerate(top_assets):
            if not isinstance(asset, dict):
                continue
            path = f"assets[{index}]"
            semantic = canonical_semantic_code(asset.get("code"))
            figure = canonical_code(asset.get("img"))
            if not semantic:
                issues.append(_issue(
                    ErrorCode.ASSET_CODE_INVALID,
                    f"资产语义码非法：{asset.get('code')!r}",
                    path=f"{path}.code",
                ))
                continue
            if semantic in semantic_to_figure:
                issues.append(_issue(
                    ErrorCode.ASSET_CODE_DUPLICATE,
                    f"资产语义码重复：{semantic}",
                    path=f"{path}.code",
                    node_code=semantic_to_figure[semantic],
                ))
                continue
            if not figure:
                issues.append(_issue(
                    ErrorCode.ASSET_CODE_INVALID,
                    f"{semantic} 缺合法 img 画布槽位码",
                    path=f"{path}.img",
                ))
                continue
            if figure in figure_to_semantic:
                issues.append(_issue(
                    ErrorCode.ASSET_CODE_DUPLICATE,
                    f"画布槽位码重复：{figure} 同时属于 "
                    f"{figure_to_semantic[figure]} 和 {semantic}",
                    path=f"{path}.img",
                    node_code=figure,
                ))
                continue
            semantic_to_figure[semantic] = figure
            figure_to_semantic[figure] = semantic

    if not semantic_to_figure:
        return normalized, issues

    def resolve_code(value: Any) -> tuple[str, str]:
        semantic = canonical_semantic_code(value)
        if semantic:
            return semantic_to_figure.get(semantic, ""), semantic
        return canonical_code(value), ""

    for asset in normalized.get("asset_prompts") or []:
        if not isinstance(asset, dict):
            continue
        figure, semantic = resolve_code(asset.get("code"))
        if not figure:
            continue
        explicit_semantic = canonical_semantic_code(asset.get("semanticCode"))
        asset["code"] = figure
        asset["semanticCode"] = explicit_semantic or semantic
        canvas = asset.get("canvas")
        if isinstance(canvas, dict):
            canvas_semantic = canonical_semantic_code(canvas.get("semanticCode"))
            canvas_code = canonical_semantic_code(canvas.get("code"))
            if canvas_semantic or canvas_code:
                canvas["semanticCode"] = canvas_semantic or canvas_code
                canvas["code"] = figure
        if semantic:
            issues.append(_issue(
                WarningCode.WARN_ASSET_IDENTITY_AUTO_CORRECTED,
                f"{semantic} 已自动映射为画布槽位 {figure}",
                path=f"asset_prompts.{figure}",
                node_code=figure,
            ))

    for voice in normalized.get("voice_prompts") or []:
        if not isinstance(voice, dict):
            continue
        figure, semantic = resolve_code(voice.get("code"))
        if figure:
            voice["code"] = figure
            voice["semanticCode"] = (
                canonical_semantic_code(voice.get("semanticCode"))
                or semantic
            )

    voice_by_role: dict[str, dict] = {}
    for voice in normalized.get("voice_prompts") or []:
        if not isinstance(voice, dict):
            continue
        role = str(voice.get("name") or "").strip()
        figure = canonical_code(voice.get("code"))
        if role and figure:
            voice_by_role[role] = voice

    contract = normalized.get("canvas_contract")
    ledger = contract.get("asset_ledger") if isinstance(contract, dict) else None
    conflicts = contract.get("ageConflicts") if isinstance(contract, dict) else None
    if isinstance(conflicts, dict):
        for semantic, conflict in conflicts.items():
            if not isinstance(conflict, dict):
                continue
            visual_age = _normalize_age(conflict.get("visualAge"))
            voice_age = _normalize_age(conflict.get("voiceAge"))
            if not visual_age or not voice_age or visual_age == voice_age:
                continue
            figure = semantic_to_figure.get(str(semantic).strip().upper(), "")
            issues.append(_issue(
                WarningCode.WARN_ASSET_IDENTITY_AUTO_CORRECTED,
                f"{semantic} 视觉年龄 {visual_age} 与声线年龄 {voice_age} 冲突，"
                "已保留图像/声线各自明确值；请在源稿统一",
                path=f"canvas_contract.ageConflicts.{semantic}",
                node_code=figure,
            ))
    if isinstance(ledger, list):
        for entry in ledger:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title") or "")
            title_match = re.fullmatch(
                r"\s*角色[｜|]\s*(.+?)\s*[（(]\s*"
                r"([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*)\s*[）)]\s*",
                title,
            )
            if not title_match:
                continue
            role, semantic = title_match.groups()
            figure = semantic_to_figure.get(semantic, "")
            if role in voice_by_role or not figure:
                continue
            prompt = str(entry.get("voicePrompt") or "").strip()
            description = str(entry.get("voiceDescription") or "").strip()
            label = str(entry.get("voiceLabel") or "").strip()
            age = _normalize_age(entry.get("voiceAge"))
            gender = str(entry.get("voiceGender") or "").strip().lower()
            dialect = str(entry.get("voiceDialect") or "").strip()
            if not (
                prompt
                and description
                and label
                and age
                and gender
                and dialect
                and prompt.casefold().endswith(AUDIO_PROMPT_SUFFIX.casefold())
            ):
                continue
            voice = {
                "code": figure,
                "semanticCode": semantic,
                "name": role.strip(),
                "title": title.strip(),
                "assetId": str(entry.get("identity") or "").strip(),
                "format": AUDIO_PROMPT_FORMAT,
                "prompt": prompt,
                "label": label,
                "description": description,
                "age": age,
                "gender": gender,
                "dialect": dialect,
            }
            normalized.setdefault("voice_prompts", []).append(voice)
            voice_by_role[voice["name"]] = voice

    for asset in normalized.get("asset_prompts") or []:
        if not isinstance(asset, dict):
            continue
        canvas = asset.get("canvas")
        if not isinstance(canvas, dict) or canvas.get("kind") != "character":
            continue
        role = str(canvas.get("name") or "").strip()
        figure = canonical_code(asset.get("code"))
        if not role or not figure or role in voice_by_role:
            continue
        raw = str(asset.get("raw") or "")
        prompt_start = raw.find("Voice Description 完整版")
        label_start = raw.find("Voice Description Label", prompt_start)
        if prompt_start < 0 or label_start <= prompt_start:
            continue
        description = re.sub(
            r"^\s*Voice Description 完整版[^\n]*?\*\*\s*",
            "",
            raw[prompt_start:label_start],
        ).strip()
        label_match = re.search(
            r"Voice Description Label[^\n]*?\*\*\s*[:：]\s*([A-Za-z][^/\n]*)",
            raw[label_start:],
        )
        label = re.sub(r"\*\*.*$", "", label_match.group(1)).strip() if label_match else ""
        age_match = re.search(r"(\d+\s*[~～-]\s*\d+|\d+)\s*岁", description)
        if age_match:
            age = _normalize_age(age_match.group(1))
        else:
            asset_age_match = re.search(
                r"\b(\d{1,3}(?:\s*-\s*\d{1,3})?)-year-old\b",
                raw,
                re.IGNORECASE,
            )
            age = _normalize_age(asset_age_match.group(1)) if asset_age_match else ""
        gender = (
            "female"
            if re.search(r"女性|\bfemale\s+voice\b", description, re.IGNORECASE)
            else (
                "male"
                if re.search(r"男性|\bmale\s+voice\b", description, re.IGNORECASE)
                else ""
            )
        )
        dialect = (
            "northeastern-accented"
            if "northeastern" in description.casefold()
            else "standard"
        )
        identity = f"A {gender} aged {age}" if gender and age else (
            f"A {gender}" if gender else ""
        )
        prompt = " ".join(
            part for part in (
                f"{label}." if label else "",
                description,
                (
                    f"{identity}, speaking {dialect} Mandarin, "
                    "short drama dubbing reference clip, "
                        "4.5 seconds clean voice, no background noise."
                ),
            )
            if part
        ).strip()
        if not (
            prompt.casefold().endswith(AUDIO_PROMPT_SUFFIX.casefold())
            and age
            and gender
        ):
            continue
        voice = {
            "code": figure,
            "semanticCode": (
                canonical_semantic_code(asset.get("semanticCode"))
                or canonical_semantic_code(canvas.get("semanticCode"))
            ),
            "name": role,
            "title": f"角色｜{role}（{figure}）",
            "assetId": str(canvas.get("assetId") or "").strip(),
            "format": AUDIO_PROMPT_FORMAT,
            "prompt": prompt,
            "label": label,
            "description": description,
            "age": age,
            "gender": gender,
            "dialect": dialect,
        }
        normalized.setdefault("voice_prompts", []).append(voice)
        voice_by_role[role] = voice

    for segment in normalized.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        raw = str(segment.get("raw") or "")
        if "【音色引用】" not in raw:
            speaking = [
                role
                for role in sorted(dialogue_speakers(raw))
                if role in voice_by_role
            ]
            if speaking:
                references = "；".join(
                    f"@音频｜{role}（{voice_by_role[role]['code']}）"
                    for role in speaking
                )
                section = f"【音色引用】{references}"
                core = re.search(r"(?m)^【核心人物】[^\n]*(?:\n|$)", raw)
                if core:
                    raw = raw[:core.end()] + section + "\n" + raw[core.end():]
                else:
                    raw = raw.rstrip() + "\n" + section + "\n"
                segment["raw"] = raw
        actual_codes = {
            canonical_code(f"图{number}")
            for number in re.findall(r"图\s*(\d+)", raw)
        }
        actual_codes.discard("")
        canvas = segment.get("canvas")
        if isinstance(canvas, dict):
            for field in ("asset_codes", "asset_refs"):
                declared = canvas.get(field)
                if isinstance(declared, list) and not declared and actual_codes:
                    if field == "asset_codes":
                        canvas[field] = sorted(
                            actual_codes,
                            key=lambda code: int(code[1:]),
                        )
                    else:
                        canvas[field] = [
                            {"code": code}
                            for code in sorted(
                                actual_codes,
                                key=lambda code: int(code[1:]),
                            )
                        ]

    if isinstance(contract, dict):
        if isinstance(ledger, list):
            for entry in ledger:
                if not isinstance(entry, dict):
                    continue
                title = str(entry.get("title") or "")
                match = re.search(
                    r"[（(]\s*([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*)\s*[）)]",
                    title,
                )
                if match:
                    entry["semanticCode"] = match.group(1)

    return normalized, issues


def _voice_ref_ids(video: dict) -> set[str]:
    params = ((video.get("metadata") or {}).get("params") or {})
    refs = params.get("voiceRefs") or (video.get("metadata") or {}).get("voiceRefs") or []
    if not refs and isinstance(params.get("voiceRef"), dict):
        refs = [params["voiceRef"]]
    return {
        str(ref.get("nodeId") or "")
        for ref in refs
        if isinstance(ref, dict) and ref.get("nodeId")
    }


def validate_final_payload(
    payload: dict,
    expectations: DeclaredExpectations,
    episode_id: str | None = None,
) -> list[ValidationIssue]:
    """Gate 2: validate derived refs and edges against Gate 1 expectations."""
    issues: list[ValidationIssue] = []
    nodes = [node for node in payload.get("nodes") or [] if isinstance(node, dict)]
    connections = [conn for conn in payload.get("connections") or [] if isinstance(conn, dict)]
    groups = [group for group in payload.get("episodeImports") or [] if isinstance(group, dict)]
    current_episode = str(episode_id or payload.get("episodeId") or "").upper()
    group = next(
        (item for item in groups if str(item.get("id") or "").upper() == current_episode),
        groups[-1] if groups else {},
    )
    group_node_ids = set(group.get("nodeIds") or [])
    video_ids = list(group.get("videoNodeIds") or [])
    node_by_id = {
        str(node.get("id")): node
        for node in nodes
        if node.get("id")
    }
    ordered_videos = [
        node_by_id[node_id]
        for node_id in video_ids
        if node_id in node_by_id and node_by_id[node_id].get("type") == "video"
    ]
    videos_by_segment: dict[int, dict] = {}
    for fallback_no, video in enumerate(ordered_videos, start=1):
        metadata = video.get("metadata") or {}
        segment_no = metadata.get("segmentNo")
        try:
            segment_no = int(segment_no)
        except (TypeError, ValueError):
            match = re.search(r"段\s*(\d+)", str(video.get("title") or ""))
            segment_no = int(match.group(1)) if match else fallback_no
        if segment_no in videos_by_segment:
            issues.append(_issue(
                ErrorCode.SEGMENT_INVALID,
                f"视频节点段号重复：{segment_no}",
                path="episodeImports.videoNodeIds",
                segment=segment_no - 1,
            ))
            continue
        videos_by_segment[segment_no] = video
    audio_nodes = [
        node for node in nodes
        if node.get("type") == "audio" and (not group_node_ids or node.get("id") in group_node_ids)
    ]
    image_nodes = [
        node for node in nodes
        if node.get("type") == "image" and (not group_node_ids or node.get("id") in group_node_ids)
    ]
    audio_by_code = {
        code: node
        for node in audio_nodes
        for code in [canonical_code_from_text(node.get("title"))]
        if code
    }
    image_by_code = {
        code: node
        for node in image_nodes
        for code in [canonical_code_from_text(node.get("title"))]
        if code
    }

    # Cross-episode code conflicts are only visible after merge.
    code_roles: dict[tuple[str, str], set[str]] = {}
    for node in nodes:
        if node.get("type") not in {"image", "audio"}:
            continue
        code = canonical_code_from_text(node.get("title"))
        role_match = TITLE_ROLE_RE.match(str(node.get("title") or ""))
        if not code or not role_match:
            continue
        key = (str(node.get("type")), code)
        code_roles.setdefault(key, set()).add(role_match.group(1).strip())
    for (_kind, code), roles in code_roles.items():
        if len(roles) > 1:
            issues.append(_issue(
                ErrorCode.ASSET_CODE_DUPLICATE,
                f"{code} 跨集对应多个角色：{', '.join(sorted(roles))}",
                path="episodeImports",
                node_code=code,
            ))
    for code, audio in audio_by_code.items():
        image = image_by_code.get(code)
        if not image:
            continue
        audio_role_match = TITLE_ROLE_RE.match(str(audio.get("title") or ""))
        image_role_match = TITLE_ROLE_RE.match(str(image.get("title") or ""))
        if (
            audio_role_match
            and image_role_match
            and audio_role_match.group(1).strip() != image_role_match.group(1).strip()
        ):
            issues.append(_issue(
                ErrorCode.VOICE_CODE_CONFLICT,
                f"{code} 的音色与角色资产不是同一角色",
                path="episodeImports",
                node_code=code,
            ))

    incoming: dict[str, list[str]] = {}
    for conn in connections:
        incoming.setdefault(str(conn.get("toNodeId") or ""), []).append(str(conn.get("fromNodeId") or ""))

    if len(ordered_videos) != len(expectations.segments):
        issues.append(_issue(
            ErrorCode.SEGMENT_INVALID,
            f"预期 {len(expectations.segments)} 个视频节点，产物有 {len(ordered_videos)} 个",
            path="episodeImports.videoNodeIds",
        ))
    for expectation in expectations.segments:
        video = videos_by_segment.get(expectation.video_no)
        if video is None:
            issues.append(_issue(
                ErrorCode.SEGMENT_INVALID,
                f"缺少段{expectation.video_no}对应的视频节点",
                segment=expectation.segment_index,
            ))
            continue
        expected_audio_ids = {
            audio_by_code[code]["id"]
            for code in expectation.audio_codes
            if code in audio_by_code
        }
        missing_audio_codes = sorted(code for code in expectation.audio_codes if code not in audio_by_code)
        if missing_audio_codes:
            issues.append(_issue(
                ErrorCode.VOICE_PROMPT_MISSING,
                f"产物缺音频节点：{', '.join(missing_audio_codes)}",
                segment=expectation.segment_index,
                node_code=missing_audio_codes[0],
            ))
        ref_ids = _voice_ref_ids(video)
        # Loaded/legacy projects may contain several voice rows for the same
        # declared role after a figure-code change. The import contract is the
        # role reference, so identity is the referenced audio node's role; an
        # extra historical row is not a contract error as long as every
        # referenced row maps to a declared speaker and all declared speakers
        # are represented. New imports still start from the exact expected ids;
        # this acceptance path only applies when the extra rows already exist.
        expected_audio_roles = {
            TITLE_ROLE_RE.match(str(audio_by_code[code].get("title") or ""))
            for code in expectation.audio_codes
            if code in audio_by_code
            and TITLE_ROLE_RE.match(str(audio_by_code[code].get("title") or ""))
        }
        expected_audio_roles = {
            match.group(1).strip() for match in expected_audio_roles if match
        }
        has_incoming_assets = bool(
            getattr(expectations, "has_incoming_assets", False)
        )
        actual_audio_roles = {
            role
            for node_id in ref_ids
            for role in [
                (
                    TITLE_ROLE_RE.match(
                        str((node_by_id.get(node_id) or {}).get("title") or "")
                    ).group(1).strip()
                    if TITLE_ROLE_RE.match(
                        str((node_by_id.get(node_id) or {}).get("title") or "")
                    )
                    else ""
                )
            ]
            if role
        }
        role_equivalent = (
            expected_audio_roles
            and actual_audio_roles == expected_audio_roles
            and ref_ids != expected_audio_ids
        )
        if ref_ids != expected_audio_ids and not role_equivalent:
            issues.append(_issue(
                ErrorCode.VOICE_REF_MISMATCH,
                f"voiceRefs 不一致：期望 {sorted(expected_audio_ids)}，实际 {sorted(ref_ids)}",
                segment=expectation.segment_index,
            ))
        audio_edge_ids = {
            source for source in incoming.get(str(video.get("id") or ""), [])
            if any(audio.get("id") == source for audio in audio_nodes)
        }
        role_equivalent_edges = (
            expected_audio_roles
            and audio_edge_ids != expected_audio_ids
            and {
                role
                for node_id in audio_edge_ids
                for role in [
                    (
                        TITLE_ROLE_RE.match(
                            str((node_by_id.get(node_id) or {}).get("title") or "")
                        ).group(1).strip()
                        if TITLE_ROLE_RE.match(
                            str((node_by_id.get(node_id) or {}).get("title") or "")
                        )
                        else ""
                    )
                ]
                if role
            } == expected_audio_roles
        )
        if audio_edge_ids != expected_audio_ids and not role_equivalent_edges:
            issues.append(_issue(
                ErrorCode.VOICE_EDGE_MISMATCH,
                f"音频连线不一致：期望 {sorted(expected_audio_ids)}，实际 {sorted(audio_edge_ids)}",
                segment=expectation.segment_index,
            ))
        linked_asset_codes = {
            code for code, image in image_by_code.items()
            if image.get("id") in incoming.get(str(video.get("id") or ""), [])
        }
        expected_asset_codes = set(expectation.asset_codes)
        missing_asset_codes = sorted(
            code for code in expectation.asset_codes
            if code not in image_by_code
        )
        if missing_asset_codes:
            issues.append(_issue(
                ErrorCode.ASSET_DECLARED_MISSING,
                f"产物缺资产节点：{', '.join(missing_asset_codes)}",
                segment=expectation.segment_index,
                node_code=missing_asset_codes[0],
            ))
        expected_asset_codes.difference_update(missing_asset_codes)
        for code in linked_asset_codes - expected_asset_codes:
            image = image_by_code.get(code) or {}
            role_match = TITLE_ROLE_RE.match(str(image.get("title") or ""))
            role = role_match.group(1).strip() if role_match else ""
            if role and role in expectation.raw:
                expected_asset_codes.add(code)
        if linked_asset_codes != expected_asset_codes:
            issues.append(_issue(
                ErrorCode.ASSET_EDGE_MISMATCH,
                f"资产连线不一致：期望 {sorted(expected_asset_codes)}，实际 {sorted(linked_asset_codes)}",
                segment=expectation.segment_index,
            ))
    return issues


def _payload_diff(before: dict, after: dict) -> dict:
    before_nodes = {
        str(node.get("id")): node
        for node in before.get("nodes") or []
        if isinstance(node, dict) and node.get("id")
    }
    after_nodes = {
        str(node.get("id")): node
        for node in after.get("nodes") or []
        if isinstance(node, dict) and node.get("id")
    }
    added = sorted(set(after_nodes) - set(before_nodes))
    removed = sorted(set(before_nodes) - set(after_nodes))
    changed = sorted(
        node_id
        for node_id in set(before_nodes) & set(after_nodes)
        if json.dumps(before_nodes[node_id], ensure_ascii=False, sort_keys=True)
        != json.dumps(after_nodes[node_id], ensure_ascii=False, sort_keys=True)
    )
    return {
        "nodesAdded": len(added),
        "nodesRemoved": len(removed),
        "nodesChanged": len(changed),
        "addedNodeIds": added,
        "removedNodeIds": removed,
        "changedNodeIds": changed,
    }


def _canonical_feed_for_hash(feed: dict) -> dict:
    canonical = copy.deepcopy(feed)

    def normalize_code_fields(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            code = canonical_code(item.get("code"))
            if code:
                item["code"] = code
            canvas = item.get("canvas")
            if not isinstance(canvas, dict):
                continue
            if isinstance(canvas.get("asset_codes"), list):
                canvas["asset_codes"] = [
                    canonical_code(value) or value
                    for value in canvas["asset_codes"]
                ]
            if isinstance(canvas.get("asset_refs"), list):
                for ref in canvas["asset_refs"]:
                    if not isinstance(ref, dict):
                        continue
                    code = canonical_code(ref.get("code"))
                    if code:
                        ref["code"] = code

    normalize_code_fields(canonical.get("asset_prompts"))
    normalize_code_fields(canonical.get("voice_prompts"))
    for segment in canonical.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        raw = str(segment.get("raw") or "")
        segment["raw"] = re.sub(
            r"图\s*0*(\d+)",
            lambda match: f"图{int(match.group(1))}",
            raw,
        )
        canvas = segment.get("canvas")
        if not isinstance(canvas, dict):
            continue
        if isinstance(canvas.get("asset_codes"), list):
            canvas["asset_codes"] = [
                canonical_code(value) or value
                for value in canvas["asset_codes"]
            ]
        if isinstance(canvas.get("asset_refs"), list):
            for ref in canvas["asset_refs"]:
                if not isinstance(ref, dict):
                    continue
                code = canonical_code(ref.get("code"))
                if code:
                    ref["code"] = code
    return canonical


def _source_provenance(feed: dict, source: dict | None) -> dict:
    source = dict(source or {})
    normalized = json.dumps(
        _canonical_feed_for_hash(feed),
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    contract = feed.get("canvas_contract") if isinstance(feed.get("canvas_contract"), dict) else {}
    generator = contract.get("generator") if isinstance(contract.get("generator"), dict) else {}
    return {
        "sourceFeedName": str(source.get("name") or source.get("path") or "").strip(),
        "sourceFeedPath": str(source.get("path") or "").strip(),
        "feedSha256": hashlib.sha256(normalized).hexdigest(),
        "schemaVersion": str(contract.get("schema") or ""),
        "skillVersion": str(
            source.get("skillVersion")
            or generator.get("skillVersion")
            or ""
        ),
        "packageRevision": str(
            source.get("packageRevision")
            or generator.get("packageRevision")
            or ""
        ),
    }


def _attach_provenance(
    payload: dict,
    episode_id: str,
    provenance: dict,
    warnings: list[ValidationIssue],
) -> None:
    group = next(
        (
            item for item in payload.get("episodeImports") or []
            if str(item.get("id") or "").upper() == str(episode_id).upper()
        ),
        None,
    )
    if group is None:
        return
    now = datetime.now(timezone.utc).isoformat()
    first_imported_at = (
        str(group.get("firstImportedAt") or "").strip()
        or str(group.get("importedAt") or "").strip()
        or now
    )
    group.update(provenance)
    group["firstImportedAt"] = first_imported_at
    group["lastImportedAt"] = now
    group["importedAt"] = now
    group["importWarnings"] = [issue_dict(issue) for issue in warnings]


def _expectations_with_incoming_assets(
    expectations: DeclaredExpectations,
    incoming: dict,
) -> DeclaredExpectations:
    nodes = {
        str(node.get("id")): node
        for node in incoming.get("nodes") or []
        if isinstance(node, dict) and node.get("id")
    }
    group = (incoming.get("episodeImports") or [{}])[-1]
    videos = [
        nodes[node_id]
        for node_id in group.get("videoNodeIds") or []
        if node_id in nodes and nodes[node_id].get("type") == "video"
    ]
    segments = []
    for index, expected in enumerate(expectations.segments):
        asset_codes = set(expected.asset_codes)
        if index < len(videos):
            params = (videos[index].get("metadata") or {}).get("params") or {}
            for ref in params.get("refs") or []:
                if not isinstance(ref, dict):
                    continue
                source = nodes.get(str(ref.get("nodeId") or ""))
                if not source or source.get("type") != "image":
                    continue
                code = canonical_code_from_text(source.get("title"))
                if code:
                    asset_codes.add(code)
        segments.append(SegmentExpectation(
            segment_index=expected.segment_index,
            video_no=expected.video_no,
            raw=expected.raw,
            speakers=expected.speakers,
            audio_codes=expected.audio_codes,
            asset_codes=frozenset(asset_codes),
        ))
    return DeclaredExpectations(
        segments=tuple(segments),
        warnings=expectations.warnings,
        has_incoming_assets=True,
    )


def prepare_import(
    existing: dict,
    feed: dict,
    project_id: str,
    revision: int,
    source: dict | None = None,
) -> ImportPlan:
    """Pure import planner shared by dry-run and commit."""
    import import_feed

    normalized_feed, normalization_issues = _prepare_feed_for_import(feed)
    gate1 = analyze_declared_feed(normalized_feed)
    gate1 = GateResult(
        issues=(*normalization_issues, *gate1.issues),
        expectations=gate1.expectations,
    )
    if gate1.errors:
        return ImportPlan(
            project_id=project_id,
            revision=revision,
            episode_id="",
            merged_payload=None,
            errors=tuple(gate1.errors),
            warnings=tuple(gate1.warnings),
            summary={"diff": _payload_diff(existing, existing)},
            provenance=_source_provenance(normalized_feed, source),
        )

    candidate = copy.deepcopy(existing)
    asset_pool = import_feed.build_asset_pool_from_payload(candidate, project_id)
    incoming = import_feed.build_payload(
        normalized_feed,
        asset_pool=asset_pool,
        target_project_id=project_id,
    )
    expectations = _expectations_with_incoming_assets(
        gate1.expectations,
        incoming,
    )
    merged = import_feed.merge_payload_by_episode(candidate, incoming)
    episode_id = str(
        merged.get("episodeId")
        or incoming.get("episodeId")
        or ((incoming.get("episodeImports") or [{}])[-1].get("id"))
        or ""
    ).upper()
    gate2_issues = validate_final_payload(
        merged,
        expectations,
        episode_id=episode_id,
    )
    warnings = [*gate1.warnings, *gate2_issues]
    errors = [issue for issue in warnings if isinstance(issue.code, ErrorCode)]
    warning_items = [issue for issue in warnings if isinstance(issue.code, WarningCode)]
    provenance = _source_provenance(normalized_feed, source)
    if not errors:
        _attach_provenance(merged, episode_id, provenance, warning_items)
    summary = {
        "episodeId": episode_id,
        "episodesBefore": len(existing.get("episodeImports") or []),
        "episodesAfter": len(merged.get("episodeImports") or []),
        "assetsReused": int((merged.get("importSummary") or {}).get("assetsReused") or 0),
        "assetsReusedGenerated": int(
            (merged.get("importSummary") or {}).get("assetsReusedGenerated") or 0
        ),
        "diff": _payload_diff(existing, merged),
    }
    return ImportPlan(
        project_id=project_id,
        revision=revision,
        episode_id=episode_id,
        merged_payload=None if errors else merged,
        errors=tuple(errors),
        warnings=tuple(warning_items),
        summary=summary,
        provenance=provenance,
    )


def commit_import(plan: ImportPlan, if_revision: int | None = None) -> dict:
    """Commit a prepared plan under a revision precondition."""
    import canvas_store as store
    import canvas_repository

    if not plan.ok or plan.merged_payload is None:
        raise ValueError("导入计划包含错误，不能提交")
    current = canvas_repository.repository.get_project(plan.project_id)
    if not current:
        raise ValueError("项目不存在")
    if if_revision is not None and int(current.get("revision") or 0) != int(if_revision):
        raise RevisionConflictError(
            f"项目已更新：期望 revision={if_revision}，实际 revision={current.get('revision')}"
        )
    title = plan.merged_payload.get("title") or current.get("title")
    try:
        restored = canvas_repository.repository.commit_payload(
            plan.project_id,
            plan.merged_payload,
            title=title,
            if_revision=if_revision,
            snapshot_mode="auto",
        )
    except store.RevisionConflictError as exc:
        raise RevisionConflictError(str(exc)) from exc
    return {
        "id": plan.project_id,
        "title": title,
        "revision": int(restored.get("revision") or 0),
        "episodeId": plan.episode_id,
    }
