# -*- coding: utf-8 -*-
"""import_feed.py — 投喂载荷 JSON → 画布 payload（五区布局 + 音色对白过滤 + 自动连线）

对齐 BeefTV 节点 schema：text/image/video/audio 四类节点字段与桌面端可渲染项目完全一致。
布局：角色列 / 场景列 / 道具列 / 音频列 / 视频列（分开排序显示，不混排）。
铁律：
  1. 只有有对白的角色才生成音色（音频节点）——解析各段台词行自动判定；
  2. 每段视频节点自动连线到该段引用的资产图（@//chr/scn/prp（图N））；
  3. 音频节点（角色声线）连线到该角色出场的段；
  4. 文本头卡 → 全段；段1→段2→…→段N 串联。
"""
import json
import copy
import math
import re
import secrets
import string
import time
from datetime import datetime, timezone

import canvas_feed_contract

_B62 = string.digits + string.ascii_letters
_NODE_CHARS = string.ascii_lowercase + string.digits
_HEX = string.hexdigits.lower()


def _rid_node():
    return "node-" + "".join(secrets.choice(_NODE_CHARS) for _ in range(12)) + "-" + \
        "".join(secrets.choice(_HEX) for _ in range(8))


def _rid_edge():
    return "edge-" + "".join(secrets.choice(_B62) for _ in range(12))


def _rid_audio():
    return "audio-" + str(int(time.time() * 1000)) + "-" + \
        "".join(secrets.choice(_NODE_CHARS) for _ in range(5))


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _canonical_figure(value):
    raw = str(value or "").strip()
    return (
        canvas_feed_contract.canonical_code(raw)
        or canvas_feed_contract.canonical_code(f"图{raw}")
    )


# 五区列 x 坐标（按卡片真实尺寸紧密排布）
COL_ROLE, COL_SCENE, COL_PROP, COL_AUDIO, COL_VIDEO = 0, 640, 960, 1280, 1600
COL_CROWD = 320

_REF_RE = re.compile(
    r"@(?:(?://(?:chr|scn|prp)\s*/\s*-\s*)|(?:角色|场景|道具|音频|群像)\s*[｜|:：]\s*)"
    r"([^（(]+?)\s*（(?:参考)?图\s*(\d+)"
)
_NAMED_REF_RE = re.compile(
    r"@?(?P<kind>角色|场景|道具|音频|群像)\s*[｜|:：]\s*"
    r"(?P<name>[^（(]+?)\s*（(?:参考)?图\s*(?P<code>\d+)"
)
_RAW_MENTION_RE = re.compile(
    r"@//(?P<kind>chr|scn|prp)\s*/\s*-\s*"
    r"(?P<name>[^（(；;，,\n@]+?)"
    r"(?P<tail>[（(][^）)\n]*[）)]|(?=[；;，,\n@]|$))"
)
_PLAIN_FIGURE_MENTION_RE = re.compile(
    r"(?<![\u4e00-\u9fffA-Za-z0-9·・@｜|])"
    r"(?P<name>[\u4e00-\u9fffA-Za-z0-9·・]{1,24})"
    r"\s*[（(]\s*(?:参考)?图\s*(?P<figure>\d+)\s*[)）]"
)
_MENTION_KIND_PREFIX = {"chr": "角色", "scn": "场景", "prp": "道具"}
REIMPORT_IDENTITY_POLICY = "episode-stable-node-edge-v1"


def _dialog_speaker_names(text):
    return canvas_feed_contract.dialogue_speakers(str(text or ""))


def _asset_kind_from_title(title):
    raw = str(title or "")
    if raw.startswith("角色｜") or raw.startswith("角色|"):
        return "角色"
    if raw.startswith("群像｜") or raw.startswith("群像|"):
        return "群像"
    if raw.startswith("场景｜") or raw.startswith("场景|"):
        return "场景"
    if raw.startswith("道具｜") or raw.startswith("道具|"):
        return "道具"
    return ""


def _asset_identity(title, kind=""):
    text = str(title or "").strip()
    text = re.sub(r"^[^｜|]*[｜|]\s*", "", text)
    text = re.sub(r"[（(]\s*图\s*\d+\s*[)）]\s*$", "", text)
    text = re.sub(
        r"[（(](?:角色立绘|场景母版|九宫格|道具设定图|群像设定图|群像资产卡)[^）)]*[）)]\s*$",
        "",
        text,
    )
    if kind == "场景":
        text = re.sub(r"\s*[·・]\s*(?:母版|九宫格)\s*$", "", text)
    text = re.sub(r"\s+", "", text).casefold()
    return f"{kind}:{text}" if kind and text else ""


def _asset_pool(records, allow_idle_project_id=""):
    pool = []
    for item in records or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        same_target_project = (
            allow_idle_project_id
            and str(item.get("projectId") or "") == str(allow_idle_project_id)
        )
        if not same_target_project and (
            not content
            or str(item.get("status") or "") != "done"
        ):
            continue
        kind = _asset_kind_from_title(item.get("title"))
        key = _asset_identity(item.get("title"), kind)
        if not key:
            continue
        pool.append({**item, "kind": kind, "assetKey": key, "content": content})
    return pool


def build_asset_pool_from_payload(payload, project_id):
    """Build an isolated image asset pool from a project payload copy."""
    pool = []
    episode_ids_by_node = {}
    for group in payload.get("episodeImports") or []:
        for node_id in group.get("nodeIds") or []:
            episode_ids_by_node.setdefault(str(node_id), set()).add(
                str(group.get("id") or "").upper()
            )
    for source_node in (payload or {}).get("nodes") or []:
        if not isinstance(source_node, dict) or source_node.get("type") != "image":
            continue
        source_meta = source_node.get("metadata") or {}
        pool.append({
            "assetKey": source_meta.get("assetKey", ""),
            "id": source_node.get("id", ""),
            "projectId": project_id,
            "title": source_node.get("title", ""),
            "prompt": source_meta.get("prompt", ""),
            "composerContent": source_meta.get("composerContent", ""),
            "content": source_meta.get("content", ""),
            "status": source_meta.get("status", ""),
            "params": copy.deepcopy(source_meta.get("params") or {}),
            "reusedAsset": copy.deepcopy(source_meta.get("reusedAsset") or {}),
            "episodeIds": sorted(episode_ids_by_node.get(str(source_node.get("id")), set())),
            "createdAt": source_node.get("createdAt", ""),
            "updatedAt": source_node.get("updatedAt", ""),
        })
    return pool


def _node_source_ready(node):
    if not isinstance(node, dict):
        return False
    meta = node.get("metadata") or {}
    if str(node.get("type") or "") == "text":
        return bool(str(meta.get("prompt") or meta.get("content") or "").strip())
    if str(meta.get("content") or "").strip():
        return True
    params = meta.get("params") if isinstance(meta.get("params"), dict) else {}
    versions = params.get("mediaVersions")
    if not isinstance(versions, list) or not versions:
        versions = params.get("videoVersions")
    if not isinstance(versions, list):
        return False
    try:
        index = int(
            params.get("mediaVersionIndex")
            if isinstance(params.get("mediaVersions"), list)
            and params.get("mediaVersions")
            else params.get("videoVersionIndex")
        )
    except (TypeError, ValueError):
        index = -1
    if 0 <= index < len(versions) and isinstance(versions[index], dict):
        if str(versions[index].get("url") or "").strip():
            return True
    return any(
        isinstance(item, dict) and str(item.get("url") or "").strip()
        for item in reversed(versions)
    )


def node_dependency_blockers(payload, node):
    """Return unresolved image/audio/text dependencies for one generation node."""
    nodes = {
        str(item.get("id")): item
        for item in (payload or {}).get("nodes") or []
        if isinstance(item, dict) and item.get("id")
    }
    meta = (node or {}).get("metadata") or {}
    params = meta.get("params") or {}
    refs = list(params.get("refs") or meta.get("refs") or [])
    voice_refs = list(params.get("voiceRefs") or meta.get("voiceRefs") or [])
    single_voice = params.get("voiceRef") or meta.get("voiceRef")
    if not voice_refs and isinstance(single_voice, dict):
        voice_refs = [single_voice]

    blockers = []
    seen = set()
    bound_ids = set()
    for ref in [*refs, *voice_refs]:
        if not isinstance(ref, dict):
            continue
        ref_id = str(ref.get("nodeId") or "")
        if not ref_id or ref_id in seen:
            continue
        seen.add(ref_id)
        bound_ids.add(ref_id)
        source = nodes.get(ref_id)
        if not source:
            blockers.append({
                "nodeId": ref_id,
                "title": str(ref.get("label") or ref_id),
                "kind": "missing",
                "reason": "引用节点不存在",
            })
            continue
        if _node_source_ready(source):
            continue
        source_meta = source.get("metadata") or {}
        blockers.append({
            "nodeId": ref_id,
            "title": str(source.get("title") or ref_id),
            "kind": str(source.get("type") or ""),
            "status": str(source_meta.get("status") or "idle"),
            "reason": "提示词为空" if source.get("type") == "text" else "尚未生成",
        })

    prompt = str(meta.get("prompt") or "")
    for code in re.findall(
        r"@(?:角色|场景|道具)｜[^（(\n]+[（(](?:参考)?图\s*(\d+)[）)]",
        prompt,
    ):
        code = _canonical_figure(code)
        source = next(
            (
                item for item in nodes.values()
                if str(item.get("type") or "") == "image"
                and _canonical_figure(
                    canvas_feed_contract.canonical_code_from_text(item.get("title"))
                ) == code
            ),
            None,
        )
        if not source:
            blockers.append({
                "nodeId": "",
                "title": code,
                "kind": "image",
                "reason": "提示词引用但节点不存在",
            })
            continue
        source_id = str(source.get("id") or "")
        if source_id not in bound_ids:
            blockers.append({
                "nodeId": source_id,
                "title": str(source.get("title") or code),
                "kind": "image",
                "status": str((source.get("metadata") or {}).get("status") or "idle"),
                "reason": "提示词已引用但未绑定参考素材",
            })
    for role, code in re.findall(
        r"@音频｜([^（(\n；]+)[（(](?:参考)?图\s*(\d+)[）)]",
        prompt,
    ):
        role = role.strip()
        code = _canonical_figure(code)
        source = next(
            (
                item for item in nodes.values()
                if str(item.get("type") or "") == "audio"
                and role in str(item.get("title") or "")
                and canvas_feed_contract.canonical_code_from_text(item.get("title")) == code
            ),
            None,
        )
        if not source:
            blockers.append({
                "nodeId": "",
                "title": f"音频｜{role}（{code}）",
                "kind": "audio",
                "reason": "提示词引用但音色节点不存在",
            })
            continue
        source_id = str(source.get("id") or "")
        if source_id not in bound_ids:
            blockers.append({
                "nodeId": source_id,
                "title": str(source.get("title") or f"音频｜{role}（{code}）"),
                "kind": "audio",
                "status": str((source.get("metadata") or {}).get("status") or "idle"),
                "reason": "提示词已引用但未绑定音色",
            })
    return blockers


def episode_readiness(payload, episode_id=None):
    """Separate structural import success from actual generation readiness."""
    groups = (payload or {}).get("episodeImports") or []
    group = None
    if episode_id:
        group = next(
            (
                item for item in groups
                if str(item.get("id") or "").upper() == str(episode_id).upper()
            ),
            None,
        )
    nodes = {
        str(item.get("id")): item
        for item in (payload or {}).get("nodes") or []
        if isinstance(item, dict) and item.get("id")
    }
    video_ids = [
        str(item)
        for item in ((group or {}).get("videoNodeIds") or [])
        if item
    ]
    if not video_ids:
        video_ids = [
            node_id for node_id, node in nodes.items()
            if str(node.get("type") or "") == "video"
        ]
    videos = [nodes[node_id] for node_id in video_ids if node_id in nodes]
    dependency_ids = set()
    video_blockers = []
    for video in videos:
        meta = video.get("metadata") or {}
        params = meta.get("params") or {}
        all_refs = list(params.get("refs") or meta.get("refs") or [])
        all_voices = list(params.get("voiceRefs") or meta.get("voiceRefs") or [])
        single_voice = params.get("voiceRef") or meta.get("voiceRef")
        if not all_voices and isinstance(single_voice, dict):
            all_voices = [single_voice]
        for ref in [*all_refs, *all_voices]:
            if isinstance(ref, dict) and ref.get("nodeId"):
                dependency_ids.add(str(ref["nodeId"]))
        blockers = node_dependency_blockers(payload, video)
        prompt_empty = not bool(str(meta.get("prompt") or "").strip())
        if blockers or prompt_empty:
            video_blockers.append({
                "nodeId": str(video.get("id") or ""),
                "title": str(video.get("title") or "视频"),
                "missing": blockers,
                "promptEmpty": prompt_empty,
            })
        for blocker in blockers:
            if blocker.get("nodeId"):
                dependency_ids.add(str(blocker["nodeId"]))
    pending_dependencies = []
    for node_id in sorted(dependency_ids):
        source = nodes.get(node_id)
        meta = (source or {}).get("metadata") or {}
        if _node_source_ready(source):
            continue
        pending_dependencies.append({
            "nodeId": node_id,
            "title": str((source or {}).get("title") or node_id),
            "kind": str((source or {}).get("type") or "missing"),
            "status": str(meta.get("status") or ("missing" if not source else "idle")),
            "reason": (
                "引用节点不存在"
                if not source
                else "尚未生成"
            ),
        })
    ready = len(videos) - len(video_blockers)
    return {
        "episodeId": str((group or {}).get("id") or (payload or {}).get("episodeId") or ""),
        "videoTotal": len(videos),
        "videoReady": ready,
        "videoBlocked": len(video_blockers),
        "dependencyTotal": len(dependency_ids),
        "dependencyReady": len(dependency_ids) - len(pending_dependencies),
        "dependencyPending": len(pending_dependencies),
        "pendingDependencies": pending_dependencies,
        "videoBlockers": video_blockers,
        "readyToGenerate": bool(videos) and not video_blockers,
    }


def _seedance_model(value):
    raw = str(value or "").lower().replace("_", "-").replace(" ", "")
    if "2.5" in raw or "2-5" in raw:
        return "doubao-seedance-2-5"
    if "mini" in raw:
        return "doubao-seedance-2-0-mini"
    if "fast" in raw:
        return "doubao-seedance-2-0-fast"
    return "doubao-seedance-2-0"


def _aspect(value, default="16:9"):
    match = re.search(r"(21\s*[:：]\s*9|16\s*[:：]\s*9|9\s*[:：]\s*16|4\s*[:：]\s*3|3\s*[:：]\s*4|1\s*[:：]\s*1)", str(value or ""))
    return match.group(1).replace("：", ":").replace(" ", "") if match else default


def _image_size(ratio):
    return {
        "16:9": "1536x864",
        "9:16": "864x1536",
        "4:3": "1536x1152",
        "3:4": "1152x1536",
        "1:1": "1536x1536",
        "21:9": "1536x658",
    }.get(ratio, "1536x864")


def _short_asset_name(name):
    return re.sub(
        r"[（(](?:角色立绘|场景母版|九宫格|道具设定图|群像设定图|群像资产卡)[^）)]*[）)]\s*$",
        "",
        str(name or "").strip(),
    ).strip()


def _canvas_asset_name(name, canvas_name, kind):
    """Keep scene variants distinct when the canvas contract supplies a base name."""
    base = str(canvas_name or "").strip()
    if not base:
        return ""
    source = str(name or "")
    if kind == "场景":
        if "九宫格" in source and "九宫格" not in base:
            return f"{base}·九宫格"
        if "母版" in source and "母版" not in base:
            return f"{base}·母版"
    return base


def _mention_asset_base(label):
    text = re.sub(r"^[^｜|]*[｜|]\s*", "", str(label or "").strip())
    text = re.sub(r"[（(]\s*图\s*\d+\s*[)）]\s*$", "", text)
    text = re.sub(r"[·・].*$", "", text)
    return re.sub(r"\s+", "", text).casefold()


def _resolve_mention_ref(kind, name, tail, refs):
    prefix = _MENTION_KIND_PREFIX.get(kind, "")
    candidates = [
        str(item.get("label") or "")
        for item in refs or []
        if isinstance(item, dict)
        and re.match(rf"^{re.escape(prefix)}[｜|]", str(item.get("label") or ""))
    ]
    if not candidates:
        return ""

    figure = re.search(r"图\s*(\d+)", str(tail or ""))
    if figure:
        code = _canonical_figure(figure.group(1))
        by_code = [label for label in candidates if code in label]
        if by_code:
            if "九宫格" in str(tail):
                nine = next((label for label in by_code if "九宫格" in label), "")
                if nine:
                    return nine
            if "母版" in str(tail):
                master = next((label for label in by_code if "母版" in label), "")
                if master:
                    return master
            return by_code[0]

    target = _mention_asset_base(name)
    exact = [label for label in candidates if _mention_asset_base(label) == target]
    if exact:
        return exact[0]
    partial = [
        label for label in candidates
        if target
        and (
            _mention_asset_base(label).startswith(target)
            or target.startswith(_mention_asset_base(label))
        )
    ]
    return partial[0] if partial else ""


