import copy
import json
from pathlib import Path

import canvas_feed_contract
import recipe_contract


FIXTURE = Path(__file__).parent / "tests" / "fixtures" / "canvas-feed-valid" / "generic.json"


def test_catalog_contract_is_complete():
    catalog = recipe_contract.catalog()
    assert catalog["recipeCount"] == 295
    assert catalog["kindCounts"] == {"creative": 292, "tool": 3}
    recipe = recipe_contract.get_recipe("handoff-ensemble-focus")
    assert recipe is not None
    assert recipe["invariants"]
    assert recipe["slots"]


def test_recipe_prompt_augmentation_is_deterministic():
    recipe = recipe_contract.get_recipe("handoff-ensemble-focus")
    slots = {slot["key"]: "已填写" for slot in recipe["slots"]}
    binding = {
        "id": recipe["id"],
        "version": recipe["version"],
        "sha256": recipe["contentSha256"],
        "role": "primary",
        "segmentNo": 1,
        "slots": slots,
    }
    first_prompt, plan = recipe_contract.augment_prompt(
        "@角色｜贾富贵（图3）走进长桌南沿。",
        binding,
        model="doubao-seedance-2-0",
    )
    second_prompt, second_plan = recipe_contract.augment_prompt(
        first_prompt,
        binding,
        model="doubao-seedance-2-0",
    )
    assert first_prompt == second_prompt
    assert plan["recipeId"] == "handoff-ensemble-focus"
    assert plan["missingSlots"] == []
    assert second_plan["promptSha256"] == plan["promptSha256"]
    assert first_prompt.count("配方ID：handoff-ensemble-focus") == 1


def test_recipe_prompt_trims_anti_copy_then_fails_closed():
    recipe = recipe_contract.get_recipe("handoff-ensemble-focus")
    slots = {slot["key"]: "已填写" for slot in recipe["slots"]}
    binding = {
        "id": recipe["id"],
        "version": recipe["version"],
        "sha256": recipe["contentSha256"],
        "role": "primary",
        "segmentNo": 1,
        "slots": slots,
    }
    full_prompt, _plan = recipe_contract.augment_prompt("主体走进画面。", binding)
    trimmed_prompt, trimmed_plan = recipe_contract.augment_prompt(
        "主体走进画面。",
        binding,
        max_chars=len(full_prompt) - 1,
    )
    assert len(trimmed_prompt) < len(full_prompt)
    assert trimmed_plan["trimmedAntiCopy"] is True
    assert "反复制边界" not in trimmed_prompt
    try:
        recipe_contract.augment_prompt(
            "主体走进画面。",
            binding,
            max_chars=40,
        )
    except recipe_contract.RecipeContractError as exc:
        assert exc.code == "RECIPE_CONTRACT_TOO_LONG"
    else:
        raise AssertionError("tiny prompt budget should fail closed")


def test_recipe_survives_canvas_import_round_trip():
    feed = json.loads(FIXTURE.read_text(encoding="utf-8"))
    recipe = recipe_contract.get_recipe("handoff-ensemble-focus")
    segment = feed["segments"][0]
    segment["recipe"] = {
        "id": recipe["id"],
        "version": recipe["version"],
        "sha256": recipe["contentSha256"],
        "role": "primary",
        "segmentNo": 1,
        "slots": {},
    }
    plan = canvas_feed_contract.prepare_import(
        {"nodes": [], "connections": [], "episodeImports": []},
        feed,
        "recipe-project",
        1,
        source={"name": "recipe-test"},
    )
    assert plan.ok, [canvas_feed_contract.issue_dict(item) for item in plan.errors]
    video = next(
        node for node in plan.merged_payload["nodes"]
        if node.get("type") == "video"
    )
    imported = copy.deepcopy(video["metadata"]["params"]["recipe"])
    assert imported["id"] == "handoff-ensemble-focus"
    assert imported["version"] == "1.0.0"
    assert imported["sha256"] == recipe["contentSha256"]


def test_reimport_preserves_or_replaces_recipe_explicitly():
    base = json.loads(FIXTURE.read_text(encoding="utf-8"))
    first_recipe = recipe_contract.get_recipe("handoff-ensemble-focus")
    second_recipe = recipe_contract.get_recipe("optical-search-evidence-contraction")
    first = copy.deepcopy(base)
    first["segments"][0]["recipe"] = {
        "id": first_recipe["id"],
        "version": first_recipe["version"],
        "sha256": first_recipe["contentSha256"],
        "role": "primary",
        "segmentNo": first["segments"][0].get("no", 1),
        "slots": {},
    }
    initial = canvas_feed_contract.prepare_import(
        {"nodes": [], "connections": [], "episodeImports": []},
        first,
        "recipe-project",
        1,
        source={"name": "recipe-test"},
    )
    assert initial.ok

    without_recipe = copy.deepcopy(first)
    without_recipe["segments"][0].pop("recipe", None)
    preserved = canvas_feed_contract.prepare_import(
        initial.merged_payload,
        without_recipe,
        "recipe-project",
        2,
        source={"name": "recipe-test"},
    )
    assert preserved.ok
    video = next(
        node for node in preserved.merged_payload["nodes"]
        if node.get("type") == "video"
    )
    assert video["metadata"]["params"]["recipe"]["id"] == first_recipe["id"]

    replacement = copy.deepcopy(first)
    replacement["segments"][0]["recipe"] = {
        "id": second_recipe["id"],
        "version": second_recipe["version"],
        "sha256": second_recipe["contentSha256"],
        "role": "primary",
        "segmentNo": 1,
        "slots": {},
    }
    replaced = canvas_feed_contract.prepare_import(
        preserved.merged_payload,
        replacement,
        "recipe-project",
        3,
        source={"name": "recipe-test"},
    )
    assert replaced.ok
    video = next(
        node for node in replaced.merged_payload["nodes"]
        if node.get("type") == "video"
    )
    assert video["metadata"]["params"]["recipe"]["id"] == second_recipe["id"]
