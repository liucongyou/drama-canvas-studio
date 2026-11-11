from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MentionIssue:
    code: str
    label: str
    message: str


@dataclass(frozen=True)
class MentionResolution:
    node_ids: list[str]
    refs: list[dict[str, Any]]
    voices: list[dict[str, Any]]
    issues: list[MentionIssue]
    has_explicit_mention: bool = False
    reference_identity_map: dict[str, str] | None = None


def _title(node: dict[str, Any]) -> str:
    return str(node.get("title") or "").strip()


def _kind(node: dict[str, Any]) -> str:
    return str(node.get("type") or "").strip()


def _label_matches(prompt: str, label: str) -> bool:
    if not label:
        return False
    token = f"@{label}"
    start = 0
    while True:
        index = prompt.find(token, start)
        if index < 0:
            return False
        if index == 0 or prompt[index - 1] != "<":
            return True
        start = index + len(token)


def _unresolved_tokens(prompt: str, labels: list[str]) -> list[str]:
    matches = sorted(
        {
            (index, index + len(f"@{label}"))
            for label in labels
            for index in _find_all(prompt, f"@{label}")
            if index == 0 or prompt[index - 1] != "<"
        }
    )
    accepted: list[tuple[int, int]] = []
    consumed = -1
    for start, end in matches:
        if start < consumed:
            continue
        accepted.append((start, end))
        consumed = end
    unresolved = []
    pattern = re.compile(r"@([^\s<>，。！？；、,:：()（）\[\]【】]+)")
    for token in pattern.finditer(prompt):
        start, end = token.span()
        if start > 0 and prompt[start - 1] == "<":
            continue
        if any(from_index <= start < to_index for from_index, to_index in accepted):
            continue
        unresolved.append(token.group(0))
    trailing = re.search(r"@\s*$", prompt)
    if trailing and not any(start <= trailing.start() < end for start, end in accepted):
        unresolved.append("@")
    return unresolved


def _find_all(text: str, needle: str) -> list[int]:
    found = []
    start = 0
    while needle and start <= len(text):
        index = text.find(needle, start)
        if index < 0:
            return found
        found.append(index)
        start = index + len(needle)
    return found


def resolve_explicit_references(
    *,
    prompt: str,
    nodes: list[dict[str, Any]],
    candidate_refs: list[dict[str, Any]],
    candidate_voices: list[dict[str, Any]],
) -> MentionResolution:
    """Return the provider references explicitly named in the prompt.

    Candidate refs are never trusted as the final provider payload. They only
    supply prior identity context; explicit canonical @ labels are authoritative.
    """
    text = str(prompt or "")
    by_title: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        if not isinstance(node, dict) or not node.get("id"):
            continue
        if _kind(node) not in {"image", "audio", "video", "text"}:
            continue
        title = _title(node)
        if title:
            by_title.setdefault(title, []).append(node)

    candidate_by_node = {
        str(ref.get("nodeId") or ""): ref
        for ref in [*(candidate_refs or []), *(candidate_voices or [])]
        if isinstance(ref, dict) and ref.get("nodeId")
    }
    refs: list[dict[str, Any]] = []
    voices: list[dict[str, Any]] = []
    issues: list[MentionIssue] = []
    seen: set[str] = set()
    reference_identity_map: dict[str, str] = {}

    for label in sorted(by_title, key=len, reverse=True):
        if not _label_matches(text, label):
            continue
        candidates = by_title[label]
        if len(candidates) > 1:
            issues.append(MentionIssue(
                code="AMBIGUOUS_MENTION",
                label=label,
                message=f"引用 @{label} 对应多个资产，请从 @ 选择器重新指定唯一资产",
            ))
            continue
        node = candidates[0]
        node_id = str(node["id"])
        if node_id in seen:
            continue
        seen.add(node_id)
        base = candidate_by_node.get(node_id) or {}
        metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        stable_identity = (
            (
                f"assetId:{str(metadata.get('assetId') or '').strip()}"
                if str(metadata.get("assetId") or "").strip()
                else ""
            )
            or node_id
        )
        resolved = {
            **base,
            "nodeId": node_id,
            "label": label,
            "stableIdentity": stable_identity,
        }
        for key in ("assetId", "seriesId", "variantKey"):
            value = str(metadata.get(key) or "").strip()
            if value:
                resolved[key] = value
        reference_identity_map[label] = stable_identity
        if _kind(node) == "audio":
            voices.append(resolved)
        else:
            refs.append(resolved)

    labels = list(by_title)
    for token in _unresolved_tokens(text, labels):
        issues.append(MentionIssue(
            code="INVALID_MENTION",
            label=token.lstrip("@"),
            message=f"无法解析引用 {token}，请从 @ 选择器重新选择资产",
        ))

    return MentionResolution(
        node_ids=[item["nodeId"] for item in [*refs, *voices]],
        refs=refs,
        voices=voices,
        issues=issues,
        has_explicit_mention=bool(refs or voices or issues),
        reference_identity_map=reference_identity_map,
    )
