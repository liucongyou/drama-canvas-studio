from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


SCHEMA = "aicg3d-recipe-catalog/v1"
CATALOG_PATH = (
    Path(__file__).resolve().parent
    / "agent"
    / "app"
    / "resources"
    / "aicg3d_recipes"
    / "catalog.v1.json"
)


class RecipeContractError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_CATALOG: dict[str, Any] | None = None
_BY_ID: dict[str, dict[str, Any]] | None = None


def catalog() -> dict[str, Any]:
    global _CATALOG, _BY_ID
    if _CATALOG is not None:
        return _CATALOG
    try:
        value = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecipeContractError(
            "RECIPE_CATALOG_UNAVAILABLE",
            f"镜头配方目录不可用：{exc}",
        ) from exc
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise RecipeContractError("RECIPE_CATALOG_INVALID", "镜头配方目录 schema 无效")
    expected = str(value.get("catalogSha256") or "")
    core = dict(value)
    core.pop("catalogSha256", None)
    if not expected or _sha256(_canonical(core)) != expected:
        raise RecipeContractError("RECIPE_CATALOG_INVALID", "镜头配方目录哈希校验失败")
    _CATALOG = value
    _BY_ID = {
        str(item.get("id") or ""): item
        for item in value.get("recipes") or []
        if isinstance(item, dict) and item.get("id")
    }
    return value


def get_recipe(recipe_id: str, version: str = "") -> dict[str, Any] | None:
    catalog()
    item = (_BY_ID or {}).get(str(recipe_id or "").strip())
    if not item:
        return None
    if version and str(item.get("version") or "") != str(version):
        return None
    return json.loads(json.dumps(item, ensure_ascii=False))


def metadata() -> dict[str, Any]:
    value = catalog()
    return {
        "schema": value.get("schema"),
        "catalogVersion": value.get("catalogVersion"),
        "recipeVersion": value.get("recipeVersion"),
        "catalogSha256": value.get("catalogSha256"),
        "recipeCount": value.get("recipeCount"),
        "kindCounts": value.get("kindCounts"),
        "categoryCounts": value.get("categoryCounts"),
    }


def compact(item: dict[str, Any], match_reason: str = "") -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "version": str(item.get("version") or ""),
        "kind": str(item.get("kind") or ""),
        "category": str(item.get("category") or ""),
        "title": str(item.get("title") or ""),
        "description": str(item.get("description") or ""),
        "models": list(item.get("models") or []),
        "requiredSlots": [
            str(slot.get("key") or "")
            for slot in item.get("slots") or []
            if isinstance(slot, dict) and slot.get("required", True)
        ],
        "contentSha256": str(item.get("contentSha256") or ""),
        "matchReason": match_reason,
    }


def _score(item: dict[str, Any], query: str) -> tuple[int, str]:
    if not query:
        return 1, ""
    title = str(item.get("title") or "").lower()
    description = str(item.get("description") or "").lower()
    mechanism = str(item.get("mechanism") or "").lower()
    category = str(item.get("category") or "").lower()
    recipe_id = str(item.get("id") or "").lower()
    if query == title or query == recipe_id:
        return 100, "名称精确匹配"
    if query in title:
        return 80, "名称匹配"
    if query in recipe_id:
        return 70, "ID 匹配"
    if query in description:
        return 50, "用途匹配"
    if query in mechanism:
        return 30, "机制匹配"
    if query in category:
        return 20, "分类匹配"
    tokens = [part for part in query.split() if part]
    haystack = " ".join((title, description, mechanism, category, recipe_id))
    hits = sum(1 for part in tokens if part in haystack)
    if hits:
        return 10 + hits, "关键词匹配"
    return 0, ""


def search(
    *,
    query: str = "",
    category: str = "",
    kind: str = "",
    model: str = "",
    cursor: int = 0,
    limit: int = 20,
) -> dict[str, Any]:
    normalized_query = str(query or "").strip().lower()
    normalized_category = str(category or "").strip()
    normalized_kind = str(kind or "").strip()
    normalized_model = model_family(str(model or ""))
    offset = max(0, int(cursor))
    page_size = min(50, max(1, int(limit)))
    ranked: list[tuple[int, str, dict[str, Any], str]] = []
    for item in catalog().get("recipes") or []:
        if not isinstance(item, dict):
            continue
        if normalized_category and item.get("category") != normalized_category:
            continue
        if normalized_kind and item.get("kind") != normalized_kind:
            continue
        models = [str(value).lower() for value in item.get("models") or []]
        if normalized_model and normalized_model not in models:
            continue
        score, reason = _score(item, normalized_query)
        if normalized_query and score <= 0:
            continue
        ranked.append((score, str(item.get("category") or ""), item, reason))
    ranked.sort(key=lambda value: (-value[0], value[1], str(value[2].get("id") or "")))
    page = ranked[offset:offset + page_size]
    next_cursor = offset + len(page) if offset + len(page) < len(ranked) else None
    return {
        "recipes": [compact(item, reason) for _score, _category, item, reason in page],
        "total": len(ranked),
        "cursor": offset,
        "nextCursor": next_cursor,
        "catalog": metadata(),
    }


def model_family(value: str) -> str:
    text = str(value or "").strip().lower()
    if "minimax" in text or "h3" in text:
        return "minimax-h3"
    if "seedance" in text:
        return "seedance-2.0"
    return text


