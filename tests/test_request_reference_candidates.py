from __future__ import annotations

from server import request_reference_candidates


def _linked_nodes(count: int) -> tuple[list[dict], list[dict]]:
    nodes = [
        {
            "id": f"image-{index}",
            "title": f"角色{index}",
            "metadata": {"assetId": f"asset-{index}"},
        }
        for index in range(count)
    ]
    connections = [
        {"fromNodeId": node["id"], "toNodeId": "target"}
        for node in nodes
    ]
    return nodes, connections


def test_explicit_refs_do_not_resurrect_removed_link() -> None:
    nodes, connections = _linked_nodes(10)
    kept = [
        {"nodeId": node["id"], "label": node["title"]}
        for node in nodes[:9]
    ]
    refs, explicit = request_reference_candidates(
        params={"refs": kept},
        meta={},
        node_type="video",
        node_id="target",
        connections=connections,
        nodes=nodes,
    )
    assert explicit is True
    assert refs == kept
    assert not any(ref["nodeId"] == "image-9" for ref in refs)


def test_empty_explicit_refs_means_no_slots() -> None:
    nodes, connections = _linked_nodes(3)
    refs, explicit = request_reference_candidates(
        params={"refs": []},
        meta={"refs": [{"nodeId": "stale"}]},
        node_type="video",
        node_id="target",
        connections=connections,
        nodes=nodes,
    )
    assert explicit is True
    assert refs == []


def test_legacy_nodes_without_refs_field_derive_links() -> None:
    nodes, connections = _linked_nodes(3)
    refs, explicit = request_reference_candidates(
        params={},
        meta={},
        node_type="video",
        node_id="target",
        connections=connections,
        nodes=nodes,
    )
    assert explicit is False
    assert [ref["nodeId"] for ref in refs] == [
        "image-0",
        "image-1",
        "image-2",
    ]
