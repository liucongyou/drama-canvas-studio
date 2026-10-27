# -*- coding: utf-8 -*-
"""ark_client.py — 火山方舟 API 客户端（标准库 urllib · 零第三方依赖）

统一走方舟：
  - 资产生图：Seedream 5.0，1.5K（1536×1536 ≈ 236万像素，0.3元/张）
  - 视频生成：Seedance 2.0
  - 音色参考：TTS 生成 4.5S 干净人声（供 Seedance 角色音色参考）
密钥从 config.json 读取；未配置密钥时返回明确错误，不静默跳过。
"""
import json
import os
import time
import urllib.request
import urllib.error

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")

ARK_HOST = "https://ark.cn-beijing.volces.com/api/v3"

DEFAULTS = {
    "ark_api_key": "",
    "seedream_model": "doubao-seedream-5-0-pro-260628",
    "seedream_size": "1536x1536",
    "seedance_model": "doubao-seedance-2-0-mini",
    "seedance_ratio": "9:16",
    "tts_model": "",
}


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = {}
    merged = dict(DEFAULTS)
    merged.update(cfg)
    return merged


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _post(path, body):
    cfg = load_config()
    if not cfg.get("ark_api_key"):
        raise RuntimeError("未配置方舟 API Key（config.json 的 ark_api_key 为空）")
    url = ARK_HOST + path
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg['ark_api_key']}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"方舟 API {e.code}: {detail}") from e


def generate_image(prompt, size=None, model=None, refs=None, guidance=None):
    """资产生图 · Seedream · 默认 1.5K（1536×1536 · 用户钦定 0.3 元/张）。

    refs: [{url, label}] 参考图——第一张传 `image`（Seedream 参考图参数），
          多余参考图以「参考图N: <label> <url>」并入 prompt（模型不支持多图参数时的兜底）。
    guidance: 推理强度 → 映射 quality 增强词（模型不支持真参数，作提示词增强）。
    """
    cfg = load_config()
    size = size or cfg.get("seedream_size", "1536x1536")
    model = model or cfg.get("seedream_model", DEFAULTS["seedream_model"])
    body = {"model": model, "prompt": prompt, "size": size, "response_format": "url"}
    if refs:
        urls = [r.get("url") for r in refs if r.get("url")]
        if urls:
            body["image"] = urls[0]
        # ★★ 参考图清单必须**语义化**，不能把 URL 当清单项塞进 prompt。
        #   旧写法是「参考图1: 资产图 (https://…)」——两个致命问题：
        #     ① label 若为泛称（资产图/角色音色），模型根本分不清哪张是谁；
        #     ② URL 对模型是无意义噪声，占掉大量 prompt 预算，还容易诱导它复述链接。
        #   现在只列「序号 + 真实名称」，并明确标出第一张是 API 级主参考图。
        listed = [(r.get("label") or "").strip() for r in refs if r.get("url")]
        extra = []
        for i, label in enumerate(listed):
            tag = "主参考图" if i == 0 else f"参考图{i + 1}"
            extra.append(f"{tag}: {label}")
        if extra:
            body["prompt"] = prompt + "\n\n[参考素材清单]\n" + "\n".join(extra)
            # ★ 单图接口限制要显式说明：超出第一张的素材只作为语义提示，
            #   否则用户会以为 7 张全被当图喂进去了，出图对不上却查不出原因。
            if len(listed) > 1:
                body["prompt"] += (
                    f"\n（说明：本模型仅支持 1 张图像级参考，已用「{listed[0]}」作为主参考；"
                    f"其余 {len(listed) - 1} 张仅按名称参与语义描述）"
                )
    if guidance:
        body["prompt"] += _GUIDANCE_WORDS.get(guidance, "")
    resp = _post("/images/generations", body)
    items = (resp.get("data") or [])
    if not items:
        raise RuntimeError(f"方舟出图无返回：{str(resp)[:300]}")
    return items[0].get("url"), resp


