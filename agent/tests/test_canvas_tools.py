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
