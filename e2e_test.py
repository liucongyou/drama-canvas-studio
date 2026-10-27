# -*- coding: utf-8 -*-
"""e2e_test.py — 端到端自检（导入 → 画布结构 → 挂接机检）"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8890"
FEED = r"C:\Users\liuch\Doubao\chats\2026-10-15\new-chat\铁西硬汉_第01集_投喂载荷.json"


def api(path, method="GET", body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ok = True
    def check(name, cond, detail=""):
        nonlocal ok
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        if not cond:
            ok = False

    # 1 新建
    r = api("/api/projects", "POST", {"title": "铁西硬汉回乡记 · 第01集 · 画布工作流（自研）"})
    pid = r["id"]
    check("新建项目", bool(pid), pid)

    # 2 导入
    r = api(f"/api/projects/{pid}/import", "POST", {"feed_path": FEED})
    check("导入载荷", r.get("nodes") == 22, f"nodes={r.get('nodes')} conns={r.get('connections')}")

    # 3 画布结构
    proj = api(f"/api/projects/{pid}")
    nodes, conns = proj["payload"]["nodes"], proj["payload"]["connections"]
    types = {}
    zones = {}
    for n in nodes:
        types[n["type"]] = types.get(n["type"], 0) + 1
        t = n["title"] or ""
        if n["type"] == "video":
            zones["视频"] = zones.get("视频", 0) + 1
        else:
            for z in ("角色", "场景", "道具", "音频"):
                if t.startswith(z):
                    zones[z] = zones.get(z, 0) + 1
    check("节点类型分布", types == {"text": 1, "image": 9, "video": 7, "audio": 5}, str(types))
    check("五区分布", zones == {"角色": 5, "场景": 2, "道具": 2, "音频": 5, "视频": 7}, str(zones))
    check("连线数", len(conns) == 72, str(len(conns)))
    xs = sorted({n["position"]["x"] for n in nodes})
    check("列间距（分区不混排）", all(b - a >= 800 for a, b in zip(xs, xs[1:])), str(xs))

    # 4 挂接机检
    g = api(f"/api/projects/{pid}/gate-check", "POST", {})
    check("挂接机检", g["passed"], f"{len(g['results'])} 段 · FAIL={g['failed']}")
    for res in g["results"]:
        print(f"    {'PASS' if res['ok'] else 'FAIL'} {res['title']}  稿:{res['script']} ↔ 实:{res['linked']}")

    # 5 前端页面
    req = urllib.request.Request(BASE + "/")
    with urllib.request.urlopen(req, timeout=10) as r:
        html = r.read().decode("utf-8")
    check("前端页面", "戏剧画布工作流" in html and "canvasWrap" in html, f"len={len(html)}")

    # 6 配置
    cfg = api("/api/config")
    check("配置读取", cfg["seedream_size"] == "1536x1536", str(cfg))
    check("密钥未配置（预期）", cfg["ark_api_key"] is False)

    print("\n" + ("✅ 全部 PASS" if ok else "❌ 有 FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
