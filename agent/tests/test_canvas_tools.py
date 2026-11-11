from app.canvas_tools import CanvasTools


def test_list_assets_uses_asset_id_not_title() -> None:
    project = {
        "id": "project-1",
        "payload": {
            "nodes": [
                {
                    "id": "n1",
                    "type": "image",
                    "title": "角色｜陈大强（图2）",
                    "metadata": {
                        "assetId": "character:陈大强:default",
                        "seriesId": "character:陈大强",
                        "variantKey": "default",
                        "status": "ready",
                    },
                },
                {
                    "id": "n2",
                    "type": "image",
                    "title": "角色｜陈大强（图9）",
                    "metadata": {
                        "assetId": "character:陈大强:old",
                        "seriesId": "character:陈大强",
                        "variantKey": "old",
                        "status": "missing",
                    },
                },
            ]
        },
    }
    tools = CanvasTools(project, None)
    assets = tools.call("list_assets", {}, {"projectId": "project-1"})["assets"]
    assert {item["assetId"] for item in assets} == {
        "character:陈大强:default",
        "character:陈大强:old",
    }


def test_get_project_context_returns_one_bounded_package() -> None:
    project = {
        "id": "project-1",
        "title": "测试项目",
        "revision": 7,
        "payload": {
            "episodeImports": [{"id": "EP001", "title": "第1集", "nodeIds": ["n1"]}],
            "nodes": [
                {
                    "id": "n1",
                    "type": "image",
                    "title": "角色｜陈大强（图2）",
                    "metadata": {
                        "assetId": "character:陈大强:default",
                        "seriesId": "character:陈大强",
                        "variantKey": "default",
                        "episodeId": "EP001",
                        "prompt": "角色参考提示词",
                    },
                }
            ],
            "connections": [],
        },
    }
    tools = CanvasTools(project, None)
    result = tools.call("get_project_context", {"nodeLimit": 20}, {"projectId": "project-1"})
    assert result["project"]["title"] == "测试项目"
    assert result["project"]["episodes"][0]["id"] == "EP001"
    assert result["nodes"][0]["assetId"] == "character:陈大强:default"
    assert result["nodes"][0]["prompt"] == "角色参考提示词"
    assert result["assets"][0]["assetId"] == "character:陈大强:default"
    assert result["nodesTruncated"] is False


def test_project_context_trims_prompt_without_losing_structure() -> None:
    project = {
        "id": "project-2",
        "title": "大项目",
        "payload": {
            "nodes": [
                {
                    "id": f"n{i}",
                    "type": "image",
                    "title": f"角色{i}",
                    "metadata": {
                        "assetId": f"character:角色{i}:default",
                        "prompt": "长提示词" * 3000,
                    },
                }
                for i in range(30)
            ],
            "connections": [],
        },
    }
    tools = CanvasTools(project, None, max_result_bytes=32 * 1024)
    result = tools.call("get_project_context", {"nodeLimit": 30}, {})
    assert len(result["nodes"]) == 30
    assert len(result["assets"]) == 30
    assert result["nodeCount"] == 30
    assert "preview" not in result
    assert result["contextBytes"] <= result["contextBudget"]
    assert all("assetId" in item for item in result["nodes"])


def test_explicit_reference_resolution_reports_missing() -> None:
    project = {
        "id": "project-1",
        "payload": {
            "nodes": [
                {
                    "id": "n1",
                    "type": "image",
                    "title": "角色｜陈大强（图2）",
                    "metadata": {
                        "assetId": "character:陈大强:default",
                        "seriesId": "character:陈大强",
                        "variantKey": "default",
                    },
                }
            ]
        },
    }
    tools = CanvasTools(project, None)
    resolved = tools.call(
        "resolve_prompt_references",
        {
            "assetIds": [
                "character:陈大强:default",
                "character:贾富贵:default",
            ]
        },
        {"projectId": "project-1"},
    )
    assert resolved["references"][0]["assetId"] == "character:陈大强:default"
    assert resolved["missing"] == ["character:贾富贵:default"]


def test_node_dependencies_do_not_add_refs() -> None:
    project = {
        "id": "project-1",
        "payload": {
            "nodes": [
                {"id": "image-1", "type": "image", "title": "角色"},
                {"id": "video-1", "type": "video", "title": "段1", "params": {"refs": []}},
            ],
            "connections": [
                {"id": "c1", "fromNodeId": "image-1", "toNodeId": "video-1"}
            ],
        },
    }
    tools = CanvasTools(project, None)
    result = tools.call(
        "get_node_dependencies",
        {"nodeId": "video-1"},
        {"projectId": "project-1"},
    )
    assert result["upstream"][0]["id"] == "image-1"
    assert project["payload"]["nodes"][1]["params"]["refs"] == []


def test_read_skill_section_is_bounded_and_rejects_traversal(tmp_path) -> None:
    root = tmp_path / "skill"
    (root / "references").mkdir(parents=True)
    (root / "references" / "runtime-quality-contract.md").write_text(
        "甲" * 2400,
        encoding="utf-8",
    )
    tools = CanvasTools({}, None, skill={"rootPath": str(root)})
    first = tools.call(
        "read_skill_section",
        {
            "path": "references/runtime-quality-contract.md",
            "offset": 0,
            "maxChars": 1000,
        },
        {},
    )
    assert first["totalChars"] == 2400
    assert len(first["content"]) == 1000
    assert first["truncated"] is True
    assert first["nextOffset"] == 1000

    import pytest

    with pytest.raises(ValueError):
        tools.call("read_skill_section", {"path": "../secret.md"}, {})
