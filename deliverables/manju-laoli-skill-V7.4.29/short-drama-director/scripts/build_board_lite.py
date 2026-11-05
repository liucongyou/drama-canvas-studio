# -*- coding: utf-8 -*-
"""build_board_lite.py — 轻量离线分镜看板编译器（轻量版 · 简化自 7.3.0 模块 K）

用法:
    python scripts/build_board_lite.py <项目.md> [--out <看板.html>] [--export-json <画布载荷.json>] [--asset-ledger <目录或总账.json>]

产出:
    单文件离线 HTML 看板（CSS/JS 全内嵌，双击浏览器即开，零依赖/零数据库/零端口）

看板区块（与 6.8.3 同构，砍掉 N1~N11 检查层 / validate 依赖 / json 快照）:
    创作蓝图层: Ⅰ 创作基准(P0A) · Ⅱ 剧本层(P1) · Ⅲ 视听圣经(P1)
    分镜生产层: ① 时间轴泳道 · ② 镜头卡矩阵(拆解) · ③ 投喂提示词原文(成品)
                ④ 资产总表(P2) · ⑤ 资产出图提示词(P2.5·与③必同板) · ⑥ 发布文案(P6·可选)

输入 md 最小契约:
    # 项目标题
    【画幅】：16:9        【模型版本】：2.5        ← A/B 双锁回显
    ## P0A · 创作基准     ← 可选表格: 项 | 内容
    ## P1 · 剧本          ← 可选，正文照录
    ## P1 · 视听圣经      ← 可选表格或正文
    ## P2 · 资产清单      ← 表格: 编码 | 名字 | 视图 | @图片N
    ## P2.5 · 资产出图提示词
    ### CHR-001 · 林越 · 主角 4 View 资产板
    ```text 出图提示词 ```
    ## P4 · 投喂提示词    ← 分镜提示词（与 ⑤ 必同板呈现）
    ### 段1
    【核心人物】：…        ← 段级栏（可选，显示为标签）
    ```text 投喂提示词 ```
    [0-3秒] 镜头内容      ← 逐镜拆解行
    ## P6 · 发布文案      ← 可选: 【标题】【封面文案】【正文】【话题标签】

轻量铁律（简化自 6.8.3 K1~K7，只留 4 条）:
    K-lite-1  md 是唯一真相，看板只读不回写；改稿改 md 后重编译
    K-lite-2  缺字段降级不中断：缺什么显示"未解析到"并记 WARN，看板永远出得来
    K-lite-3  拆解视图与原文并存：逐镜表/镜头卡用于定位问题，围栏原文可整段复制投喂
    K-lite-4  ⑤ 资产出图提示词与 ③ 分镜投喂提示词必须同板呈现（缺 P2.5 → WARN）
"""

import argparse
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    from canvas_feed_contract import (
        AUDIO_PROMPT_FORMAT,
        AUDIO_PROMPT_SUFFIX,
        SCHEMA_VERSION,
    )
except ImportError:
    AUDIO_PROMPT_FORMAT = "audio-prompt-v1"
    AUDIO_PROMPT_SUFFIX = (
        "short drama dubbing reference clip, 4.5 seconds clean voice, no background noise."
    )
    SCHEMA_VERSION = "drama-canvas-feed/v2"

# 兼容三种镜行写法：本包原生 `00:00-00:05 [镜头1] …` / `[00:00-00:05] …` / `[0-3秒] …`
TS_RE = re.compile(
    r"^\s*(?:[\[【]?\s*镜头\s*\d+\s*[\]】]?\s*[,，]?\s*)?"
    r"\[?\s*(\d{1,2}(?::\d{2})?(?:\.\d+)?)\s*s?\s*[-~～—→]\s*"
    r"(\d{1,2}(?::\d{2})?(?:\.\d+)?)\s*(?:秒|s)?\s*\]?\s*[,，]?\s*(.*)$", re.I)
SHOT_NO_RE = re.compile(r"^[\[【]?\s*镜头\s*\d+\s*[\]】]?\s*[,，]?\s*")


def shot_text(s: str) -> str:
    """镜行文本：剥掉 `[镜头N] ` 前缀，卡片上只留景别·运镜·动作。"""
    return SHOT_NO_RE.sub("", (s or "").strip())
FENCE_RE = re.compile(r"```(?:text|prompt)?\s*\n(.*?)```", re.S)
FIELD_RE = re.compile(r"^【([^】]{2,8})】\s*[:：]\s*(.+)$")


def _canvas_aspect(value, default="16:9"):
    match = re.search(r"(21\s*[:：]\s*9|16\s*[:：]\s*9|9\s*[:：]\s*16|4\s*[:：]\s*3|3\s*[:：]\s*4|1\s*[:：]\s*1)", str(value or ""))
    return match.group(1).replace("：", ":").replace(" ", "") if match else default


def _canvas_video_model(value):
    raw = str(value or "").lower().replace("_", "-").replace(" ", "")
    if "2.5" in raw or "2-5" in raw:
        return "doubao-seedance-2-5"
    if "mini" in raw:
        return "doubao-seedance-2-0-mini"
    if "fast" in raw:
        return "doubao-seedance-2-0-fast"
    return "doubao-seedance-2-0"


def _canvas_asset_kind(name):
    text = str(name or "")
    if "角色" in text:
        return "character"
    if "场景" in text or "母版" in text or "九宫格" in text:
        return "scene"
    return "prop"


def _canvas_asset_name(name):
    return re.sub(
        r"[（(](?:角色立绘|场景母版|九宫格|道具设定图)[）)]\s*$",
        "",
        str(name or "").strip(),
    ).strip()


def _canvas_kind_label(kind):
    return {"character": "角色", "scene": "场景", "prop": "道具"}.get(kind, "资产")


def _canvas_figure_number(code):
    match = re.search(r"图\s*(\d+)", str(code or ""))
    return int(match.group(1)) if match else 999999


def _canvas_reference_code(asset):
    """Return the figure slot used by canvas references, never the semantic ID."""
    figure = re.search(r"图\s*\d+", str(asset.get("code") or ""))
    if figure:
        return re.sub(r"\s+", "", figure.group(0))
    figure = re.search(r"图\s*\d+", str(asset.get("img") or ""))
    if figure:
        return re.sub(r"\s+", "", figure.group(0))
    return str(asset.get("code") or "").strip()