def _mention_tail_without_code(tail):
    raw = str(tail or "")
    if not raw:
        return ""
    inside = raw[1:-1]
    match = re.match(r"\s*图\s*\d+\s*(?:[,，、]\s*)?(.*)", inside)
    if not match:
        return raw
    rest = match.group(1).strip()
    return f"（{rest}）" if rest else ""


def _resolve_figure_ref(name, figure, refs):
    code = _canonical_figure(figure)
    candidates = [
        str(item.get("label") or "")
        for item in refs or []
        if isinstance(item, dict)
        and canvas_feed_contract.canonical_code_from_text(item.get("label")) == code
    ]
    if not candidates:
        return "", ""
    target = _mention_asset_base(name)
    exact = [label for label in candidates if _mention_asset_base(label) == target]
    if exact:
        return exact[0], ""
    if len(candidates) == 1:
        label = candidates[0]
        base = _mention_asset_base(label)
        if target and len(base) >= 2 and base.endswith(target):
            return label, ""
    for label in sorted(candidates, key=lambda item: len(_mention_asset_base(item)), reverse=True):
        base = _mention_asset_base(label)
        if len(base) < 2 or not target.endswith(base):
            continue
        raw_name = str(name)
        prefix = raw_name[:max(0, len(raw_name) - len(base))]
        return label, prefix
    return "", ""


def repair_prompt_mentions(prompt, refs):
    """把技能载荷占位符换成输入框可识别、能渲染缩略图的 @资产节点名。"""

    def replace(match):
        title = _resolve_mention_ref(
            match.group("kind"),
            match.group("name"),
            match.group("tail"),
            refs,
        )
        if not title:
            return match.group(0)
        return f"@{title}{_mention_tail_without_code(match.group('tail'))}"

    repaired = _RAW_MENTION_RE.sub(replace, str(prompt or ""))
    repaired = re.sub(r"@//(?:chr|scn|prp)\s*/\s*-\s*", "", repaired)

    # Resolve later-episode figure aliases and inline names even when they are
    # embedded in Chinese prose such as "抽出账本（图18）".
    aliases = []
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        title = str(ref.get("label") or "").strip()
        if not title or "｜" not in title:
            continue
        base = _asset_title_base(title).split("·", 1)[0]
        if len(base) >= 2:
            aliases.append((base, title))
    for base, title in sorted(aliases, key=lambda item: len(item[0]), reverse=True):
        canonical_figure = _canonical_figure(
            canvas_feed_contract.canonical_code_from_text(title)
        )
        alias_codes = {
            _canonical_figure(code)
            for code in re.findall(
                rf"{re.escape(base)}\s*[（(]\s*图\s*(\d+)",
                repaired,
            )
        }
        figure_mention = rf"{re.escape(base)}\s*[（(]\s*图\s*\d+\s*[）)]"
        repaired = re.sub(
            rf"(?<![@｜]){figure_mention}",
            f"@{title}",
            repaired,
        )
        repaired = re.sub(
            rf"@{figure_mention}",
            f"@{title}",
            repaired,
        )
        repaired = re.sub(
            rf"@{re.escape(base)}(?!｜)",
            f"@{title}",
            repaired,
        )
        if canonical_figure:
            for alias_code in alias_codes:
                if alias_code == canonical_figure:
                    continue
                alias_digits = alias_code[1:] if alias_code.startswith("图") else alias_code
                repaired = re.sub(
                    rf"图\s*{re.escape(alias_digits)}(?!\d)",
                    canonical_figure,
                    repaired,
                )

    # A unique figure code is stronger than an imprecise display alias:
    # e.g. "纸条（图32）" must resolve to 道具｜匿名纸条（图32）.
    refs_by_code = {}
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        code = canvas_feed_contract.canonical_code_from_text(ref.get("label"))
        if code:
            refs_by_code.setdefault(code, []).append(
                str(ref.get("label") or "").strip()
            )
    code_pattern = re.compile(
        r"(?P<at>@?)(?P<name>[\u4e00-\u9fffA-Za-z0-9·・]{1,32})"
        r"\s*[（(]\s*图\s*(?P<code>\d+)"
        r"(?:\s*[，,、·・]\s*(?P<tail>[^）)]*))?\s*[）)]"
    )

    def replace_unique_code(match):
        if match.start() > 0 and repaired[match.start() - 1] == "｜":
            return match.group(0)
        titles = refs_by_code.get(_canonical_figure(match.group("code"))) or []
        if len(titles) != 1:
            return match.group(0)
        title = titles[0]
        base = _mention_asset_base(title)
        raw_name = match.group("name")
        aliases_found = [
            base[index:]
            for index in range(len(base))
            if len(base[index:]) >= 2 and raw_name.endswith(base[index:])
        ]
        alias = max(aliases_found, key=len) if aliases_found else ""
        if not alias:
            return match.group(0)
        prefix = raw_name[:-len(alias)]
        tail = str(match.group("tail") or "").strip()
        return f"{prefix}@{title}" + (f"（{tail}）" if tail else "")

    repaired = code_pattern.sub(replace_unique_code, repaired)
    repaired = repaired.replace("（母版+九宫格双挂）", "（母版）")
    repaired = repaired.replace("母版+九宫格双挂", "母版")
    repaired = re.sub(r"（图\s*(\d+)）（图\s*\1）", r"（图\1）", repaired)

    def replace_plain(match):
        if match.start() > 0 and repaired[match.start() - 1].isalnum():
            return match.group(0)
        prefix_context = repaired[max(0, match.start() - 80):match.start()]
        if re.search(r"@(?:角色|场景|道具|音频)[｜|][^（(\n]*$", prefix_context):
            return match.group(0)
        title, prefix = _resolve_figure_ref(match.group("name"), match.group("figure"), refs)
        return f"{prefix}@{title}" if title else match.group(0)

    repaired = _PLAIN_FIGURE_MENTION_RE.sub(replace_plain, repaired)
    repaired = re.sub(r"\s*[（(]\s*model\s*=\s*[^）)]*[）)]", "", repaired, flags=re.IGNORECASE)
    return repaired


def _ensure_audio_mentions(prompt, voices):
    """Keep concrete voice nodes visible in the prompt, not only in voiceRefs."""
    text = str(prompt or "")
    existing = re.compile(r"(?m)^【音色引用】[^\n]*(?:\n|$)")
    text = existing.sub("", text, count=1)
    text = re.sub(r"\n{3,}", "\n\n", text)
    labels = []
    seen = set()
    for voice in voices or []:
        if not isinstance(voice, dict):
            continue
        label = str(voice.get("label") or "").strip()
        if not label.startswith("音频｜") or label in seen:
            continue
        seen.add(label)
        labels.append(f"@{label}")
    if not labels:
        return text
    section = "【音色引用】" + "；".join(labels)
    core = re.search(r"(?m)^【核心人物】[^\n]*(?:\n|$)", text)
    if core:
        return text[:core.end()] + "\n" + section + "\n" + text[core.end():]
    for marker in ("\n\n【音效】", "\n\n【强制禁止项】"):
        if marker in text:
            return text.replace(marker, "\n\n" + section + marker, 1)
    return text.rstrip() + "\n\n" + section + "\n"


_ASSET_PLACEHOLDER_MARKERS = (
    "请根据角色补充音色描述后再生成",
    "音色占位",
    "当前投喂载荷未提供资产提示词",
    "已自动补占位卡",
)


def _is_placeholder_asset_prompt(prompt):
    text = str(prompt or "").strip()
    return not text or any(marker in text for marker in _ASSET_PLACEHOLDER_MARKERS)


def _restore_generated_asset_prompt(meta, node_type, title=""):
    """Recover a generated asset prompt that a placeholder import overwrote."""
    if not isinstance(meta, dict) or node_type not in {"image", "audio"}:
        return False
    if not str(meta.get("content") or "").strip():
        return False
    prompt = str(meta.get("prompt") or "").strip()
    if not _is_placeholder_asset_prompt(prompt):
        return False
    params = meta.get("params") if isinstance(meta.get("params"), dict) else {}
    fallback = (
        str(params.get("voicePrompt") or "").strip()
        if node_type == "audio"
        else ""
    )
    if not fallback or _is_placeholder_asset_prompt(fallback):
        return False
    meta["prompt"] = fallback
    if node_type == "audio":
        role = _asset_title_base(title, audio=True).split("·", 1)[0].strip()
        meta["composerContent"] = (
            f"角色：{role}\n\n【音色自动提示词 · Audio Prompt · 4.5S 参考音色】\n"
            f"{fallback}"
        )
    else:
        meta["composerContent"] = fallback
    meta["autoPlaceholder"] = False
    if str(meta.get("promptStaleReason") or "").startswith("源音色提示词已更新"):
        meta["promptStale"] = False
        meta["promptStaleReason"] = ""
        meta.pop("sourcePromptUpdatedAt", None)
    return True


def repair_reused_asset_prompts(payload):
    changed = 0
    for node in payload.get("nodes") or []:
        if not isinstance(node, dict) or node.get("type") not in {"image", "audio"}:
            continue
        meta = node.setdefault("metadata", {})
        if _restore_generated_asset_prompt(meta, node.get("type"), node.get("title")):
            changed += 1
    return changed


def detach_cross_project_reused_assets(payload, project_id):
    """Make legacy cross-project image links independent without deleting files."""
    project_id = str(project_id or "").strip()
    if not project_id:
        return 0
    changed = 0
    for node in payload.get("nodes") or []:
        if not isinstance(node, dict) or node.get("type") != "image":
            continue
        meta = node.setdefault("metadata", {})
        reused = meta.get("reusedAsset")
        if not isinstance(reused, dict):
            continue
        source_project_id = str(reused.get("sourceProjectId") or "").strip()
        if not source_project_id or source_project_id == project_id:
            continue
        meta["content"] = ""
        meta["status"] = "idle"
        meta.pop("reusedAsset", None)
        meta["promptStale"] = False
        meta["promptStaleReason"] = ""
        meta.pop("sourcePromptUpdatedAt", None)
        for key in (
            "generatedAt",
            "generationStartedAt",
            "generationFinishedAt",
            "providerTaskStatus",
            "providerTaskPhase",
            "providerTaskProgress",
            "taskId",
            "lastError",
        ):
            meta.pop(key, None)
        params = meta.get("params") if isinstance(meta.get("params"), dict) else {}
        params.pop("mediaVersions", None)
        params.pop("mediaVersionIndex", None)
        params.pop("thumbUrl", None)
        changed += 1
    return changed


def repair_payload_prompt_mentions(payload):
    """幂等修复已导入视频节点的提示词；返回发生变更的节点数。"""
    changed = 0
    nodes = [
        node for node in payload.get("nodes", []) or []
        if isinstance(node, dict)
    ]
    node_by_id = {
        str(node.get("id") or ""): node
        for node in nodes
        if node.get("id")
    }
    role_names = {
        _asset_title_base(node.get("title")).split("·", 1)[0]
        for node in nodes
        if node.get("type") == "image"
        and str(node.get("title") or "").startswith("角色｜")
    }
    allowed_audio_by_video = {}

    def voice_role(ref):
        source = node_by_id.get(str(ref.get("nodeId") or ""))
        title = (
            source.get("title")
            if source and source.get("type") == "audio"
            else ref.get("label")
        )
        return _asset_title_base(title, audio=True).split("·", 1)[0].strip()

    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "video":
            continue
        meta = node.get("metadata") or {}
        params = meta.get("params") or {}
        refs = list(params.get("refs") or meta.get("refs") or [])
        voices = list(params.get("voiceRefs") or meta.get("voiceRefs") or [])
        prompt = str(meta.get("prompt") or "")
        composer = str(meta.get("composerContent") or "")
        speaking = _prompt_speaking_roles(f"{prompt}\n{composer}", role_names)
        filtered_voices = [
            voice for voice in voices
            if isinstance(voice, dict) and voice_role(voice) in speaking
        ]
        filtered_refs = [
            ref for ref in refs
            if not (
                isinstance(ref, dict)
                and (
                    (node_by_id.get(str(ref.get("nodeId") or "")) or {}).get("type") == "audio"
                    or str(ref.get("label") or "").startswith("音频｜")
                )
                and voice_role(ref) not in speaking
            )
        ]
        allowed_audio_by_video[str(node.get("id") or "")] = {
            str(voice.get("nodeId") or "")
            for voice in filtered_voices
            if voice.get("nodeId")
        }
        node_changed = False
        if filtered_voices != voices:
            params["voiceRefs"] = filtered_voices
            params["voiceRef"] = filtered_voices[0] if filtered_voices else None
            meta["voiceRefs"] = filtered_voices
            node_changed = True
        if filtered_refs != refs:
            params["refs"] = filtered_refs
            meta["refs"] = filtered_refs
            node_changed = True
        for key in ("prompt", "composerContent"):
            value = str(meta.get(key) or "")
            if not value:
                continue
            asset_ref_labels = [
                str(ref.get("label") or "").strip()
                for ref in filtered_refs
                if isinstance(ref, dict)
                and (
                    (node_by_id.get(str(ref.get("nodeId") or "")) or {}).get("type")
                    in {"image", "audio"}
                    or str(ref.get("label") or "").startswith(
                        ("角色｜", "群像｜", "场景｜", "道具｜", "音频｜")
                    )
                )
                and str(ref.get("label") or "").strip()
            ]
            already_canonical = bool(asset_ref_labels) and all(
                f"@{label}" in value
                for label in asset_ref_labels
            )
            repaired = (
                value
                if already_canonical
                else _ensure_audio_mentions(
                    repair_prompt_mentions(value, filtered_refs),
                    filtered_voices,
                )
            )
            if repaired != value:
                meta[key] = repaired
                node_changed = True
        if node_changed:
            meta["params"] = params
            changed += 1
    connections = payload.get("connections") or []
    kept_connections = []
    for connection in connections:
        if not isinstance(connection, dict):
            kept_connections.append(connection)
            continue
        target_id = str(connection.get("toNodeId") or "")
        source = node_by_id.get(str(connection.get("fromNodeId") or ""))
        if (
            target_id in allowed_audio_by_video
            and source
            and source.get("type") == "audio"
            and str(source.get("id") or "") not in allowed_audio_by_video[target_id]
        ):
            changed += 1
            continue
        kept_connections.append(connection)
    if len(kept_connections) != len(connections):
        payload["connections"] = kept_connections
    return changed


def _segment_duration(segment, max_seconds):
    ends = []
    for shot in segment.get("shots") or []:
        try:
            ends.append(float(shot.get("end")))
        except (TypeError, ValueError):
            continue
    value = math.ceil(max(ends, default=4))
    return max(4, min(max_seconds, value))


def _episode_identity(feed):
    meta = feed.get("meta") or {}
    episode = feed.get("episode") if isinstance(feed.get("episode"), dict) else {}
    title = str(
        episode.get("title")
        or feed.get("episode_title")
        or meta.get("episode_title")
        or feed.get("title")
        or meta.get("title")
        or "第01集"
    ).strip()
    raw_id = str(
        episode.get("id")
        or feed.get("episode_id")
        or meta.get("episode_id")
        or meta.get("ep")
        or ""
    ).strip()
    match = re.search(r"第\s*(\d+)\s*集", title)
    if not raw_id and match:
        raw_id = f"EP{int(match.group(1)):03d}"
    if not raw_id:
        raw_id = "EP001"
    normalized = re.sub(r"[^0-9A-Za-z_-]+", "", raw_id)
    if normalized.isdigit():
        normalized = f"EP{int(normalized):03d}"
    elif normalized.upper().startswith("EP"):
        normalized = "EP" + re.sub(r"\D", "", normalized).zfill(3)
    else:
        normalized = normalized or "EP001"
    return normalized.upper(), title


