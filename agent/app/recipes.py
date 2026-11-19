from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app import db


CATALOG_SCHEMA = "aicg3d-recipe-catalog/v1"
CATALOG_PATH = (
    Path(__file__).resolve().parent
    / "resources"
    / "aicg3d_recipes"
    / "catalog.v1.json"
)


class RecipeError(ValueError):
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


def _load_catalog() -> dict[str, Any]:
    try:
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecipeError("RECIPE_CATALOG_UNAVAILABLE", f"镜头配方目录不可用：{exc}") from exc
    if not isinstance(catalog, dict) or catalog.get("schema") != CATALOG_SCHEMA:
        raise RecipeError("RECIPE_CATALOG_INVALID", "镜头配方目录 schema 无效")
    expected = str(catalog.get("catalogSha256") or "")
    core = dict(catalog)
    core.pop("catalogSha256", None)
    if not expected or _sha256(_canonical(core)) != expected:
        raise RecipeError("RECIPE_CATALOG_INVALID", "镜头配方目录哈希校验失败")
    recipes = catalog.get("recipes")
    if not isinstance(recipes, list) or len(recipes) != 295:
        raise RecipeError("RECIPE_CATALOG_INVALID", "镜头配方目录数量不完整")
    return catalog


_CATALOG: dict[str, Any] | None = None
_BY_ID: dict[str, dict[str, Any]] | None = None


def catalog() -> dict[str, Any]:
    global _CATALOG, _BY_ID
    if _CATALOG is None:
        _CATALOG = _load_catalog()
        _BY_ID = {
            str(item.get("id") or ""): item
            for item in _CATALOG.get("recipes") or []
            if isinstance(item, dict) and item.get("id")
        }
    return _CATALOG


def recipe_map() -> dict[str, dict[str, Any]]:
    catalog()
    return _BY_ID or {}


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


