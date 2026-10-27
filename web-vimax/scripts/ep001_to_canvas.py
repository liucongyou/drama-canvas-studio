# -*- coding: utf-8 -*-
"""EP001 喂投载荷 → ViMax/BeefTV 画布工作流节点（media-nodes JSON）

节点产出：
  图片节点 9 个（角色 5 + 场景 2〔母版/九宫格〕 + 道具 2）——prompt=资产出图提示词
  音频节点 5 个（角色声线）——prompt=Voice Description 完整版，refIds→对应角色图
  视频节点 7 个（段1~段7）——prompt=七段式投喂原文，refIds=段内 @//chr/scn/prp（图N）解析出的图片节点
连线：画布前端按 refIds 自动绘制 图片→视频/音频 边。
"""
import io
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

LOAD = r"C:\Users\liuch\Doubao\chats\2026-10-15\new-chat\铁西硬汉_第01集_投喂载荷.json"
REPO = r"D:\短剧\120集最新全剑宗都是剑修除了我\剧本正文\ViMax-refactor"
SESSION = sys.argv[1] if len(sys.argv) > 1 else "20261023-ep001-vimax"
OUT = os.path.join(REPO, ".working_dir", SESSION, ".working_dir", "canvas_media_nodes.json")

d = json.load(io.open(LOAD, encoding="utf-8"))
now = datetime.now(timezone.utc).isoformat()
nodes = []

# ---- 图片节点（角色/场景/道具分列） ----
IMG_X = {"角色": 0, "场景": 320, "道具": 640}
img_by_code = {}          # 图号 → [节点id]
img_seq = {}               # 图号 → 已用序号（同图多条时 -1/-2 区分）
kind_count = {"角色": 0, "场景": 0, "道具": 0}
for a in d["asset_prompts"]:
    code = a["code"]                     # 图N
    name = a["name"]
    seq = img_seq.get(code, 0) + 1
    img_seq[code] = seq
    nid = f"img-{code[-1]}" if seq == 1 else f"img-{code[-1]}-{seq}"
    if "角色" in name or "立绘" in name:
        kind = "角色"
    elif "场景" in name or "母版" in name or "九宫格" in name:
        kind = "场景"
    else:
        kind = "道具"
    x = {"角色": 0, "场景": 320, "道具": 640}[kind]
    y = kind_count[kind] * 360
    kind_count[kind] += 1
    img_by_code.setdefault(code, []).append(nid)
    nodes.append({
        "id": nid,
        "kind": "image",
        "title": name,
        "prompt": a["raw"],
        "mediaUrl": "",
        "status": "idle",
        "x": x,
        "y": y,
        "updatedAt": now,
        "refIds": [],
    })

# ---- 音频节点（角色声线 · Voice Description 完整版） ----
for code, ids in img_by_code.items():
    if code not in ("图1", "图2", "图3", "图4", "图5"):
        continue
    asset = next(a for a in d["asset_prompts"] if a["code"] == code and "角色" in a["name"])
    raw = asset["raw"]
    m = re.search(r"Voice Description 完整版[^\n]*\*\*\s*[:：]?\s*(.*?)(?=图底|声纹|音色速览|$)", raw, re.S)
    voice_prompt = (m.group(1).strip() if m else "").strip()
    nodes.append({
        "id": f"voice-{code[-1]}",
        "kind": "audio",
        "title": f"{asset['name'].split('（')[0]} 声线",
        "prompt": voice_prompt,
        "mediaUrl": "",
        "status": "idle",
        "x": 960,
        "y": (int(code[-1]) - 1) * 240,
        "updatedAt": now,
        "refIds": ids[:1],
    })

# ---- 视频节点（段1~7 · 七段式原文 · refIds 解析引用） ----
FIG_RE = re.compile(r"图\s*(\d)")
for s in d["segments"]:
    no = s["no"]
    refs = set()
    for m in FIG_RE.finditer(s["raw"]):
        code = f"图{m.group(1)}"
        if code in img_by_code:
            refs.update(img_by_code[code])
    nodes.append({
        "id": f"video-{no}",
        "kind": "video",
        "title": f"段{no} · {s['name'].split('·', 1)[-1].strip()}",
        "prompt": s["raw"],
        "mediaUrl": "",
        "status": "idle",
        "x": 1280,
        "y": (no - 1) * 360,
        "updatedAt": now,
        "refIds": sorted(refs),
    })

os.makedirs(os.path.dirname(OUT), exist_ok=True)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUT), suffix=".tmp")
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    json.dump(nodes, fh, ensure_ascii=False, indent=2)
os.replace(tmp, OUT)

print(f"nodes={len(nodes)} -> {OUT}")
print(f"  图片 {sum(1 for n in nodes if n['kind']=='image')} · 音频 {sum(1 for n in nodes if n['kind']=='audio')} · 视频 {sum(1 for n in nodes if n['kind']=='video')}")
for n in nodes:
    if n["kind"] == "video":
        print(f"  {n['id']} refs={n['refIds']} prompt={len(n['prompt'])}字")