def _extract_voice(raw):
    """从角色资产 raw 提取 Voice Description 完整版（剥标题/尾部标记）。"""
    vstart = raw.find("Voice Description 完整版")
    vend = raw.find("Voice Description Label", vstart)
    voice = ""
    if vstart >= 0 and vend > vstart:
        voice = raw[vstart:vend].strip()
        voice = re.sub(r"^Voice Description 完整版[^\n]*?\*\*\s*", "", voice).strip()
        voice = re.sub(r"\s*/\s*\*\*\s*②?\s*$", "", voice).strip()
        voice = re.sub(r"\*\*\s*②\s*$", "", voice).strip()
        voice = voice.lstrip(":： ").strip()
    if not voice:
        m = re.search(r"\*\*\s*①\s*Voice Description 完整版[^*]*\*\*[^\n]*?\*\*\s*([\s\S]*?)\s*\*\*\s*②\s*Voice Description Label", raw)
        if m:
            voice = m.group(1).strip()
    if not voice:
        seg = raw[raw.find("Voice Description 完整版"):]
        seg = seg[:seg.find("Voice Description Label")] if "Voice Description Label" in seg else seg[:400]
        voice = seg.replace("**", "").strip()
    return voice


_AUDIO_PROMPT_SUFFIX = canvas_feed_contract.AUDIO_PROMPT_SUFFIX


def _compose_audio_prompt(label, description, gender, age, dialect):
    normalized_age = re.sub(r"\s*[~～—–-]\s*", "-", str(age or ""))
    if gender and normalized_age:
        identity = f"A {gender} aged {normalized_age}"
    else:
        identity = f"A {gender}" if gender else ""
    prefix = " ".join(
        part for part in (
            f"{label}." if label else "",
            str(description or "").strip(),
        ) if part
    ).strip()
    tail = ", ".join(part for part in (
        identity,
        (
            f"speaking {dialect or 'standard'} Mandarin, short drama dubbing reference clip, "
            "4.5 seconds clean voice, no background noise"
        ),
    ) if part) + "."
    return f"{prefix} {tail}".strip()


def _audio_prompt_is_complete(prompt):
    return str(prompt or "").strip().casefold().endswith(_AUDIO_PROMPT_SUFFIX.casefold())


def _normalize_voice_catalog_item(voice):
    if not isinstance(voice, dict):
        return None
    prompt = str(voice.get("prompt") or voice.get("voicePrompt") or "").strip()
    description = str(
        voice.get("description")
        or voice.get("voiceDescription")
        or ""
    ).strip()
    label = str(voice.get("label") or voice.get("voiceLabel") or "").strip()
    age = str(voice.get("age") or voice.get("voiceAge") or "").strip()
    gender = str(voice.get("gender") or voice.get("voiceGender") or "").strip().lower()
    dialect = str(voice.get("dialect") or voice.get("voiceDialect") or "").strip()
    if not age:
        age_match = re.search(r"(\d+\s*[~～-]\s*\d+|\d+)\s*岁", description + "\n" + prompt)
        age = re.sub(r"\s+", "", age_match.group(1)) if age_match else ""
    if not gender:
        gender = (
            "female"
            if re.search(r"女性|\bfemale voice\b", description, re.IGNORECASE)
            else ("male" if re.search(r"男性|\bmale voice\b", description, re.IGNORECASE) else "")
        )
    if not dialect:
        dialect = "northeastern-accented" if "northeastern" in description.lower() else "standard"
    normalized_age = re.sub(r"\s*[~～—–-]\s*", "-", age)
    identity_ok = (
        not (gender and normalized_age)
        or bool(re.search(
            rf"\bA {re.escape(gender)} aged {re.escape(normalized_age)}\b",
            prompt,
            re.IGNORECASE,
        ))
    )
    if description and (not _audio_prompt_is_complete(prompt) or not identity_ok):
        prompt = _compose_audio_prompt(label, description, gender, age, dialect)
    if not prompt or _is_placeholder_asset_prompt(prompt) or not _audio_prompt_is_complete(prompt):
        return None
    code = str(voice.get("code") or "").strip()
    role = str(
        voice.get("name")
        or _asset_title_base(voice.get("title"), audio=True).split("·", 1)[0]
        or ""
    ).strip()
    return {
        **voice,
        "code": code,
        "name": role,
        "prompt": prompt,
        "description": description,
        "label": label,
        "age": normalized_age,
        "gender": gender,
        "dialect": dialect,
    }


def build_payload(feed, asset_pool=None, target_project_id=""):
    """投喂载荷 → 画布 payload dict。"""
    assets = [
        {
            **asset,
            "code": _canonical_figure(asset.get("code")),
        }
        for asset in feed.get("asset_prompts", [])
        if isinstance(asset, dict)
    ]      # 9 项（图6 ×2）
    voice_prompts = [
        {
            **voice,
            "code": _canonical_figure(voice.get("code")),
        }
        for voice in feed.get("voice_prompts", [])
        if isinstance(voice, dict)
    ]
    normalized_voices = [
        item
        for voice in voice_prompts
        for item in [_normalize_voice_catalog_item(voice)]
        if item
    ]
    voice_by_code = {item["code"]: item for item in normalized_voices if item.get("code")}
    voice_by_role = {item["name"]: item for item in normalized_voices if item.get("name")}
    voice_figures_by_role = {
        item["name"]: _canonical_figure(item.get("code"))
        for item in normalized_voices
        if item.get("name") and _canonical_figure(item.get("code"))
    }
    segs = feed.get("segments", [])              # N 段
    meta = feed.get("meta", {}) or {}
    p0a = feed.get("p0a") or {}
    if isinstance(p0a, list):
        p0a = (p0a[0] if p0a and isinstance(p0a[0], dict) else {})
    script = feed.get("script", "")
    contract = feed.get("canvas_contract") if isinstance(feed.get("canvas_contract"), dict) else {}
    episode_id, episode_title = _episode_identity(feed)
    reusable_assets = [
        item
        for item in _asset_pool(asset_pool, target_project_id)
        if target_project_id and item.get("projectId") == target_project_id
    ]
    master_scene_codes = {
        str(asset.get("code") or "")
        for asset in assets
        if (
            "母版" in str(asset.get("name") or "")
            or "场景母版" in str(asset.get("name") or "")
        )
    }

    aspect = _aspect(meta.get("画幅") or p0a.get("画幅") or contract.get("video_aspect"), "9:16")
    asset_aspect = _aspect(contract.get("asset_aspect"), "16:9")
    video_model = _seedance_model(contract.get("video_model") or meta.get("模型版本") or meta.get("model"))
    vbase = meta.get("visual_base") or meta.get("影像基底") or p0a.get("影像基底") or "真人实拍质感"
    vtone = meta.get("visual_tone") or meta.get("视觉基调") or "2006 年东北铁西旧工业区，冷灰蓝与铁锈红，自然硬光，轻手持纪实感，克制表演，细颗粒。"

    pid = "".join(secrets.choice(_B62) for _ in range(22))
    # Project identity belongs to the episode, not to whichever node happened
    # to occupy the feed's top-level title field.
    title = episode_title or feed.get("title") or meta.get("title") or "画布工作流"
    now0 = _now_iso()
    nodes, conns = [], []

    # ---------- 1) text 头卡 ----------
    head_lines = ["素材形态：M1（编号占位）", "模型：Seedance 2.0（方舟统一）",
                  f"成片画幅：{aspect}", f"影像基底：{vbase}", f"视觉基调：{vtone}",
                  "交付边界：只显示画布、提示词与连线；不生成图片，不生成视频，不提交付费任务。", ""]
    if script:
        head_lines.append("【剧本原文】")
        head_lines.append(str(script)[:1600])
    head_text = "\n".join(head_lines)
    text_id = _rid_node()
    nodes.append({
        "height": 240, "width": 340, "id": text_id,
        "metadata": {
            "content": head_text, "fontSize": 14, "prompt": head_text, "status": "idle",
            "episodeId": episode_id,
            "params": {
                "provider": "teamorouter", "model": "deepseek-flash-1017",
                "maxTokens": 3000, "temperature": 0.7,
            },
        },
        "position": {"x": 0, "y": 0}, "title": f"{title} · 剧本头卡", "type": "text",
        "createdAt": now0, "updatedAt": now0,
    })

    # ---------- 2) image 节点（按类型分区） ----------
    image_ids = {}          # 图N -> [node ids]
    asset_kinds = {}        # node id -> 类型
    col_x = {
        "角色": COL_ROLE,
        "群像": COL_CROWD,
        "场景": COL_SCENE,
        "道具": COL_PROP,
    }
    col_y = {k: 0 for k in col_x}
    reused_asset_keys = set()
    reused_generated_asset_keys = set()
    for a in assets:
        code = a.get("code", "")
        name = a.get("name", "")
        if (
            code in master_scene_codes
            and "九宫格" in str(name)
        ):
            continue
        raw = a.get("raw", "").strip()
        canvas_asset = a.get("canvas") if isinstance(a.get("canvas"), dict) else {}
        canvas_kind = str(canvas_asset.get("kind") or "")
        if canvas_kind in {"character", "scene", "prop", "crowd"}:
            kind = {"character": "角色", "scene": "场景", "prop": "道具", "crowd": "群像"}[canvas_kind]
            short_name = _canvas_asset_name(
                name,
                canvas_asset.get("name"),
                kind,
            ) or _short_asset_name(name)
        elif "群像资产卡" in name or "群像设定" in name or "群像" in name:
            # 第四族资产（GRP-N）：必须排在「角色立绘」之前判——群像卡名里常带
            # 「群像资产卡 GRP-01」，落到 else 会被当成道具，身份键变 `道具:街坊群`，
            # 与角色/场景复用池对不上，跨集复用直接失效。
            kind = "群像"
            short_name = _short_asset_name(name)
        elif "角色立绘" in name:
            kind, short_name = "角色", name.replace("（角色立绘）", "")
        elif "场景" in name or "母版" in name or "九宫格" in name:
            kind = "场景"
            short_name = (name.replace("（九宫格）", "") + "·九宫格") if "九宫格" in name else (name.replace("（场景母版）", "") + "·母版")
        else:
            kind, short_name = "道具", name.replace("（道具设定图）", "")
        asset_key = _asset_identity(short_name, kind)
        same_project = [
            item for item in reusable_assets
            if item.get("assetKey") == asset_key and item.get("projectId") == target_project_id
        ]
        candidates = same_project
        candidates.sort(
            key=lambda item: (
                bool(str(item.get("content") or "").strip()),
                str(item.get("status") or "") == "done",
                str(item.get("updatedAt") or ""),
            ),
            reverse=True,
        )
        source_asset = candidates[0] if candidates else None
        same_project_reuse = bool(
            source_asset
            and source_asset.get("projectId") == target_project_id
        )
        same_episode_asset = bool(
            same_project_reuse
            and str(episode_id).upper() in {
                str(item).upper()
                for item in source_asset.get("episodeIds") or []
            }
        )
        cross_episode_reuse = bool(source_asset and not same_episode_asset)
        if cross_episode_reuse:
            reused_asset_keys.add(asset_key)
        nid = str(source_asset["id"]) if (source_asset and source_asset.get("projectId") == target_project_id) else _rid_node()
        image_ids.setdefault(code, []).append(nid)
        asset_kinds[nid] = kind
        # All role/scene/prop/staging cards share one canvas card ratio. The
        # provider-side ratio stays 16:9 as well so generated previews match.
        visible_ratio = _aspect(canvas_asset.get("ratio"), "16:9")
        visible_clarity = str(canvas_asset.get("clarity") or "1.5K")
        visible_size = str(canvas_asset.get("size") or _image_size(visible_ratio))
        image_model = str(canvas_asset.get("model") or "doubao-seedream-5-0-pro")
        generated_composer = f"### {code} · {name}\n\n{raw}"
        source_reuse_meta = (
            source_asset.get("reusedAsset") or {}
            if same_project_reuse
            else {}
        )
        previous_composer = (
            str(source_reuse_meta.get("sourceComposerContent") or "").strip()
            or (
                str(source_asset.get("composerContent") or "").strip()
                if same_project_reuse
                else ""
            )
        )
        previous_prompt = (
            str(source_reuse_meta.get("sourcePrompt") or "").strip()
            or (
                str(source_asset.get("prompt") or "").strip()
                if same_project_reuse
                else ""
            )
        )
        composer = generated_composer
        image_prompt = generated_composer
        canonical_title = ""
        if same_project_reuse:
            canonical_title = str(
                ((source_asset.get("reusedAsset") or {}).get("sourceTitle"))
                or source_asset.get("title")
                or ""
            ).strip()
        node_title = canonical_title or f"{kind}｜{short_name}（{code}）"
        reused_content = str(source_asset.get("content") or "") if source_asset else ""
        if cross_episode_reuse and reused_content:
            reused_generated_asset_keys.add(asset_key)
        prompt_stale = bool(
            reused_content
            and previous_prompt
            and previous_prompt.strip() != image_prompt.strip()
        )
        image_params = {
            "provider": str(canvas_asset.get("provider") or "ark"), "model": image_model,
            "ratio": visible_ratio, "clarity": visible_clarity, "size": visible_size,
            "guidance": str(canvas_asset.get("guidance") or "中等"),
            "watermark": bool(canvas_asset.get("watermark", False)),
        }
        if isinstance(canvas_asset.get("params"), dict):
            image_params.update(canvas_asset["params"])
        if source_asset and isinstance(source_asset.get("params"), dict):
            image_params = {**image_params, **source_asset["params"]}
        reuse_meta = None
        if source_asset and cross_episode_reuse:
            reuse_meta = dict(source_asset.get("reusedAsset") or {})
            reuse_meta.update({
                "assetKey": asset_key,
                "sourceProjectId": source_asset.get("projectId", ""),
                "sourceNodeId": source_asset.get("id", ""),
                "sourceTitle": canonical_title or source_asset.get("title", ""),
                "content": reused_content,
                "sourcePrompt": previous_prompt,
                "sourceComposerContent": previous_composer,
            })
        elif same_episode_asset and source_asset.get("reusedAsset"):
            reuse_meta = dict(source_asset.get("reusedAsset") or {})
        nodes.append({
            "height": 405, "width": 720, "id": nid,
            "metadata": {
                "composerContent": composer, "content": reused_content, "count": 1,
                "model": image_model, "prompt": image_prompt, "quality": "high",
                "size": visible_size, "status": "done" if reused_content else "idle",
                "transparentBackground": "false", "nodeRole": "generator",
                "assetKey": asset_key, "reusedAsset": reuse_meta,
                "assetId": str(canvas_asset.get("assetId") or ""),
                "seriesId": str(canvas_asset.get("seriesId") or ""),
                "variantKey": str(canvas_asset.get("variantKey") or "default"),
                "promptStale": prompt_stale,
                "promptStaleReason": "源提示词已更新，当前复用图片仍是旧版本" if prompt_stale else "",
                "sourcePromptUpdatedAt": now0 if prompt_stale else "",
                "params": image_params,
            },
            "position": {"x": col_x[kind], "y": col_y[kind]},
            "title": node_title, "type": "image",
            "createdAt": source_asset.get("createdAt", now0) if source_asset else now0,
            "updatedAt": now0,
        })
        col_y[kind] += 560

    # ---------- 3) video 节点（视频列） ----------
    video_ids = {}
    vid_y = 0
    for s in segs:
        no = s.get("no", "")
        name = s.get("name", "")
        raw = s.get("raw", "").strip()
        canvas_segment = s.get("canvas") if isinstance(s.get("canvas"), dict) else {}
        nid = _rid_node()
        video_ids[no] = nid
        segment_model = _seedance_model(canvas_segment.get("model") or video_model)
        model_limit = 30 if segment_model == "doubao-seedance-2-5" else 15
        try:
            duration = int(canvas_segment.get("duration"))
        except (TypeError, ValueError):
            duration = _segment_duration(s, model_limit)
        duration = max(4, min(model_limit, duration))
        vsize = _aspect(canvas_segment.get("ratio") or raw, aspect)
        nodes.append({
            "height": 746.7, "width": 420, "id": nid,
            "metadata": {
                "composerContent": raw, "content": "", "generateAudio": "true",
                "episodeId": episode_id, "segmentNo": no,
                "model": segment_model, "prompt": raw,
                "seconds": str(duration), "size": vsize,
                "status": "idle", "vquality": "480p", "nodeRole": "generator",
                "params": {
                    "provider": "ark", "model": segment_model,
                    "ratio": vsize, "resolution": "480P", "duration": duration,
                    "count": 1, "guidance": "中等", "generateAudio": True, "watermark": False,
                },
            },
            "position": {"x": COL_VIDEO, "y": vid_y},
            "title": name, "type": "video",
            "createdAt": now0, "updatedAt": now0,
        })
        vid_y += 820

    # ---------- 3.5) audio 节点（只有有对白的角色才生成音色） ----------
    speaking = set()
    for _s in segs:
        speaking |= _dialog_speaker_names(_s.get("raw", ""))
    character_names = {}
    for asset in assets:
        if not isinstance(asset, dict) or "角色立绘" not in str(asset.get("name") or ""):
            continue
        canvas = asset.get("canvas") if isinstance(asset.get("canvas"), dict) else {}
        role = str(canvas.get("name") or _short_asset_name(asset.get("name", ""))).strip()
        if role:
            character_names[asset.get("code", "")] = role
    audio_ids, audio_by_role = {}, {}
    audio_y = 0
    for a in assets:
        code, name, raw = a.get("code", ""), a.get("name", ""), a.get("raw", "")
        role = character_names.get(code, "")
        if not role or "角色立绘" not in name:
            continue
        if role not in speaking:
            continue
        catalog_voice = voice_by_code.get(_canonical_figure(code)) or voice_by_role.get(role)
        voice = _extract_voice(raw)
        lm = re.search(r"Voice Description Label[^\n]*?(?::|\*\*)\s*([A-Za-z][^/\n]*)", raw)
        label = lm.group(1).strip() if lm else ""
        gm = re.search(r"一名\s*(\d+~?\d*)\s*岁\s*(男性|女性)", raw)
        age = gm.group(1) if gm else ""
        gender = "male" if (gm and gm.group(2) == "男性") else ("female" if (gm and gm.group(2) == "女性") else "")
        dialect = "northeastern-accented" if "northeastern" in voice.lower() else "standard"
        audio_prompt = (
            str(catalog_voice.get("prompt") or "").strip()
            if catalog_voice
            else _compose_audio_prompt(label, voice, gender, age, dialect)
        )
        code = voice_figures_by_role.get(role) or code
        aid = _rid_audio()
        audio_ids[code], audio_by_role[role] = aid, aid
        audio_composer = f"角色：{role}（{code}）\n\n【音色自动提示词 · Audio Prompt · 4.5S 参考音色】\n{audio_prompt}"
        nodes.append({
            "height": 320, "width": 320, "id": aid,
            "metadata": {
                "content": "", "composerContent": audio_composer,
                "assetKey": _asset_identity(role, "音色"),
                "prompt": audio_prompt, "status": "idle", "generationMode": "audio", "nodeRole": "generator",
                "promptStale": False, "promptStaleReason": "",
                "params": {
                    "provider": "aliyun_bailian", "model": "cosyvoice-v3-5-flash",
                    "ttsModel": "cosyvoice-v3.5-flash", "ttsMode": "design",
                    "outputFormat": "mp3", "sampleRate": 24000,
                    "previewDuration": 4.5, "voicePrompt": audio_prompt,
                },
            },
            "position": {"x": COL_AUDIO, "y": audio_y},
            "title": f"音频｜{role}（{code}）", "type": "audio",
            "createdAt": now0, "updatedAt": now0,
        })
        audio_y += 400

    # Before wiring references, synthesize cards for @资产 mentions that the
    # current feed omitted. Without these cards the connection phase has no
    # target node and the canvas appears to have no automatic links.
    _ensure_referenced_assets({
        "nodes": nodes,
        "episodeId": episode_id,
        "episodeImports": [{
            "id": episode_id,
            "nodeIds": [node["id"] for node in nodes],
            "videoNodeIds": [node["id"] for node in nodes if node.get("type") == "video"],
        }],
    }, episode_id, assets, voice_prompts)
    # The synthesis helper appends cards after the initial asset loop, so
    # rebuild the code indexes before the wiring phase.
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_title = str(node.get("title") or "")
        code_match = re.search(r"图\s*(\d+)", node_title)
        code = _canonical_figure(code_match.group(0)) if code_match else ""
        if node.get("type") == "image":
            if code:
                image_ids.setdefault(code, []).append(node["id"])
            kind = _asset_kind_from_title(node_title)
            if kind == "角色" and code:
                character_names[code] = _asset_title_base(node_title).split("·", 1)[0]
        elif node.get("type") == "audio":
            role = _asset_title_base(node_title, audio=True).split("·", 1)[0].strip()
            if code:
                audio_ids.setdefault(code, node["id"])
            if role:
                audio_by_role.setdefault(role, node["id"])

    # ---------- 4) connections ----------
    # 4.1 每段 → 该段引用的图
    for s in segs:
        no, raw = s.get("no", ""), s.get("raw", "")
        vid = video_ids.get(no)
        if not vid:
            continue
        seen = set()
        for _name, fig_no in _REF_RE.findall(raw):
            candidates = image_ids.get(_canonical_figure(fig_no), [])
            if not candidates:
                continue
            target = candidates[1] if (len(candidates) > 1 and ("九宫格" in _name or "窗内" in _name)) else candidates[0]
            key = (target, vid)
            if key in seen:
                continue
            seen.add(key)
            conns.append({"fromNodeId": target, "id": _rid_edge(), "toNodeId": vid})
    # 4.2 音频 → 该角色出场的段
    for s in segs:
        no, raw = s.get("no", ""), s.get("raw", "")
        vid = video_ids.get(no)
        if not vid:
            continue
        speaking_in_segment = _dialog_speaker_names(raw)
        seen_a = set()
        for _name, fig_no in _REF_RE.findall(raw):
            role = character_names.get(_canonical_figure(fig_no), "")
            if role not in speaking_in_segment:
                continue
            role_audio = audio_ids.get(_canonical_figure(fig_no))
            if not role_audio:
                continue
            key = (role_audio, vid)
            if key in seen_a:
                continue
            seen_a.add(key)
            conns.append({"fromNodeId": role_audio, "id": _rid_edge(), "toNodeId": vid})
    # 4.3 段串联
    nos = sorted(video_ids.keys(), key=lambda x: int(x))
    for i in range(len(nos) - 1):
        conns.append({"fromNodeId": video_ids[nos[i]], "id": _rid_edge(), "toNodeId": video_ids[nos[i + 1]]})

    # Keep provider-facing references inside the selected model budget. The
    # importer's text-head and previous-segment connections are useful context,
    # but on Seedance 2.0 they must yield to character/scene/prop/voice locks.
    for no, video_id in video_ids.items():
        target_node = next(node for node in nodes if node["id"] == video_id)
        model_id = str(target_node.get("metadata", {}).get("params", {}).get("model") or video_model)
        max_refs, max_audios = (30, 10) if model_id == "doubao-seedance-2-5" else (9, 3)
        incoming = [conn for conn in conns if conn.get("toNodeId") == video_id]
        type_of = {node["id"]: node["type"] for node in nodes}
        visuals = [conn for conn in incoming if type_of.get(conn["fromNodeId"]) == "image"]
        audios = [conn for conn in incoming if type_of.get(conn["fromNodeId"]) == "audio"]
        contexts = [conn for conn in incoming if type_of.get(conn["fromNodeId"]) in {"text", "video"}]
        if len(audios) > max_audios:
            audios = audios[:max_audios]
        room = max(0, max_refs - len(visuals) - len(audios))
        contexts.sort(key=lambda conn: 0 if type_of.get(conn["fromNodeId"]) == "video" else 1)
        keep_ids = {conn.get("id") for conn in visuals + audios + contexts[:room]}
        conns = [
            conn for conn in conns
            if conn.get("toNodeId") != video_id or conn.get("id") in keep_ids
        ]

    # Persist the same relationships into node metadata immediately. Connections
    # alone are enough for the canvas renderer, but the generation endpoint reads
    # params.refs/voiceRefs before the browser has had a chance to hydrate them.
    node_by_id = {n["id"]: n for n in nodes}
    for conn in conns:
        target = node_by_id.get(conn["toNodeId"])
        source = node_by_id.get(conn["fromNodeId"])
        if not target or not source or target.get("type") != "video":
            continue
        meta = target.setdefault("metadata", {})
        params = meta.setdefault("params", {})
        ref = {"nodeId": source["id"], "label": source.get("title") or source.get("type", "引用")}
        if source.get("type") == "audio":
            voices = params.setdefault("voiceRefs", [])
            if not any(item.get("nodeId") == source["id"] for item in voices):
                voices.append(ref)
            params["voiceRef"] = voices[0]
            meta["voiceRefs"] = voices
        else:
            if (
                source.get("type") == "video"
                and str(source.get("title") or "") not in str(meta.get("prompt") or "")
            ):
                continue
            refs = params.setdefault("refs", [])
            if not any(item.get("nodeId") == source["id"] for item in refs):
                refs.append(ref)
            meta["refs"] = refs

    payload = {
        "activeChatId": None, "appearance": {"mode": "dark"}, "backgroundMode": "dots",
        "chatSessions": [], "connections": conns, "createdAt": now0, "directorScenes": [],
        "folderId": None, "id": pid, "nodes": nodes, "projectId": pid, "revision": 1,
        "showImageInfo": False, "title": title, "updatedAt": now0,
        "viewport": {"x": 0, "y": 0, "zoom": 1}, "workspaceProjectId": pid,
        "importSummary": {
            "assetsReused": len(reused_asset_keys),
            "assetsReusedGenerated": len(reused_generated_asset_keys),
            "reusedAssetKeys": sorted(reused_asset_keys),
            "reusedGeneratedAssetKeys": sorted(reused_generated_asset_keys),
            "assetPoolSize": len(reusable_assets),
        },
        "episodeId": episode_id,
        "episodeTitle": episode_title,
        "episodeImports": [{
            "id": episode_id,
            "title": episode_title,
            "nodeIds": [node["id"] for node in nodes],
            "videoNodeIds": [node["id"] for node in nodes if node.get("type") == "video"],
            "connectionIds": [conn["id"] for conn in conns],
            "importedAt": now0,
        }],
    }
    apply_import_layout(payload)
    repair_payload_prompt_mentions(payload)
    return payload