# 推理强度 → 提示词质量增强词（Seedream 无官方强度参数，作为提示词增强映射，界面已注明）
_GUIDANCE_WORDS = {
    "轻度": "\nlight detail, keep loose and natural",
    "中等": "\nbalanced detail, standard quality",
    "高": "\nhigh detail, refined textures, sharp focus",
    "极高": "\nultra-high detail, intricate textures, crisp edges, rich material fidelity",
    "最高": "\nextreme detail, photorealistic fidelity, maximum sharpness, perfect material accuracy",
}


def generate_video(prompt, ratio=None, duration=15, resolution="480P",
                   model=None, refs=None, voice=None, voices=None):
    """视频生成 · Seedance。

    refs: [{url, label}] 资产/参考图引用 → content 数组 image_url 项（若 API 支持多图）；
          Seedance content 支持 [{type:text},{type:image_url,...}]。
    voice: {prompt, url, label} 单个角色音色参考（旧参数，仍兼容）。
    voices: [{prompt, url, label}] **多角色音色参考**（第 17 轮）。
      为什么必须支持多值：一段戏里钱万金、陈大强各说各的，各有各的音色。
      旧实现只收单值 voice，前端连 4 个音频节点时后连的覆盖先连的，
      最终只有 1 个音色进模型 —— 这就是"连了 4 个只带 1 个"的投喂侧根因。

    ★★ 为什么必须把「素材名 → 第 N 张图」的对应关系写进 text：
      content 里的 image_url 是无名数组，模型只知道"有几张图、按顺序"，
      **完全不知道第 3 张是「角色｜钱万金」还是「道具｜买断协议」**。
      7 段视频各自连了 4~7 张资产图，模型只能瞎猜哪张对谁 →
      表现就是用户反馈的「连线后引用资产对不上」，且每次错法都不一样。
      修法：在 text 里显式列出「图1 = 真实节点名」的映射，让顺序有名可依。
      这个映射必须是**真实节点名**（上游 _resolve_refs 已保证），不能是「资产图」泛称。
    """
    cfg = load_config()
    ratio = ratio or cfg.get("seedance_ratio", "9:16")
    model = model or cfg.get("seedance_model", DEFAULTS["seedance_model"])
    duration = max(4, min(30, int(duration or 15)))
    content = [{"type": "text", "text": prompt}]
    if refs:
        usable = [r for r in refs if r.get("url")]
        for r in usable:
            content.append({"type": "image_url", "image_url": {"url": r["url"]}})
        # 素材顺序表：与上面 image_url 的追加顺序严格一一对应（同一次循环，绝不另起排序）
        if usable:
            lines = []
            for i, r in enumerate(usable):
                label = (r.get("label") or "").strip() or f"素材{i + 1}"
                lines.append(f"图{i + 1} = {label}")
            content[0]["text"] += "\n\n[参考图顺序] " + "；".join(lines)

    # ── 音色：合并 voice（旧单值）与 voices（新多值），去重保序 ──
    all_voices = []
    seen_v = set()
    for v in ([voice] if voice else []) + list(voices or []):
        if not v:
            continue
        key = (v.get("label") or "", v.get("url") or "", (v.get("prompt") or "")[:40])
        if key in seen_v:
            continue
        seen_v.add(key)
        all_voices.append(v)
    if all_voices:
        vlines = []
        for i, v in enumerate(all_voices):
            vp = v.get("prompt", "")
            vlabel = (v.get("label") or f"角色{i + 1}").strip()
            # 每个音色的音频 URL 也作为 image_url 传进去（Seedance 侧对音频参考的承载方式）
            if v.get("url"):
                content.append({"type": "image_url", "image_url": {"url": v["url"]}})
            if vp:
                vlines.append(f"{vlabel}: {vp}")
            else:
                vlines.append(f"{vlabel}（音色参考素材已随请求上传）")
        if vlines:
            content[0]["text"] += (
                f"\n\n[角色音色参考 · 共 {len(all_voices)} 位，按连线顺序]\n" + "\n".join(vlines)
            )
    resp = _post("/videos/generations", {
        "model": model,
        "content": content,
        "resolution": resolution,
        "duration": duration,
        "rate": ratio,
    })
    # Seedance 任务为异步：返回 task_id，需轮询
    task_id = resp.get("id")
    if not task_id:
        raise RuntimeError(f"方舟视频无任务 id：{str(resp)[:300]}")
    return _poll_video(task_id)


