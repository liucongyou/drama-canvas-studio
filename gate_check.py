# -*- coding: utf-8 -*-
"""gate_check.py — 画布挂接机检（完整 @资产 ↔ 实连线图 双向核对）

铁律：投喂稿 @角色/场景/道具（图N）出现名单 与实连线图集合
必须双向一致，通过才可提交生成。本机检从 payload 读取：
  - 稿侧：video 节点 prompt 里完整资产引用名单
  - 实侧：video 节点入边来自 image 节点的图号集合（节点 title 含（图N））
返回 PASS/FAIL 明细。
"""
import re

import canvas_feed_contract


def _scene_prompt(raw):
    text = str(raw or "")
    lines = []
    for line in text.splitlines():
        if "对应@" in line:
            continue
        match = re.search(r"(?:【\s*)?台词\s*[:：]", line)
        lines.append(line[:match.start()] if match else line)
    return "\n".join(lines)


def _declared_image_refs(raw, params):
    declared = set()
    for refs_key in ("refs",):
        refs = params.get(refs_key) if isinstance(params, dict) else None
        for ref in refs if isinstance(refs, list) else []:
            if not isinstance(ref, dict):
                continue
            label = str(ref.get("label") or ref.get("title") or "")
            code = canvas_feed_contract.canonical_code_from_text(label)
            if code:
                declared.add(code)
    # The preamble's "素材引用" mapping can be stale after a prompt edit.
    # Scene references are authoritative only in the timeline body.
    declared.update(canvas_feed_contract.asset_ref_codes(_scene_prompt(raw)))
    return declared


def _dialogue_only_codes(raw):
    scene_codes = canvas_feed_contract.asset_ref_codes(_scene_prompt(raw))
    dialogue_codes = set()
    for line in str(raw or "").splitlines():
        match = re.search(r"(?:【\s*)?台词\s*[:：]", line)
        if match:
            dialogue_codes.update(
                canvas_feed_contract.asset_ref_codes(line[match.start():])
            )
    return dialogue_codes - scene_codes


def run_check(payload, episode_id=None):
    nodes = payload.get("nodes", [])
    conns = payload.get("connections", [])
    videos = [n for n in nodes if n.get("type") == "video"]
    images = [n for n in nodes if n.get("type") == "image"]
    audios = [n for n in nodes if n.get("type") == "audio"]
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
        code = canvas_feed_contract.canonical_code_from_text(n.get("title", ""))
        if code:
            fig_of.setdefault(n["id"], code)
    audio_fig_of = {}
    for n in audios:
        code = canvas_feed_contract.canonical_code_from_text(n.get("title", ""))
        if code:
            audio_fig_of.setdefault(n["id"], code)

    in_edges = {}
    for c in conns:
        in_edges.setdefault(c["toNodeId"], []).append(c["fromNodeId"])

    results, errors = [], []
    for v in videos:
        raw = v.get("metadata", {}).get("prompt", "")
        params = (v.get("metadata") or {}).get("params") or {}
        scene_prompt = _scene_prompt(raw)
        script_set = canvas_feed_contract.asset_ref_codes(scene_prompt)
        declared_refs = (
            _declared_image_refs(raw, params)
            - _dialogue_only_codes(raw)
        )
        all_linked_figs = set()
        for src in in_edges.get(v["id"], []):
            if src in fig_of:
                all_linked_figs.add(fig_of[src])
        # A stale edge is not an effective attachment. Only edges whose source
        # is still declared by the current prompt or params.refs count.
        linked_figs = {
            code for code in all_linked_figs if code in declared_refs
        }
        stale_linked = all_linked_figs - linked_figs
        audio_script = {
            code for _role, code in canvas_feed_contract.audio_ref_entries(raw)
        }
        voice_refs = params.get("voiceRefs") or (v.get("metadata") or {}).get("voiceRefs") or []
        if not voice_refs and isinstance(params.get("voiceRef"), dict):
            voice_refs = [params["voiceRef"]]
        voice_ref_figs = {
            audio_fig_of.get(str(ref.get("nodeId") or ""), "")
            for ref in voice_refs
            if isinstance(ref, dict)
        }
        voice_ref_figs.discard("")
        linked_audio = {
            audio_fig_of[src]
            for src in in_edges.get(v["id"], [])
            if src in audio_fig_of
        }
        # `params.refs`/link edges describe attached reference slots. Only
        # body @ mentions select the identities that must appear in this shot.
        # Extra attached identities are intentionally allowed (for example a
        # background character kept available without an on-screen action).
        missing_script_figs = script_set - linked_figs
        carried_only_figs = linked_figs - script_set
        image_ok = not missing_script_figs
        audio_ok = audio_script == voice_ref_figs == linked_audio
        ok = image_ok and audio_ok
        results.append({
            "title": v.get("title", ""), "ok": ok,
            "script": sorted(script_set, key=lambda x: int(x[1:])),
            "linked": sorted(linked_figs, key=lambda x: int(x[1:])),
            "staleLinked": sorted(stale_linked, key=lambda x: int(x[1:])),
            "voiceScript": sorted(audio_script, key=lambda x: int(x[1:])),
            "voiceRefs": sorted(voice_ref_figs, key=lambda x: int(x[1:])),
            "voiceLinked": sorted(linked_audio, key=lambda x: int(x[1:])),
            "imageOk": image_ok,
            "audioOk": audio_ok,
            "imageStaleOk": not stale_linked,
            "missing": sorted(missing_script_figs, key=lambda x: int(x[1:])),
            "carriedOnly": sorted(carried_only_figs, key=lambda x: int(x[1:])),
        })
        if not ok or stale_linked:
            errors.append(v.get("title", ""))
    return results, errors


def run_check_json(payload, episode_id=None):
    results, errors = run_check(payload, episode_id=episode_id)
    return {
        "passed": not errors,
        "results": results,
        "failed": errors,
    }
