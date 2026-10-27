# -*- coding: utf-8 -*-
"""新建 ViMax 会话（铁西硬汉回乡记 EP001 画布工作流专用）+ 写入画布节点。

用法：
  python create_tiexi_session.py
产出：
  - .vimax/sessions.json 新增一条会话记录（project_name=《铁西硬汉回乡记》EP001 画布工作流）
  - .working_dir/<sessionId>/.working_dir/canvas_media_nodes.json 写入 21 个节点
"""
import io
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO = r"D:\短剧\120集最新全剑宗都是剑修除了我\剧本正文\ViMax-refactor"
SESSIONS = os.path.join(REPO, ".vimax", "sessions.json")
NOW = datetime.now()

session_id = f"{NOW:%Y%m%d-%H%M%S}-vimax"
project_name = "《铁西硬汉回乡记》EP001 画布工作流"

with io.open(SESSIONS, "r", encoding="utf-8") as fh:
    payload = json.load(fh)

if any(s.get("session_id") == session_id for s in payload.get("sessions", {}).values()):
    raise SystemExit(f"会话已存在：{session_id}")

record = {
    "session_id": session_id,
    "project_name": project_name,
    "working_dir": f".working_dir\\{session_id}",
    "stage": "created",
    "summary": "EP001《三千块，买断一条命》画布工作流：角色5/场景2/道具2 出图节点 + 角色声线5 + 视频段1~7（含挂接连线 refIds）",
    "idea": "《铁西硬汉回乡记》第1集《三千块，买断一条命》：2006年东北老厂改制末期，退伍工人赵铁柱回乡讨要安置费。",
    "created_at": NOW.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    "updated_at": NOW.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
}
payload.setdefault("sessions", {})[session_id] = record
with io.open(SESSIONS, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, ensure_ascii=False, indent=2)

print(f"created session: {session_id} | {project_name}")
print(session_id)