def _ensure_episode_imports(payload):
    groups = payload.get("episodeImports")
    if isinstance(groups, list) and groups:
        return [dict(group) for group in groups if isinstance(group, dict)]
    title = str(payload.get("title") or "第01集")
    match = re.search(r"第\s*(\d+)\s*集", title)
    episode_id = f"EP{int(match.group(1)):03d}" if match else str(payload.get("episodeId") or "EP001")
    nodes = payload.get("nodes") or []
    conns = payload.get("connections") or []
    return [{
        "id": episode_id.upper(),
        "title": title,
        "nodeIds": [node.get("id") for node in nodes if node.get("id")],
        "videoNodeIds": [node.get("id") for node in nodes if node.get("id") and node.get("type") == "video"],
        "connectionIds": [conn.get("id") for conn in conns if conn.get("id")],
        "importedAt": payload.get("createdAt") or _now_iso(),
    }]


def attach_nodes_to_episode(payload, episode_id, node_ids):
    """Associate newly saved canvas cards with the episode currently open."""
    episode_id = str(episode_id or "").strip().upper()
    wanted = list(dict.fromkeys(
        str(node_id)
        for node_id in (node_ids or [])
        if node_id
    ))
    if not episode_id or not wanted:
        return 0

    groups = payload.setdefault("episodeImports", [])
    group = next(
        (
            item
            for item in groups
            if isinstance(item, dict)
            and str(item.get("id") or "").strip().upper() == episode_id
        ),
        None,
    )
    if group is None:
        group = {
            "id": episode_id,
            "title": payload.get("episodeTitle") or payload.get("title") or episode_id,
            "nodeIds": [],
            "videoNodeIds": [],
            "connectionIds": [],
            "importedAt": _now_iso(),
        }
        groups.append(group)

    existing = list(group.get("nodeIds") or [])
    existing_set = set(existing)
    added_ids = [node_id for node_id in wanted if node_id not in existing_set]
    group["nodeIds"] = list(dict.fromkeys([*existing, *wanted]))

    node_by_id = {
        str(node.get("id")): node
        for node in payload.get("nodes") or []
        if isinstance(node, dict) and node.get("id")
    }
    group["videoNodeIds"] = [
        node_id
        for node_id in group["nodeIds"]
        if str((node_by_id.get(node_id) or {}).get("type") or "") == "video"
    ]

    layout = payload.setdefault("episodeLayouts", {}).setdefault(episode_id, {})
    for node_id in wanted:
        position = (node_by_id.get(node_id) or {}).get("position")
        if isinstance(position, dict):
            layout[node_id] = {
                "x": float(position.get("x") or 0),
                "y": float(position.get("y") or 0),
            }
    return len(added_ids)


def _series_title(value):
    title = str(value or "").strip()
    title = re.sub(r"\s*·\s*第\s*\d+\s*集.*$", "", title).strip()
    if title and "画布工作流" not in title:
        title = f"{title} · 画布工作流"
    return title or "多集画布工作流"


def _asset_title_base(title, audio=False):
    value = str(title or "").strip()
    if audio:
        value = re.sub(r"^音频[｜|]\s*", "", value)
    else:
        value = re.sub(r"^[^｜|]*[｜|]\s*", "", value)
    value = re.sub(r"[（(]\s*图\s*\d+\s*[)）]\s*$", "", value).strip()
    return value


def _episode_sort_number(value):
    match = re.search(r"(\d+)", str(value or ""))
    return int(match.group(1)) if match else 0


def _layout_kind(node):
    node_type = str(node.get("type") or "")
    title = str(node.get("title") or "")
    if node_type == "image":
        if title.startswith("角色｜"):
            return "role"
        if title.startswith("群像｜"):
            return "crowd"
        if title.startswith("场景｜"):
            return "scene"
        if title.startswith("道具｜"):
            return "prop"
        return ""
    return node_type if node_type in {"text", "audio", "video"} else ""


def _layout_sort_key(node):
    title = str(node.get("title") or "")
    kind = _layout_kind(node)
    figure = re.search(r"图\s*(\d+)", title)
    figure_number = int(figure.group(1)) if figure else 0
    if kind == "video":
        segment = re.search(r"段\s*(\d+)", title)
        return (int(segment.group(1)) if segment else 0, title.casefold())
    if kind in {"role", "crowd", "scene", "prop", "audio"}:
        return (figure_number, title.casefold())
    return (title.casefold(),)


def apply_import_layout(payload, episode_ids=None):
    """Store a compact layout per episode so shared assets never leave blank slots."""
    nodes = [node for node in payload.get("nodes") or [] if isinstance(node, dict)]
    node_by_id = {node.get("id"): node for node in nodes if node.get("id")}
    groups = _ensure_episode_imports(payload)
    selected = {
        str(episode_id).upper()
        for episode_id in (episode_ids or [group.get("id") for group in groups])
        if episode_id
    }
    fixed_columns = {
        "text": (-320, 150),
        "role": (COL_ROLE, 360),
        "crowd": (COL_CROWD, 360),
        "scene": (COL_SCENE, 360),
        "prop": (COL_PROP, 360),
        "audio": (COL_AUDIO, 200),
    }
    video_columns = 3
    video_x_step = 260
    # Portrait video cards are ~364px tall after their chrome. The old 380px
    # row pitch left only ~16px between rows and made segment cards look glued
    # together. Keep a readable ~76px breathing gap at import time.
    video_y_step = 440
    layouts = payload.setdefault("episodeLayouts", {})
    globally_positioned = set()
    laid_out = 0

    for group in groups:
        episode_id = str(group.get("id") or "").upper()
        if episode_id not in selected:
            continue
        group_nodes = [
            node_by_id[node_id]
            for node_id in group.get("nodeIds") or []
            if node_id in node_by_id
        ]
        buckets = {key: [] for key in fixed_columns}
        videos = []
        for node in group_nodes:
            kind = _layout_kind(node)
            if kind == "video":
                videos.append(node)
            elif kind:
                buckets[kind].append(node)

        positions = {}
        for kind, items in buckets.items():
            items.sort(key=_layout_sort_key)
            x, step_y = fixed_columns[kind]
            for index, node in enumerate(items):
                positions[node["id"]] = {"x": x, "y": index * step_y}
        videos.sort(key=_layout_sort_key)
        for index, node in enumerate(videos):
            row, column = divmod(index, video_columns)
            positions[node["id"]] = {
                "x": COL_VIDEO + column * video_x_step,
                "y": row * video_y_step,
            }

        layouts[episode_id] = positions
        laid_out += len(positions)
        # Legacy/global positions remain a fallback for overview mode. Assign a
        # shared node from its earliest episode once, never from a later episode.
        for node_id, position in positions.items():
            if node_id in globally_positioned:
                continue
            node_by_id[node_id]["position"] = position
            globally_positioned.add(node_id)

    payload["layoutMode"] = "type-columns-v2"
    payload["layoutUpdatedAt"] = _now_iso()
    return laid_out


