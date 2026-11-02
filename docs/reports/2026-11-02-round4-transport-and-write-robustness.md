# 第四轮：传输压缩与并发写入健壮性

日期：2026-11-02
版本：`2026.11.02.05`
范围：`server.py`（静态/JSON 传输）、`canvas_store.py`（写事务）、`web-vimax`（节点判等、按需加载）

---

## 一、结论先说

这轮找到两个**真问题**，都不是"锦上添花"：

1. **整个应用没有任何传输压缩。** 首屏 1.52MB 明文下发，打开一本画布还要再下 772KB JSON。
   加 gzip 后首屏 0.38MB、画布读取 96KB。**首屏 −74.8%，画布读取 −87.6%。**
2. **并发写入有约 1% 的概率直接失败，且不重试。** SQLite 在 Windows + WAL 下并发打开时
   会抛 `attempt to write a readonly database`，而重试逻辑只认 `locked/busy`，
   这个错误直接冒到接口变成 500 —— 用户看到"保存失败"，再点一次又成功。
   20 线程压测 300 次写里稳定复现 2 次。

---

## 二、改动明细

### 1. 传输压缩（`server.py`）

| 位置 | 行为 |
|---|---|
| `_serve_webapp` | 文本类静态资源（html/js/css/svg/json/map）按 `Accept-Encoding` 协商 gzip，压缩结果按 `(路径, mtime, size)` 缓存，重新构建后 mtime 变化自然失效 |
| `_serve_webapp` | 新增 `ETag` + `If-None-Match → 304`。`index.html` 是 `no-cache`，每次导航都要回源确认，现在确认只花一个 304 |
| `_serve_webapp` | 修正 `HEAD` 会写出响应体的问题 |
| `_send` | JSON/文本响应同样协商 gzip（阈值 1KB），`Vary` 与 CORS 的 `Vary: Origin` 合并成一条 |

实现要点：

- `_accepts_gzip()` 解析 q 值，显式 `gzip;q=0` 会覆盖 `*;q=1`，不会被通配符误放行。
- `_compressible_content_type()` 只放行文本类；图片/视频/字体/zip 不压。
- 压缩后体积反而变大时退回明文（极小 JSON、已优化过的 svg）。
- 静态压缩缓存上限 24MB；超限时清空并本次回退明文，不会先把超额对象插进去。
- 压缩失败（`OSError`/`ValueError`）静默退回明文，不能因为压缩把资源本身搞坏。

### 2. 并发写事务重试（`canvas_store.py`）

- `_is_retryable_write_error()` 增加 `readonly` 分支，并抽出 `_is_readonly_write_error()`。
- `_run_write_transaction()` 给 readonly 单独记账（`_MAX_READONLY_RETRIES = 4`，约 1.15s）：
  瞬态竞态重试一两次就恢复；**真·只读**（权限、只读介质）不会白等满 35 秒 deadline。
- 4 个写路径改为统一走重试包装，原先都是裸连接 + `BEGIN IMMEDIATE`：
  - `create_project`（原来还漏了 `try/finally`，异常时连接不关）
  - `update_project`（**画布主保存路径**，乐观锁 `WHERE revision=?` 保证重跑不重复写）
  - `delete_project`
  - `append_event`（每次生成进度/完成都打一行，全库最热的写路径）
  - `ensure_project_default_episode`（在读路径上，写失败会让老项目打不开）

### 3. 画布卡片判等补全（`web-vimax/src/canvasNodeMemo.ts`）

上一轮给 `MediaNode` 加了 `memo`，穿透判等依赖"引用节点签名"。原签名只覆盖
`status/kind/promptStale/mediaUrl 有无`，而卡片实际用到引用节点的四处信息，
漏了三处，表现为**卡片永远停在旧显示**：

| 漏掉的信息 | 用户看到的现象 |
|---|---|
| 文本节点 `prompt` 是否为空 | 引用的文本从空填上内容后，徽标还显示"缺 1 项" |
| `mediaAvailability` | 素材被标记缺失后，徽标仍是"就绪" |
| `title` | 徽标 tooltip 里的"点击定位缺失依赖：<标题>"还是旧名字 |
| `x` / `y` | 拖动被引用的节点后，点徽标跳到**旧坐标** |

签名改成 `kind/status/stale/availability/has/文本就绪/标题/取整坐标`。
坐标取整：亚像素位移不刷新卡片，拖动时不抖。

### 4. 首屏按需加载（`CanvasView.tsx`）

`html-to-image` 只在「导出画布 PNG」用一次，却打进首屏主包。改成函数内
`await import('html-to-image')`，主包 533,316 → 520,500 字节（−12.8KB），
拆出的 13.7KB 独立 chunk 只在点导出时下载。

### 5. 让 `pytest` 在根目录能跑（`pytest.ini`）

根目录 480+ 个 `_tmp_*` 脚本 + `deliverables/` 里两套同名 skill 副本，
默认递归收集必然撞 `import file mismatch`，`pytest` 在根目录**根本跑不起来**，
只能手工点名单个文件 —— 这也是之前没人发现并发写问题的原因之一。
新增 `norecursedirs` 限定收集范围。

---

## 三、实测数据

### 首屏传输（`vite build` 产物，真实 HTTP 往返）

