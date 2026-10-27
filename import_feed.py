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
import math
import re
import secrets
import string
import time
from datetime import datetime, timezone

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


# 五区列 x 坐标（按卡片真实尺寸紧密排布）
COL_ROLE, COL_SCENE, COL_PROP, COL_AUDIO, COL_VIDEO = 0, 320, 640, 960, 1240

# 台词行判定（有对白 → 才生成音色）
_DIALOG_RE = re.compile(r"台词[:：]\s*([^（(：:]{1,10}?)\s*（[^）)]*?）\s*[:：]\s*[\"“『]")
_REF_RE = re.compile(r"@//(?:chr|scn|prp)\s*/\s*-\s*([^（(]+)（(?:参考)?图\s*(\d+)")
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


def _asset_kind_from_title(title):
    raw = str(title or "")
    if raw.startswith("角色｜") or raw.startswith("角色|"):
        return "角色"
    if raw.startswith("场景｜") or raw.startswith("场景|"):
        return "场景"
    if raw.startswith("道具｜") or raw.startswith("道具|"):
        return "道具"
    return ""


def _asset_identity(title, kind=""):
    text = str(title or "").strip()
    text = re.sub(r"^[^｜|]*[｜|]\s*", "", text)
    text = re.sub(r"[（(]\s*图\s*\d+\s*[)）]\s*$", "", text)
    text = re.sub(r"[（(](?:角色立绘|场景母版|九宫格|道具设定图)[)）]\s*$", "", text)
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