def _remove_redundant_scene_grids(payload):
    """Collapse scene master/grid pairs to the master card requested by the user."""
    nodes = [node for node in payload.get("nodes") or [] if isinstance(node, dict)]
    node_by_id = {node.get("id"): node for node in nodes if node.get("id")}
    pattern = re.compile(r"^场景｜(.+?)·(母版|九宫格)（图\s*(\d+)）$")
    masters = {}
    grids = []
    for node in nodes:
        if node.get("type") != "image":
            continue
        match = pattern.match(str(node.get("title") or ""))
        if not match:
            continue
        key = (match.group(1), match.group(3))
        if match.group(2) == "母版":
            masters[key] = node
        else:
            grids.append((key, node))

    removed = set()
    for key, grid in grids:
        master = masters.get(key)
        if not master:
            continue
        grid_id = grid.get("id")
        master_id = master.get("id")
        grid_title = str(grid.get("title") or "")
        master_title = str(master.get("title") or "")
        removed.add(grid_id)

        for node in nodes:
            if node.get("type") != "video":
                continue
            meta = node.get("metadata") or {}
            prompt = str(meta.get("prompt") or "")
            prompt = prompt.replace(f"@{grid_title}", f"@{master_title}")
            prompt = prompt.replace(grid_title, master_title)
            prompt = prompt.replace("（母版+九宫格双挂）", "（母版）")
            prompt = prompt.replace("母版+九宫格双挂", "母版")
            meta["prompt"] = prompt
            params = meta.setdefault("params", {})
            for field in ("refs", "voiceRefs"):
                merged = []
                seen = set()
                for item in params.get(field) or []:
                    if not isinstance(item, dict):
                        continue
                    if item.get("nodeId") == grid_id:
                        item = {**item, "nodeId": master_id, "label": master_title}
                    label = str(item.get("label") or "")
                    if label == grid_title:
                        label = master_title
                    node_id = item.get("nodeId")
                    ref_key = (node_id, label)
                    if ref_key in seen:
                        continue
                    seen.add(ref_key)
                    merged.append({**item, "label": label})
                params[field] = merged
                if field == "refs":
                    meta["refs"] = merged
                elif field == "voiceRefs":
                    meta["voiceRefs"] = merged

    if not removed:
        return 0

    connections = payload.setdefault("connections", [])
    existing_pairs = {
        (conn.get("fromNodeId"), conn.get("toNodeId"))
        for conn in connections
        if isinstance(conn, dict)
    }
    for grid_id in list(removed):
        grid = node_by_id.get(grid_id) or {}
        match = pattern.match(str(grid.get("title") or ""))
        master = masters.get((match.group(1), match.group(3))) if match else None
        if not master:
            continue
        for conn in list(connections):
            if not isinstance(conn, dict) or conn.get("fromNodeId") != grid_id:
                continue
            target_id = conn.get("toNodeId")
            if target_id and (master["id"], target_id) not in existing_pairs:
                connections.append({
                    "fromNodeId": master["id"],
                    "id": _rid_edge(),
                    "toNodeId": target_id,
                })
                existing_pairs.add((master["id"], target_id))
    connections[:] = [
        conn for conn in connections
        if not (isinstance(conn, dict) and conn.get("fromNodeId") in removed)
    ]

    updated_groups = []
    for group in _ensure_episode_imports(payload):
        old_ids = list(group.get("nodeIds") or [])
        next_ids = []
        for node_id in old_ids:
            if node_id not in removed:
                next_ids.append(node_id)
                continue
            grid = node_by_id.get(node_id) or {}
            match = pattern.match(str(grid.get("title") or ""))
            master = masters.get((match.group(1), match.group(3))) if match else None
            if master and master["id"] not in next_ids:
                next_ids.append(master["id"])
        group["nodeIds"] = list(dict.fromkeys(next_ids))
        video_ids = {
            node_id for node_id in group.get("videoNodeIds") or []
            if node_id in node_by_id
        }
        group["connectionIds"] = [
            conn.get("id") for conn in connections
            if isinstance(conn, dict) and conn.get("toNodeId") in video_ids
        ]
        updated_groups.append(group)

    payload["episodeImports"] = updated_groups
    payload["nodes"] = [
        node for node in payload.get("nodes") or []
        if not (isinstance(node, dict) and node.get("id") in removed)
    ]
    layouts = payload.setdefault("episodeLayouts", {})
    for layout in layouts.values():
        if isinstance(layout, dict):
            for node_id in removed:
                layout.pop(node_id, None)
    return len(removed)


def _prompt_speaking_roles(prompt, role_names):
    """Return the concrete character voices required by this segment's dialogue."""
    known = {
        str(role or "").strip()
        for role in role_names
        if str(role or "").strip()
    }
    speaking = {
        role
        for role in _dialog_speaker_names(prompt)
        if role in known
    }
    for line in str(prompt or "").splitlines():
        # ① 显式「画外·角色名」标注对 OS 与 VO 都生效——作者已写死发声人，直接采信。
        #    形如：声音：OS："…"（画外·赵铁柱） / 声音：画外·赵铁柱内心独白（OS）："…"
        #    分隔符允许「·」「・」「•」与空白。
        annotated = None
        for anno in re.finditer(r"画外\s*[·・•]\s*([^\s·・•（）()，,。;；:：\"“”]+)", line):
            token = anno.group(1).strip()
            if token in known:
                annotated = token
                break
        if annotated is None:
            for role in known:
                if re.search(rf"画外\s*[·・•]\s*{re.escape(role)}", line):
                    annotated = role
                    break
        if annotated:
            speaking.add(annotated)
            continue
        # ② 无标注时的推断**只对 OS / 内心独白**生效。
        #    VO（旁白 / 广播 / 系统音）不是场内某个角色的内心，根本不适用
        #    「从画面挑角色」这套推断——加进来会把画内站着的人误挂成本段音色
        #    （第01集段8 画外广播 VO 曾被误判出工人甲，即此坑）。
        if not re.search(r"\bOS\b|内心独白", line, re.IGNORECASE):
            continue
        # ③ 推断时只看「画面段」找发声人——绝不允许把【声音：】之后的
        #    OS 台词正文纳入搜名范围。台词里提到别的角色名（如"我背陈大强…"）
        #    不代表那个角色在说话；旧实现切到「台词：」，把整段 OS 引号正文都搜了
        #    进来，再按 rfind 取最靠后者，导致「被提到的人」被判成「说话的人」。
        stop = len(line)
        for marker in ("声音：", "声音:", "台词：", "台词:"):
            pos = line.find(marker)
            if 0 <= pos < stop:
                stop = pos
        search_region = line[:stop]
        # An OS/VO line can start with `赵铁柱（OS: ...）："..."` where the
        # annotation itself contains an ASCII colon. Stop at the opening quote
        # so names mentioned inside the quoted dialogue never become speakers.
        quote_positions = [
            pos for pos in (
                search_region.find('"'),
                search_region.find('“'),
            )
            if pos >= 0
        ]
        if quote_positions:
            search_region = search_region[:min(quote_positions)]
        ranked = sorted(
            (
                (search_region.rfind(role), role)
                for role in known
                if role in search_region
            ),
            reverse=True,
        )
        if ranked and ranked[0][0] >= 0:
            speaking.add(ranked[0][1])
    return speaking


def _inline_asset_mention(prompt, base, full_title):
    if not base or not full_title or f"@{full_title}" in prompt:
        return prompt
    pattern = re.compile(rf"(?<!@)(?<!｜){re.escape(base)}")
    match = pattern.search(prompt)
    return prompt[:match.start()] + f"@{full_title}" + prompt[match.end():] if match else prompt


def _iso_datetime(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _repair_reused_asset_identities(payload):
    """Restore the first locked series title when a later feed changes its figure code."""
    nodes = [node for node in payload.get("nodes") or [] if isinstance(node, dict)]
    repairs = []
    for node in nodes:
        meta = node.get("metadata") or {}
        source_title = str((meta.get("reusedAsset") or {}).get("sourceTitle") or "").strip()
        current_title = str(node.get("title") or "").strip()
        if not source_title or not current_title or source_title == current_title:
            continue
        current_code = re.search(r"图\s*(\d+)", current_title)
        source_code = re.search(r"图\s*(\d+)", source_title)
        repairs.append((current_title, source_title, current_code, source_code))
        node["title"] = source_title
        for key in ("prompt", "composerContent"):
            value = str(meta.get(key) or "")
            value = value.replace(current_title, source_title)
            if current_code and source_code:
                value = re.sub(
                    rf"图\s*{re.escape(current_code.group(1))}(?!\d)",
                    f"图{source_code.group(1)}",
                    value,
                )
            meta[key] = value
    if not repairs:
        return 0
    for node in nodes:
        if node.get("type") != "video":
            continue
        meta = node.get("metadata") or {}
        prompt = str(meta.get("prompt") or "")
        for current_title, source_title, current_code, source_code in repairs:
            prompt = prompt.replace(f"@{current_title}", f"@{source_title}")
            prompt = prompt.replace(current_title, source_title)
            if current_code and source_code:
                prompt = re.sub(
                    rf"图\s*{re.escape(current_code.group(1))}(?!\d)",
                    f"图{source_code.group(1)}",
                    prompt,
                )
        meta["prompt"] = prompt
        params = meta.setdefault("params", {})
        for key in ("refs", "voiceRefs"):
            items = []
            for item in params.get(key) or []:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("label") or "")
                for current_title, source_title, _, _ in repairs:
                    label = label.replace(current_title, source_title)
                items.append({**item, "label": label})
            params[key] = items
    return len(repairs)


def _remove_unmentioned_video_refs(payload):
    """Keep sequence edges visual, but never send undeclared videos as generation refs."""
    node_by_id = {
        node.get("id"): node
        for node in payload.get("nodes") or []
        if isinstance(node, dict) and node.get("id")
    }
    removed = 0
    for node in node_by_id.values():
        if node.get("type") != "video":
            continue
        meta = node.setdefault("metadata", {})
        params = meta.setdefault("params", {})
        prompt = str(meta.get("prompt") or "")
        refs = []
        for ref in params.get("refs") or []:
            if not isinstance(ref, dict):
                continue
            source = node_by_id.get(ref.get("nodeId")) or {}
            if (
                source.get("type") == "video"
                and str(source.get("title") or "") not in prompt
            ):
                removed += 1
                continue
            refs.append(ref)
        params["refs"] = refs
        meta["refs"] = refs
    return removed


def repair_dangling_media_references(payload):
    """Resolve stale media node IDs by the stable asset identity in their label.

    Re-importing an episode creates fresh node IDs. A video can therefore retain
    ``refs[].nodeId`` from the previous import even though the correctly matched
    asset still exists in the project under another ID. Agent tooling also saw
    those dangling IDs as "missing dependencies", locking otherwise ready video
    cards. Resolve by asset title/identity first, preferring generated media.
    """
    nodes = [
        node for node in payload.get("nodes") or []
        if isinstance(node, dict) and node.get("id")
    ]
    by_id = {str(node["id"]): node for node in nodes}
    by_label: dict[str, list[dict]] = {}
    by_identity: dict[str, list[dict]] = {}
    by_role: dict[str, list[dict]] = {}
    for node in nodes:
        node_type = str(node.get("type") or "")
        if node_type not in {"image", "audio"}:
            continue
        title = str(node.get("title") or "").strip()
        if title:
            by_label.setdefault(title, []).append(node)
        kind = _asset_kind_from_title(title)
        key = str((node.get("metadata") or {}).get("assetKey") or "")
        if not key:
            key = _asset_identity(
                _asset_title_base(title, audio=node_type == "audio"),
                kind or ("音色" if node_type == "audio" else ""),
            )
        if key:
            by_identity.setdefault(key, []).append(node)
        if node_type == "audio":
            role = _asset_title_base(title, audio=True).split("·", 1)[0].strip()
            if role:
                by_role.setdefault(role, []).append(node)

    def score(node):
        meta = node.get("metadata") or {}
        versions = (meta.get("params") or {}).get("mediaVersions") or []
        return (
            str(meta.get("status") or "") == "done",
            bool(str(meta.get("content") or "").strip()),
            any(bool(str(item.get("url") or "").strip()) for item in versions if isinstance(item, dict)),
            str(node.get("updatedAt") or ""),
        )

    changed = 0

    def resolve(item, valid_identities):
        nonlocal changed
        if not isinstance(item, dict):
            return True
        current = str(item.get("nodeId") or "").strip()
        if current and current in by_id:
            return True
        label = str(item.get("label") or "").strip()
        candidates = by_label.get(label) or []
        identity = _asset_identity(
            _asset_title_base(
                label,
                audio=label.startswith("音频｜"),
            ),
            _asset_kind_from_title(label)
            or ("音色" if label.startswith("音频｜") else ""),
        )
        role = ""
        if label.startswith("音频｜"):
            role = _asset_title_base(label, audio=True).split("·", 1)[0].strip()
        if not candidates and label:
            candidates = by_identity.get(identity, [])
        if not candidates and role:
            candidates = by_role.get(role) or []
        if not candidates:
            return False
        candidate_identity = (
            role
            if role
            else identity
        )
        if candidate_identity and candidate_identity in valid_identities:
            # A later valid ref already binds this identity. Drop the stale
            # duplicate instead of adding a second blocker for the same asset.
            changed += 1
            return False
        replacement = max(candidates, key=score)
        item["nodeId"] = replacement["id"]
        item["label"] = str(replacement.get("title") or label)
        if candidate_identity:
            valid_identities.add(candidate_identity)
        changed += 1
        return True

    def identity_of(node_id):
        node = by_id.get(str(node_id or ""))
        if not node:
            return ""
        title = str(node.get("title") or "")
        if node.get("type") == "audio":
            return _asset_title_base(title, audio=True).split("·", 1)[0].strip()
        return str((node.get("metadata") or {}).get("assetKey") or _asset_identity(
            _asset_title_base(title),
            _asset_kind_from_title(title),
        ))

    for node in nodes:
        if node.get("type") != "video":
            continue
        meta = node.setdefault("metadata", {})
        params = meta.setdefault("params", {})
        for key in ("refs", "voiceRefs"):
            items = params.get(key)
            if not isinstance(items, list):
                continue
            valid_identities = {
                identity
                for item in items
                if isinstance(item, dict)
                for identity in [identity_of(item.get("nodeId"))]
                if identity and str(item.get("nodeId") or "") in by_id
            }
            kept = []
            for item in items:
                if resolve(item, valid_identities):
                    kept.append(item)
            if kept != items:
                params[key] = kept
                meta[key] = kept
        voice = params.get("voiceRef")
        if isinstance(voice, dict):
            resolve(voice, set())
        voice = meta.get("voiceRef")
        if isinstance(voice, dict):
            resolve(voice, set())
    return changed