| 资源 | 明文 | gzip | 节省 |
|---|---:|---:|---:|
| `index.html` | 2,142 | 1,182 | 44.8% |
| `index-*.css` | 583,664 | 97,472 | 83.3% |
| `index-*.js` | 520,500 | 151,940 | 70.8% |
| `react-vendor-*.js` | 145,341 | 46,684 | 67.9% |
| `flow-vendor-*.js` | 179,596 | 58,311 | 67.5% |
| `flow-vendor-*.css` | 15,869 | 2,667 | 83.2% |
| `icon-vendor-*.js` | 38,777 | 7,705 | 80.1% |
| `ChangelogDialog-*.js`（懒） | 55,656 | 20,589 | 63.0% |
| `index-*.js`（导出模块，懒） | 13,728 | 5,415 | 60.6% |
| **表述口径合计（含懒加载 chunk）** | **1,555,273** | **391,965** | **−74.8%** |

浏览器首屏实测只请求 6 个首屏资源，CDP `Network.loadingFinished` 的编码后合计为
**369,526 B**；上面 391,965 B 包含更新记录与导出模块两个懒加载 chunk，不能当作首屏口径。

### 画布读取（`gk6woLnGjh998UeNwvhqYG`，127 节点，真实载荷）

```
772,298 B → gzip 96,116 B   (−87.6%)
```

每次打开画布省 676KB；生成完成后前端会再整份拉一次，同样受益。

### 并发写（20 线程 / 300 次写，独立复现脚本）

| | 首轮 | 加重试后 |
|---|---:|---:|
| 成功 | 298 | 300 |
| `readonly` 失败 | 2 | 0 |

### 回归

| 项 | 结果 |
|---|---|
| 后端全量 `pytest -q` | **528 passed / 187 subtests passed / 0 failed** |
| 并发/持久化压力 | `test_canvas_store_concurrency.py` + `test_generation_task_persistence.py` **90 passed**（改前 7 failed） |
| 前端 `vitest run` | **433 passed**（37 文件） |
| 类型检查 | `tsc -p tsconfig.json` 与 `tsconfig.canvas.json` **0 错误** |
| 新增测试 | `test_webapp_static.py` 18 项、`test_write_retry.py` 5 项、`test_storage_maintenance_thread.py` 1 项、`test_auth_static_boundary.py` 1 项、`canvasNodeMemo.test.ts` +5 项 |
| 浏览器端到端 | 用一个匿名可读的临时实例跑 puppeteer：`#root` 正常挂载（288 DOM）、界面文案正常渲染、**0 个 pageerror**、6 个 `/assets/*` 全部 `content-encoding: gzip` 且字节数与上表一致（合计 364,779 B + index.html 1,182 B）。唯一非 2xx 是 `/api/admin/accounts` 403——host 模式下该接口本就不存在，属临时实例环境差异 |

---

## 四、发现但**没有**执行

按风险排序，都留证据不留改动：

1. **`data/` 下 48 个 `canvas.db.bak-*`，合计 8.8GB**（其中 10-26 那批 183,709,696 B 完全同尺寸，
   是同一份库的重复拷贝）。当前 `canvas.db` 只有 30MB、freelist 为 0，备份没有信息价值。
   删除属不可逆操作，需要用户逐条确认。
2. **根目录 752 个条目含 483 个 `_tmp_*`**。同理，清理需要确认。
3. **CSS 产物 583KB**，仍是首屏最大的单个资源（gzip 后 97KB）。但项目约定是
   "改配色只能就地改原声明"（覆盖层会被后面 2900 行旧规则翻盘），
   去重/拆分属于高风险改动，需要单独排期。
4. **`CanvasView.tsx` 仍是 17,443 行单模块**。拆分收益是构建期与维护性，
   但会牵动整条画布数据流，不在本轮范围。
5. **评估后否决：开启 HTTP/1.1 keep-alive。** `_send`、静态、上传三个出口都正确设了
   `Content-Length`，技术上可行；但 keep-alive 会让 `ThreadingHTTPServer` 为每条空闲连接
   常驻一个线程（浏览器每标签页 6 条），多开标签时线程数线性增长。收益（省一次本地
   TCP 握手）小于代价，不做。
6. **仍有 3 个写路径未接重试**：`create_snapshot`、`record_audit_event`、
   `backfill_generation_task_episode_ids` / `interrupt_orphan_generation_tasks`。
   都是低频或启动期一次性路径，暂不动。
7. **未登录首页的认证边界已修正。**
   修正前 `/`、JS、CSS 与 API 一起返回 401，登录表单没有机会渲染。
   现在 `/`、JS、CSS 允许匿名 GET/HEAD；`/api/*` 和 `/uploads/*` 继续要求会话。
   隔离实例实测：首页 200 且 `#root` 可挂载，API/上传均 401。

---

## 五、生效条件

`server.py` 与 `canvas_store.py` 的改动**必须重启后端进程**才生效；
前端已重新构建到 `web-vimax/dist`，刷新页面即可（`assets/` 带指纹，浏览器会取新文件）。

本次已在本地 8890 重启并验证；生产服务器需按同一流程发布。

验证方法（浏览器 DevTools → Network）：

- 任意 `/assets/*.js|css` 响应头应出现 `content-encoding: gzip` 与 `vary: accept-encoding`
- 刷新页面时 `index.html` 第二次应返回 `304`
- 打开画布后 `/api/projects/<id>/workspace` 的 `Size` 列应远小于 `Content` 列
