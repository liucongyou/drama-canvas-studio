# -*- coding: utf-8 -*-
"""gate_check.py — 画布挂接机检（完整 @资产 ↔ 实连线图 双向核对）

铁律：投喂稿 @角色/场景/道具（图N）出现名单 与实连线图集合
必须双向一致，通过才可提交生成。本机检从 payload 读取：
  - 稿侧：video 节点 prompt 里完整资产引用名单
  - 实侧：video 节点入边来自 image 节点的图号集合（节点 title 含（图N））
返回 PASS/FAIL 明细。
"""
import json
import re

_REF_RE = re.compile(
    r"@(?:角色|场景|道具)｜[^（(\n]+[（(](?:参考)?图\s*(\d+)[）)]"
)
_LEGACY_REF_RE = re.compile(
    r"@//(?:chr|scn|prp)\s*/\s*-\s*([^（(]+)（(?:参考)?图\s*(\d+)"
)
_TITLE_FIG_RE = re.compile(r"（图\s*(\d+)\s*）")


def run_check(payload, episode_id=None):
    nodes = payload.get("nodes", [])
    conns = payload.get("connections", [])
    videos = [n for n in nodes if n.get("type") == "video"]
    images = [n for n in nodes if n.get("type") == "image"]
    if episode_id:
        groups = payload.get("episodeImports") or []
        group = next(
            (
                item for item in groups
                if str(item.get("id") or "").upper() == str(episode_id).upper()
            ),
            None,
        )
        if group:
            video_ids = set(group.get("videoNodeIds") or [])
            videos = [node for node in videos if node.get("id") in video_ids]

    fig_of = {}
    for n in images:
        m = _TITLE_FIG_RE.search(n.get("title", ""))
        if m:
            fig_of.setdefault(n["id"], "图" + m.group(1))

    in_edges = {}
    for c in conns:
        in_edges.setdefault(c["toNodeId"], []).append(c["fromNodeId"])

    results, errors = [], []
    for v in videos:
        raw = v.get("metadata", {}).get("prompt", "")
        script_figs = ["图" + n for n in _REF_RE.findall(raw)]
        script_figs.extend("图" + n for _name, n in _LEGACY_REF_RE.findall(raw))
        script_set = set(script_figs)
        linked_figs = set()
        for src in in_edges.get(v["id"], []):
            if src in fig_of:
                linked_figs.add(fig_of[src])
        ok = script_set == linked_figs
        results.append({
            "title": v.get("title", ""), "ok": ok,
            "script": sorted(script_set, key=lambda x: int(x[1:])),
            "linked": sorted(linked_figs, key=lambda x: int(x[1:])),
        })
        if not ok:
            errors.append(v.get("title", ""))
    return results, errors


def run_check_json(payload, episode_id=None):
    results, errors = run_check(payload, episode_id=episode_id)
    return {
        "passed": not errors,
        "results": results,
        "failed": errors,
    }