def _ensure_referenced_assets(payload, episode_id=None, asset_sources=None, voice_sources=None):
    """Create placeholder cards for assets referenced by prompts but absent from the feed."""
    nodes = payload.setdefault("nodes", [])
    source_by_code = {}
    source_by_key = {}
    voice_by_code = {}
    voice_by_role = {}
    for voice in voice_sources or []:
        item = _normalize_voice_catalog_item(voice)
        if not item:
            continue
        code = _canonical_figure(item.get("code"))
        role = str(item.get("name") or "")
        if code:
            voice_by_code[code] = item
        if role:
            voice_by_role[role] = item
    for asset in asset_sources or []:
        if not isinstance(asset, dict):
            continue
        code = _canonical_figure(asset.get("code"))
        canvas = asset.get("canvas") if isinstance(asset.get("canvas"), dict) else {}
        canvas_kind = str(canvas.get("kind") or "")
        if canvas_kind in {"character", "scene", "prop", "crowd"}:
            kind = {"character": "角色", "scene": "场景", "prop": "道具", "crowd": "群像"}[canvas_kind]
            short_name = str(canvas.get("name") or _short_asset_name(asset.get("name", "")))
        elif "群像" in str(asset.get("name") or ""):
            kind = "群像"
            short_name = _short_asset_name(str(asset.get("name") or ""))
        elif "角色立绘" in str(asset.get("name") or ""):
            kind = "角色"
            short_name = str(asset.get("name") or "").replace("（角色立绘）", "")
        elif "场景" in str(asset.get("name") or "") or "母版" in str(asset.get("name") or ""):
            kind = "场景"
            short_name = str(asset.get("name") or "").replace("（场景母版）", "")
            if "母版" not in short_name:
                short_name = f"{short_name}·母版"
        else:
            kind = "道具"
            short_name = str(asset.get("name") or "").replace("（道具设定图）", "")
        source = {
            "code": code,
            "kind": kind,
            "name": short_name,
            "raw": str(asset.get("raw") or "").strip(),
            "canvas": canvas,
        }
        if code:
            source_by_code[code] = source
        key = _asset_identity(short_name, kind)
        if key:
            source_by_key[key] = source
    groups = _ensure_episode_imports(payload)
    target_group = next(
        (
            group for group in groups
            if episode_id
            and str(group.get("id") or "").upper() == str(episode_id).upper()
        ),
        None,
    )
    if target_group is None and groups:
        target_group = groups[-1]
    target_ids = set((target_group or {}).get("videoNodeIds") or [])
    videos = [
        node for node in nodes
        if isinstance(node, dict)
        and node.get("type") == "video"
        and (not target_ids or node.get("id") in target_ids)
    ]
    image_by_code = {}
    image_by_key = {}
    audio_by_role = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        meta = node.get("metadata") or {}
        if "自动补占位卡" in str(meta.get("prompt") or ""):
            meta["autoPlaceholder"] = True
        if node.get("type") == "image":
            image_meta = node.setdefault("metadata", {})
            image_params = image_meta.setdefault("params", {})
            image_params["ratio"] = "16:9"
            image_params["size"] = "1536x864"
            node["width"] = 720
            node["height"] = 405
            key = str((node.get("metadata") or {}).get("assetKey") or "")
            if not key:
                key = _asset_identity(
                    _asset_title_base(node.get("title")),
                    _asset_kind_from_title(node.get("title")),
                )
            if key:
                image_by_key.setdefault(key, node)
            for code in re.findall(r"图\s*(\d+)", str(node.get("title") or "")):
                image_by_code.setdefault(_canonical_figure(code), node)
        elif node.get("type") == "audio":
            audio_meta = node.get("metadata") or {}
            if _is_placeholder_asset_prompt(audio_meta.get("prompt")):
                audio_meta["autoPlaceholder"] = True
            elif audio_meta.get("autoPlaceholder"):
                audio_meta["autoPlaceholder"] = False
            role = _asset_title_base(node.get("title"), audio=True).split("·", 1)[0].strip()
            if role:
                audio_by_role.setdefault(role, node)
                (node.get("metadata") or {}).setdefault(
                    "assetKey",
                    _asset_identity(role, "音色"),
                )

    now = _now_iso()
    created_at = str(
        (target_group or {}).get("importedAt")
        or payload.get("createdAt")
        or now
    )
    target_imported_at = _iso_datetime(created_at)

    def allowed_for_target(node):
        if (node.get("metadata") or {}).get("_importCreated"):
            return True
        if target_imported_at is None:
            return True
        if (node.get("metadata") or {}).get("autoPlaceholder"):
            return True
        node_created_at = _iso_datetime(node.get("createdAt"))
        return (
            node_created_at is None
            or (node_created_at - target_imported_at).total_seconds() <= 2
        )
    added = []
    used_ids = set()

    def find_image(kind, name, code):
        code = _canonical_figure(code)
        existing = image_by_code.get(code)
        if existing:
            return existing
        key = _asset_identity(name, kind)
        existing = image_by_key.get(key)
        if existing:
            image_by_code[code] = existing
        return existing

    def add_image(kind, name, code, video):
        code = _canonical_figure(code)
        source = source_by_code.get(code) or source_by_key.get(_asset_identity(name, kind))
        if source:
            kind = source["kind"] or kind
            name = source["name"] or name
        raw_prompt = source.get("raw", "") if source else ""
        canvas = source.get("canvas", {}) if source else {}
        title = f"{kind}｜{name}（{code}）"
        ratio = _aspect(canvas.get("ratio"), "16:9")
        model = str(canvas.get("model") or "doubao-seedream-5-0-pro")
        clarity = str(canvas.get("clarity") or "1.5K")
        size = str(canvas.get("size") or _image_size(ratio))
        prompt = (
            f"### {code} · {name}\n\n{raw_prompt}"
            if raw_prompt
            else (
                f"### {code} · {name}\n\n"
                "该资产由视频提示词引用，但当前投喂载荷未提供资产提示词；"
                "已自动补占位卡，请补充提示词后再生成。"
            )
        )
        image_params = {
            "provider": str(canvas.get("provider") or "ark"),
            "model": model,
            "ratio": ratio,
            "clarity": clarity,
            "size": size,
            "guidance": str(canvas.get("guidance") or "中等"),
            "watermark": bool(canvas.get("watermark", False)),
        }
        if isinstance(canvas.get("params"), dict):
            image_params.update(canvas["params"])
        node_id = _rid_node()
        node = {
            "height": 405,
            "width": 720,
            "id": node_id,
            "metadata": {
                "composerContent": prompt,
                "content": "",
                "model": model,
                "prompt": prompt,
                "status": "idle",
                "nodeRole": "generator",
                "autoPlaceholder": True,
                "assetKey": _asset_identity(name, kind),
                "assetId": str(canvas.get("assetId") or ""),
                "seriesId": str(canvas.get("seriesId") or ""),
                "variantKey": str(canvas.get("variantKey") or "default"),
                "episodeId": str(episode_id or payload.get("episodeId") or ""),
                "_importCreated": True,
                "params": image_params,
            },
            "position": {"x": 0, "y": 0},
            "title": title,
            "type": "image",
            "createdAt": created_at,
            "updatedAt": now,
        }
        nodes.append(node)
        image_by_code[code] = node
        image_by_key.setdefault(_asset_identity(name, kind), node)
        added.append(node)
        return node

    def add_audio(role, code, video):
        code = _canonical_figure(code)
        title = f"音频｜{role}（{code}）"
        voice_source = voice_by_code.get(code) or voice_by_role.get(role) or {}
        detailed_prompt = str(voice_source.get("prompt") or "").strip()
        generic_prompt = (
            f"{role}，普通话对白参考音色，短剧配音参考，"
            "4.5 秒清晰人声，无背景噪声。请根据角色补充音色描述后再生成。"
        )
        if detailed_prompt:
            label = str(voice_source.get("label") or "").strip()
            prompt = (
                f"{label}. {detailed_prompt}"
                if label and not detailed_prompt.casefold().startswith(label.casefold())
                else detailed_prompt
            )
        else:
            prompt = generic_prompt
        node_id = _rid_audio()
        node = {
            "height": 320,
            "width": 320,
            "id": node_id,
            "metadata": {
                "content": "",
                "composerContent": f"角色：{role}（{code}）\n\n【音色占位 · 4.5S】\n{prompt}",
                "assetKey": _asset_identity(role, "音色"),
                "prompt": prompt,
                "status": "idle",
                "generationMode": "audio",
                "nodeRole": "generator",
                "autoPlaceholder": not bool(detailed_prompt),
                "episodeId": str(episode_id or payload.get("episodeId") or ""),
                "_importCreated": True,
                "params": {
                    "provider": "aliyun_bailian",
                    "model": "cosyvoice-v3-5-flash",
                    "ttsModel": "cosyvoice-v3.5-flash",
                    "ttsMode": "design",
                    "outputFormat": "mp3",
                    "sampleRate": 24000,
                    "previewDuration": 4.5,
                    "voicePrompt": prompt,
                },
            },
            "position": {"x": 0, "y": 0},
            "title": title,
            "type": "audio",
            "createdAt": created_at,
            "updatedAt": now,
        }
        nodes.append(node)
        audio_by_role[role] = node
        added.append(node)
        return node

    for video in videos:
        meta = video.get("metadata") or {}
        prompt = str(meta.get("prompt") or "")
        composer = str(meta.get("composerContent") or "")
        reference_text = f"{prompt}\n{composer}"
        named_roles = {
            name.strip()
            for kind, name, _code in _NAMED_REF_RE.findall(reference_text)
            if kind == "角色" and name.strip()
        }
        speaking = _dialog_speaker_names(reference_text)
        speaking |= _prompt_speaking_roles(
            reference_text,
            set(audio_by_role.keys()) | named_roles,
        )
        for match in _NAMED_REF_RE.finditer(reference_text):
            kind = match.group("kind")
            name = match.group("name").strip()
            code = match.group("code").strip()
            if kind == "音频":
                continue
            source = find_image(kind, name, code)
            if source is None:
                source = add_image(kind, name, code, video)
            if source not in added and not allowed_for_target(source):
                continue
            used_ids.add(source["id"])
            meta = video.setdefault("metadata", {})
            params = meta.setdefault("params", {})
            refs = params.setdefault("refs", [])
            ref_ids = {
                item.get("nodeId")
                for item in refs
                if isinstance(item, dict)
            }
            if source["id"] not in ref_ids:
                refs.append({
                    "nodeId": source["id"],
                    "label": source.get("title") or name,
                })
                meta["refs"] = refs
                pair = (source["id"], video.get("id"))
                existing_pairs = {
                    (item.get("fromNodeId"), item.get("toNodeId"))
                    for item in payload.setdefault("connections", [])
                    if isinstance(item, dict)
                }
                if pair not in existing_pairs:
                    payload["connections"].append({
                        "id": _rid_edge(),
                        "fromNodeId": source["id"],
                        "toNodeId": video.get("id"),
                    })
            if kind == "角色":
                voice = audio_by_role.get(name)
                if voice is None and name in speaking:
                    voice = add_audio(name, code, video)
                if voice is None:
                    continue
                if voice not in added and not allowed_for_target(voice):
                    continue
                used_ids.add(voice["id"])
                voices = params.setdefault("voiceRefs", [])
                voice_ids = {
                    item.get("nodeId")
                    for item in voices
                    if isinstance(item, dict)
                }
                if voice["id"] not in voice_ids:
                    voices.append({
                        "nodeId": voice["id"],
                        "label": voice.get("title") or name,
                    })
                    params["voiceRefs"] = voices
                    params["voiceRef"] = voices[0]
                    meta["voiceRefs"] = voices
                    pair = (voice["id"], video.get("id"))
                    existing_pairs = {
                        (item.get("fromNodeId"), item.get("toNodeId"))
                        for item in payload.setdefault("connections", [])
                        if isinstance(item, dict)
                    }
                    if pair not in existing_pairs:
                        payload["connections"].append({
                            "id": _rid_edge(),
                            "fromNodeId": voice["id"],
                            "toNodeId": video.get("id"),
                        })

    for node in added:
        (node.get("metadata") or {}).pop("_importCreated", None)
    group_changed = False
    if target_group is not None and used_ids:
        next_ids = list(dict.fromkeys([
            *(target_group.get("nodeIds") or []),
            *used_ids,
        ]))
        group_changed = next_ids != list(target_group.get("nodeIds") or [])
        target_group["nodeIds"] = next_ids
        actual_groups = payload.get("episodeImports") or []
        actual_group = next(
            (
                group for group in actual_groups
                if str(group.get("id") or "").upper() == str(target_group.get("id") or "").upper()
            ),
            None,
        )
        if actual_group is None and actual_groups:
            actual_group = actual_groups[-1]
        if actual_group is not None:
            previous_ids = list(actual_group.get("nodeIds") or [])
            actual_group["nodeIds"] = list(dict.fromkeys([
                *(actual_group.get("nodeIds") or []),
                *used_ids,
            ]))
            group_changed = group_changed or actual_group["nodeIds"] != previous_ids
    return len(added) + (1 if group_changed else 0)