def _node_source_ready(node):
    if not isinstance(node, dict):
        return False
    meta = node.get("metadata") or {}
    if str(node.get("type") or "") == "text":
        return bool(str(meta.get("prompt") or meta.get("content") or "").strip())
    if str(node.get("type") or "") in {"image", "audio"} and meta.get("promptStale"):
        return False
    return bool(str(meta.get("content") or "").strip())


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
        figure = re.compile(rf"[（(]图\s*{re.escape(code)}[）)]")
        source = next(
            (
                item for item in nodes.values()
                if str(item.get("type") or "") == "image"
                and figure.search(str(item.get("title") or ""))
            ),
            None,
        )
        if not source:
            blockers.append({
                "nodeId": "",
                "title": f"图{code}",
                "kind": "image",
                "reason": "提示词引用但节点不存在",
            })
            continue
        source_id = str(source.get("id") or "")
        if source_id not in bound_ids:
            blockers.append({
                "nodeId": source_id,
                "title": str(source.get("title") or f"图{code}"),
                "kind": "image",
                "status": str((source.get("metadata") or {}).get("status") or "idle"),
                "reason": "提示词已引用但未绑定参考素材",
            })
    for role, code in re.findall(
        r"@音频｜([^（(\n；]+)[（(](?:参考)?图\s*(\d+)[）)]",
        prompt,
    ):
        role = role.strip()
        source = next(
            (
                item for item in nodes.values()
                if str(item.get("type") or "") == "audio"
                and role in str(item.get("title") or "")
                and re.search(rf"[（(]图\s*{re.escape(code)}[）)]", str(item.get("title") or ""))
            ),
            None,
        )
        if not source:
            blockers.append({
                "nodeId": "",
                "title": f"音频｜{role}（图{code}）",
                "kind": "audio",
                "reason": "提示词引用但音色节点不存在",
            })
            continue
        source_id = str(source.get("id") or "")
        if source_id not in bound_ids:
            blockers.append({
                "nodeId": source_id,
                "title": str(source.get("title") or f"音频｜{role}（图{code}）"),
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
            "reason": "引用节点不存在" if not source else "尚未生成",
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
        r"[（(](?:角色立绘|场景母版|九宫格|道具设定图)[）)]\s*$",
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
        code = f"图{figure.group(1)}"
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
    code = f"图{figure}"
    candidates = [
        str(item.get("label") or "")
        for item in refs or []
        if isinstance(item, dict) and code in str(item.get("label") or "")
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
        canonical_code = re.search(r"图\s*(\d+)", title)
        alias_codes = set(re.findall(
            rf"{re.escape(base)}\s*[（(]\s*图\s*(\d+)",
            repaired,
        ))
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
        if canonical_code:
            for alias_code in alias_codes:
                if alias_code == canonical_code.group(1):
                    continue
                repaired = re.sub(
                    rf"图\s*{re.escape(alias_code)}(?!\d)",
                    f"图{canonical_code.group(1)}",
                    repaired,
                )

    # A unique figure code is stronger than an imprecise display alias:
    # e.g. "纸条（图32）" must resolve to 道具｜匿名纸条（图32）.
    refs_by_code = {}
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        code_match = re.search(r"图\s*(\d+)", str(ref.get("label") or ""))
        if code_match:
            refs_by_code.setdefault(code_match.group(1), []).append(
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
        titles = refs_by_code.get(match.group("code")) or []
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
        title, prefix = _resolve_figure_ref(match.group("name"), match.group("figure"), refs)
        return f"{prefix}@{title}" if title else match.group(0)

    repaired = _PLAIN_FIGURE_MENTION_RE.sub(replace_plain, repaired)
    repaired = re.sub(r"\s*[（(]\s*model\s*=\s*[^）)]*[）)]", "", repaired, flags=re.IGNORECASE)
    return repaired


def _ensure_audio_mentions(prompt, voices):
    """Keep concrete voice nodes visible in the prompt, not only in voiceRefs."""
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
        return prompt
    section = "【音色引用】" + "；".join(labels)
    text = str(prompt or "")
    existing = re.compile(r"(?m)^【音色引用】[^\n]*(?:\n|$)")
    text = existing.sub("", text, count=1)
    text = re.sub(r"\n{3,}", "\n\n", text)
    core = re.search(r"(?m)^【核心人物】[^\n]*(?:\n|$)", text)
    if core:
        return text[:core.end()] + "\n" + section + "\n" + text[core.end():]
    for marker in ("\n\n【音效】", "\n\n【强制禁止项】"):
        if marker in text:
            return text.replace(marker, "\n\n" + section + marker, 1)
    return text.rstrip() + "\n\n" + section + "\n"


def repair_payload_prompt_mentions(payload):
    """幂等修复已导入视频节点的提示词；返回发生变更的节点数。"""
    changed = 0
    for node in payload.get("nodes", []) or []:
        if not isinstance(node, dict) or node.get("type") != "video":
            continue
        meta = node.get("metadata") or {}
        params = meta.get("params") or {}
        refs = params.get("refs") or meta.get("refs") or []
        voices = params.get("voiceRefs") or meta.get("voiceRefs") or []
        node_changed = False
        for key in ("prompt", "composerContent"):
            value = str(meta.get(key) or "")
            if not value:
                continue
            repaired = _ensure_audio_mentions(
                repair_prompt_mentions(value, refs),
                voices,
            )
            if repaired != value:
                meta[key] = repaired
                node_changed = True
        if node_changed:
            changed += 1
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


def build_payload(feed, asset_pool=None, target_project_id=""):
    """投喂载荷 → 画布 payload dict。"""
    assets = feed.get("asset_prompts", [])      # 9 项（图6 ×2）
    segs = feed.get("segments", [])              # N 段
    meta = feed.get("meta", {}) or {}
    p0a = feed.get("p0a") or {}
    if isinstance(p0a, list):
        p0a = (p0a[0] if p0a and isinstance(p0a[0], dict) else {})
    script = feed.get("script", "")
    contract = feed.get("canvas_contract") if isinstance(feed.get("canvas_contract"), dict) else {}
    reusable_assets = _asset_pool(asset_pool, target_project_id)
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
    title = feed.get("title") or meta.get("title") or "画布工作流"
    if title and "画布工作流" not in title:
        title = f"{title} · 画布工作流"
    now0 = _now_iso()
    episode_id, episode_title = _episode_identity(feed)
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
    col_x = {"角色": COL_ROLE, "场景": COL_SCENE, "道具": COL_PROP}
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
        if canvas_kind in {"character", "scene", "prop"}:
            kind = {"character": "角色", "scene": "场景", "prop": "道具"}[canvas_kind]
            short_name = _canvas_asset_name(
                name,
                canvas_asset.get("name"),
                kind,
            ) or _short_asset_name(name)
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
        cross_project = [
            item for item in reusable_assets
            if item.get("assetKey") == asset_key
            and item.get("projectId") != target_project_id
            and kind == "角色"
        ]
        candidates = same_project or cross_project
        candidates.sort(
            key=lambda item: (
                bool(str(item.get("content") or "").strip()),
                str(item.get("status") or "") == "done",
                str(item.get("updatedAt") or ""),
            ),
            reverse=True,
        )
        source_asset = candidates[0] if candidates else None
        if source_asset:
            reused_asset_keys.add(asset_key)
        same_project_reuse = bool(
            source_asset
            and source_asset.get("projectId") == target_project_id
        )
        nid = str(source_asset["id"]) if (source_asset and source_asset.get("projectId") == target_project_id) else _rid_node()
        image_ids.setdefault(code, []).append(nid)
        asset_kinds[nid] = kind
        visible_ratio = _aspect(
            canvas_asset.get("ratio") or a.get("ratio") or a.get("aspect") or raw,
            asset_aspect,
        )
        visible_size = _image_size(visible_ratio)
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
        if reused_content:
            reused_generated_asset_keys.add(asset_key)
        prompt_stale = bool(
            reused_content
            and previous_prompt
            and previous_prompt.strip() != image_prompt.strip()
        )
        image_params = {
            "provider": "ark", "model": image_model,
            "ratio": visible_ratio, "clarity": "1.5K", "size": visible_size,
            "guidance": "中等", "watermark": False,
        }
        if source_asset and isinstance(source_asset.get("params"), dict):
            image_params = {**image_params, **source_asset["params"]}
        reuse_meta = None
        if source_asset:
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
        nodes.append({
            "height": 405, "width": 720, "id": nid,
            "metadata": {
                "composerContent": composer, "content": reused_content, "count": 1,
                "model": image_model, "prompt": image_prompt, "quality": "high",
                "size": visible_size, "status": "done" if reused_content else "idle",
                "transparentBackground": "false", "nodeRole": "generator",
                "assetKey": asset_key, "reusedAsset": reuse_meta,
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
                "episodeId": episode_id,
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
        for _m in _DIALOG_RE.finditer(_s.get("raw", "")):
            speaking.add(_m.group(1).strip())
    character_names = {
        a.get("code", ""): _short_asset_name(a.get("name", ""))
        for a in assets
        if "角色立绘" in a.get("name", "")
    }
    audio_ids, audio_by_role = {}, {}
    audio_y = 0
    for a in assets:
        code, name, raw = a.get("code", ""), a.get("name", ""), a.get("raw", "")
        role = character_names.get(code, "")
        if not role or "角色立绘" not in name:
            continue
        if role not in speaking:
            continue
        voice = _extract_voice(raw)
        lm = re.search(r"Voice Description Label[^\n]*?(?::|\*\*)\s*([A-Za-z][^/\n]*)", raw)
        label = lm.group(1).strip() if lm else ""
        gm = re.search(r"一名\s*(\d+~?\d*)\s*岁\s*(男性|女性)", raw)
        age = gm.group(1) if gm else ""
        gender = "male" if (gm and gm.group(2) == "男性") else ("female" if (gm and gm.group(2) == "女性") else "")
        dialect = "northeastern-accented" if "northeastern" in voice.lower() else "standard"
        audio_prompt = (f"{label}. {voice} A {gender}"
                        + (f" in his {age}" if gender == "male" and age else (f" in her {age}" if gender == "female" and age else ""))
                        + f", speaking {dialect} Mandarin, short drama dubbing reference clip, 4.5 seconds clean voice, no background noise.")
        aid = _rid_audio()
        audio_ids[code], audio_by_role[role] = aid, aid
        audio_composer = f"角色：{role}（{code}）\n\n【音色自动提示词 · Audio Prompt · 4.5S 参考音色】\n{audio_prompt}"
        nodes.append({
            "height": 320, "width": 320, "id": aid,
            "metadata": {
                "content": "", "composerContent": audio_composer,
                "prompt": audio_prompt, "status": "idle", "generationMode": "audio", "nodeRole": "generator",
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

    # ---------- 4) connections ----------
    # 4.1 每段 → 该段引用的图
    for s in segs:
        no, raw = s.get("no", ""), s.get("raw", "")
        vid = video_ids.get(no)
        if not vid:
            continue
        seen = set()
        for _name, fig_no in _REF_RE.findall(raw):
            candidates = image_ids.get("图" + fig_no.strip(), [])
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
        speaking_in_segment = {
            match.group(1).strip()
            for match in _DIALOG_RE.finditer(raw)
        }
        seen_a = set()
        for _name, fig_no in _REF_RE.findall(raw):
            role = character_names.get("图" + fig_no.strip(), "")
            if role not in speaking_in_segment:
                continue
            role_audio = audio_ids.get("图" + fig_no.strip())
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
    if kind in {"role", "scene", "prop", "audio"}:
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
        "scene": (COL_SCENE, 360),
        "prop": (COL_PROP, 360),
        "audio": (COL_AUDIO, 200),
    }
    video_columns = 3
    video_x_step = 260
    video_y_step = 380
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
        match.group(1).strip()
        for match in _DIALOG_RE.finditer(str(prompt or ""))
        if match.group(1).strip() in known
    }
    for line in str(prompt or "").splitlines():
        if "台词" not in line or not re.search(r"\bOS\b|内心独白", line, re.IGNORECASE):
            continue
        before_dialogue = line.split("台词", 1)[0]
        ranked = sorted(
            (
                (before_dialogue.rfind(role), role)
                for role in known
                if role in before_dialogue
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
        current_score = (current_meta.get("status") == "done", bool(current_meta.get("content")), str(current.get("updatedAt") or ""))
        node_score = (node_meta.get("status") == "done", bool(node_meta.get("content")), str(node.get("updatedAt") or ""))
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
        speaking_roles = _prompt_speaking_roles(prompt, role_names)
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
                        prompt,
                    )
                    for index in range(len(match_base))
                    if len(match_base[index:]) >= 2
                )
            if len(match_base) < 2 or (
                match_base not in prompt
                and base not in prompt
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
                prompt = _inline_asset_mention(prompt, match_base, full_title)
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
    payload["episodeImports"] = groups
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
    total += _remove_unmentioned_video_refs(payload)
    total += repair_payload_prompt_mentions(payload)
    for group in _ensure_episode_imports(payload):
        total += bind_shared_assets_for_episode(payload, group.get("id"))
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
            old_prompt = str(old_meta.get("prompt") or "").strip()
            new_prompt = str(new_meta.get("prompt") or "").strip()
            has_content = bool(str(old_meta.get("content") or "").strip())
            old_meta["prompt"] = new_prompt or old_prompt
            old_meta["composerContent"] = (
                str(new_meta.get("composerContent") or "").strip()
                or old_meta.get("composerContent")
                or old_meta["prompt"]
            )
            prompt_changed = bool(
                has_content and old_prompt and new_prompt and old_prompt != new_prompt
            )
            old_meta["promptStale"] = bool(prompt_changed or (has_content and old_meta.get("promptStale")))
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
        merged_meta = {**old_meta, **new_meta}
        if node.get("type") in {"image", "audio"} and str(old_meta.get("content") or "").strip():
            old_prompt = str(old_meta.get("prompt") or "").strip()
            new_prompt = str(new_meta.get("prompt") or "").strip()
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
                    if key in {"videoVersions", "videoVersionIndex", "thumbUrl"}
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

    incoming_group = {
        **incoming_group,
        "id": episode_id,
        "nodeIds": list(dict.fromkeys([
            *[node.get("id") for node in incoming_nodes if node.get("id")],
            *[node_id for node_id in reuse_remap.values() if node_id],
        ])),
        "videoNodeIds": [node.get("id") for node in incoming_nodes if node.get("type") == "video"],
        "connectionIds": [conn.get("id") for conn in incoming_conns if conn.get("id")],
        "importedAt": _now_iso(),
    }
    merged_groups = sorted(
        [*other_groups, incoming_group],
        key=lambda group: str(group.get("id") or ""),
    )
    series_title = _series_title(existing.get("title") or incoming.get("title"))
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
    _remove_unmentioned_video_refs(merged_payload)
    repair_payload_prompt_mentions(merged_payload)
    bind_shared_assets_for_episode(merged_payload, episode_id)
    _prune_orphan_duplicate_assets(merged_payload)
    repair_payload_prompt_mentions(merged_payload)
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
    return merged_payload