def _canvas_semantic_code(asset):
    explicit = str(asset.get("semanticCode") or "").strip().upper()
    if re.fullmatch(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*", explicit):
        return explicit
    code = str(asset.get("code") or "").strip().upper()
    if re.fullmatch(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*", code):
        return code
    match = re.search(
        r"[（(]([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*)[）)]",
        str(asset.get("title") or ""),
    )
    return match.group(1) if match else ""


def _canvas_source_manifest(data):
    manifest = {}
    for asset in data.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        figure = _canvas_reference_code(asset)
        semantic = _canvas_semantic_code(asset)
        if not semantic and re.fullmatch(r"图\s*\d+", figure):
            kind = _canvas_asset_kind(asset.get("name"))
            prefix = {
                "character": "CHR",
                "scene": "SCN",
                "prop": "PRP",
            }.get(kind, "AST")
            semantic = f"{prefix}-{int(figure[1:]):03d}"
            asset["semanticCode"] = semantic
        if semantic and re.fullmatch(r"图\s*\d+", figure):
            manifest[semantic] = re.sub(r"\s+", "", figure)
    return manifest


def validate_export_contract(data):
    """Reject exports whose asset identity fields would contradict each other."""
    errors = []
    assets = [
        item
        for item in data.get("assets") or []
        if isinstance(item, dict)
    ]
    prompts = [
        item
        for item in data.get("asset_prompts") or []
        if isinstance(item, dict)
    ]
    voices = [
        item
        for item in data.get("voice_prompts") or []
        if isinstance(item, dict)
    ]
    by_figure = {}
    for asset in assets:
        semantic = _canvas_semantic_code(asset)
        figure = _canvas_reference_code(asset)
        if not semantic:
            errors.append(
                f"资产 {asset.get('name') or figure} 缺稳定语义码"
            )
        if not re.fullmatch(r"图\s*\d+", figure):
            errors.append(
                f"资产 {asset.get('name') or semantic} 缺合法画布图号"
            )
        if semantic and figure:
            by_figure[re.sub(r"\s+", "", figure)] = semantic
    for prompt in prompts:
        figure = _canvas_reference_code(prompt)
        semantic = _canvas_semantic_code(prompt)
        if not figure or not re.fullmatch(r"图\s*\d+", figure):
            errors.append(
                f"资产提示词 {prompt.get('name') or '?'} 画布码非法"
            )
            continue
        expected = by_figure.get(figure)
        if not semantic:
            errors.append(
                f"资产提示词 {prompt.get('name') or figure} 缺语义码"
            )
        elif expected and semantic != expected:
            errors.append(
                f"资产提示词 {figure} 语义码 {semantic} 与资产表 {expected} 不一致"
            )
        canvas = prompt.get("canvas")
        if isinstance(canvas, dict):
            canvas_semantic = _canvas_semantic_code(canvas)
            if canvas_semantic and canvas_semantic != semantic:
                errors.append(
                    f"资产提示词 {figure} 的 canvas.semanticCode 不一致"
                )
    for voice in voices:
        figure = _canvas_reference_code(voice)
        semantic = _canvas_semantic_code(voice)
        expected = by_figure.get(figure)
        if not semantic:
            errors.append(
                f"音色 {voice.get('name') or figure} 缺语义码"
            )
        elif expected and semantic != expected:
            errors.append(
                f"音色 {voice.get('name') or figure} 语义码 {semantic} 与资产表 {expected} 不一致"
            )
    if errors:
        raise ValueError(
            "画布 JSON 契约自检失败：\n- " + "\n- ".join(errors)
        )
    return data


def _canvas_asset_identity(asset):
    kind = _canvas_asset_kind(asset.get("name"))
    name = _canvas_asset_name(asset.get("name"))
    name = re.sub(r"[·・](?:母版|九宫格)$", "", name).strip()
    variant = "master" if kind == "scene" else _canvas_variant_key(asset)
    return f"{kind}:{name}:{variant}" if name else ""


def _canvas_variant_key(asset):
    canvas = asset.get("canvas") if isinstance(asset.get("canvas"), dict) else {}
    explicit = str(
        canvas.get("variantKey")
        or asset.get("variantKey")
        or asset.get("variant")
        or ""
    ).strip()
    if explicit:
        return re.sub(r"\s+", "-", explicit.casefold())
    source = str(asset.get("name") or "")
    if "母版" in source:
        return "master"
    if "九宫格" in source:
        return "grid"
    variants = re.findall(
        r"[（(]([^（）()]*(?:形态|版本|时期|年龄|造型|服装)[^（）()]*)[）)]",
        source,
    )
    if variants:
        return re.sub(r"\s+", "-", variants[-1].strip().casefold())
    return "default"


def _canvas_asset_title(asset):
    source_name = str(asset.get("name") or "")
    kind = _canvas_asset_kind(source_name)
    name = _canvas_asset_name(asset.get("name"))
    name = re.sub(r"[·・](?:母版|九宫格)$", "", name).strip()
    variant = _canvas_variant_key(asset)
    if kind == "scene" and "母版" in source_name:
        name = f"{name}·母版"
    elif variant != "default" and variant not in {"master", "grid"} and f"·{variant}" not in name:
        name = f"{name}·{variant}"
    return f"{_canvas_kind_label(kind)}｜{name}（{asset.get('code') or ''}）"


def _load_canvas_asset_ledger(paths):
    """Read prior canvas JSON exports and lock each asset name to its first figure."""
    ledger = {}
    def remember(candidate):
        identity = candidate["identity"]
        current = ledger.get(identity)
        if current is None or _canvas_figure_number(candidate["code"]) < _canvas_figure_number(current["code"]):
            chosen = candidate
        else:
            chosen = dict(current)
        for key in (
            "raw",
            "desc",
            "fence_body",
            "canvas",
            "voicePrompt",
            "voiceLabel",
            "voiceDescription",
            "voiceFormat",
            "voiceAge",
            "voiceGender",
            "voiceDialect",
        ):
            if not chosen.get(key) and candidate.get(key):
                chosen[key] = candidate[key]
            elif not chosen.get(key) and current and current.get(key):
                chosen[key] = current[key]
        ledger[identity] = chosen

    for path in paths:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        for asset in payload.get("asset_prompts") or []:
            identity = _canvas_asset_identity(asset)
            if not identity:
                continue
            kind = _canvas_asset_kind(asset.get("name"))
            name = _canvas_asset_name(asset.get("name"))
            name = re.sub(r"[·・](?:母版|九宫格)$", "", name).strip()
            candidate = {
                "identity": identity,
                "kind": kind,
                "name": name,
                "code": str(asset.get("code") or ""),
                "semanticCode": str(asset.get("semanticCode") or ""),
                "variant": _canvas_variant_key(asset),
                "raw": str(asset.get("raw") or ""),
                "desc": str(asset.get("desc") or ""),
                "fence_body": asset.get("fence_body") or [],
                "canvas": asset.get("canvas") or {},
                **_canvas_voice_fields(asset.get("raw")),
            }
            remember(candidate)
        contract = payload.get("canvas_contract") if isinstance(payload.get("canvas_contract"), dict) else {}
        for entry in contract.get("asset_ledger") or []:
            if not isinstance(entry, dict) or not entry.get("identity") or not entry.get("title"):
                continue
            title = str(entry.get("title") or "")
            code_match = re.search(r"图\s*\d+", title)
            if not code_match:
                continue
            kind = _canvas_asset_kind(title)
            name = re.sub(r"^[^｜|]*[｜|]\s*", "", title)
            name = re.sub(r"[（(]图\s*\d+[）)]\s*$", "", name).strip()
            if kind == "scene":
                name = re.sub(r"[·・]母版$", "", name).strip()
                variant = "master"
            else:
                variant = str(entry.get("variantKey") or "default")
                if variant != "default" and name.endswith(f"·{variant}"):
                    name = name[:-(len(variant) + 1)]
            remember({
                "identity": str(entry.get("identity") or ""),
                "kind": kind,
                "name": name,
                "code": code_match.group(0),
                "semanticCode": str(entry.get("semanticCode") or ""),
                "variant": variant,
                "raw": str(entry.get("raw") or ""),
                "voicePrompt": str(entry.get("voicePrompt") or ""),
                "voiceLabel": str(entry.get("voiceLabel") or ""),
                "voiceDescription": str(entry.get("voiceDescription") or ""),
                "voiceFormat": str(entry.get("voiceFormat") or ""),
                "voiceAge": str(entry.get("voiceAge") or ""),
                "voiceGender": str(entry.get("voiceGender") or ""),
                "voiceDialect": str(entry.get("voiceDialect") or ""),
            })
    return ledger


def discover_canvas_asset_ledger(search_dir, exclude=None):
    directory = Path(search_dir)
    if not directory.exists():
        return {}
    exclude_path = Path(exclude).resolve() if exclude else None
    paths = []
    for path in sorted(directory.glob("*.json")):
        if exclude_path and path.resolve() == exclude_path:
            continue
        paths.append(path)
    return _load_canvas_asset_ledger(paths)


def _normalize_canvas_prompt(prompt, aliases):
    text = str(prompt or "")
    text = re.sub(r"@//(?:chr|scn|prp)\s*/\s*-\s*", "", text)
    text = text.replace("（母版+九宫格双挂）", "（母版）")
    text = text.replace("母版+九宫格双挂", "母版")
    base_counts = {}
    for alias in aliases:
        base_counts[alias["base"]] = base_counts.get(alias["base"], 0) + 1
    for alias in sorted(aliases, key=lambda item: len(item["base"]), reverse=True):
        base = re.escape(alias["base"])
        title = alias["title"]
        canonical_code = re.search(r"图\s*(\d+)", title)
        alias_codes = set(alias.get("codes") or set())
        old_codes = set(re.findall(rf"{base}\s*[（(]\s*图\s*(\d+)", text)) & alias_codes
        figure_pattern = re.compile(
            rf"(?P<prefix>@?){base}\s*[（(]\s*图\s*(?P<code>\d+)"
            rf"(?:\s*[，,、·・]\s*(?P<tail>[^）)]*))?\s*[）)]"
        )

        def replace_figure(match):
            prefix = match.group("prefix")
            if not prefix and match.start() > 0 and text[match.start() - 1] in "@｜":
                return match.group(0)
            matched_code = match.group("code")
            if base_counts.get(alias["base"], 0) > 1 and matched_code not in alias_codes:
                return match.group(0)
            tail = str(match.group("tail") or "").strip()
            if tail in {"母版", "九宫格", "母版+九宫格双挂"}:
                tail = ""
            return f"@{title}" + (f"（{tail}）" if tail else "")

        text = figure_pattern.sub(replace_figure, text)
        if base_counts.get(alias["base"], 0) == 1:
            text = re.sub(rf"@{base}(?!｜)", f"@{title}", text)
        if canonical_code:
            for old_code in old_codes:
                if old_code == canonical_code.group(1):
                    continue
                text = re.sub(
                    rf"图\s*{re.escape(old_code)}(?!\d)",
                    f"图{canonical_code.group(1)}",
                    text,
                )

    # If exactly one asset owns a figure code, that code is authoritative even
    # when the source prompt uses a short display alias ("纸条（图32）").
    titles_by_code = {}
    for alias in aliases:
        code_match = re.search(r"图\s*(\d+)", alias["title"])
        if code_match:
            titles_by_code.setdefault(code_match.group(1), []).append(alias["title"])
    code_pattern = re.compile(
        r"(?P<at>@?)(?P<name>[\u4e00-\u9fffA-Za-z0-9·・]{1,32})"
        r"\s*[（(]\s*图\s*(?P<code>\d+)"
        r"(?:\s*[，,、·・]\s*(?P<tail>[^）)]*))?\s*[）)]"
    )

    def replace_unique_code(match):
        if match.start() > 0 and text[match.start() - 1] == "｜":
            return match.group(0)
        titles = titles_by_code.get(match.group("code")) or []
        if len(titles) != 1:
            return match.group(0)
        title = titles[0]
        base = title.split("｜", 1)[-1]
        base = re.sub(r"\s*[（(]\s*图\s*\d+\s*[）)]\s*$", "", base).strip()
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

    text = code_pattern.sub(replace_unique_code, text)
    text = re.sub(r"（图\s*(\d+)）（图\s*\1）", r"（图\1）", text)
    text = re.sub(r"\s*[（(]\s*model\s*=\s*[^）)]*[）)]", "", text, flags=re.IGNORECASE)
    return text


_DIALOG_SPEAKER_RE = re.compile(
    r"台词[：:]\s*[【\[]?(?P<name>[^（(\n：:】\]]{1,12})"
    r"\s*[（(]"
)


def _ensure_audio_prompt_refs(prompt, aliases):
    """Expose the concrete audio node name inside the prompt contract."""
    text = str(prompt or "")
    existing = re.compile(r"(?m)^【音色引用】[^\n]*(?:\n|$)")
    text = existing.sub("", text, count=1)
    text = re.sub(r"\n{3,}", "\n\n", text)
    characters = {
        str(alias.get("base") or "").strip(): alias
        for alias in aliases
        if alias.get("kind") == "character"
        and str(alias.get("base") or "").strip()
    }
    prompt_character_codes = {
        name.strip(): code
        for name, code in re.findall(r"@角色｜([^（\n]+)（图\s*(\d+)）", text)
    }
    speaking = []
    for match in _DIALOG_SPEAKER_RE.finditer(text):
        name = match.group("name").strip()
        if (name in characters or name in prompt_character_codes) and name not in speaking:
            speaking.append(name)
    if not speaking:
        return text
    labels = []
    for name in speaking:
        title = str(characters.get(name, {}).get("title") or "")
        code_match = re.search(r"图\s*(\d+)", title) if name in characters else None
        code = code_match.group(1) if code_match else prompt_character_codes.get(name)
        if not code:
            continue
        labels.append(f"@音频｜{name}（图{code}）")
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


def _material_reference_contract(prompt, aliases):
    """Render the Volcano drama-creation style material identity contract."""
    text = str(prompt or "")
    image_titles = re.findall(
        r"@(角色｜[^；\n@]+|场景｜[^；\n@]+|道具｜[^；\n@]+)",
        text,
    )
    seen_titles = set()
    groups = {"人物": [], "场景": [], "道具": []}
    for title in image_titles:
        title = title.strip()
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        if title.startswith("角色｜"):
            groups["人物"].append(
                f"<{title}> 对应角色参考图，仅采用该人物的外貌、发型和服装。"
                "人物身份必须按角色名锁定，不得按画面左右位置替换。"
            )
        elif title.startswith("场景｜"):
            groups["场景"].append(
                f"<{title}> 对应场景参考图，仅采用空间布局、建筑、陈设和光线，不采用图中人物。"
            )
        elif title.startswith("道具｜"):
            groups["道具"].append(
                f"<{title}> 对应道具参考图，仅采用造型、材质与状态，不作为人物身份参考。"
            )

    voice_titles = []
    for title in re.findall(r"@(音频｜[^；\n@]+)", text):
        title = title.strip()
        if title and title not in voice_titles:
            voice_titles.append(title)
    voice_lines = [
        f"<{title}> 的音色只用于该角色发声，不得分配给其他角色。"
        for title in voice_titles
    ]

    sections = ["### 素材引用"]
    for heading in ("人物", "场景", "道具"):
        if groups[heading]:
            sections.extend([f"【{heading}】", *groups[heading]])
    if voice_lines:
        sections.extend(["【声音】", *voice_lines])
    return "\n".join(sections) if len(sections) > 1 else ""


def _replace_material_reference_contract(prompt, aliases):
    """Rebuild the terminal identity contract from the canonical asset labels."""
    text = str(prompt or "")
    contract = _material_reference_contract(text, aliases)
    if not contract:
        return text
    base = re.split(
        r"(?m)^### 素材引用\s*$",
        text,
        maxsplit=1,
    )[0].rstrip()
    return f"{base}\n\n{contract}"


_TEXT_BAN_POLICY_RE = re.compile(
    r"文字[：:]\s*禁\s*(?:任何)?文字"
    r"(?:\s*[/／、,，]\s*UI)?"
    r"(?:\s*[/／、,，]\s*角标)?"
)


def _normalize_material_text_policy(prompt):
    """Resolve the common contradiction between 'no text' and story-critical signs.

    A blanket ban also removes the registration-form and banner text explicitly
    requested by the shot. Keep subtitle/UI/watermark bans, while allowing only
    the in-world lettering demanded by this segment.
    """
    text = str(prompt or "")

    def replace(match):
        allowed = []
        if "登记表" in text:
            allowed.append("登记表上的指定字样")
        if "横幅" in text:
            allowed.append("庆典横幅上的指定字样")
        if allowed:
            tail = f"；仅保留{'、'.join(allowed)}，不得新增其他文字"
        else:
            tail = "；不得新增剧情未要求的文字"
        return f"文字：禁字幕、水印、UI与角标{tail}"

    return _TEXT_BAN_POLICY_RE.sub(replace, text)


def _normalize_asset_prompt_contract(prompt):
    """Repair obvious pose contradictions before JSON export."""
    return str(prompt or "").replace("站姿端坐", "站姿站立于")


def _generator_metadata():
    root = Path(__file__).resolve().parents[2]
    data = {}
    try:
        data = json.loads((root / "VERSION.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return {
        "name": "build_board_lite.py",
        "skillVersion": str(data.get("version") or "7.4.29"),
        "packageRevision": str(data.get("packageRevision") or "20261028-r10"),
    }


_CANVAS_AUDIO_PROMPT_SUFFIX = AUDIO_PROMPT_SUFFIX


def _canvas_audio_prompt_is_complete(prompt, description=""):
    text = str(prompt or "").strip()
    if not text.lower().endswith(_CANVAS_AUDIO_PROMPT_SUFFIX):
        return False
    description = str(description or "")
    gender = (
        "female"
        if re.search(r"女性|\bfemale\s+voice\b", description, re.IGNORECASE)
        else ("male" if re.search(r"男性|\bmale\s+voice\b", description, re.IGNORECASE) else "")
    )
    if gender and not re.search(rf"\bA {gender} aged\b", text, re.IGNORECASE):
        return False
    age_match = re.search(r"(\d+\s*[~～-]\s*\d+|\d+)\s*岁", description)
    if age_match:
        age = re.sub(r"\s*[~～—–-]\s*", "-", age_match.group(1))
        if f"aged {age}" not in text:
            return False
    return True


def _canvas_voice_fields(raw):
    """Extract and compile the final English audio prompt used by the canvas."""
    text = str(raw or "")
    start = text.find("Voice Description 完整版")
    end = text.find("Voice Description Label", start)
    description = ""
    if start >= 0 and end > start:
        description = text[start:end].strip()
        description = re.sub(r"^Voice Description 完整版[^\n]*?\*\*\s*", "", description).strip()
        description = re.sub(r"\s*/\s*\*\*\s*②?\s*$", "", description).strip()
        description = re.sub(r"\*\*\s*②\s*$", "", description).strip()
        description = description.lstrip(":： ").strip()
    label_match = re.search(
        r"Voice Description Label[^\n]*?\*\*\s*[:：]\s*([A-Za-z][^/\n]*)",
        text,
    )
    label = label_match.group(1).strip() if label_match else ""
    label = re.sub(r"\*\*.*$", "", label).strip()
    voice_age_match = re.search(
        r"(?:A\s+(?:male|female)\s+(?:aged\s+)?)?"
        r"(\d{1,3}(?:\s*[~～-]\s*\d{1,3})?)-year-old",
        description,
        re.IGNORECASE,
    )
    voice_cn_age_match = re.search(
        r"(\d+\s*[~～-]\s*\d+|\d+)\s*岁",
        description,
    )
    age = (
        re.sub(r"\s+", "", voice_age_match.group(1))
        if voice_age_match
        else (
            re.sub(r"\s+", "", voice_cn_age_match.group(1))
            if voice_cn_age_match
            else ""
        )
    )
    portrait_age_match = re.search(
        r"\b(\d{1,3}(?:\s*[~～-]\s*\d{1,3})?)-year-old\b",
        text,
        re.IGNORECASE,
    )
    portrait_age = (
        re.sub(r"\s+", "", portrait_age_match.group(1))
        if portrait_age_match
        else ""
    )
    original_voice_age = age
    age = portrait_age or age
    normalized_age = re.sub(r"\s*[~～—–-]\s*", "-", age)
    normalized_voice_age = re.sub(
        r"\s*[~～—–-]\s*",
        "-",
        original_voice_age,
    )
    normalized_portrait_age = re.sub(
        r"\s*[~～—–-]\s*",
        "-",
        portrait_age,
    )
    age_conflict = bool(
        normalized_voice_age
        and normalized_portrait_age
        and normalized_voice_age != normalized_portrait_age
    )
    if normalized_age:
        description = re.sub(
            r"(\d{1,3}(?:\s*[~～-]\s*\d{1,3})?)-year-old",
            f"{normalized_age}-year-old",
            description,
            flags=re.IGNORECASE,
        )
        description = re.sub(
            r"\d{1,3}(?:\s*[~～-]\s*\d{1,3})?\s*岁",
            f"{normalized_age}岁",
            description,
        )
    if re.search(r"女性|\bfemale\s+voice\b", text, re.IGNORECASE):
        gender = "female"
    elif re.search(r"男性|\bmale\s+voice\b", text, re.IGNORECASE):
        gender = "male"
    else:
        gender = ""
    dialect = "northeastern-accented" if "northeastern" in description.lower() else "standard"
    if gender and normalized_age:
        identity = f"A {gender} aged {normalized_age}"
    else:
        identity = f"A {gender}" if gender else ""
    prompt_description = re.sub(
        r"\b\d{1,3}(?:\s*[~～-]\s*\d{1,3})?-year-old\b",
        "",
        description,
        flags=re.IGNORECASE,
    )
    prompt_description = re.sub(
        r"\d{1,3}(?:\s*[~～-]\s*\d{1,3})?\s*岁",
        "",
        prompt_description,
    )
    prompt_description = re.sub(r"\s{2,}", " ", prompt_description).strip(" ,，")
    parts = [
        part
        for part in (
            f"{label}." if label else "",
            prompt_description,
        )
        if part
    ]
    prompt = " ".join(parts).strip()
    tail = ", ".join(part for part in (
        identity,
        (
            f"speaking {dialect} Mandarin, short drama dubbing reference clip, "
            "4.5 seconds clean voice, no background noise"
        ),
    ) if part) + "."
    if prompt:
        prompt = f"{prompt} {tail}"
    return {
        "voicePrompt": prompt,
        "voiceLabel": label,
        "voiceDescription": description,
        "voiceFormat": AUDIO_PROMPT_FORMAT,
        "voiceAge": normalized_age,
        "_voiceAgeConflict": age_conflict,
        "_visualAge": normalized_portrait_age,
        "_voiceAgeOriginal": normalized_voice_age,
        "voiceGender": gender,
        "voiceDialect": dialect,
    }


def enrich_canvas_contract(data, asset_ledger=None):
    """Attach a stable machine contract while preserving the legacy feed fields.

    The Markdown-derived feed remains the source of truth. This block prevents
    canvas import from depending on hard-coded 图1~图5 role assignments or
    natural-language reference syntax.
    """
    assets = data.setdefault("asset_prompts", [])
    manifest_figure_by_semantic = _canvas_source_manifest(data)
    ledger = dict(asset_ledger or {})
    aliases_by_identity = {}
    ledger_assets_by_code = {}
    for identity, entry in ledger.items():
        kind = entry.get("kind") or _canvas_asset_kind(entry.get("name"))
        base = str(entry.get("name") or "").strip()
        code = str(entry.get("code") or "").strip()
        figure_code = re.search(r"图\s*\d+", code)
        semantic_code = str(entry.get("semanticCode") or "").strip().upper()
        variant = str(entry.get("variant") or "default")
        if not base or not code:
            continue
        title_name = f"{base}·母版" if kind == "scene" else base
        if kind != "scene" and variant != "default" and f"·{variant}" not in title_name:
            title_name = f"{title_name}·{variant}"
        title = f"{_canvas_kind_label(kind)}｜{title_name}（{code}）"
        alias_code = re.sub(r"\s+", "", figure_code.group(0)) if figure_code else code
        aliases_by_identity[identity] = {
            "identity": identity,
            "kind": kind,
            "base": base,
            "title": title,
            "codes": {re.search(r"图\s*(\d+)", code).group(1)} if re.search(r"图\s*(\d+)", code) else set(),
            "voicePrompt": str(entry.get("voicePrompt") or ""),
            "voiceLabel": str(entry.get("voiceLabel") or ""),
            "voiceDescription": str(entry.get("voiceDescription") or ""),
            "voiceFormat": str(entry.get("voiceFormat") or ""),
            "voiceAge": str(entry.get("voiceAge") or ""),
            "voiceGender": str(entry.get("voiceGender") or ""),
            "voiceDialect": str(entry.get("voiceDialect") or ""),
            "semanticCode": semantic_code,
        }
        ledger_assets_by_code[alias_code] = {
            "code": alias_code,
            "semanticCode": semantic_code,
            "kind": kind,
            "name": title_name,
            "ratio": "16:9",
            "model": "doubao-seedream-5-0-pro",
            "clarity": "2K",
            "size": "2048x1152",
            "guidance": "中等",
            "watermark": False,
            "assetId": identity,
            "seriesId": f"{kind}:{base}",
            "variantKey": variant,
            "voicePrompt": str(entry.get("voicePrompt") or ""),
            "voiceLabel": str(entry.get("voiceLabel") or ""),
            "voiceDescription": str(entry.get("voiceDescription") or ""),
            "voiceFormat": str(entry.get("voiceFormat") or ""),
            "voiceAge": str(entry.get("voiceAge") or ""),
            "voiceGender": str(entry.get("voiceGender") or ""),
            "voiceDialect": str(entry.get("voiceDialect") or ""),
        }
    by_identity = {}
    for asset in assets:
        identity = _canvas_asset_identity(asset)
        if not identity:
            continue
        by_identity.setdefault(identity, []).append(asset)

    selected_assets = []
    for identity, candidates in by_identity.items():
        kind = _canvas_asset_kind(candidates[0].get("name"))
        if kind == "scene":
            master = next((asset for asset in candidates if "母版" in str(asset.get("name") or "")), None)
            chosen = master or candidates[0]
        else:
            chosen = candidates[0]
        canonical = ledger.get(identity) or {}
        computed_voice = _canvas_voice_fields(chosen.get("raw"))
        original_code = str(chosen.get("code") or "").strip()
        chosen_semantic = _canvas_semantic_code(chosen)
        ledger_figure = re.search(r"图\s*\d+", str(canonical.get("code") or ""))
        manifest_figure = manifest_figure_by_semantic.get(chosen_semantic)
        figure_code = (
            manifest_figure
            or (
                re.sub(r"\s+", "", ledger_figure.group(0))
                if ledger_figure
                else _canvas_reference_code(chosen)
            )
        )
        chosen["semanticCode"] = str(
            chosen_semantic
            or (original_code if not re.search(r"图\s*\d+", original_code) else "")
        )
        if not chosen["semanticCode"]:
            chosen["semanticCode"] = next(
                (
                    semantic
                    for semantic, figure
                    in manifest_figure_by_semantic.items()
                    if figure == _canvas_reference_code(chosen)
                ),
                "",
            )
        if not chosen["semanticCode"]:
            figure_code_match = re.search(
                r"图\s*(\d+)",
                _canvas_reference_code(chosen),
            )
            if figure_code_match:
                prefix = {
                    "character": "CHR",
                    "scene": "SCN",
                    "prop": "PRP",
                }.get(
                    _canvas_asset_kind(chosen.get("name")),
                    "AST",
                )
                chosen["semanticCode"] = (
                    f"{prefix}-{int(figure_code_match.group(1)):03d}"
                )
        if not chosen["semanticCode"]:
            raise ValueError(
                f"无法为资产 {chosen.get('name') or chosen.get('code')} 生成稳定语义码"
            )
        if re.fullmatch(r"图\s*\d+", figure_code):
            chosen["code"] = re.sub(r"\s+", "", figure_code)
        for source_asset in data.get("assets") or []:
            if not isinstance(source_asset, dict):
                continue
            if _canvas_reference_code(source_asset) != figure_code:
                continue
            source_asset["code"] = chosen["semanticCode"]
            source_asset["semanticCode"] = chosen["semanticCode"]
            source_asset["img"] = figure_code
        base = _canvas_asset_name(chosen.get("name"))
        base = re.sub(r"[·・](?:母版|九宫格)$", "", base).strip()
        aliases_by_identity[identity] = {
            "identity": identity,
            "kind": _canvas_asset_kind(chosen.get("name")),
            "base": base,
            "title": _canvas_asset_title(chosen),
            "semanticCode": str(chosen.get("semanticCode") or ""),
            "codes": {
                code
                for candidate in candidates
                for code in [re.sub(r"\D", "", str(candidate.get("code") or ""))]
                if code
            } | {
                re.search(r"图\s*(\d+)", chosen.get("code") or "").group(1)
            } if re.search(r"图\s*(\d+)", chosen.get("code") or "") else set(),
            "voicePrompt": str(
                (canonical.get("voicePrompt") if canonical else "")
                or computed_voice.get("voicePrompt")
                or ""
            ),
            "voiceLabel": str(
                (canonical.get("voiceLabel") if canonical else "")
                or computed_voice.get("voiceLabel")
                or ""
            ),
            "voiceDescription": str(
                (canonical.get("voiceDescription") if canonical else "")
                or computed_voice.get("voiceDescription")
                or ""
            ),
            "voiceFormat": str(
                (canonical.get("voiceFormat") if canonical else "")
                or computed_voice.get("voiceFormat")
                or ""
            ),
            "voiceAge": str((canonical.get("voiceAge") if canonical else "") or computed_voice.get("voiceAge") or ""),
            "voiceGender": str((canonical.get("voiceGender") if canonical else "") or computed_voice.get("voiceGender") or ""),
            "voiceDialect": str((canonical.get("voiceDialect") if canonical else "") or computed_voice.get("voiceDialect") or ""),
        }
        selected_assets.append(chosen)
    age_conflicts = {}
    for asset in selected_assets:
        voice = _canvas_voice_fields(asset.get("raw"))
        visual = re.search(
            r"\b(\d{1,3}(?:\s*-\s*\d{1,3})?)-year-old\b",
            str(asset.get("raw") or ""),
            re.IGNORECASE,
        )
        if (
            visual
            and voice.get("voiceAge")
            and re.sub(r"\s+", "", visual.group(1)) != voice["voiceAge"]
        ):
            age_conflicts[str(asset.get("code") or "")] = {
                "visualAge": re.sub(r"\s+", "", visual.group(1)),
                "voiceAge": voice["voiceAge"],
            }
    for asset in selected_assets:
        asset["raw"] = _normalize_asset_prompt_contract(asset.get("raw"))
        if isinstance(asset.get("fence_body"), list):
            asset["fence_body"] = _normalize_asset_prompt_contract(
                "\n".join(asset["fence_body"])
            ).splitlines()
    aliases = list(aliases_by_identity.values())
    voice_prompts = []
    for alias in aliases:
        if alias.get("kind") != "character" or not alias.get("voicePrompt"):
            continue
        code_match = re.search(r"图\s*\d+", str(alias.get("title") or ""))
        if not code_match:
            continue
        prompt = str(alias.get("voicePrompt") or "")
        description = str(alias.get("voiceDescription") or "")
        if not _canvas_audio_prompt_is_complete(prompt, description):
            raise ValueError(
                f"音色 Audio Prompt 不完整：{alias.get('title') or alias.get('identity')}"
            )
        voice_prompts.append({
            "code": code_match.group(0),
            "semanticCode": _canvas_semantic_code(
                {"title": alias.get("title"), "semanticCode": alias.get("semanticCode")}
            ),
            "name": alias.get("base") or "",
            "title": alias.get("title") or "",
            "assetId": alias.get("identity") or "",
            "format": alias.get("voiceFormat") or AUDIO_PROMPT_FORMAT,
            "prompt": prompt,
            "label": alias.get("voiceLabel") or "",
            "description": description,
            "age": alias.get("voiceAge") or "",
            "gender": alias.get("voiceGender") or "",
            "dialect": alias.get("voiceDialect") or "",
        })
    data["voice_prompts"] = sorted(
        voice_prompts,
        key=lambda item: _canvas_figure_number(item.get("code")),
    )
    data["asset_prompts"] = selected_assets
    assets = selected_assets
    for segment in data.setdefault("segments", []):
        segment["raw"] = _normalize_canvas_prompt(segment.get("raw"), aliases)
        segment["raw"] = _ensure_audio_prompt_refs(segment.get("raw"), aliases)
        segment["raw"] = _normalize_material_text_policy(segment.get("raw"))
        segment["raw"] = _replace_material_reference_contract(
            segment["raw"],
            aliases,
        )
        if isinstance(segment.get("fence_body"), list):
            segment["fence_body"] = _normalize_material_text_policy(
                "\n".join(segment["fence_body"])
            ).splitlines()

    by_code = dict(ledger_assets_by_code)
    for asset in assets:
        code = str(asset.get("code") or "")
        if not re.fullmatch(r"图\s*\d+", code):
            continue
        kind = _canvas_asset_kind(asset.get("name"))
        variant = _canvas_variant_key(asset)
        display_name = _canvas_asset_name(asset.get("name"))
        if kind == "scene" and "母版" in str(asset.get("name") or ""):
            display_name = f"{display_name}·母版"
        elif kind != "scene" and variant != "default" and f"·{variant}" not in display_name:
            display_name = f"{display_name}·{variant}"
        item = {
            "code": code,
            "semanticCode": _canvas_semantic_code(asset),
            "kind": kind,
            "name": display_name,
            "ratio": "16:9",
            "model": "doubao-seedream-5-0-pro",
            "clarity": "2K",
            "size": "2048x1152",
            "guidance": "中等",
            "watermark": False,
            "assetId": _canvas_asset_identity(asset),
            "seriesId": f"{kind}:{_canvas_asset_name(asset.get('name'))}",
            "variantKey": variant,
        }
        asset["canvas"] = item
        by_code[code] = item

    video_aspect = _canvas_aspect((data.get("meta") or {}).get("画幅"), "16:9")
    video_model = _canvas_video_model((data.get("meta") or {}).get("模型版本"))
    data["canvas_contract"] = {
        "schema": SCHEMA_VERSION,
        "asset_aspect": "16:9",
        "video_aspect": video_aspect,
        "video_model": video_model,
        "asset_identity_policy": "series-variant-locked-first-figure",
        "scene_variant_policy": "master-only",
        "prompt_reference_syntax": "inline-full-asset-title",
        "generator": _generator_metadata(),
        "ageConflicts": age_conflicts,
        "asset_ledger": [
            {
                "identity": alias["identity"],
                "title": alias["title"],
                "code": next(
                    (asset.get("code") for asset in selected_assets if _canvas_asset_identity(asset) == alias["identity"]),
                    "",
                ),
                "semanticCode": _canvas_semantic_code(alias),
                "variantKey": alias["identity"].rsplit(":", 1)[-1],
                "voicePrompt": alias.get("voicePrompt") or "",
                "voiceLabel": alias.get("voiceLabel") or "",
                "voiceDescription": alias.get("voiceDescription") or "",
                "voiceFormat": alias.get("voiceFormat") or "",
                "voiceAge": alias.get("voiceAge") or "",
                "voiceGender": alias.get("voiceGender") or "",
                "voiceDialect": alias.get("voiceDialect") or "",
            }
            for alias in aliases
        ],
    }
    for segment in data.setdefault("segments", []):
        ends = []
        for shot in segment.get("shots") or []:
            try:
                ends.append(float(shot.get("end")))
            except (TypeError, ValueError):
                continue
        max_seconds = 30 if video_model == "doubao-seedance-2-5" else 15
        duration = max(4, min(max_seconds, int(max(ends, default=4) + 0.999)))
        codes = []
        for code in re.findall(r"图\s*(\d+)", segment.get("raw", "")):
            token = f"图{code}"
            if token not in codes:
                codes.append(token)
        segment["canvas"] = {
            "ratio": _canvas_aspect(segment.get("raw"), video_aspect),
            "model": video_model,
            "duration": duration,
            "asset_codes": codes,
            "asset_refs": [by_code[code] for code in codes if code in by_code],
        }
    return data


def to_seconds(tok: str) -> float:
    """时码 → 秒。兼容 MM:SS / MM:SS.s / MM:SS.ss 与纯秒。

    ⚠️ 秒位必须 float()（2026-10-14 Agent 修复）：
    原实现写 `int(s)`，遇半秒时码 `00:10.5` 直接抛 ValueError，
    该镜行随即被调用点的 `except ValueError: continue` **静默跳过**——
    实测《盐枭劫-60s测试局》源稿 17 镜只解析出 10 镜（丢 7 镜 / 41%），
    看板与 SRT·CSV·JSON 三导出的全片时间轴随之出现 7 秒空洞，而 CLI 零告警。
    本包真实产物普遍使用半秒粒度（`00:10.5` / `00:03.5`），故此处必须收 float。
    """
    tok = tok.strip().replace("：", ":")
    if ":" in tok:
        m, s = tok.split(":", 1)
        return int(m) * 60 + float(s)
    return float(tok)


def table_rows(lines):
    rows = []
    for l in lines:
        if l.startswith("|") and "---" not in l:
            cells = [c.strip() for c in l.strip("|").split("|")]
            if cells and cells[0]:
                rows.append(cells)
    return rows


# ── 宽容章节识别：不要求 md 照抄「## P2 · 资产清单」这类标题，按关键词归区 ──
def zone_of(title: str) -> str:
    t = title or ""
    if re.search(r"发布文案|P6", t) and not re.search(r"投喂", t):
        return "publish"
    if re.search(r"出图提示词|P2\.5|P2c|资产出图", t):
        return "ap"
    if re.search(r"投喂提示词|P4|分镜提示词", t):
        return "seg"
    if re.search(r"视听圣经|风格圣经", t):
        return "bible"
    if re.search(r"剧本|故事|梗概", t):
        return "script"
    if re.search(r"资产清单|资产表|P2", t):
        return "assets"
    if re.search(r"创作基准|P0A", t):
        return "p0a"
    return ""


# 资产表头别名 → 标准字段（兼容「类 | 编码 | 类型 | 出图 | 复用单元」这类自定义列序）
HEAD_ALIAS = {
    "code": ["编码", "code", "资产编码", "id", "资产id"],
    "name": ["名字", "名称", "角色", "人物", "资产", "name", "资产名"],
    "view": ["视图", "出图", "出图类型", "规格", "类型", "view"],
    "img":  ["@图片n", "图号", "图片", "参考图", "img", "绑定图"],
}


def map_head(cells):
    """表头行 → {标准字段: 列索引}；返回 None 表示不像表头，退回按位置取。
    按「标准字段 × 别名优先级」扫描（而非按列顺序），保证"出图"优先于"类型"命中 view。"""
    norm = [re.sub(r"[\s*`_@]", "", c).lower() for c in cells]
    idx = {}
    for std, aliases in HEAD_ALIAS.items():
        for a in aliases:
            an = re.sub(r"[\s*`_@]", "", a).lower()
            if an in norm:
                idx[std] = norm.index(an)
                break
    return idx or None


def clean_code(s: str) -> str:
    """子标题里的资产编码清洗：`1. `@CHR-沈砚`` → `@CHR-沈砚`"""
    s = (s or "").strip()
    s = re.sub(r"^\d+[.、)]\s*", "", s)     # 去前导序号
    s = s.replace("`", "").strip()
    return s


def parse_md(md_text: str, asset_ledger=None) -> dict:
    lines = md_text.splitlines()
    data = {"title": "分镜看板",
            "meta": {"画幅": "未锁定", "模型版本": "未锁定"},
            "p0a": [], "script": [], "bible": [],
            "assets": [], "asset_prompts": [], "publish": [],
            "segments": [], "warnings": [], "_head": None, "extras": []}

    section = ""          # 当前 ## 章节
    seg = None            # 当前段
    ap = None             # 当前资产出图条目
    sub_head = ""         # 当前 ### 子标题
    in_fence = False
    plain = []            # 当前章节的普通正文行

    def _extra_block(title, block):
        """未归区章节的内容块（K-lite-6：md 里写的每一样东西都必须出现在看板上）。"""
        if not data["extras"] or data["extras"][-1]["title"] != title:
            data["extras"].append({"title": title, "body": []})
        data["extras"][-1]["body"].append(block)

    pending_tbl = []      # 剧本区/未归区的待归组表格行（按 markdown 分隔行切成多张表）

    def _push_tbl(rows, z):
        block = {"t": "tbl", "rows": rows}
        if z == "script":
            data["script"].append(block)
        else:
            _extra_block(section, block)

    def flush_tbl():
        nonlocal pending_tbl
        if pending_tbl:
            rows, pending_tbl = pending_tbl, []
            _push_tbl(rows, zone_of(section) if section else "")

    def flush_plain():
        nonlocal plain
        if not plain:
            return
        text = "\n".join(plain).strip()
        plain = []
        if not text:
            return
        z = zone_of(section) if section else ""
        if not section:
            _extra_block("（卷首说明）", {"t": "p", "v": text})
        elif "剧本" in section or z == "script":
            data["script"].append({"t": "p", "v": text})
        elif z == "bible":
            data["bible"].append(text)
        elif "P0A" in section or z == "p0a" or "创作基准" in section:
            data["p0a"].append({"k": section, "v": text})
        elif z == "":
            _extra_block(section, {"t": "p", "v": text})

    for raw in lines:
        line = raw.rstrip()
        s = line.strip()

        if s.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            if ap is not None:
                ap.setdefault("fence_body", []).append(line)
            elif seg is not None:
                seg.setdefault("fence_body", []).append(line)
            continue

        if pending_tbl and not (s.startswith("|") and section):
            flush_tbl()          # 表格被正文/标题/围栏打断 → 先落表，保原文顺序

        if s.startswith("# ") and data["title"] == "分镜看板":
            flush_plain()
            data["title"] = s[2:].strip()
            continue

        m_sec = re.match(r"^##\s+(.+)$", s)
        if m_sec:
            flush_plain()
            section = m_sec.group(1).strip()
            seg, ap, sub_head = None, None, ""
            data["_head"] = None            # 表头映射不跨章节
            if re.match(r"^【?投喂单元", section) or re.match(r"^段\s*\d", section):
                seg = {"no": len(data["segments"]) + 1, "name": section,
                       "raw": "", "shots": [], "fields": {}, "warnings": []}
                data["segments"].append(seg)
                continue
            if zone_of(section) == "":
                data["extras"].append({"title": section, "body": []})
            continue

        m_sub = re.match(r"^#{3,4}\s+(.+)$", s)
        if m_sub:
            flush_plain()
            sub_head = m_sub.group(1).strip()
            z_sec = zone_of(section)
            # 段标题宽容：`### 段1` / `### U01 · …` / `### 第1段` 都认
            if re.match(r"^段\s*\d", sub_head) or re.match(r"^U\d+\b", sub_head) \
               or re.match(r"^第\s*\d+\s*段", sub_head):
                seg = {"no": len(data["segments"]) + 1, "name": sub_head,
                       "raw": "", "shots": [], "fields": {}, "warnings": []}
                data["segments"].append(seg)
                ap = None
            elif z_sec == "ap":
                parts = [p.strip() for p in sub_head.split("·")]
                ap = {"code": clean_code(parts[0]) if parts else sub_head,
                      "_match": sub_head,
                      "semanticCode": (
                          clean_code(parts[0])
                          if parts and re.fullmatch(
                              r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*",
                              clean_code(parts[0]).upper(),
                          )
                          else ""
                      ),
                      "name": clean_code(parts[1]) if len(parts) > 1 else "",
                      "desc": parts[2] if len(parts) > 2 else "",
                      "raw": ""}
                data["asset_prompts"].append(ap)
                seg = None
            else:
                ap = None
            continue

        fm_meta = FIELD_RE.match(s)
        if fm_meta:
            key, val = fm_meta.group(1), fm_meta.group(2).strip()
            if key in ("画幅", "模型版本"):        # A/B 双锁：任意位置声明都认
                data["meta"][key] = val
                continue
            if seg is not None:
                seg["fields"][key] = val
                continue
            if zone_of(section) == "publish":
                data["publish"].append({"k": key, "v": val})
                continue

        if s.startswith("|") and section:
            z = zone_of(section)
            if "---" in s and z in ("script", ""):
                # markdown 分隔行＝表头确认：把已收的「表头+数据」落表，末行留给下一张表当表头
                if len(pending_tbl) > 1:
                    head = pending_tbl[-1]
                    _push_tbl(pending_tbl[:-1], z)
                    pending_tbl = [head]
                continue
            rows = table_rows([s])
            if rows:
                cells = rows[0]
                k0 = re.sub(r"[*`]", "", cells[0]).strip()
                # meta 兜底：从 A/B 双锁表等任意表格里捞模型版本/画幅
                if data["meta"]["模型版本"] == "未锁定" and "模型版本" in k0 and len(cells) > 1:
                    m = re.search(r"Seedance\s*[\d.]+|\d+(?:\.\d+)?", re.sub(r"[*`]", "", cells[1]))
                    if m:
                        data["meta"]["模型版本"] = m.group(0).strip()
                if data["meta"]["画幅"] == "未锁定" and "画幅" in k0 and len(cells) > 1:
                    m = re.search(r"\d{1,2}\s*:\s*\d{1,2}", cells[1])
                    if m:
                        data["meta"]["画幅"] = re.sub(r"\s", "", m.group(0))
                if z in ("script", ""):
                    flush_plain()            # 先落之前挂着的正文，保原文顺序
                    pending_tbl.append(cells)
                elif z == "assets":
                    head = map_head(cells)   # 表头探测只在资产区生效（防止吞掉剧本区表头行）
                    if head is not None:
                        data["_head"] = head
                        continue
                    h = data.get("_head")
                    if h:
                        g = lambda f: cells[h[f]] if f in h and h[f] < len(cells) else ""
                        row = {"code": g("code") or cells[0], "name": g("name"),
                               "view": g("view"), "img": g("img"), "extra": []}
                        used = set(h.values())
                        row["extra"] = [c for i, c in enumerate(cells)
                                        if i not in used and c and c not in ("类", "编码")]
                    else:
                        row = {"code": cells[0], "name": cells[1] if len(cells) > 1 else "",
                               "view": cells[2] if len(cells) > 2 else "",
                               "img": cells[3] if len(cells) > 3 else "", "extra": []}
                    if row["code"] and row["code"] not in ("编码",):
                        row["semanticCode"] = (
                            row["code"]
                            if re.fullmatch(
                                r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*",
                                str(row["code"]).upper(),
                            )
                            else ""
                        )
                        data["assets"].append(row)
                elif z == "p0a":
                    if cells[0] not in ("项",):
                        data["p0a"].append({"k": cells[0], "v": " | ".join(cells[1:])})
                elif z == "bible":
                    data["bible"].append(" | ".join(cells))
                continue

        if seg is not None:
            fmt = TS_RE.match(s)
            if fmt:
                try:
                    start, end = to_seconds(fmt.group(1)), to_seconds(fmt.group(2))
                except ValueError:
                    seg["warnings"].append(
                        f"镜行时码无法解析、该镜已被跳过（看板与三导出会缺此镜）：{s[:40]}")
                else:
                    seg["shots"].append({"no": len(seg["shots"]) + 1,
                                         "start": start, "end": end,
                                         "text": shot_text(fmt.group(3))})
                continue
        plain.append(line)

    flush_tbl()
    flush_plain()

    # 围栏原文归属：段 / 资产出图
    parts = re.split(r"^#{2,4}\s+", md_text, flags=re.M)
    def fence_of(body):
        fs = FENCE_RE.findall(body)
        return max(fs, key=len).strip() if fs else ""
    for seg in data["segments"]:
        token = seg["name"].split()[0]                 # 段1 / U01 / 第1段
        for chunk in parts[1:]:
            first = chunk.splitlines()[0].strip()
            if first == seg["name"] or first.startswith(token):
                seg["raw"] = fence_of("\n".join(chunk.splitlines()[1:]))
                break
    for ap in data["asset_prompts"]:
        for chunk in parts[1:]:
            first = chunk.splitlines()[0].strip()
            if first == ap.get("_match") or first.startswith(ap.get("_match", "\0")):
                ap["raw"] = fence_of("\n".join(chunk.splitlines()[1:]))
                break

    # 校验（轻量 WARN 层）
    for seg in data["segments"]:
        if not seg["raw"]:
            seg["warnings"].append("未找到 ```text 围栏投喂原文（无法直接复制投喂）")
        _skipped = []
        if not seg["shots"]:
            for l in seg["raw"].splitlines():     # K-lite-2 降级：从围栏内提取时间戳
                fm = TS_RE.match(l.strip())
                if fm:
                    try:
                        a, b = to_seconds(fm.group(1)), to_seconds(fm.group(2))
                    except ValueError:
                        # ★2026-10-14 Agent 修复：原为裸 `continue` —— 丢镜不留痕。
                        # 静默丢镜会让看板与三导出悄悄缺镜（实测曾丢 41%），必须记账。
                        _skipped.append(l.strip()[:48])
                        continue
                    seg["shots"].append({"no": len(seg["shots"]) + 1,
                                         "start": a, "end": b, "text": shot_text(fm.group(3))})
        # ★丢镜安全网（2026-10-14 Agent 新增）：以「原文带时码的行数」为基准对账，
        #   任何解析器缺陷造成的静默丢镜都会被这一条兜住，不必逐类枚举病因。
        _tc_lines = [l for l in seg["raw"].splitlines() if TS_RE.match(l.strip())]
        if _skipped:
            seg["warnings"].append(
                f"有 {len(_skipped)} 行镜行时码无法解析、该镜已被跳过"
                f"（看板与 SRT/CSV/JSON 三导出会缺这些镜）：{'；'.join(_skipped[:4])}"
                + ("…" if len(_skipped) > 4 else ""))
        if len(_tc_lines) > len(seg["shots"]):
            _lost = len(_tc_lines) - len(seg["shots"])
            seg["warnings"].append(
                f"丢镜告警：原文带时码的镜行 {len(_tc_lines)} 行，仅解析出 {len(seg['shots'])} 镜，"
                f"缺 {_lost} 镜——请核对镜行时码写法（支持 `MM:SS` / `MM:SS.s` / `Xs-Ys` / `N-N秒`）")
        if not seg["shots"]:
            seg["warnings"].append("未解析到 [起-止秒] 逐镜时间戳行")
        else:
            prev = None
            for sh in seg["shots"]:
                if prev is not None and sh["start"] < prev - 0.01:
                    seg["warnings"].append(f"镜头时间码回跳：{sh['start']:g}s 早于上镜结束 {prev:g}s")
                    break
                prev = sh["end"]
        for w in seg["warnings"]:
            data["warnings"].append({"seg": seg["no"], "msg": f"{seg['name']}：{w}"})

    for ap in data["asset_prompts"]:
        if not ap["raw"]:
            data["warnings"].append({"seg": 0, "msg": f"资产出图提示词 {ap['code']} 未找到 ```text 围栏原文"})
    if not data["segments"]:
        data["warnings"].append({"seg": 0, "msg": "全档未解析到「### 段N」分镜段——请检查标题契约"})
    if not data["asset_prompts"]:
        data["warnings"].append({"seg": 0,
            "msg": "未解析到「## P2.5 · 资产出图提示词」——⑤ 资产出图提示词与 ③ 分镜投喂提示词必须同板呈现（K-lite-4）"})
    else:
        for _it in data["asset_prompts"]:
            _bd = (_it.get("raw") or "").strip()
            _cd = (_it.get("code") or _it.get("name") or "").upper()
            _missing_fp = (
                len(_bd) < 150
                or "同板四视图" in _bd or "T-pose" in _bd or "九机位小图" in _bd or "详见 P2" in _bd or "不另写文字描述" in _bd
                or ("CHR" in _cd and not ("无脸全身" in _bd or "featureless face" in _bd))
                or ("SCN" in _cd and not ("九宫格" in _bd or "摄影机矩阵" in _bd))
            )
            if _missing_fp:
                data["warnings"].append({
                    "seg": 0, "lvl": "WARN", "code": "K-lite-4b",
                    "msg": f"K-lite-4b 资产「{_it.get('code') or _it.get('name', '')}」出图提示词疑似简笔空壳（仅 {len(_bd)} 字或未落权威模板指纹）——须按 sd-asset-engine 权威模板写满（角色四格无脸+声纹锚 / 场景母版+九宫格8字段 / 道具4格）"
                })
    if data["meta"]["画幅"] == "未锁定":
        data["warnings"].append({"seg": 0, "msg": "未声明【画幅】——B 锁未锁定（用户未指定 ≠ 默认 9:16）"})
    if data["meta"]["模型版本"] == "未锁定":
        data["warnings"].append({"seg": 0, "msg": "未声明【模型版本】——A 锁未锁定（用户未指定 ≠ 默认 2.5）"})

    # 时间轴排布：段内独立计时 → 按累计偏移铺成整片时间轴（泳道才读得出 57s 成片节奏）
    off = 0.0
    for seg in data["segments"]:
        seg["off"] = off
        if seg["shots"]:
            off += max(sh["end"] for sh in seg["shots"])

    # 资产引用段：用编码在段原文里回查（如 @CHR-沈砚 出现在哪些段）
    for a in data["assets"]:
        code = a.get("code", "")
        a["refs"] = [seg["no"] for seg in data["segments"] if code and code in seg["raw"]]
    return enrich_canvas_contract(data, asset_ledger)


def total_duration(data):
    """各段独立计时（每段都从 0 起）→ 累加成整片时长；连续时间轴 → 取最大 end。"""
    segs = [s for s in data["segments"] if s["shots"]]
    if not segs:
        return 0
    starts = [s["shots"][0]["start"] for s in segs]
    if starts and all(abs(x - starts[0]) < 0.01 for x in starts) and starts[0] == 0:
        return sum(max(sh["end"] for sh in s["shots"]) for s in segs)
    return max(max(sh["end"] for sh in s["shots"]) for s in segs)


def build_html(d: dict) -> str:
    payload = {
        "meta": {"model": d["meta"]["模型版本"], "aspect": d["meta"]["画幅"],
                 "seg_count": len(d["segments"]),
                 "shot_count": sum(len(s["shots"]) for s in d["segments"]),
                 "total": total_duration(d),
                 "built_at": datetime.now().strftime("%Y-%m-%d %H:%M")},
        "p0a": d["p0a"], "script": d["script"], "bible": d["bible"],
        "assets": d["assets"], "aprompts": d["asset_prompts"], "publish": d["publish"],
        "segments": d["segments"], "warnings": d["warnings"], "extras": d["extras"],
    }
    return TEMPLATE.replace("@@DATA@@", json.dumps(payload, ensure_ascii=False)) \
                   .replace("@@TITLE@@", html.escape(d["title"], quote=True))


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>@@TITLE@@ · 分镜看板</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#F7F6F2;color:#1F1E1B;
 font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:20px}
h1{font-size:19px;font-weight:600;margin:0 0 4px}
h2{font-size:15px;font-weight:600;margin:26px 0 10px;padding-bottom:6px;border-bottom:1px solid #E2E0D9}
.sub{color:#6B6963;font-size:12px;font-weight:400}
.bar{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 4px}
.chip{background:#fff;border:1px solid #E2E0D9;border-radius:8px;padding:6px 10px;font-size:12px}
.chip b{font-weight:600}
.chip.k{color:#0C447C}.chip.warn{color:#854F0B;border-color:#EF9F27}
.seg-f{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0}
.seg-f button{cursor:pointer;background:#fff;border:1px solid #E2E0D9;border-radius:999px;padding:4px 12px;font-size:12px}
.seg-f button.on{background:#185FA5;border-color:#185FA5;color:#fff}
.f{border:1px solid #E2E0D9;background:#fff;border-radius:8px;padding:8px 10px;margin:6px 0;font-size:12.5px}
.f .code{font-family:ui-monospace,Consolas,monospace;font-weight:600;margin-right:6px;color:#854F0B}
.lane{margin:8px 0}
.lane .nm{font-size:12px;color:#6B6963;margin-bottom:3px}
.lane .track{position:relative;height:32px;background:#fff;border:1px solid #E2E0D9;border-radius:6px}
.blk{position:absolute;top:3px;height:26px;border-radius:4px;background:#E6F1FB;border:1px solid #85B7EB;
 font-size:11px;color:#0C447C;text-align:center;line-height:24px;overflow:hidden;cursor:default}
.blk.s1{background:#E1F5EE;border-color:#5DCAA5;color:#085041}
.blk.s2{background:#FAEEDA;border-color:#EF9F27;color:#633806}
.blk.s3{background:#EEEDFE;border-color:#AFA9EC;color:#3C3489}
.blk:hover{outline:2px solid #185FA5}
.ruler{position:relative;height:16px;margin-top:2px}
.ruler span{position:absolute;top:4px;font-size:10.5px;color:#888780;transform:translateX(-50%)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(258px,1fr));gap:8px}
.card{background:#fff;border:1px solid #E2E0D9;border-radius:8px;padding:9px 10px;font-size:12px}
.card .hd{display:flex;justify-content:space-between;font-weight:600;margin-bottom:5px;font-size:12.5px}
.card .row{color:#444441;margin:3px 0}
.card .row i{color:#888780;font-style:normal;margin-right:3px}
table{width:100%;border-collapse:collapse;font-size:12px;background:#fff}
th,td{border:1px solid #E2E0D9;padding:5px 7px;text-align:left;vertical-align:top}
th{background:#F1EFE8;font-weight:600}
td.num{text-align:center;font-family:ui-monospace,Consolas,monospace}
.aimg{display:inline-block;font-size:11px;padding:1px 7px;border-radius:999px;background:#E6F1FB;
 border:1px solid #85B7EB;color:#0C447C;font-weight:600;white-space:nowrap}
.asegs{font-size:11.5px;color:#185FA5}
details.pbox{background:#fff;border:1px solid #E2E0D9;border-radius:8px;margin:8px 0;padding:10px 12px}
details.pbox>summary{cursor:pointer;font-size:12.5px;color:#33312C;list-style:none}
details.pbox>summary::-webkit-details-marker{display:none}
details.pbox>summary::before{content:"▸ ";color:#888780}
details.pbox[open]>summary::before{content:"▾ "}
.cpbtn{float:right;font-size:11.5px;padding:2px 9px;border-radius:5px;border:1px solid #85B7EB;
 background:#E6F1FB;color:#0C447C;cursor:pointer}
.cpbtn:hover{background:#D6E9FA}
pre{white-space:pre-wrap;word-break:break-word;font-family:ui-monospace,Consolas,"Courier New",monospace;
 font-size:12px;line-height:1.68;color:#33312C;background:#FAF9F5;border:1px solid #E2E0D9;
 border-radius:6px;padding:10px 12px;margin:8px 0 2px}
.note{font-size:11.5px;color:#888780;margin-top:6px}
.zone{font-size:12px;font-weight:600;letter-spacing:.06em;background:#E6F1FB;color:#0C447C;
 border:1px solid #85B7EB;border-radius:6px;padding:5px 10px;margin:26px 0 2px}
.zone.prod{background:#F1EFE8;border-color:#D3D1C7;color:#6B6963}
.kv{margin:9px 0}
.kv .k2{font-size:11px;color:#888780;letter-spacing:.04em;margin-bottom:2px}
.kv .v2{font-size:12.5px;line-height:1.72;color:#33312C;white-space:pre-wrap;word-break:break-word}
.script .sc{background:#fff;border:1px solid #E2E0D9;border-radius:8px;padding:9px 11px;margin:7px 0}
.script .sc .bd{font-size:12.5px;line-height:1.72;color:#33312C;white-space:pre-wrap}
.chips{display:flex;flex-wrap:wrap;gap:4px;margin-top:2px}
.chips .c2{font-size:11px;padding:1px 7px;border-radius:999px;background:#F7F6F2;border:1px solid #E2E0D9;color:#6B6963}
.hide{display:none}
.bline{background:#fff;border:1px solid #E2E0D9;border-radius:8px;padding:10px 12px;margin:8px 0}
.bline .bt{font-size:11px;color:#888780;letter-spacing:.04em;margin-bottom:3px}
.bline .bv{font-size:12.5px;color:#33312C;line-height:1.7;white-space:pre-wrap}
footer{text-align:center;color:#9A968C;font-size:11.5px;padding:16px 0 26px}
</style></head><body><div class="wrap">
<h1>@@TITLE@@</h1>
<div class="sub" id="hdr"></div>
<div class="bar" id="bar"></div>
<div class="seg-f" id="filters"></div>
<div id="alerts"></div>
<div class="zone">创作蓝图层 · P0~P1 —— 先看这里（故事 / 基准 / 风格）</div>
<h2>Ⅰ · 创作基准 <span class="sub">（P0A · 表格项 → 看板行）</span></h2><div id="p0a"></div>
<h2>Ⅱ · 剧本层 <span class="sub">（P1 产出物 · 正文照录）</span></h2><div id="script"></div>
<h2>Ⅲ · 视听圣经 <span class="sub">（P1 一次锁定 · 全片继承）</span></h2><div id="bible"></div>
<div class="zone prod">分镜生产层 · P2~P6 —— 拿去干活的那一份（资产 / 分镜 / 投喂 / 文案）</div>
<h2>① 时间轴泳道</h2><div id="lanes"></div>
<h2>② 镜头卡矩阵 <span class="sub">（拆解视图 — 逐镜栏位）</span></h2><div class="cards" id="cards"></div>
<h2>③ 投喂提示词原文 <span class="sub">（成品视图 — 照录未改写，可整段复制直接投喂）</span></h2><div id="prompts"></div>
<h2>④ 资产总表 <span class="sub">（人物 / 场景 / 道具 → 图号 → 引用段 · 来自 P2 资产清单）</span></h2><div id="assets"></div>
<h2>⑤ 资产出图提示词 <span class="sub">（成品视图 — 生成人物 / 场景 / 道具资产图用，可整段复制；<b>不是</b>视频投喂提示词，与 ③ 必同板）</span></h2><div id="aprompts"></div>
<h2>⑥ 发布文案</h2><div id="copy"></div>
<h2>⑦ 其他章节 <span class="sub">（md 内未被归区的章节 · 原文照录，K-lite-6 信息零丢失）</span></h2><div id="extras"></div>
<div class="note" id="foot"></div>
<footer>由 scripts/build_board_lite.py 生成 · 源 md 是唯一真相，本看板只读不回写 · 双击即开，零依赖</footer>
</div>
<script>
const D = @@DATA@@;
let curSeg = 0, onlyBad = false;
const esc = s => (s==null?'':String(s)).replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const segBad = n => D.warnings.some(w=>w.seg===n);
// 内容块渲染：连续 row 合并成表格，p 块照录（K-lite-6 信息零丢失）
function renderBlocks(bl){
  let out='', tbl=null;
  const flushT=()=>{ if(tbl){ out+='<table>'+tbl.map((r,i)=>'<tr>'+r.map(c=>(i?'<td>':'<th>')+esc(c)+'</td>').join('')+'</tr>').join('')+'</table>'; tbl=null; } };
  (bl||[]).forEach(b=>{
    if(b.t==='row'){ (tbl=tbl||[]).push(b.cells||[]); }
    else if(b.t==='tbl'){ flushT(); out+='<table>'+(b.rows||[]).map((r,i)=>'<tr>'+r.map(c=>(i?'<td>':'<th>')+esc(c)+'</td>').join('')+'</tr>').join('')+'</table>'; }
    else { flushT(); out+='<div class="bd">'+esc(b.v)+'</div>'; }
  });
  flushT();
  return out;
}

function hdr(){
  const p = D.meta;
  document.getElementById('hdr').textContent =
    `模型 ${p.model} · 画幅 ${p.aspect} · ${p.seg_count} 段 / ${p.shot_count} 镜 / 总时长 ${p.total}s · 编译于 ${p.built_at}`;
  document.getElementById('bar').innerHTML =
    `<div class="chip k"><b>轻量校验</b> ${D.warnings.length} WARN / 0 ERROR</div>`
    + `<button class="chip" onclick="toggleOnly()" id="btnOnly">只看告警</button>`
    + `<button class="chip" onclick="exportSRT()">⬇ 导出 SRT 字幕</button>`
    + `<button class="chip" onclick="exportTimelineCSV()">⬇ 导出剪辑时间线 CSV</button>`
    + `<button class="chip" onclick="exportPayloadJSON()">⬇ 导出 API 载荷 JSON</button>`;
}
function toggleOnly(){ onlyBad=!onlyBad; document.getElementById('btnOnly').classList.toggle('on',onlyBad);
  document.getElementById('btnOnly').classList.toggle('warn',onlyBad); paint(); }

function segBtns(){
  document.getElementById('filters').innerHTML =
    `<button class="${curSeg===0?'on':''}" onclick="setSeg(0)">全部段</button>`
    + D.segments.map(s=>`<button class="${curSeg===s.no?'on':''}" onclick="setSeg(${s.no})">${esc(s.name)}</button>`).join('');
}
function setSeg(n){ curSeg=n; segBtns(); paint(); }
const vis = n => (!onlyBad || segBad(n)) && (curSeg===0 || curSeg===n);

function paint(){
  hdr(); segBtns();
  // 告警区
  const al = D.warnings.map(w=>`<div class="f"><span class="code">WARN</span>${esc(w.msg)}</div>`).join('');
  document.getElementById('alerts').innerHTML = al || '';
  // Ⅰ 创作基准
  document.getElementById('p0a').innerHTML = D.p0a.length
    ? '<table><tr><th style="width:150px">项</th><th>内容</th></tr>'
      + D.p0a.map(x=>`<tr><td>${esc(x.k)}</td><td>${esc(x.v)}</td></tr>`).join('') + '</table>'
    : '<div class="f">未解析到「## P0A · 创作基准」章节</div>';
  // Ⅱ 剧本
  document.getElementById('script').innerHTML = (D.script && D.script.length)
    ? `<div class="script"><div class="sc">${renderBlocks(D.script)}</div></div>`
    : '<div class="f">未解析到「## P1 · 剧本」章节</div>';
  // Ⅲ 视听圣经
  document.getElementById('bible').innerHTML = D.bible.length
    ? D.bible.map(b=>`<div class="bline"><div class="bv">${esc(b)}</div></div>`).join('')
    : '<div class="f">未解析到「## P1 · 视听圣经」章节</div>';
  // ① 时间轴泳道
  const total = Math.max(D.meta.total, 1);
  document.getElementById('lanes').innerHTML = D.segments.map(s=>{
    const cls = ['','s1','s2','s3'];
    const off = s.off||0;
    const blocks = s.shots.map((sh,i)=>{
      const a=sh.start+off, b=sh.end+off;
      const L=(a/total*100).toFixed(2), W=Math.max((b-a)/total*100,1.2).toFixed(2);
      return `<div class="blk ${cls[i%4]}" style="left:${L}%;width:${W}%"
        title="段内 ${sh.start}-${sh.end}s（整片 ${a}-${b}s） ${esc(sh.text)}">镜${sh.no}</div>`;
    }).join('');
    const ticks = Array.from({length:5},(_,i)=>{
      const t=Math.round(total*i/4);
      return `<span style="left:${i*25}%">${t}s</span>`;
    }).join('');
    return `<div class="lane ${vis(s.no)?'':'hide'}"><div class="nm">${esc(s.name)} · ${esc(String(s.shots.length))} 镜 · 整片 ${off}s 起</div>
      <div class="track">${blocks}</div><div class="ruler">${ticks}</div></div>`;
  }).join('') || '<div class="f">未解析到分镜段</div>';
  // ② 镜头卡矩阵
  document.getElementById('cards').innerHTML = D.segments.filter(s=>vis(s.no)).flatMap(s=>
    s.shots.map(sh=>`<div class="card"><div class="hd"><span>${esc(s.name)} · 镜${sh.no}</span>
      <span class="ph">[${sh.start}-${sh.end}s] · ${Math.max(sh.end-sh.start,0).toFixed(1).replace(/\.0$/,'')}s</span></div>
      <div class="row">${esc(sh.text)||'—'}</div></div>`)).join('')
    || '<div class="f">无镜头卡</div>';
  // ③ 投喂原文
  document.getElementById('prompts').innerHTML = D.segments.filter(s=>vis(s.no)).map((s,i)=>{
    const chips = Object.entries(s.fields||{}).map(([k,v])=>`<span class="c2">${esc(k)}：${esc(v)}</span>`).join('');
    return `<details class="pbox" open><summary>${esc(s.name)} · ${s.shots.length} 镜
      <button class="cpbtn" onclick="cp('p${s.no}',event)">复制投喂原文</button></summary>
      ${chips?`<div class="chips">${chips}</div>`:''}
      <pre id="p${s.no}">${esc(s.raw||'（未解析到围栏原文）')}</pre></details>`;
  }).join('') || '<div class="f">无投喂原文</div>';
  // ④ 资产总表
  document.getElementById('assets').innerHTML = D.assets.length
    ? '<table><tr><th>编码</th><th>名字</th><th>视图 / 出图</th><th>@图片N</th><th>附加信息</th><th>引用段</th></tr>'
      + D.assets.map(a=>`<tr><td>${esc(a.code)}</td><td>${esc(a.name||'—')}</td><td>${esc(a.view||'—')}</td>
        <td><span class="aimg">${esc(a.img||'未绑图')}</span></td>
        <td class="asegs">${esc((a.extra||[]).join(' · '))||'—'}</td>
        <td class="asegs">${(a.refs||[]).length?('段 '+a.refs.join('、')):'—'}</td></tr>`).join('') + '</table>'
    : '<div class="f">未解析到「## P2 · 资产清单」表格</div>';
  // ⑤ 资产出图提示词（与 ③ 必同板）；同一场景资产的连排条目（①②③）聚合为一张卡
  const _grp=[];
  D.aprompts.forEach((a,i)=>{
    const g=_grp[_grp.length-1];
    if(g&&g.code===a.code&&a.code.indexOf('@SCN-')===0){g.items.push([a,i]);}
    else{_grp.push({code:a.code,items:[[a,i]]});}
  });
  document.getElementById('aprompts').innerHTML = _grp.length
    ? _grp.map(g=>{
        if(g.items.length===1){
          const [a,i]=g.items[0];
          return `<details class="pbox" open><summary>${esc(a.code)} · ${esc(a.name)}
            <button class="cpbtn" onclick="cp('a${i}',event)">复制出图提示词</button></summary>
            ${a.desc?`<div class="note">${esc(a.desc)}</div>`:''}
            <pre id="a${i}">${esc(a.raw||'（未解析到围栏原文）')}</pre></details>`;
        }
        return `<details class="pbox" open><summary>${esc(g.code.replace(/^@/,''))} · ${g.items.map(x=>esc(x[0].name)).join('+')} · ${g.items.length} 条复制全部
            <button class="cpbtn" onclick="cpall('${g.items.map(x=>'a'+x[1]).join('|')}',event)">复制全部</button></summary>
            ${g.items.map(([a,i])=>`<div class="note">${esc(a.name)}
              <button class="cpbtn" onclick="cp('a${i}',event)">复制本条</button></div>
              <pre id="a${i}">${esc(a.raw||'（未解析到围栏原文）')}</pre>`).join('')}
          </details>`;
      }).join('')
    : '<div class="f">未解析到「## P2.5 · 资产出图提示词」（K-lite-4：与 ③ 必同板，请补齐章节）</div>';
  // ⑥ 发布文案
  document.getElementById('copy').innerHTML = D.publish.length
    ? D.publish.map(x=>`<div class="kv"><div class="k2">${esc(x.k)}</div><div class="v2">${esc(x.v)}</div></div>`).join('')
    : '<div class="f">未解析到「## P6 · 发布文案」（可选章节）</div>';
  // ⑦ 其他章节（未归区原文照录）
  document.getElementById('extras').innerHTML = (D.extras && D.extras.length)
    ? D.extras.map(x=>`<details class="pbox"><summary>${esc(x.title)}</summary>${renderBlocks(x.body)||'<div class="f">（空）</div>'}</details>`).join('')
    : '<div class="note">（无未归区章节）</div>';
  document.getElementById('foot').textContent =
    `本看板为轻量版：无 N1~N11 深度检查层与状态快照；深度质检请走 P0~P2 质检门禁与改动后复验铁律。`;
}

function cp(id,e){
  const t=document.getElementById(id).innerText;
  const done=()=>{e.target.innerText='已复制 ✓';setTimeout(()=>{e.target.innerText='复制';},1200);};
  const fail=()=>{e.target.innerText='复制失败·请手动全选';setTimeout(()=>{e.target.innerText='复制';},2000);};
  const legacy=()=>{try{const ta=document.createElement('textarea');ta.value=t;ta.style.position='fixed';ta.style.opacity='0';document.body.appendChild(ta);ta.focus();ta.select();const ok=document.execCommand('copy');document.body.removeChild(ta);ok?done():fail();}catch(err){fail();}};
  if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(t).then(done).catch(legacy);}
  else{legacy();}
}

function cpall(ids,e){
  const t=ids.split('|').map(id=>document.getElementById(id).innerText).join('\\n\\n');
  const done=()=>{e.target.innerText='已复制全部 ✓';setTimeout(()=>{e.target.innerText='复制全部';},1500);};
  const fail=()=>{e.target.innerText='复制失败·请手动全选';setTimeout(()=>{e.target.innerText='复制全部';},2000);};
  const legacy=()=>{try{const ta=document.createElement('textarea');ta.value=t;ta.style.position='fixed';ta.style.opacity='0';document.body.appendChild(ta);ta.focus();ta.select();const ok=document.execCommand('copy');document.body.removeChild(ta);ok?done():fail();}catch(err){fail();}};
  if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(t).then(done).catch(legacy);}
  else{legacy();}
}
function dlBlob(filename, content, mime){
  const blob = new Blob([content], {type: mime});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(()=>{ URL.revokeObjectURL(a.href); document.body.removeChild(a); }, 300);
}
function fmtSrtTime(sec){
  // ★2026-10-15 修复：原 `ms=Math.round((sec%1)*1000)` + `totalS=Math.floor(sec)` 分立取整，
  //   小数位进位到 1000ms 时印出非法 `,1000` 且秒位不结转。改为「总毫秒一次取整再拆分」。
  let totalMs = Math.round(sec * 1000); if (totalMs < 0) totalMs = 0;
  const ms = totalMs % 1000;
  const totalS = Math.floor(totalMs / 1000);
  const s = totalS % 60;
  const m = Math.floor(totalS / 60) % 60;
  const h = Math.floor(totalS / 3600);
  const pad = (n, w=2) => String(n).padStart(w, '0');
  return `${pad(h)}:${pad(m)}:${pad(s)},${pad(ms, 3)}`;
}
function exportSRT(){
  let idx = 1, lines = [];
  (D.segments||[]).forEach(seg=>{
    const off = seg.off || 0;
    (seg.shots||[]).forEach(sh=>{
      const m = (sh.text||'').match(/台词：【([^】]+)】/);
      if(m && !m[1].startsWith('无')){
        lines.push(`${idx++}\n${fmtSrtTime(off + sh.start)} --> ${fmtSrtTime(off + sh.end)}\n${m[1]}\n`);
      }
    });
  });
  dlBlob((D.meta&&D.meta.title||'short_drama') + '.srt', lines.join('\n') || '1\n00:00:00,000 --> 00:00:02,000\n（无对白）\n', 'text/plain;charset=utf-8');
}
function exportTimelineCSV(){
  const escCsv = v => '"' + String(v==null?'':v).replace(/"/g, '""') + '"';
  const rows = [['段落', '镜号', '段内起秒', '段内止秒', '全片起秒', '全片止秒', '时长(s)', '镜头内容与台词']];
  (D.segments||[]).forEach(seg=>{
    const off = seg.off || 0;
    (seg.shots||[]).forEach(sh=>{
      rows.push([seg.name, '镜'+sh.no, sh.start, sh.end, off+sh.start, off+sh.end, Math.max(sh.end-sh.start,0), sh.text||'']);
    });
  });
  const csv = '\uFEFF' + rows.map(r=>r.map(escCsv).join(',')).join('\r\n');
  dlBlob((D.meta&&D.meta.title||'short_drama') + '_timeline.csv', csv, 'text/csv;charset=utf-8');
}
function exportPayloadJSON(){
  dlBlob((D.meta&&D.meta.title||'short_drama') + '_payload.json', JSON.stringify(D, null, 2), 'application/json;charset=utf-8');
}
paint();
</script></body></html>"""


def export_srt_text(data):
    idx, lines = 1, []
    def _fmt(sec):
        # ★2026-10-15 修复：原 `ms=int(round((sec%1)*1000))` + `ts=int(sec)` 分立取整，
        #   当小数位进位到 1000ms（如 4.9995→round(999.5)=1000）会印出非法 `,1000`
        #   且秒位不结转 → 00:00:04,1000（SRT 毫秒须 000~999）。改为「总毫秒一次取整再拆分」。
        total_ms = max(int(round(sec * 1000)), 0)
        ms = total_ms % 1000
        ts = total_ms // 1000
        return f"{ts // 3600:02d}:{(ts // 60) % 60:02d}:{ts % 60:02d},{ms:03d}"
    for seg in data.get("segments", []):
        off = seg.get("off", 0)
        for sh in seg.get("shots", []):
            m = re.search(r"台词：【([^】]+)】", sh.get("text", ""))
            if m and not m.group(1).startswith("无"):
                lines.append(f"{idx}\n{_fmt(off + sh['start'])} --> {_fmt(off + sh['end'])}\n{m.group(1)}\n")
                idx += 1
    return "\n".join(lines)


def export_timeline_csv_text(data):
    def _esc(v):
        return '"' + str(v if v is not None else "").replace('"', '""') + '"'
    rows = [["段落", "镜号", "段内起秒", "段内止秒", "全片起秒", "全片止秒", "时长(s)", "镜头内容与台词"]]
    for seg in data.get("segments", []):
        off = seg.get("off", 0)
        for sh in seg.get("shots", []):
            rows.append([
                seg.get("name", ""),
                f"镜{sh.get('no', '')}",
                sh.get("start", 0),
                sh.get("end", 0),
                off + sh.get("start", 0),
                off + sh.get("end", 0),
                round(max(sh.get("end", 0) - sh.get("start", 0), 0), 2),
                sh.get("text", ""),
            ])
    return "\ufeff" + "\r\n".join(",".join(_esc(c) for c in r) for r in rows)


def self_test():
    sample = (
        "# 测试剧目\n"
        "## 【投喂单元 1/1 · 段1】\n"
        "```text\n"
        "【画幅风格】16:9 横屏\n"
        "【场景资产】@走廊\n"
        "【核心人物】@甲\n"
        "【站位声明】@甲（画面左）\n"
        "【时间轴分镜】\n"
        "[镜头1] 0-4秒，中景·平视·固定。甲开口。声音：无。台词：【甲（低沉）：“你好。”】\n"
        "[镜头2] 4-8秒，近景·平视·缓推。甲定格。声音：无。台词：无。\n"
        "【音效】环境音\n"
        "【强制禁止项】无字幕\n"
        "```\n"
    )
    d = parse_md(sample)
    assert len(d["segments"]) == 1 and len(d["segments"][0]["shots"]) == 2
    assert d["canvas_contract"]["schema"] == "drama-canvas-feed/v2"
    assert d["segments"][0]["canvas"]["model"] == "doubao-seedance-2-0"
    assert d["segments"][0]["canvas"]["duration"] == 8
    srt = export_srt_text(d)
    csv_txt = export_timeline_csv_text(d)
    html = build_html(d)
    assert "00:00:00,000 --> 00:00:04,000" in srt and "你好" in srt
    assert "镜1" in csv_txt and "exportSRT()" in html and "exportTimelineCSV()" in html

    # 通用性锁：全新剧名、全新角色、无历史台账也必须独立生成完整音色。
    _generic_data = enrich_canvas_contract({
        "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
        "asset_prompts": [{
            "code": "图21",
            "name": "林夏（角色立绘）",
            "raw": (
                "一名 27 岁 女性。\n"
                "**① Voice Description 完整版（不进图）**: A 27-year-old female voice, clear and restrained.\n"
                "**② Voice Description Label（进图）**: clear restrained female voice\n"
            ),
        }],
        "segments": [{
            "raw": (
                "【核心人物】@角色｜林夏（图21）\n"
                "【站位声明】@角色｜林夏（图21）（画面左）\n"
                "台词：林夏（平静）：\"开始吧。\""
            ),
            "shots": [],
        }],
    }, {})
    _generic_voice = _generic_data["voice_prompts"][0]
    assert _generic_voice["code"] == "图21"
    assert _generic_voice["name"] == "林夏"
    assert "A female aged 27" in _generic_voice["prompt"]
    assert _generic_voice["age"] == "27"
    assert _generic_voice["gender"] == "female"
    assert "@音频｜林夏（图21）" in _generic_data["segments"][0]["raw"]

    # ---- 2026-10-26 源头合同锁：同图号场景只留母版，跨集角色图号锁死，提示词内嵌完整资产名 ----
    _ledger = {
        "character:钱万金:default": {
            "identity": "character:钱万金:default",
            "kind": "character",
            "name": "钱万金",
            "variant": "default",
            "code": "图2",
            "voicePrompt": (
                "Low, oily command tone. A male voice, cold and oily with a smooth command tone. "
                "A male aged 48-54, speaking standard Mandarin, short drama dubbing "
                "reference clip, 4.5 seconds clean voice, no background noise."
            ),
            "voiceLabel": "low oily command voice",
            "voiceDescription": "A male voice, cold and oily with a smooth command tone.",
            "voiceFormat": "audio-prompt-v1",
        },
    }
    _canvas_data = enrich_canvas_contract({
        "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
        "asset_prompts": [
            {"code": "CHR-旧稿", "img": "图27", "name": "钱万金（角色立绘）", "raw": "### 图27 · 钱万金"},
            {"code": "图29", "name": "街口饭馆（场景母版）", "raw": "### 图29 · 街口饭馆母版"},
            {"code": "图29", "name": "街口饭馆（九宫格）", "raw": "### 图29 · 街口饭馆九宫格"},
            {"code": "图32", "name": "匿名纸条（道具设定图）", "raw": "### 图32 · 匿名纸条"},
        ],
        "segments": [{
            "raw": (
                "【画幅风格】9:16 竖屏（model=seedance-2.0）\n"
                "【场景资产】@//scn/ - 街口饭馆（图29）（母版+九宫格双挂）\n"
                "【核心人物】@//chr/ - 钱万金（图27）\n"
                "【站位声明】@钱万金（画面左）\n"
                "钱万金（图27）进入饭馆，把纸条（图32）压进账本。"
                "台词：钱万金（低沉·语速缓）：\"账本拿来。\""
            ),
            "shots": [],
        }],
    }, _ledger)
    assert [a["code"] for a in _canvas_data["asset_prompts"]] == ["图2", "图29", "图32"]
    assert _canvas_data["asset_prompts"][0]["semanticCode"] == "CHR-旧稿"
    assert all(
        asset["canvas"]["clarity"] == "2K"
        and asset["canvas"]["size"] == "2048x1152"
        for asset in _canvas_data["asset_prompts"]
    )
    _raw = _canvas_data["segments"][0]["raw"]
    assert "@角色｜钱万金（图2）" in _raw and "图27" not in _raw
    assert "@场景｜街口饭馆·母版（图29）（母版）" in _raw
    assert "@道具｜匿名纸条（图32）" in _raw
    assert "【音色引用】@音频｜钱万金（图2）" in _raw
    assert _raw.index("【核心人物】") < _raw.index("【音色引用】") < _raw.index("【站位声明】")
    assert "model=" not in _raw
    assert "九宫格" not in _raw and "@//" not in _raw
    assert _canvas_data["voice_prompts"][0]["code"] == "图2"
    assert _canvas_data["voice_prompts"][0]["prompt"].startswith("Low, oily command tone")
    assert _canvas_data["voice_prompts"][0]["format"] == "audio-prompt-v1"
    assert _canvas_data["canvas_contract"]["asset_ledger"][0]["voicePrompt"].startswith("Low, oily command tone")
    _voice_golden = _canvas_voice_fields(
        "一名 48~54 岁 男性。\n"
        "**① Voice Description 完整版（不进图）**: A 48~54-year-old male voice, cold and oily with a smooth "
        "command tone and clipped, confident rhythm; slick and magnanimous on the surface, "
        "turning brittle and shrill when cornered. Ideal for short drama dubbing of a factory director.\n"
        "**② Voice Description Label（进图）**: cold, oily, command tone\n"
    )
    assert "A male aged 48-54" in _voice_golden["voicePrompt"]
    assert _voice_golden["voiceAge"] == "48-54"
    _voice_single_age = _canvas_voice_fields(
        "一名 42 岁 男性。\n"
        "**① Voice Description 完整版（不进图）**: A 42-year-old male voice, low and restrained.\n"
        "**② Voice Description Label（进图）**: low restrained male voice\n"
    )
    assert "A male aged 42" in _voice_single_age["voicePrompt"]
    assert "42-year-old male" not in _voice_single_age["voicePrompt"]
    _speaker_scoped = _ensure_audio_prompt_refs(
        (
            "【核心人物】@角色｜钱万金（图2）；@角色｜贾富贵（图3）\n"
            "【音色引用】@音频｜钱万金（图2）；@音频｜贾富贵（图3）\n"
            "【站位声明】@角色｜钱万金（图2）；@角色｜贾富贵（图3）\n"
            '台词：钱万金（低沉）："账本拿来。"\n'
            "贾富贵猛地抬头，台词：无"
        ),
        [
            {"kind": "character", "base": "钱万金", "title": "角色｜钱万金（图2）"},
            {"kind": "character", "base": "贾富贵", "title": "角色｜贾富贵（图3）"},
        ],
    )
    assert "【音色引用】@音频｜钱万金（图2）" in _speaker_scoped
    assert "@音频｜贾富贵（图3）" not in _speaker_scoped

    _variant_ledger = {
        "character:钱万金:default": {
            "identity": "character:钱万金:default",
            "kind": "character",
            "name": "钱万金",
            "variant": "default",
            "code": "图2",
        },
        "character:钱万金:old": {
            "identity": "character:钱万金:old",
            "kind": "character",
            "name": "钱万金",
            "variant": "old",
            "code": "图24",
        },
    }
    _variant_data = enrich_canvas_contract({
        "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
        "asset_prompts": [{
            "code": "图39",
            "name": "钱万金（角色立绘）",
            "canvas": {"variantKey": "young"},
            "raw": "### 图39 · 钱万金青年形态",
        }],
        "segments": [{
            "raw": "钱万金（图39）进入画面；钱万金（图2）站在门外。",
            "shots": [],
        }],
    }, _variant_ledger)
    _young = _variant_data["asset_prompts"][0]
    assert _young["code"] == "图39"
    assert _young["canvas"]["assetId"] == "character:钱万金:young"
    assert "@角色｜钱万金·young（图39）" in _variant_data["segments"][0]["raw"]

    # ---- 2026-10-14 Agent 修复回归锁：半秒时码 MM:SS.s 必须被正确解析，且不得丢镜 ----
    for _tok, _exp in (("00:10.5", 10.5), ("00:03.5", 3.5), ("00:12.5", 12.5),
                       ("00:04", 4.0), ("12.5", 12.5), ("0-3秒", None)):
        if _exp is not None:
            _got = to_seconds(_tok)
            assert abs(_got - _exp) < 1e-9, f"to_seconds({_tok!r}) = {_got}，应为 {_exp}"
    sample2 = (
        "# 半秒时码回归用例\n"
        "## 【投喂单元 1/1 · 段1】\n"
        "```text\n"
        "【画幅风格】16:9 横屏\n"
        "【场景资产】@走廊\n"
        "【核心人物】@甲\n"
        "【站位声明】@甲（画面左）\n"
        "【时间轴分镜】\n"
        "00:00-00:04 [镜头1] 中景 · 平视 · 固定。甲开口。声音：无。台词：【甲（低沉）：“一。”】\n"
        "00:04-00:08 [镜头2] 近景 · 平视 · 缓推。甲转头。声音：无。台词：无。\n"
        "00:08-00:10.5 [镜头3] 特写 · 平视 · 固定。甲眨眼。声音：无。台词：无。\n"
        "00:10.5-00:12.5 [镜头4] 近景 · 斜角 · 极缓慢推。甲抬手。声音：无。台词：无。\n"
        "00:12.5-00:15 [镜头5] 中景 · 正面 · 缓拉。甲定格。声音：无。台词：无。\n"
        "【音效】环境音\n"
        "【强制禁止项】无字幕\n"
        "```\n"
    )
    d2 = parse_md(sample2)
    _shots2 = d2["segments"][0]["shots"]
    assert len(_shots2) == 5, f"半秒时码稿应解析出 5 镜，实得 {len(_shots2)} 镜（丢镜即本用例拦截目标）"
    assert abs(_shots2[2]["end"] - 10.5) < 1e-9 and abs(_shots2[3]["start"] - 10.5) < 1e-9, \
        "半秒时码应解为 10.5s（原缺陷为 10.05s）"
    assert not any("丢镜" in w["msg"] or "跳过" in w["msg"] for w in d2["warnings"]), \
        f"半秒时码稿不应报丢镜告警：{[w['msg'] for w in d2['warnings']]}"

    # ---- 2026-10-15 Agent 修复回归锁：SRT 毫秒进位 ----
    #   原 `_fmt` 用 `ms=int(round((sec%1)*1000))` + `ts=int(sec)` 分立取整，
    #   小数位进位到 1000ms（如 4.9995→round(999.5)=1000）会印出非法 `,1000` 且秒位不结转。
    #   改为「总毫秒一次取整再拆分」后，4.9995s 必须结转为 00:00:05,000。
    _d3 = parse_md(
        "# SRT进位\n"
        "## 【投喂单元 1/1 · 段1】\n"
        "```text\n"
        "【画幅风格】16:9 横屏\n"
        "【场景资产】@走廊\n"
        "【核心人物】@甲\n"
        "【站位声明】@甲（画面左）\n"
        "【时间轴分镜】\n"
        "0-4.9995s [镜头1] 中景。甲。台词：【甲：“进位测试。”】\n"
        "```\n"
    )
    _srt3 = export_srt_text(_d3)
    assert ",1000" not in _srt3, f"SRT 毫秒不得进位成非法 ,1000：{_srt3!r}"
    assert "00:00:05,000" in _srt3, f"4.9995s 应结转为 00:00:05,000：{_srt3!r}"
    assert "00:00:00,000 --> 00:00:05,000" in _srt3, f"进位后起止应连续：{_srt3!r}"

    # ---- 2026-10-26 源头合同锁：剧情实物文字不得被“全禁文字”误删 ----
    _material_text = _normalize_material_text_policy(
        "登记表上有「注册日期」；庆典横幅写着「改制庆典」。\n"
        "【强制禁止项】文字：禁任何文字/UI/角标；音频：仅环境音"
    )
    assert "禁任何文字" not in _material_text
    assert "登记表上的指定字样" in _material_text
    assert "庆典横幅上的指定字样" in _material_text
    assert "文字：禁字幕、水印、UI与角标" in _material_text
    assert "站姿端坐" not in _normalize_asset_prompt_contract("站姿端坐桌前")
    assert "站姿站立于桌前" in _normalize_asset_prompt_contract("站姿端坐桌前")

    _material_data = enrich_canvas_contract({
        "meta": {"画幅": "9:16", "模型版本": "Seedance 2.0"},
        "asset_prompts": [{
            "code": "图1",
            "name": "办事员（角色立绘）",
            "raw": "站姿端坐桌前，禁止坐姿。",
            "fence_body": ["站姿端坐桌前，禁止坐姿。"],
        }],
        "segments": [{
            "name": "段1 · 文字合同",
            "raw": (
                "【场景资产】@道具｜清算组登记表（图2）；@道具｜庆典横幅（图3）\n"
                "【强制禁止项】文字：禁任何文字/UI/角标；音频：仅环境音"
            ),
            "fence_body": [
                "【场景资产】@道具｜清算组登记表（图2）；@道具｜庆典横幅（图3）",
                "【强制禁止项】文字：禁任何文字/UI/角标；音频：仅环境音",
            ],
            "shots": [],
        }],
    })
    assert "禁任何文字" not in _material_data["segments"][0]["raw"]
    assert all(
        "禁任何文字" not in line
        for line in _material_data["segments"][0]["fence_body"]
    )
    assert "站姿站立于桌前" in _material_data["asset_prompts"][0]["raw"]
    assert "站姿站立于桌前" in _material_data["asset_prompts"][0]["fence_body"][0]

    print("[+] build_board_lite self-test PASSED")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?")
    ap.add_argument("--out", default=None)
    ap.add_argument("--export-srt", default=None, help="同时导出 .srt 字幕文件")
    ap.add_argument("--export-csv", default=None, help="同时导出剪辑时间线 .csv 文件")
    ap.add_argument("--export-json", default=None, help="同时导出结构化 API 载荷 .json 文件")
    ap.add_argument(
        "--asset-ledger",
        default=None,
        help="系列资产总账 JSON 或目录；省略时自动读取输出目录内既往画布载荷",
    )
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        sys.exit(self_test())
    if not a.src:
        ap.print_help()
        sys.exit(2)
    src = Path(a.src)
    if not src.exists():
        print(f"[X] 源文件不存在：{src}")
        sys.exit(1)
    out = Path(a.out) if a.out else src.with_name(src.stem + "_分镜看板.html")
    ledger = {}
    if a.asset_ledger:
        ledger_path = Path(a.asset_ledger)
        if ledger_path.is_dir():
            ledger = discover_canvas_asset_ledger(ledger_path, exclude=a.export_json)
        elif ledger_path.exists():
            ledger = _load_canvas_asset_ledger([ledger_path])
    else:
        ledger_dir = Path(a.export_json).parent if a.export_json else src.parent
        ledger = discover_canvas_asset_ledger(ledger_dir, exclude=a.export_json)
    if not ledger:
        print("[i] 未发现历史资产台账：按独立新剧模式生成；跨集一致性请传入 --asset-ledger")
    data = parse_md(src.read_text(encoding="utf-8"), asset_ledger=ledger)
    out.write_text(build_html(data), encoding="utf-8")
    n_shots = sum(len(s["shots"]) for s in data["segments"])
    print(f"[+] 段落：{len(data['segments'])} · 镜头：{n_shots} · 资产：{len(data['assets'])} · 出图提示词：{len(data['asset_prompts'])}")
    print(f"[+] 总时长：{total_duration(data):g}s · WARN：{len(data['warnings'])}")
    for w in data["warnings"]:
        print(f"    ⚠ {w['msg']}")
    print(f"[+] 看板：{out}")
    if a.export_srt:
        Path(a.export_srt).write_text(export_srt_text(data), encoding="utf-8")
        print(f"[+] SRT 字幕：{a.export_srt}")
    if a.export_csv:
        Path(a.export_csv).write_text(export_timeline_csv_text(data), encoding="utf-8")
        print(f"[+] 剪辑时间线 CSV：{a.export_csv}")
    if a.export_json:
        validate_export_contract(data)
        Path(a.export_json).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[+] API 载荷 JSON：{a.export_json}")


if __name__ == "__main__":
    main()