def bind_shared_assets_for_episode(payload, episode_id):
    """Bind only existing series assets explicitly mentioned by this episode."""
    nodes = [node for node in payload.get("nodes") or [] if isinstance(node, dict)]
    node_by_id = {node.get("id"): node for node in nodes if node.get("id")}
    groups = _ensure_episode_imports(payload)
    group = next((item for item in groups if str(item.get("id") or "").upper() == str(episode_id).upper()), None)
    if not group:
        return 0
    group_node_ids = [
        node_id for node_id in group.get("nodeIds") or []
        if node_id in node_by_id
    ]
    group_video_ids = [
        node_id for node_id in group.get("videoNodeIds") or []
        if node_id in node_by_id
    ]
    videos = [node_by_id[node_id] for node_id in group_video_ids if node_id in node_by_id and node_by_id[node_id].get("type") == "video"]
    connections = payload.setdefault("connections", [])
    cleanup_changes = 0

    # An asset created after an episode was imported cannot be a dependency of
    # that earlier episode. Remove cross-episode pollution introduced by older
    # name-only shared-asset matching.
    imported_at = _iso_datetime(group.get("importedAt"))
    invalid_scope_ids = set()
    if imported_at:
        for node_id in group_node_ids:
            node = node_by_id.get(node_id)
            if not node or node.get("type") not in {"image", "audio"}:
                continue
            created_at = _iso_datetime(node.get("createdAt"))
            if created_at and (created_at - imported_at).total_seconds() > 2:
                invalid_scope_ids.add(node_id)
    if invalid_scope_ids:
        group_node_ids = [node_id for node_id in group_node_ids if node_id not in invalid_scope_ids]
        video_ids = {video.get("id") for video in videos}
        connections[:] = [
            conn for conn in connections
            if not (
                isinstance(conn, dict)
                and conn.get("toNodeId") in video_ids
                and conn.get("fromNodeId") in invalid_scope_ids
            )
        ]
        for video in videos:
            meta = video.setdefault("metadata", {})
            params = meta.setdefault("params", {})
            refs = [
                ref for ref in (params.get("refs") or [])
                if not (isinstance(ref, dict) and ref.get("nodeId") in invalid_scope_ids)
            ]
            voices = [
                voice for voice in (params.get("voiceRefs") or [])
                if not (isinstance(voice, dict) and voice.get("nodeId") in invalid_scope_ids)
            ]
            prompt = str(meta.get("prompt") or "")
            for node_id in invalid_scope_ids:
                node = node_by_id.get(node_id) or {}
                full_title = str(node.get("title") or "")
                base = _asset_title_base(full_title, audio=node.get("type") == "audio").split("·", 1)[0]
                if full_title:
                    prompt = prompt.replace(f"@{full_title}", base)
            params["refs"] = refs
            params["voiceRefs"] = voices
            params["voiceRef"] = voices[0] if voices else None
            meta["refs"] = refs
            meta["voiceRefs"] = voices
            meta["prompt"] = prompt
        group["nodeIds"] = group_node_ids
        cleanup_changes += 1

    grouped_asset_ids = {
        node_id
        for item in groups
        for node_id in (item.get("nodeIds") or [])
    }

    def available_for_episode(node):
        if not imported_at or node.get("type") not in {"image", "audio"}:
            return True
        if (node.get("metadata") or {}).get("autoPlaceholder"):
            return True
        created_at = _iso_datetime(node.get("createdAt"))
        return not created_at or (created_at - imported_at).total_seconds() <= 2

    image_by_identity = {}
    image_duplicates = {}
    for node in nodes:
        if (
            node.get("type") != "image"
            or not node.get("id")
            or node.get("id") not in grouped_asset_ids
            or not available_for_episode(node)
        ):
            continue
        identity = str((node.get("metadata") or {}).get("assetKey") or _asset_title_base(node.get("title")))
        current = image_by_identity.get(identity)
        if current is None:
            image_by_identity[identity] = node
            continue
        current_meta = current.get("metadata") or {}
        node_meta = node.get("metadata") or {}
        current_score = (
            not bool(current_meta.get("autoPlaceholder")),
            current_meta.get("status") == "done",
            bool(current_meta.get("content")),
            str(current.get("updatedAt") or ""),
        )
        node_score = (
            not bool(node_meta.get("autoPlaceholder")),
            node_meta.get("status") == "done",
            bool(node_meta.get("content")),
            str(node.get("updatedAt") or ""),
        )
        if node_score > current_score:
            image_by_identity[identity] = node
            image_duplicates[current["id"]] = node["id"]
        else:
            image_duplicates[node["id"]] = current["id"]
    image_assets = list(image_by_identity.values())
    audio_by_identity = {}
    audio_duplicates = {}
    for node in nodes:
        if (
            node.get("type") != "audio"
            or not node.get("id")
            or node.get("id") not in grouped_asset_ids
            or not available_for_episode(node)
        ):
            continue
        identity = _asset_title_base(node.get("title"), audio=True)
        current = audio_by_identity.get(identity)
        if current is None:
            audio_by_identity[identity] = node
            continue
        current_meta = current.get("metadata") or {}
        node_meta = node.get("metadata") or {}
        if (bool(node_meta.get("content")), str(node.get("updatedAt") or "")) > (
            bool(current_meta.get("content")), str(current.get("updatedAt") or "")
        ):
            audio_by_identity[identity] = node
            audio_duplicates[current["id"]] = node["id"]
        else:
            audio_duplicates[node["id"]] = current["id"]
    audio_assets = list(audio_by_identity.values())
    role_names = {
        _asset_title_base(image.get("title")).split("·", 1)[0]
        for image in image_assets
        if str(image.get("title") or "").startswith("角色｜")
    }
    video_id_set = {video.get("id") for video in videos}
    duplicate_asset_ids = set(image_duplicates) | set(audio_duplicates)
    removable_duplicate_ids = {
        node_id
        for node_id in duplicate_asset_ids
        if (node_by_id.get(node_id) or {}).get("metadata", {}).get("autoPlaceholder")
        and not str(((node_by_id.get(node_id) or {}).get("metadata") or {}).get("content") or "").strip()
    }
    if removable_duplicate_ids:
        payload["nodes"] = [
            node for node in payload.get("nodes") or []
            if not (
                isinstance(node, dict)
                and node.get("id") in removable_duplicate_ids
            )
        ]
        for item in payload.get("episodeImports") or []:
            item["nodeIds"] = [
                node_id for node_id in (item.get("nodeIds") or [])
                if node_id not in removable_duplicate_ids
            ]
        cleanup_changes += len(removable_duplicate_ids)
        duplicate_asset_ids -= removable_duplicate_ids
    connections_before_dedupe = len(connections)
    connections[:] = [
        conn for conn in connections
        if not (
            isinstance(conn, dict)
            and conn.get("toNodeId") in video_id_set
            and conn.get("fromNodeId") in duplicate_asset_ids
        )
    ]
    cleanup_changes += 1 if len(connections) != connections_before_dedupe else 0
    existing_pairs = {(conn.get("fromNodeId"), conn.get("toNodeId")) for conn in connections if isinstance(conn, dict)}
    added = 0
    added_node_ids = set()
    for video in videos:
        meta = video.setdefault("metadata", {})
        params = meta.setdefault("params", {})
        prompt = str(meta.get("prompt") or "")
        composer = str(meta.get("composerContent") or "")
        match_text = f"{prompt}\n{composer}"
        model_id = str(params.get("model") or "")
        max_refs, max_audios = (30, 10) if model_id == "doubao-seedance-2-5" else (9, 3)
        refs = [
            ref for ref in (params.get("refs") or [])
            if isinstance(ref, dict) and ref.get("nodeId") in node_by_id
        ]
        voices = [
            voice for voice in (params.get("voiceRefs") or [])
            if isinstance(voice, dict)
            and (node_by_id.get(voice.get("nodeId")) or {}).get("type") == "audio"
        ]
        deduped_refs = []
        seen_ref_labels = set()
        for ref in refs:
            label = str(ref.get("label") or "") if isinstance(ref, dict) else ""
            if label and label in seen_ref_labels:
                continue
            if label:
                seen_ref_labels.add(label)
            deduped_refs.append(ref)
        refs = deduped_refs
        deduped_voices = []
        seen_voice_labels = set()
        for voice in voices:
            label = str(voice.get("label") or "") if isinstance(voice, dict) else ""
            if label and label in seen_voice_labels:
                continue
            if label:
                seen_voice_labels.add(label)
            deduped_voices.append(voice)
        voices = deduped_voices
        speaking_roles = _prompt_speaking_roles(match_text, role_names)
        voices = [
            voice for voice in voices
            if _asset_title_base(
                (node_by_id.get(voice.get("nodeId")) or {}).get("title"),
                audio=True,
            ).split("·", 1)[0] in speaking_roles
        ]
        if len(deduped_refs) != len(params.get("refs") or []):
            cleanup_changes += 1
        if len(deduped_voices) != len(params.get("voiceRefs") or []):
            cleanup_changes += 1
        ref_ids = {item.get("nodeId") for item in refs if isinstance(item, dict)}
        voice_ids = {item.get("nodeId") for item in voices if isinstance(item, dict)}
        for image in image_assets:
            full_title = str(image.get("title") or "")
            base = _asset_title_base(full_title)
            match_base = base.split("·", 1)[0]
            figure = re.search(r"图\s*(\d+)", full_title)
            figure_match = False
            if figure and len(match_base) >= 2:
                figure_id = re.escape(figure.group(1))
                figure_match = any(
                    re.search(
                        rf"(?<![@｜]){re.escape(match_base[index:])}"
                        rf"\s*[（(]\s*图\s*{figure_id}(?!\d)",
                        match_text,
                    )
                    for index in range(len(match_base))
                    if len(match_base[index:]) >= 2
                )
            if len(match_base) < 2 or (
                match_base not in match_text
                and base not in match_text
                and not figure_match
            ):
                continue
            if image.get("id") not in ref_ids and len(refs) + len(voices) < max_refs:
                ref = {"nodeId": image["id"], "label": full_title}
                refs.append(ref)
                ref_ids.add(image["id"])
                added_node_ids.add(image["id"])
                pair = (image["id"], video["id"])
                if pair not in existing_pairs:
                    connections.append({"fromNodeId": image["id"], "id": _rid_edge(), "toNodeId": video["id"]})
                    existing_pairs.add(pair)
                if f"@{full_title}" not in prompt:
                    repaired_prompt = _inline_asset_mention(prompt, match_base, full_title)
                    prompt = (
                        repaired_prompt
                        if repaired_prompt != prompt
                        else (prompt.rstrip() + f"\n【自动修复引用】@{full_title}").lstrip()
                    )
                added += 1
        if params.get("refs") != refs:
            params["refs"] = refs
            meta["refs"] = refs
        for audio in audio_assets:
            full_title = str(audio.get("title") or "")
            role = _asset_title_base(full_title, audio=True).split("·", 1)[0]
            if role not in speaking_roles or audio.get("id") in voice_ids:
                continue
            if len(voices) >= max_audios:
                continue
            voice = {"nodeId": audio["id"], "label": full_title}
            voices.append(voice)
            voice_ids.add(audio["id"])
            added_node_ids.add(audio["id"])
            pair = (audio["id"], video["id"])
            if pair not in existing_pairs:
                connections.append({"fromNodeId": audio["id"], "id": _rid_edge(), "toNodeId": video["id"]})
                existing_pairs.add(pair)
            added += 1
        if voices:
            params["voiceRefs"] = voices
            params["voiceRef"] = voices[0]
            meta["voiceRefs"] = voices
        meta["prompt"] = prompt
        image_ids = {
            ref.get("nodeId") for ref in refs
            if (node_by_id.get(ref.get("nodeId")) or {}).get("type") == "image"
        }
        refs = sorted(refs, key=lambda ref: 0 if ref.get("nodeId") in image_ids else 1)
        voices = voices[:max_audios]
        while refs and len(refs) + len(voices) > max_refs:
            refs.pop()
        params["refs"] = refs
        params["voiceRefs"] = voices
        params["voiceRef"] = voices[0] if voices else None
        meta["refs"] = refs
        meta["voiceRefs"] = voices
        allowed_sources = {ref.get("nodeId") for ref in refs + voices}
        connections[:] = [
            conn for conn in connections
            if not (
                isinstance(conn, dict)
                and conn.get("toNodeId") == video.get("id")
                and conn.get("fromNodeId") not in allowed_sources
            )
        ]
        existing_pairs = {(conn.get("fromNodeId"), conn.get("toNodeId")) for conn in connections if isinstance(conn, dict)}
    if added_node_ids:
        group_node_ids.extend(sorted(added_node_ids))
    original_group_node_count = len(group.get("nodeIds") or [])
    group_node_ids = [
        node_id for node_id in dict.fromkeys(group_node_ids)
        if node_id not in duplicate_asset_ids
    ]
    group["nodeIds"] = group_node_ids
    group["videoNodeIds"] = group_video_ids
    group["connectionIds"] = [
        conn.get("id") for conn in connections
        if isinstance(conn, dict) and conn.get("toNodeId") in {video.get("id") for video in videos}
    ]
    if len(group_node_ids) != original_group_node_count:
        cleanup_changes += 1
    actual_groups = payload.get("episodeImports") or []
    actual_group = next(
        (
            item for item in actual_groups
            if str(item.get("id") or "").upper() == str(group.get("id") or "").upper()
        ),
        None,
    )
    if actual_group is None:
        payload["episodeImports"] = groups
    else:
        previous_ids = [
            node_id
            for node_id in (actual_group.get("nodeIds") or [])
            if node_id not in node_by_id
            or node_by_id[node_id].get("type") not in {"image", "audio"}
            or available_for_episode(node_by_id[node_id])
        ]
        actual_group.update(group)
        actual_group["nodeIds"] = list(dict.fromkeys([
            *previous_ids,
            *(group.get("nodeIds") or []),
        ]))
    return added + cleanup_changes


def _prune_orphan_duplicate_assets(payload):
    """Remove idle, unconnected duplicates left by older cross-episode imports."""
    nodes = [node for node in payload.get("nodes") or [] if isinstance(node, dict)]
    groups = _ensure_episode_imports(payload)
    grouped_ids = {
        node_id
        for group in groups
        for node_id in (group.get("nodeIds") or [])
    }
    connected_ids = {
        node_id
        for conn in payload.get("connections") or []
        if isinstance(conn, dict)
        for node_id in (conn.get("fromNodeId"), conn.get("toNodeId"))
        if node_id
    }
    grouped_keys = set()
    grouped_scene_bases = set()
    for node in nodes:
        if node.get("id") not in grouped_ids or node.get("type") not in {"image", "audio"}:
            continue
        meta = node.get("metadata") or {}
        key = str(meta.get("assetKey") or _asset_title_base(
            node.get("title"),
            audio=node.get("type") == "audio",
        ))
        if key:
            grouped_keys.add(key)
        scene = re.match(r"^场景｜(.+?)·母版（图\s*(\d+)）$", str(node.get("title") or ""))
        if scene:
            grouped_scene_bases.add((scene.group(1), scene.group(2)))
    removed = set()
    for node in nodes:
        node_id = node.get("id")
        node_type = node.get("type")
        if not node_id or node_id in grouped_ids or node_id in connected_ids or node_type not in {"image", "audio"}:
            continue
        meta = node.get("metadata") or {}
        if meta.get("status") == "done" or str(meta.get("content") or "").strip():
            continue
        key = str(meta.get("assetKey") or _asset_title_base(
            node.get("title"),
            audio=node_type == "audio",
        ))
        scene = re.match(r"^场景｜(.+?)（图\s*(\d+)）$", str(node.get("title") or ""))
        if (key and key in grouped_keys) or (
            scene and (scene.group(1), scene.group(2)) in grouped_scene_bases
        ):
            removed.add(node_id)
    if removed:
        payload["nodes"] = [
            node for node in payload.get("nodes") or []
            if not (isinstance(node, dict) and node.get("id") in removed)
        ]
    return len(removed)


def bind_shared_assets_for_all_episodes(payload):
    removed_grids = _remove_redundant_scene_grids(payload)
    total = removed_grids + _repair_reused_asset_identities(payload)
    total += repair_reused_asset_prompts(payload)
    total += _remove_unmentioned_video_refs(payload)
    total += repair_payload_prompt_mentions(payload)
    for group in _ensure_episode_imports(payload):
        total += bind_shared_assets_for_episode(payload, group.get("id"))
        total += _ensure_referenced_assets(payload, group.get("id"))
    total += _prune_orphan_duplicate_assets(payload)
    total += repair_payload_prompt_mentions(payload)
    if removed_grids:
        apply_import_layout(payload)
    return total


