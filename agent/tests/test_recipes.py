from concurrent.futures import ThreadPoolExecutor

import pytest

from app import db, recipes
from app.config import get_settings


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "agent.db"))
    monkeypatch.setenv("CANVAS_ALLOW_SQLITE", "1")
    get_settings.cache_clear()
    db.init_db()
    yield
    get_settings.cache_clear()


def test_catalog_is_complete() -> None:
    info = recipes.metadata()
    assert info["recipeCount"] == 295
    assert info["kindCounts"] == {"creative": 292, "tool": 3}
    assert info["categoryCounts"]["01_动作与战斗"] == 32
    assert info["categoryCounts"]["12_综合镜头配方"] == 62


def test_search_chinese_recipe() -> None:
    result = recipes.search(query="群演聚焦接力", limit=8)
    assert result["recipes"]
    assert result["recipes"][0]["id"] == "handoff-ensemble-focus"
    assert result["recipes"][0]["matchReason"]


def test_search_category_and_model() -> None:
    result = recipes.search(
        category="12_综合镜头配方",
        model="seedance-2.0",
        limit=10,
    )
    assert result["total"] >= 50
    assert all(item["category"] == "12_综合镜头配方" for item in result["recipes"])


def test_recipe_detail_contains_contract() -> None:
    item = recipes.get_recipe("handoff-ensemble-focus")
    assert item is not None
    assert item["kind"] == "creative"
    assert item["invariants"]
    assert item["slots"]
    assert "Seedance" in item["template"]


def test_binding_validation_rejects_two_primary_recipes() -> None:
    with pytest.raises(recipes.RecipeError) as exc:
        recipes.validate_bindings(
            [
                {"id": "handoff-ensemble-focus", "segmentNo": 3, "role": "primary"},
                {"id": "optical-search-evidence-contraction", "segmentNo": 3, "role": "primary"},
            ],
            model="seedance-2.0",
        )
    assert exc.value.code == "RECIPE_PRIMARY_CONFLICT"


def test_binding_validation_reports_missing_slots() -> None:
    with pytest.raises(recipes.RecipeError) as exc:
        recipes.validate_bindings(
            [{"id": "handoff-ensemble-focus", "segmentNo": 1}],
            model="seedance-2.0",
            require_slots=True,
        )
    assert exc.value.code == "RECIPE_REQUIRED_SLOT_MISSING"


def test_execution_contract_contains_invariants_and_slots() -> None:
    item = recipes.get_recipe("handoff-ensemble-focus")
    assert item is not None
    slots = {
        str(slot["key"]): "已填写"
        for slot in item["slots"]
    }
    compiled = recipes.compile_execution_contract(
        "handoff-ensemble-focus",
        slots=slots,
        model="seedance-2.0",
    )
    assert "镜头配方执行锁" in compiled["text"]
    assert "必须保留" in compiled["text"]
    assert "已绑定槽位" in compiled["text"]
    assert compiled["missingSlots"] == []


def test_catalog_installs_idempotently(tmp_db) -> None:
    first = recipes.install_catalog()
    second = recipes.install_catalog()
    assert first["recipeCount"] == second["recipeCount"] == 295
    with db.db() as conn:
        catalog_count = conn.execute("SELECT COUNT(*) AS n FROM recipe_catalog").fetchone()["n"]
        version_count = conn.execute("SELECT COUNT(*) AS n FROM recipe_versions").fetchone()["n"]
    assert catalog_count == 295
    assert version_count == 295


def test_agent_recipe_binding_is_applied_to_single_segment_feed() -> None:
    feed, applied, warnings = recipes.apply_to_feed(
        {
            "segments": [
                {"no": 1, "name": "段1", "raw": "测试"},
            ],
        },
        [{
            "id": "handoff-ensemble-focus",
            "version": "1.0.0",
            "role": "primary",
            "segmentNo": 0,
            "slots": {},
        }],
        model="seedance-2.0",
    )
    assert feed["segments"][0]["recipe"]["id"] == "handoff-ensemble-focus"
    assert applied[0]["segmentNo"] == 1
    assert warnings == []


def test_agent_global_recipe_is_not_silently_applied_to_multi_segment_feed() -> None:
    feed, applied, warnings = recipes.apply_to_feed(
        {
            "segments": [
                {"no": 1, "name": "段1", "raw": "一"},
                {"no": 2, "name": "段2", "raw": "二"},
            ],
        },
        [{
            "id": "handoff-ensemble-focus",
            "version": "1.0.0",
            "role": "primary",
            "segmentNo": 0,
            "slots": {},
        }],
        model="seedance-2.0",
    )
    assert "recipe" not in feed["segments"][0]
    assert applied == []
    assert warnings[0]["code"] == "RECIPE_NOT_APPLIED"


def test_run_bindings_merge_and_replace(tmp_db) -> None:
    merged = recipes.merge_binding_lists(
        [{
            "id": "handoff-ensemble-focus",
            "version": "1.0.0",
            "sha256": "hash",
            "role": "primary",
            "segmentNo": 0,
            "slots": {},
        }],
        [{
            "id": "optical-search-evidence-contraction",
            "version": "1.0.0",
            "sha256": "hash-2",
            "role": "primary",
            "segmentNo": 2,
            "slots": {},
        }],
    )
    assert len(merged) == 2
    recipes.replace_run_bindings("run-test", merged)
    with db.db() as conn:
        stored = recipes.run_bindings(conn, "run-test")
    assert [item["segmentNo"] for item in stored] == [0, 2]


def test_recipe_search_supports_concurrent_reads() -> None:
    def search(index: int) -> int:
        return recipes.search(
            query="镜头" if index % 2 else "群像",
            limit=8,
        )["total"]

    with ThreadPoolExecutor(max_workers=20) as executor:
        totals = list(executor.map(search, range(100)))
    assert len(totals) == 100
    assert all(total >= 0 for total in totals)