def _poll_video(task_id, max_wait=600):
    cfg = load_config()
    url = f"{ARK_HOST}/videos/get?id={task_id}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {cfg['ark_api_key']}"})
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                info = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"方舟视频查询失败 {e.code}") from e
        status = info.get("status")
        if status in ("succeeded", "success", "completed"):
            urls = []
            for it in (info.get("output", {}) or {}).get("video_urls", []):
                if isinstance(it, dict):
                    urls.append(it.get("url"))
                else:
                    urls.append(it)
            return urls[0] if urls else info
        if status in ("failed", "error", "cancelled"):
            raise RuntimeError(f"方舟视频生成失败：{str(info)[:300]}")
        time.sleep(5)
    raise RuntimeError("方舟视频生成超时（600s）")


def generate_tts(text, voice="zh_female_shuangkuaisisi_moon_bigtts", duration_hint=4.5):
    """音色参考 · 4.5S 干净人声（方舟 TTS）。模型未配置时返回提示。"""
    cfg = load_config()
    model = cfg.get("tts_model")
    if not model:
        raise RuntimeError("未配置 tts_model（config.json）——音色生成走方舟语音合成，需填写模型名")
    resp = _post("/audio/speech", {
        "model": model,
        "input": text,
        "voice": voice,
        "response_format": "wav",
    })
    return resp


# 文本节点可用模型（方舟 Responses / Chat Completions 通用对话模型）
TEXT_MODELS = [
    "doubao-seed-1-6-250615",
    "doubao-seed-1-6-flash-250615",
    "deepseek-v3-250324",
]


def generate_text(prompt, model=None, system=None, max_tokens=2000, temperature=0.7, refs=None):
    """文本生成 · 方舟对话模型（Chat Completions）。

    用于「文本节点」的 AI 生成：用户在卡片里写意图（如"写一段本集梗概"），
    这里返回纯文本，前端直接覆盖节点正文。

    refs: [{label, text}] 已生成的上游文本引用（其它文本节点 / 资产说明），
          作为额外上下文并入 system，让模型沿用同一套设定。
    model 留空时用 config.json 的 arange_text_model，再退到 TEXT_MODELS[0]。
    """
    cfg = load_config()
    model = model or cfg.get("text_model") or TEXT_MODELS[0]
    sys_parts = []
    if system:
        sys_parts.append(system)
    for r in refs or []:
        label = r.get("label") or "参考"
        text = (r.get("text") or "").strip()
        if text:
            sys_parts.append(f"【{label}】\n{text}")
    messages = []
    if sys_parts:
        messages.append({"role": "system", "content": "\n\n".join(sys_parts)})
    messages.append({"role": "user", "content": prompt or ""})
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max(64, min(8000, int(max_tokens or 2000))),
        "temperature": max(0.0, min(2.0, float(temperature if temperature is not None else 0.7))),
    }
    resp = _post("/chat/completions", body)
    choices = resp.get("choices") or []
    if not choices:
        raise RuntimeError(f"方舟文本无返回：{str(resp)[:300]}")
    msg = choices[0].get("message") or {}
    text = msg.get("content")
    if isinstance(text, list):
        # 少数模型返回分段内容 [{type:'text', text:'...'}]
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
    if not (text or "").strip():
        raise RuntimeError("方舟返回空文本")
    return text.strip(), resp


def config_status():
    cfg = load_config()
    return {
        "ark_api_key": bool(cfg.get("ark_api_key")),
        "seedream_model": cfg.get("seedream_model"),
        "seedream_size": cfg.get("seedream_size"),
        "seedance_model": cfg.get("seedance_model"),
        "seedance_ratio": cfg.get("seedance_ratio"),
        "tts_model": cfg.get("tts_model"),
        "text_model": cfg.get("text_model") or TEXT_MODELS[0],
    }