def merge_payload_by_episode(existing, incoming):
    """Merge one imported episode without replacing other episodes.

    Re-importing the same episode replaces only that episode's text/video segment
    nodes and connections. Generated image/audio assets are preserved by stable
    identity so later episodes continue using the same series assets.
    """
    existing = copy.deepcopy(existing)
    incoming = copy.deepcopy(incoming)
    if not isinstance(existing, dict) or not existing.get("nodes"):
        return incoming
    base = {**existing}
    old_groups = _ensure_episode_imports(existing)
    incoming_groups = _ensure_episode_imports(incoming)
    incoming_group = incoming_groups[0] if incoming_groups else {}
    episode_id = str(incoming.get("episodeId") or incoming_group.get("id") or "EP001").upper()
    same_old = next((group for group in old_groups if str(group.get("id") or "").upper() == episode_id), None)
    other_groups = [group for group in old_groups if group is not same_old]
    shared_by_other_episodes = {
        node_id for group in other_groups for node_id in (group.get("nodeIds") or [])
    }

    existing_nodes = [node for node in existing.get("nodes") or [] if isinstance(node, dict)]
    existing_by_id = {node.get("id"): node for node in existing_nodes if node.get("id")}
    incoming_nodes = [node for node in incoming.get("nodes") or [] if isinstance(node, dict)]

    same_old_ids = set((same_old or {}).get("nodeIds") or [])
    same_old_nodes = [
        existing_by_id[node_id]
        for node_id in same_old_ids
        if node_id in existing_by_id
    ]
    stable_node_remap = {}
    claimed_old_ids = set()
    for node in incoming_nodes:
        node_type = node.get("type")
        if node_type not in {"image", "text", "video"} or not node.get("id"):
            continue
        candidates = [
            old for old in same_old_nodes
            if old.get("type") == node_type and old.get("id") not in claimed_old_ids
        ]
        old = None
        if node_type == "image":
            asset_key = str((node.get("metadata") or {}).get("assetKey") or "")
            if asset_key:
                old = next(
                    (
                        candidate for candidate in candidates
                        if str((candidate.get("metadata") or {}).get("assetKey") or "") == asset_key
                    ),
                    None,
                )
            if old is None:
                old = next(
                    (
                        candidate for candidate in candidates
                        if str(candidate.get("title") or "") == str(node.get("title") or "")
                    ),
                    None,
                )
        elif node_type == "text":
            if len(candidates) == 1:
                old = candidates[0]
            else:
                old = next(
                    (
                        candidate for candidate in candidates
                        if str(((candidate.get("metadata") or {}).get("episodeId")) or "").upper() == episode_id
                    ),
                    None,
                )
        else:
            segment = re.search(r"段\s*(\d+)", str(node.get("title") or ""))
            if segment:
                old = next(
                    (
                        candidate for candidate in candidates
                        if re.search(r"段\s*(\d+)", str(candidate.get("title") or ""))
                        and re.search(r"段\s*(\d+)", str(candidate.get("title") or "")).group(1)
                        == segment.group(1)
                    ),
                    None,
                )
            if old is None:
                old = next(
                    (
                        candidate for candidate in candidates
                        if str(candidate.get("title") or "") == str(node.get("title") or "")
                    ),
                    None,
                )
        if old and old.get("id") != node.get("id"):
            stable_node_remap[str(node["id"])] = str(old["id"])
            node["id"] = old["id"]
        if old:
            claimed_old_ids.add(str(old["id"]))

    # Audio identity is role-based across the whole series. Reuse an existing
    # generated voice node instead of creating a new random audio node each import.
    audio_by_role = {}
    for node in existing_nodes:
        if node.get("type") != "audio":
            continue
        title = str(node.get("title") or "").strip()
        role = _asset_title_base(title, audio=True).split("·", 1)[0]
        if not role:
            continue
        figure = re.search(r"图\s*(\d+)", title)
        figure_number = int(figure.group(1)) if figure else 9999
        meta = node.get("metadata") or {}
        score = (
            meta.get("status") == "done",
            bool(str(meta.get("content") or "").strip()),
            -figure_number,
        )
        current = audio_by_role.get(role)
        if current is None or score > current[0]:
            audio_by_role[role] = (score, node)
    audio_remap = {}
    for node in list(incoming_nodes):
        if node.get("type") != "audio":
            continue
        title = str(node.get("title") or "").strip()
        role = _asset_title_base(title, audio=True).split("·", 1)[0]
        old = (audio_by_role.get(role) or (None, None))[1]
        if old and old.get("id") != node.get("id"):
            old_meta = old.setdefault("metadata", {})
            new_meta = node.get("metadata") or {}
            restored = _restore_generated_asset_prompt(
                old_meta,
                "audio",
                old.get("title"),
            )
            old_prompt = str(old_meta.get("prompt") or "").strip()
            new_prompt = str(new_meta.get("prompt") or "").strip()
            has_content = bool(str(old_meta.get("content") or "").strip())
            incoming_placeholder = _is_placeholder_asset_prompt(new_prompt)
            cross_episode_reuse = str(old.get("id") or "") in shared_by_other_episodes
            preserve_generated_prompt = bool(
                (has_content or (old_prompt and not _is_placeholder_asset_prompt(old_prompt)))
                and (incoming_placeholder or cross_episode_reuse)
            )
            if preserve_generated_prompt:
                prompt_changed = bool(
                    not incoming_placeholder
                    and old_prompt
                    and new_prompt
                    and old_prompt != new_prompt
                )
                if prompt_changed:
                    old_meta["promptStale"] = True
                    old_meta["promptStaleReason"] = "源音色提示词已更新，当前参考音频仍是旧版本"
                    old_meta["sourcePromptUpdatedAt"] = _now_iso()
                elif restored:
                    old_meta["promptStale"] = False
                    old_meta["promptStaleReason"] = ""
                    old_meta.pop("sourcePromptUpdatedAt", None)
            else:
                old_meta["prompt"] = new_prompt or old_prompt
                old_meta["composerContent"] = (
                    str(new_meta.get("composerContent") or "").strip()
                    or old_meta.get("composerContent")
                    or old_meta["prompt"]
                )
                prompt_changed = bool(
                    has_content and old_prompt and new_prompt and old_prompt != new_prompt
                )
                old_meta["promptStale"] = bool(
                    prompt_changed or (has_content and old_meta.get("promptStale"))
                )
                old_meta["promptStaleReason"] = (
                    "源音色提示词已更新，当前参考音频仍是旧版本"
                    if old_meta["promptStale"] else ""
                )
                if old_meta["promptStale"]:
                    old_meta["sourcePromptUpdatedAt"] = _now_iso()
            if not has_content and isinstance(new_meta.get("params"), dict):
                old_meta["params"] = {
                    **(old_meta.get("params") or {}),
                    **new_meta["params"],
                }
            audio_remap[str(node.get("id"))] = old.get("id")
            incoming_nodes.remove(node)

    reuse_remap = {**stable_node_remap, **audio_remap}
    incoming_ids_after_remap = {
        str(node.get("id"))
        for node in incoming_nodes
        if node.get("id")
    } | {str(node_id) for node_id in audio_remap.values() if node_id}
    removed_ids = {
        node_id for node_id in same_old_ids
        if node_id
        and node_id not in shared_by_other_episodes
        and node_id not in incoming_ids_after_remap
    }
    reused_asset_keys = set((incoming.get("importSummary") or {}).get("reusedAssetKeys") or [])
    reused_generated_asset_keys = set(
        (incoming.get("importSummary") or {}).get("reusedGeneratedAssetKeys") or []
    )
    for node in incoming_nodes:
        if node.get("type") not in {"image", "audio"}:
            continue
        node_id = str(node.get("id") or "")
        old = existing_by_id.get(node_id)
        if not old:
            continue
        old_meta = old.get("metadata") or {}
        key = str(
            old_meta.get("assetKey")
            or _asset_title_base(old.get("title"), audio=old.get("type") == "audio")
        )
        if not key:
            continue
        reused_asset_keys.add(key)
        if str(old_meta.get("content") or "").strip():
            reused_generated_asset_keys.add(key)
    for node_id in audio_remap.values():
        old = existing_by_id.get(str(node_id))
        if not old:
            continue
        old_meta = old.get("metadata") or {}
        key = str(
            old_meta.get("assetKey")
            or _asset_title_base(old.get("title"), audio=old.get("type") == "audio")
        )
        if not key:
            continue
        reused_asset_keys.add(key)
        if str(old_meta.get("content") or "").strip():
            reused_generated_asset_keys.add(key)

    for node in incoming_nodes:
        meta = node.get("metadata") or {}
        params = meta.get("params") or {}
        for key in ("refs", "voiceRefs"):
            items = params.get(key) or meta.get(key) or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                source_id = str(item.get("nodeId") or "")
                if source_id in reuse_remap:
                    item["nodeId"] = reuse_remap[source_id]
            if key in params:
                params[key] = items
            if key in meta:
                meta[key] = items
        voice_ref = params.get("voiceRef")
        if isinstance(voice_ref, dict):
            source_id = str(voice_ref.get("nodeId") or "")
            if source_id in reuse_remap:
                voice_ref["nodeId"] = reuse_remap[source_id]

    old_conns = existing.get("connections") or []
    old_pair_ids = {
        (str(conn.get("fromNodeId") or ""), str(conn.get("toNodeId") or "")): str(conn.get("id"))
        for conn in old_conns
        if isinstance(conn, dict) and conn.get("id")
    }
    incoming_conns = []
    for conn in incoming.get("connections") or []:
        if not isinstance(conn, dict):
            continue
        rewritten = dict(conn)
        rewritten["fromNodeId"] = reuse_remap.get(str(rewritten.get("fromNodeId")), rewritten.get("fromNodeId"))
        rewritten["toNodeId"] = reuse_remap.get(str(rewritten.get("toNodeId")), rewritten.get("toNodeId"))
        pair = (str(rewritten.get("fromNodeId") or ""), str(rewritten.get("toNodeId") or ""))
        rewritten["id"] = old_pair_ids.get(pair) or rewritten.get("id") or _rid_edge()
        incoming_conns.append(rewritten)

    merged_by_id = {
        node_id: node
        for node_id, node in existing_by_id.items()
        if node_id not in removed_ids
    }
    generated_keys = {
        "content", "status", "production", "taskId", "voiceId", "lastError", "lastNote",
        "generatedAt", "generationStartedAt", "generationFinishedAt", "providerTaskStatus",
        "providerTaskPhase", "providerTaskProgress", "actualDuration", "videoValidation",
        "reusedAsset",
    }
    for node in incoming_nodes:
        node_id = node.get("id")
        old = existing_by_id.get(node_id)
        if not old:
            merged_by_id[node_id] = node
            continue
        old_meta = old.get("metadata") or {}
        new_meta = node.get("metadata") or {}
        restored = _restore_generated_asset_prompt(old_meta, node.get("type"), old.get("title"))
        merged_meta = {**old_meta, **new_meta}
        old_prompt = str(old_meta.get("prompt") or "").strip()
        new_prompt = str(new_meta.get("prompt") or "").strip()
        has_content = bool(str(old_meta.get("content") or "").strip())
        has_detailed_prompt = bool(
            old_prompt and not _is_placeholder_asset_prompt(old_prompt)
        )
        if node.get("type") in {"image", "audio"} and (has_content or has_detailed_prompt):
            incoming_placeholder = _is_placeholder_asset_prompt(new_prompt)
            cross_episode_reuse = str(old.get("id") or "") in shared_by_other_episodes
            if incoming_placeholder or cross_episode_reuse:
                merged_meta["prompt"] = old_prompt
                merged_meta["composerContent"] = (
                    old_meta.get("composerContent")
                    or old_prompt
                )
                merged_meta["autoPlaceholder"] = False
                prompt_changed = bool(
                    not incoming_placeholder
                    and old_prompt
                    and new_prompt
                    and old_prompt != new_prompt
                )
                if prompt_changed:
                    merged_meta["promptStale"] = True
                    merged_meta["promptStaleReason"] = (
                        "源音色提示词已更新，当前参考音频仍是旧版本"
                        if node.get("type") == "audio"
                        else "源提示词已更新，当前复用图片仍是旧版本"
                    )
                    merged_meta["sourcePromptUpdatedAt"] = _now_iso()
                elif restored:
                    merged_meta["promptStale"] = False
                    merged_meta["promptStaleReason"] = ""
                    merged_meta.pop("sourcePromptUpdatedAt", None)
            else:
                if old_prompt and new_prompt and old_prompt != new_prompt:
                    merged_meta["promptStale"] = True
                    merged_meta["promptStaleReason"] = (
                        "源音色提示词已更新，当前参考音频仍是旧版本"
                        if node.get("type") == "audio"
                        else "源提示词已更新，当前复用图片仍是旧版本"
                    )
                    merged_meta["sourcePromptUpdatedAt"] = _now_iso()
                elif old_meta.get("promptStale"):
                    merged_meta["promptStale"] = True
                    merged_meta["promptStaleReason"] = (
                        old_meta.get("promptStaleReason")
                        or (
                            "源音色提示词已更新，当前参考音频仍是旧版本"
                            if node.get("type") == "audio"
                            else "源提示词已更新，当前复用图片仍是旧版本"
                        )
                    )
        for key in generated_keys:
            if old_meta.get(key) not in (None, "", [], {}):
                merged_meta[key] = old_meta[key]
        if isinstance(old_meta.get("params"), dict) and isinstance(new_meta.get("params"), dict):
            merged_meta["params"] = {
                **new_meta["params"],
                **{
                    key: value for key, value in old_meta["params"].items()
                    if key in {
                        "mediaVersions",
                        "mediaVersionIndex",
                        "activeVersionId",
                        "videoVersions",
                        "videoVersionIndex",
                        "thumbUrl",
                    }
                },
            }
        merged_by_id[node_id] = {
            **old,
            **node,
            "position": old.get("position") or node.get("position"),
            "createdAt": old.get("createdAt") or node.get("createdAt"),
            "metadata": merged_meta,
            "updatedAt": _now_iso(),
        }

    valid_node_ids = set(merged_by_id)
    removed_conn_ids = set((same_old or {}).get("connectionIds") or [])
    merged_conns = {
        conn.get("id"): conn for conn in old_conns
        if isinstance(conn, dict)
        and conn.get("id") not in removed_conn_ids
        and conn.get("fromNodeId") in valid_node_ids
        and conn.get("toNodeId") in valid_node_ids
    }
    for conn in incoming_conns:
        if conn.get("fromNodeId") in valid_node_ids and conn.get("toNodeId") in valid_node_ids:
            merged_conns[conn.get("id") or _rid_edge()] = conn

    imported_now = _now_iso()
    # every node used by this episode, including reused series assets.
    # A reused audio/image node is still part of the current episode when a
    # video references it; omitting it makes the asset exist in the payload but
    # disappear from the episode canvas.
    episode_referenced_ids = set()
    for connection in incoming_conns:
        target_id = str(connection.get("toNodeId") or "")
        source_id = str(connection.get("fromNodeId") or "")
        if target_id in set(incoming_group.get("videoNodeIds") or []):
            episode_referenced_ids.add(source_id)
    for node in incoming_nodes:
        if node.get("type") != "video":
            continue
        meta = node.get("metadata") or {}
        params = meta.get("params") or {}
        for key in ("refs", "voiceRefs"):
            for item in [*(params.get(key) or []), *(meta.get(key) or [])]:
                if isinstance(item, dict) and item.get("nodeId"):
                    episode_referenced_ids.add(str(item["nodeId"]))

    incoming_group = {
        **incoming_group,
        "id": episode_id,
        "nodeIds": list(dict.fromkeys([
            *[node.get("id") for node in incoming_nodes if node.get("id")],
            *[node_id for node_id in reuse_remap.values() if node_id],
            *[
                reuse_remap.get(node_id, node_id)
                for node_id in episode_referenced_ids
                if existing_by_id.get(reuse_remap.get(node_id, node_id))
                or node_id in {node.get("id") for node in incoming_nodes}
            ],
        ])),
        "videoNodeIds": [node.get("id") for node in incoming_nodes if node.get("type") == "video"],
        "connectionIds": [conn.get("id") for conn in incoming_conns if conn.get("id")],
        "firstImportedAt": (
            str((same_old or {}).get("firstImportedAt") or "").strip()
            or str((same_old or {}).get("importedAt") or "").strip()
            or imported_now
        ),
        "lastImportedAt": imported_now,
        "importedAt": imported_now,
    }
    merged_groups = sorted(
        [*other_groups, incoming_group],
        key=lambda group: str(group.get("id") or ""),
    )
    series_title = (
        str(existing.get("title") or incoming.get("title") or "画布工作流")
        if not other_groups
        else _series_title(existing.get("title") or incoming.get("title"))
    )
    merged_payload = {
        **base,
        "title": series_title,
        "nodes": list(merged_by_id.values()),
        "connections": list(merged_conns.values()),
        "episodeImports": merged_groups,
        "episodeId": episode_id,
        "episodeTitle": incoming.get("episodeTitle") or incoming_group.get("title") or episode_id,
        "importSummary": {
            **(incoming.get("importSummary") or {}),
            "assetsReused": len(reused_asset_keys),
            "assetsReusedGenerated": len(reused_generated_asset_keys),
            "reusedAssetKeys": sorted(reused_asset_keys),
            "reusedGeneratedAssetKeys": sorted(reused_generated_asset_keys),
        },
        "updatedAt": _now_iso(),
    }
    removed_grids = _remove_redundant_scene_grids(merged_payload)
    _repair_reused_asset_identities(merged_payload)
    repair_dangling_media_references(merged_payload)
    _remove_unmentioned_video_refs(merged_payload)
    repair_payload_prompt_mentions(merged_payload)
    bind_shared_assets_for_episode(merged_payload, episode_id)
    _ensure_referenced_assets(merged_payload, episode_id)
    _prune_orphan_duplicate_assets(merged_payload)
    repair_payload_prompt_mentions(merged_payload)
    repair_reused_asset_prompts(merged_payload)
    for connection in merged_payload.get("connections") or []:
        if not isinstance(connection, dict):
            continue
        pair = (
            str(connection.get("fromNodeId") or ""),
            str(connection.get("toNodeId") or ""),
        )
        stable_edge_id = old_pair_ids.get(pair)
        if stable_edge_id:
            connection["id"] = stable_edge_id
    merged_episode = next(
        (
            group for group in merged_payload.get("episodeImports") or []
            if str(group.get("id") or "").upper() == episode_id
        ),
        None,
    )
    if merged_episode:
        merged_video_ids = set(merged_episode.get("videoNodeIds") or [])
        merged_episode["connectionIds"] = [
            connection.get("id")
            for connection in merged_payload.get("connections") or []
            if isinstance(connection, dict)
            and connection.get("toNodeId") in merged_video_ids
        ]
        final_asset_nodes = [
            node
            for node in merged_payload.get("nodes") or []
            if isinstance(node, dict)
            and node.get("id") in set(merged_episode.get("nodeIds") or [])
            and node.get("type") in {"image", "audio"}
        ]
        reused_final_nodes = [
            node for node in final_asset_nodes
            if (
                node.get("id") in existing_by_id
                or (node.get("metadata") or {}).get("reusedAsset")
            )
        ]
        reused_final_keys = {
            str(
                (node.get("metadata") or {}).get("assetKey")
                or _asset_title_base(
                    node.get("title"),
                    audio=node.get("type") == "audio",
                )
            )
            for node in reused_final_nodes
        }
        reused_final_keys.discard("")
        reused_generated_keys = {
            str(
                (node.get("metadata") or {}).get("assetKey")
                or _asset_title_base(
                    node.get("title"),
                    audio=node.get("type") == "audio",
                )
            )
            for node in reused_final_nodes
            if str((node.get("metadata") or {}).get("content") or "").strip()
        }
        reused_generated_keys.discard("")
        merged_payload["importSummary"] = {
            **(merged_payload.get("importSummary") or {}),
            "assetsReused": len(reused_final_nodes),
            "assetsReusedGenerated": sum(
                1
                for node in reused_final_nodes
                if str((node.get("metadata") or {}).get("content") or "").strip()
            ),
            "reusedAssetKeys": sorted(reused_final_keys),
            "reusedGeneratedAssetKeys": sorted(reused_generated_keys),
        }
    if removed_grids:
        apply_import_layout(merged_payload)
    elif merged_payload.get("layoutMode") == "type-columns-v2":
        apply_import_layout(merged_payload, [episode_id])
    else:
        apply_import_layout(merged_payload)
    _repair_episode_asset_membership(merged_payload, episode_id)
    return merged_payload


def _repair_episode_asset_membership(payload, episode_id):
    """Attach every asset actually referenced by this episode's videos.

    Import reuse deliberately keeps one stable audio/image node across the
    series. Older imports could retain the node globally but omit it from
    `episodeImports[].nodeIds`; the canvas then queried the episode and never
    saw the reused voice. Membership is derived from the videos' persisted
    refs/voiceRefs, which are the authoritative generation contract.
    """
    if not isinstance(payload, dict):
        return 0
    target = str(episode_id or payload.get("episodeId") or "").strip().upper()
    if not target:
        return 0
    groups = _ensure_episode_imports(payload)
    group = next(
        (
            item for item in groups
            if str(item.get("id") or "").strip().upper() == target
        ),
        None,
    )
    if group is None:
        return 0
    nodes = {
        str(node.get("id") or ""): node
        for node in payload.get("nodes") or []
        if isinstance(node, dict) and node.get("id")
    }
    video_ids = [
        str(node_id) for node_id in (group.get("videoNodeIds") or [])
        if node_id
    ]
    referenced = set()
    for node_id in video_ids:
        meta = (nodes.get(node_id) or {}).get("metadata") or {}
        params = meta.get("params") or {}
        for key in ("refs", "voiceRefs"):
            for item in [*(params.get(key) or []), *(meta.get(key) or [])]:
                if isinstance(item, dict):
                    source_id = str(item.get("nodeId") or "").strip()
                    if source_id and source_id in nodes:
                        referenced.add(source_id)
        for key in ("voiceRef",):
            item = params.get(key) or meta.get(key)
            if isinstance(item, dict):
                source_id = str(item.get("nodeId") or "").strip()
                if source_id and source_id in nodes:
                    referenced.add(source_id)
    if not referenced:
        return 0
    current = [str(item) for item in (group.get("nodeIds") or []) if item]
    merged = list(dict.fromkeys([*current, *sorted(referenced)]))
    added = [node_id for node_id in merged if node_id not in set(current)]
    if added:
        group["nodeIds"] = merged
        actual = next(
            (
                item for item in payload.get("episodeImports") or []
                if str(item.get("id") or "").strip().upper() == target
            ),
            group,
        )
        actual["nodeIds"] = merged
    return len(added)
