# -*- coding: utf-8 -*-
import json, io
p = r"D:\短剧\120集最新全剑宗都是剑修除了我\剧本正文\ViMax-refactor\.working_dir\20261015-133416-vimax\.working_dir\canvas_media_nodes.json"
d = json.load(io.open(p, encoding="utf-8"))
print("total=", len(d))
for n in d:
    print(f"{n['id']:<10} {n['kind']:<6} x={n['x']:<5} y={n['y']:<5} {n['title'][:20]}")
# 校验 refIds 全部指向存在的图片节点
img_ids = {n["id"] for n in d if n["kind"] == "image"}
bad = []
for n in d:
    if n["kind"] in ("video", "audio"):
        for r in n.get("refIds", []):
            if r not in img_ids:
                bad.append((n["id"], r))
print("bad_refs=", bad if bad else "无（全部指向图片节点）")