def install_catalog() -> dict[str, Any]:
    value = catalog()
    now = db.utcnow()
    with db.db() as conn:
        for item in value.get("recipes") or []:
            if not isinstance(item, dict):
                continue
            recipe_id = str(item.get("id") or "")
            version = str(item.get("version") or "")
            if not recipe_id or not version:
                continue
            contract_json = json.dumps(item, ensure_ascii=False, sort_keys=True)
            conn.execute(
                """
                INSERT INTO recipe_versions(
                    recipe_id, version, content_sha256, contract_json, source_path, created_at
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(recipe_id, version) DO UPDATE SET
                    content_sha256=excluded.content_sha256,
                    contract_json=excluded.contract_json,
                    source_path=excluded.source_path
                """,
                (
                    recipe_id,
                    version,
                    str(item.get("contentSha256") or ""),
                    contract_json,
                    str(item.get("sourcePath") or ""),
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO recipe_catalog(
                    id, kind, category, title, description, active_version, manifest_json, updated_at
                ) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    kind=excluded.kind,
                    category=excluded.category,
                    title=excluded.title,
                    description=excluded.description,
                    active_version=excluded.active_version,
                    manifest_json=excluded.manifest_json,
                    updated_at=excluded.updated_at
                """,
                (
                    recipe_id,
                    str(item.get("kind") or ""),
                    str(item.get("category") or ""),
                    str(item.get("title") or ""),
                    str(item.get("description") or ""),
                    version,
                    contract_json,
                    now,
                ),
            )
    return metadata()


def get_recipe(recipe_id: str, version: str = "") -> dict[str, Any] | None:
    item = recipe_map().get(str(recipe_id or "").strip())
    if not item:
        return None
    if version and str(item.get("version") or "") != str(version):
        return None
    return json.loads(json.dumps(item, ensure_ascii=False))


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


def _model_family(value: str) -> str:
    text = str(value or "").strip().lower()
    if "minimax" in text or "h3" in text:
        return "minimax-h3"
    if "seedance" in text:
        return "seedance-2.0"
    return text


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
    normalized_model = _model_family(str(model or ""))
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
    ranked.sort(
        key=lambda value: (
            -value[0],
            value[1],
            str(value[2].get("id") or ""),
        )
    )
    page = ranked[offset:offset + page_size]
    next_cursor = offset + len(page) if offset + len(page) < len(ranked) else None
    return {
        "recipes": [compact(item, reason) for _score, _category, item, reason in page],
        "total": len(ranked),
        "cursor": offset,
        "nextCursor": next_cursor,
        "catalog": metadata(),
    }


def validate_bindings(
    bindings: list[dict[str, Any]] | None,
    *,
    model: str = "",
    require_slots: bool = False,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    primary_by_segment: dict[int, str] = {}
    for raw in bindings or []:
        if not isinstance(raw, dict):
            raise RecipeError("RECIPE_BINDING_INVALID", "镜头配方绑定必须是对象")
        recipe_id = str(raw.get("id") or raw.get("recipeId") or "").strip()
        item = get_recipe(recipe_id, str(raw.get("version") or raw.get("recipeVersion") or ""))
        if not item:
            raise RecipeError("RECIPE_NOT_FOUND", f"镜头配方不存在：{recipe_id}")
        expected_hash = str(item.get("contentSha256") or "")
        actual_hash = str(raw.get("sha256") or raw.get("contentSha256") or "")
        if actual_hash and actual_hash != expected_hash:
            raise RecipeError("RECIPE_HASH_MISMATCH", f"镜头配方版本哈希不一致：{recipe_id}")
        model_value = _model_family(str(model or ""))
        if model_value and item.get("models") and model_value not in item.get("models"):
            raise RecipeError("RECIPE_MODEL_UNSUPPORTED", f"镜头配方 {recipe_id} 不支持当前模型")
        segment_no = max(0, int(raw.get("segmentNo") or 0))
        role = str(raw.get("role") or "primary")
        if role not in {"primary", "modifier"}:
            raise RecipeError("RECIPE_BINDING_INVALID", "镜头配方角色只能是 primary 或 modifier")
        if role == "primary":
            existing = primary_by_segment.get(segment_no)
            if existing and existing != recipe_id:
                raise RecipeError(
                    "RECIPE_PRIMARY_CONFLICT",
                    f"段 {segment_no} 只能设置一个主镜头配方",
                )
            primary_by_segment[segment_no] = recipe_id
        slots = raw.get("slots") if isinstance(raw.get("slots"), dict) else {}
        normalized_slots = {
            str(key): str(value)
            for key, value in slots.items()
            if str(key).strip() and isinstance(value, (str, int, float, bool))
        }
        missing = [
            str(slot.get("key") or "")
            for slot in item.get("slots") or []
            if isinstance(slot, dict)
            and slot.get("required", True)
            and not normalized_slots.get(str(slot.get("key") or ""), "").strip()
        ]
        if require_slots and missing:
            raise RecipeError(
                "RECIPE_REQUIRED_SLOT_MISSING",
                f"镜头配方 {recipe_id} 缺少必填槽位：{', '.join(missing)}",
            )
        result.append(
            {
                "id": recipe_id,
                "version": str(item.get("version") or ""),
                "sha256": expected_hash,
                "role": role,
                "segmentNo": segment_no,
                "slots": normalized_slots,
                "missingSlots": missing,
                "matchReason": str(raw.get("matchReason") or ""),
            }
        )
    return result


def save_run_bindings(
    conn: Any,
    run_id: str,
    bindings: list[dict[str, Any]],
    *,
    created_at: str,
) -> None:
    for binding in bindings:
        conn.execute(
            """
            INSERT INTO run_recipe_bindings(
                run_id, segment_no, recipe_id, recipe_version, recipe_sha256,
                role, slots_json, match_reason, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,
                int(binding.get("segmentNo") or 0),
                str(binding.get("id") or ""),
                str(binding.get("version") or ""),
                str(binding.get("sha256") or ""),
                str(binding.get("role") or "primary"),
                json.dumps(binding.get("slots") or {}, ensure_ascii=False),
                str(binding.get("matchReason") or ""),
                created_at,
            ),
        )


def run_bindings(conn: Any, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM run_recipe_bindings WHERE run_id=? ORDER BY segment_no, id",
        (str(run_id or ""),),
    ).fetchall()
    return [
        {
            "segmentNo": int(row["segment_no"]),
            "id": row["recipe_id"],
            "version": row["recipe_version"],
            "sha256": row["recipe_sha256"],
            "role": row["role"],
            "slots": db.json_loads(row["slots_json"], {}),
            "matchReason": row["match_reason"],
        }
        for row in rows
    ]


def merge_binding_lists(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[int, str, str], dict[str, Any]] = {}
    for group in groups:
        for binding in group:
            if not isinstance(binding, dict) or not binding.get("id"):
                continue
            key = (
                int(binding.get("segmentNo") or 0),
                str(binding.get("id") or ""),
                str(binding.get("role") or "primary"),
            )
            merged[key] = {
                **merged.get(key, {}),
                **binding,
            }
    return sorted(
        merged.values(),
        key=lambda item: (
            int(item.get("segmentNo") or 0),
            str(item.get("role") or ""),
            str(item.get("id") or ""),
        ),
    )


def replace_run_bindings(
    run_id: str,
    bindings: list[dict[str, Any]],
) -> None:
    now = db.utcnow()
    with db.db() as conn:
        conn.execute(
            "DELETE FROM run_recipe_bindings WHERE run_id=?",
            (str(run_id or ""),),
        )
        save_run_bindings(
            conn,
            run_id,
            bindings,
            created_at=now,
        )


def compile_execution_contract(
    recipe_id: str,
    *,
    version: str = "",
    slots: dict[str, Any] | None = None,
    model: str = "",
) -> dict[str, Any]:
    item = get_recipe(recipe_id, version)
    if not item:
        raise RecipeError("RECIPE_NOT_FOUND", f"镜头配方不存在：{recipe_id}")
    normalized = validate_bindings(
        [
            {
                "id": recipe_id,
                "version": item.get("version"),
                "sha256": item.get("contentSha256"),
                "role": "primary",
                "segmentNo": 0,
                "slots": slots or {},
            }
        ],
        model=model,
        require_slots=False,
    )[0]
    invariants = [
        str(value.get("rule") or "")
        for value in item.get("invariants") or []
        if isinstance(value, dict) and value.get("rule")
    ]
    lines = [
        f"【镜头配方执行锁｜{item.get('title')}｜{item.get('id')} v{item.get('version')}】",
    ]
    if item.get("mechanism"):
        lines.append("机制：" + str(item.get("mechanism")).strip())
    if invariants:
        lines.append("必须保留：")
        lines.extend("- " + value for value in invariants)
    if normalized["slots"]:
        lines.append("已绑定槽位：")
        lines.extend(
            f"- {key}：{value}"
            for key, value in sorted(normalized["slots"].items())
        )
    if item.get("antiCopy"):
        lines.append("反复制边界：")
        lines.extend("- " + str(value) for value in item.get("antiCopy") or [])
    return {
        "recipeId": recipe_id,
        "version": item.get("version"),
        "sha256": item.get("contentSha256"),
        "model": str(model or ""),
        "slots": normalized["slots"],
        "missingSlots": normalized["missingSlots"],
        "text": "\n".join(lines).strip(),
    }


def apply_to_feed(
    feed: dict[str, Any],
    preferred_bindings: list[dict[str, Any]] | None,
    *,
    model: str = "",
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    out = json.loads(json.dumps(feed if isinstance(feed, dict) else {}, ensure_ascii=False))
    segments = out.get("segments") if isinstance(out.get("segments"), list) else []
    preferred = validate_bindings(preferred_bindings, model=model)
    applied: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    used_unassigned = False
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        segment_no = int(segment.get("no") or index + 1)
        raw_binding = segment.get("recipe")
        source = "segment"
        if not isinstance(raw_binding, dict):
            raw_binding = next(
                (
                    binding
                    for binding in preferred
                    if int(binding.get("segmentNo") or 0) == segment_no
                ),
                None,
            )
            source = "run"
        if not isinstance(raw_binding, dict):
            global_binding = next(
                (
                    binding
                    for binding in preferred
                    if int(binding.get("segmentNo") or 0) == 0
                ),
                None,
            )
            if global_binding and len(segments) == 1:
                raw_binding = global_binding
                source = "run-global"
        if not isinstance(raw_binding, dict):
            continue
        normalized = validate_bindings(
            [{**raw_binding, "segmentNo": segment_no}],
            model=model,
        )[0]
        segment["recipe"] = normalized
        applied.append({**normalized, "source": source})
        used_unassigned = used_unassigned or int(raw_binding.get("segmentNo") or 0) == 0
    if preferred and not applied:
        warnings.append(
            {
                "code": "RECIPE_NOT_APPLIED",
                "message": "本轮选择了镜头配方，但模型没有为任何段落返回配方绑定。",
            }
        )
    elif used_unassigned and len(segments) > 1:
        warnings.append(
            {
                "code": "RECIPE_SEGMENT_UNASSIGNED",
                "message": "镜头配方未指定段号，多段项目中请核对实际应用的段落。",
            }
        )
    return out, applied, warnings
