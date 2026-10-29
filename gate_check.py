# -*- coding: utf-8 -*-
"""gate_check.py — 画布挂接机检（完整 @资产 ↔ 实连线图 双向核对）

铁律：投喂稿 @角色/场景/道具（图N）出现名单 与实连线图集合
必须双向一致，通过才可提交生成。本机检从 payload 读取：
  - 稿侧：video 节点 prompt 里完整资产引用名单
  - 实侧：video 节点入边来自 image 节点的图号集合（节点 title 含（图N））
返回 PASS/FAIL 明细。
"""
import canvas_feed_contract

_TITLE_FIG_RE = canvas_feed_contract.TITLE_CODE_RE


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
        m = _TITLE_FIG_RE.search(n.get("title", ""))
        if m:
            fig_of.setdefault(n["id"], "图" + m.group(1))
    audio_fig_of = {}
    for n in audios:
        m = _TITLE_FIG_RE.search(n.get("title", ""))
        if m:
            audio_fig_of.setdefault(n["id"], "图" + m.group(1))

    in_edges = {}
    for c in conns:
        in_edges.setdefault(c["toNodeId"], []).append(c["fromNodeId"])

    results, errors = [], []
    for v in videos:
        raw = v.get("metadata", {}).get("prompt", "")
        params = (v.get("metadata") or {}).get("params") or {}
        script_set = canvas_feed_contract.asset_ref_codes(raw)
        linked_figs = set()
        for src in in_edges.get(v["id"], []):
            if src in fig_of:
                linked_figs.add(fig_of[src])
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
        image_ok = script_set == linked_figs
        audio_ok = audio_script == voice_ref_figs == linked_audio
        ok = image_ok and audio_ok
        results.append({
            "title": v.get("title", ""), "ok": ok,
            "script": sorted(script_set, key=lambda x: int(x[1:])),
            "linked": sorted(linked_figs, key=lambda x: int(x[1:])),
            "voiceScript": sorted(audio_script, key=lambda x: int(x[1:])),
            "voiceRefs": sorted(voice_ref_figs, key=lambda x: int(x[1:])),
            "voiceLinked": sorted(linked_audio, key=lambda x: int(x[1:])),
            "imageOk": image_ok,
            "audioOk": audio_ok,
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
