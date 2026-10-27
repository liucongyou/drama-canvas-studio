# -*- coding: utf-8 -*-
"""db_slim.py — 数据库瘦身（安全、可重复执行）

背景：
    `canvas_snapshots` 每次保存都整包插一条快照。历史累积过多会让
    data/canvas.db 涨到几百 MB，读接口变慢甚至超时。
    第 2026-10-25 轮已在 `canvas_store._snapshot` 加了自动裁剪（保留最近 30 条），
    但**之前已经堆积的历史**需要本工具清一次。

做什么：
    1. 按画布保留最近 KEEP 条快照（默认 30，与代码里的 SNAPSHOT_KEEP 一致）
    2. VACUUM 回收磁盘空间

安全策略：
    · 只删快照，**不动 canvas_projects**（当前真实数据）
    · 默认「预演模式」只打印不删；加 --apply 才真执行
    · 执行前自动备份到 data/canvas.db.bak-<时间戳>

用法：
    python db_slim.py            # 预演，看会删多少
    python db_slim.py --apply    # 真执行
"""
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "data", "canvas.db")
KEEP = 30


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}"
        n /= 1024.0


def main():
    apply = "--apply" in sys.argv
    if not os.path.exists(DB):
        print(f"[X] 找不到数据库：{DB}")
        return 1

    size_before = os.path.getsize(DB)
    con = sqlite3.connect(DB, timeout=30)
    cur = con.cursor()

    cur.execute("SELECT canvas_id, COUNT(*), SUM(LENGTH(payload_json)) FROM canvas_snapshots GROUP BY canvas_id")
    rows = cur.fetchall()
    if not rows:
        print("[i] canvas_snapshots 为空，无需清理")
        con.close()
        return 0

    print("=" * 62)
    print("  数据库瘦身" + ("（预演，不实际删除）" if not apply else "（执行模式）"))
    print("=" * 62)
    print(f"  库文件      {human(size_before)}")
    print(f"  保留策略    每个画布最近 {KEEP} 条快照")
    print()

    total_del = 0
    for cid, cnt, nbytes in rows:
        cur.execute("SELECT id FROM canvas_snapshots WHERE canvas_id=? ORDER BY revision DESC LIMIT ?", (cid, KEEP))
        keep_ids = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT id FROM canvas_snapshots WHERE canvas_id=?", (cid,))
        all_ids = {r[0] for r in cur.fetchall()}
        will_del = all_ids - keep_ids
        total_del += len(will_del)
        print(f"  画布 {cid[:18]}…")
        print(f"    现有 {cnt} 条 / {human(nbytes or 0)}  →  保留 {len(keep_ids)} 条，删除 {len(will_del)} 条")

    print()
    print(f"  合计将删除 {total_del} 条快照")

    if not apply:
        print()
        print("  这是预演。确认无误后执行：")
        print("      python db_slim.py --apply")
        con.close()
        return 0

    if total_del == 0:
        print("  [i] 没有需要删除的快照")
        if size_before > 50 * 1024 * 1024:
            print("  [i] 但库文件偏大，仍执行 VACUUM 回收空间")
        else:
            con.close()
            return 0

    # ---------- 备份 ----------
    bak = f"{DB}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    print()
    print(f"  备份中 → {os.path.basename(bak)}")
    con.close()
    shutil.copy2(DB, bak)
    print(f"  [OK] 备份完成 {human(os.path.getsize(bak))}")

    # ---------- 删除 + VACUUM ----------
    con = sqlite3.connect(DB, timeout=30)
    cur = con.cursor()
    deleted = 0
    for cid, _cnt, _nb in rows:
        cur.execute("SELECT id FROM canvas_snapshots WHERE canvas_id=? ORDER BY revision DESC LIMIT ?", (cid, KEEP))
        keep_ids = [r[0] for r in cur.fetchall()]
        if not keep_ids:
            continue
        ph = ",".join("?" * len(keep_ids))
        cur.execute(
            f"DELETE FROM canvas_snapshots WHERE canvas_id=? AND id NOT IN ({ph})",
            [cid] + keep_ids,
        )
        deleted += cur.rowcount
    con.commit()
    print(f"  [OK] 已删除 {deleted} 条快照")

    # VACUUM 必须在事务外
    con.isolation_level = None
    cur.execute("VACUUM")
    con.close()

    size_after = os.path.getsize(DB)
    print(f"  [OK] VACUUM 完成")
    print()
    print("=" * 62)
    print(f"  库文件  {human(size_before)}  →  {human(size_after)}")
    saved = size_before - size_after
    if saved > 0:
        print(f"  释放    {human(saved)}")
    print(f"  备份    {os.path.basename(bak)}（确认无异常后可删除）")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