def normalize_binding(
    value: Any,
    *,
    model: str = "",
    require_slots: bool = False,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    recipe_id = str(value.get("id") or value.get("recipeId") or "").strip()
    if not recipe_id:
        return None
    version = str(value.get("version") or value.get("recipeVersion") or "").strip()
    item = get_recipe(recipe_id, version)
    if not item:
        raise RecipeContractError("RECIPE_NOT_FOUND", f"镜头配方不存在：{recipe_id}")
    expected_hash = str(item.get("contentSha256") or "")
    actual_hash = str(value.get("sha256") or value.get("contentSha256") or "")
    if actual_hash and actual_hash != expected_hash:
        raise RecipeContractError(
            "RECIPE_HASH_MISMATCH",
            f"镜头配方版本哈希不一致：{recipe_id}",
        )
    family = model_family(model)
    if family and item.get("models") and family not in item.get("models"):
        raise RecipeContractError(
            "RECIPE_MODEL_UNSUPPORTED",
            f"镜头配方 {recipe_id} 不支持当前模型",
        )
    slots = value.get("slots") if isinstance(value.get("slots"), dict) else {}
    normalized_slots = {
        str(key): str(slot_value)
        for key, slot_value in slots.items()
        if str(key).strip() and isinstance(slot_value, (str, int, float, bool))
    }
    missing = [
        str(slot.get("key") or "")
        for slot in item.get("slots") or []
        if isinstance(slot, dict)
        and slot.get("required", True)
        and not normalized_slots.get(str(slot.get("key") or ""), "").strip()
    ]
    if require_slots and missing:
        raise RecipeContractError(
            "RECIPE_REQUIRED_SLOT_MISSING",
            f"镜头配方 {recipe_id} 缺少必填槽位：{', '.join(missing)}",
        )
    role = str(value.get("role") or "primary")
    if role not in {"primary", "modifier"}:
        raise RecipeContractError(
            "RECIPE_BINDING_INVALID",
            "镜头配方角色只能是 primary 或 modifier",
        )
    return {
        "id": recipe_id,
        "version": str(item.get("version") or ""),
        "sha256": expected_hash,
        "role": role,
        "segmentNo": max(0, int(value.get("segmentNo") or 0)),
        "slots": normalized_slots,
        "missingSlots": missing,
        "matchReason": str(value.get("matchReason") or ""),
    }


def compile_execution_lock(
    value: Any,
    *,
    model: str = "",
    require_slots: bool = True,
) -> dict[str, Any]:
    binding = normalize_binding(value, model=model, require_slots=require_slots)
    if not binding:
        raise RecipeContractError("RECIPE_BINDING_INVALID", "缺少镜头配方绑定")
    item = get_recipe(binding["id"], binding["version"])
    if not item:
        raise RecipeContractError("RECIPE_NOT_FOUND", f"镜头配方不存在：{binding['id']}")
    lines = [
        f"配方ID：{binding['id']} v{binding['version']}",
        f"配方名称：{item.get('title')}",
    ]
    if item.get("mechanism"):
        lines.append("镜头机制：" + str(item.get("mechanism")).strip())
    invariants = [
        str(rule.get("rule") or "").strip()
        for rule in item.get("invariants") or []
        if isinstance(rule, dict) and rule.get("rule")
    ]
    if invariants:
        lines.append("不可删除的结构锚点：")
        lines.extend("- " + rule for rule in invariants)
    if binding["slots"]:
        lines.append("本次槽位：")
        lines.extend(
            f"- {key}：{slot_value}"
            for key, slot_value in sorted(binding["slots"].items())
        )
    if item.get("antiCopy"):
        lines.append("反复制边界：")
        lines.extend("- " + str(rule) for rule in item.get("antiCopy") or [])
    return {
        **binding,
        "text": "\n".join(lines).strip(),
        "title": str(item.get("title") or ""),
    }


def augment_prompt(
    prompt: str,
    binding: Any,
    *,
    model: str = "",
    require_slots: bool = True,
    max_chars: int = 0,
) -> tuple[str, dict[str, Any]]:
    compiled = compile_execution_lock(
        binding,
        model=model,
        require_slots=require_slots,
    )
    text = str(prompt or "").rstrip()
    marker = f"配方ID：{compiled['id']} v{compiled['version']}"
    trimmed = False
    if marker not in text:
        section = "【镜头配方执行锁】\n" + compiled["text"]
        text = (text + "\n\n" + section).strip() if text else section
    if max_chars > 0 and len(text) > max_chars:
        anti_copy_pos = text.find("\n反复制边界：")
        if anti_copy_pos > 0:
            text = text[:anti_copy_pos].rstrip()
            trimmed = True
    if max_chars > 0 and len(text) > max_chars:
        raise RecipeContractError(
            "RECIPE_CONTRACT_TOO_LONG",
            f"镜头配方执行锁超过模型提示词上限（{len(text)} > {max_chars}）",
        )
    return text, {
        "recipeId": compiled["id"],
        "version": compiled["version"],
        "sha256": compiled["sha256"],
        "role": compiled["role"],
        "slots": compiled["slots"],
        "missingSlots": compiled["missingSlots"],
        "promptSha256": _sha256(text),
        "trimmedAntiCopy": trimmed,
    }
