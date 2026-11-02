# 系统优化升级审计报告

审计对象：`drama-canvas-studio`（戏剧画布工作流系统）
审计时间：2026-11-02
审计方式：静态度量 + 本地实测（不修改任何业务代码、不调用付费接口、不触发部署）

---

## 一、实测基线

| 项目 | 结果 | 说明 |
|---|---|---|
| 前端类型检查（画布工程） | **通过 0 错误** | `tsc -p tsconfig.canvas.json` |
| 前端全量类型检查 | **3 错误** | main.tsx ×2、ModelSettings.tsx ×1 |
| 前端单元测试 | **395 通过 / 32 文件 / 38.6s** | `npm test` |
| 后端单元测试 | **498 通过 / 187 subtests / 501.7s（8 分 21 秒）** | 仅 `pytest test_*.py` |
| 后端测试（根目录直接跑） | **收集失败** | 3 个 `import file mismatch` |
| 构建产物 | index.js 578KB(gz 174KB) + CSS 600KB(gz 100KB) | `web-vimax/dist` |
| 工作区体积 | **11 GB** | 其中 9.4GB 在 `data/` |
| 本地服务 | 8890 → 401（鉴权生效）、8891 → 200 | 运行正常 |

**结论：功能层面是健康的——类型检查干净、893 个测试全绿、鉴权与安全头齐备。问题集中在「体积」「结构」「可维护性」「运维流程」四类技术债，不涉及功能回归。**

---

## 二、优先级总览

| # | 问题 | 等级 | 实测证据 | 预估收益 |
|---|---|---|---|---|
| 1 | 磁盘被历史备份吃满 | **P0** | 48 个 `canvas.db.bak-*` 占 8.8GB，工作区 11GB | 释放 ~9.1GB |
| 2 | SQLite 文件 83% 是空闲页 | **P0** | 175.2MB 文件，`freelist` 145.5MB，`auto_vacuum=0` | 库体积 -80% |
| 3 | 根目录 666 个杂物 | **P0** | 764 个条目：520 `_tmp_*`、92 `*.log`、102 `*.tgz`、30 `*.png` | 仓库可读性 |
| 4 | 根目录测试套件跑不起来 | **P0** | `pytest` 直接 3 个收集错误 | CI 前置条件 |
| 5 | 管理员「账号管理」弹窗必崩 | **P0** | `main.tsx:982` 缺必填 `initialSnapshot` | 修真 bug |
| 6 | 画布主文件 17,345 行 | **P1** | 单组件 `CanvasViewInner` 9,412 行 / 414 个 hooks | 可维护性 |
| 7 | CSS 靠 `!important` 叠层 | **P1** | 28,539 行源码 / 600KB 产物 / 447 个重复选择器 / 659 个 `!important` | 产物 -40% |
| 8 | 359KB 前端死代码仍在跑测试 | **P1** | 14 个文件从 `main.tsx` 不可达 | 类型+测试提速 |
| 9 | 旧 Node 服务 232KB 已无人启动 | **P1** | `server.mjs` + `server-lib.mjs`，无任何脚本引用 | 测试时长 -85% |
| 10 | 无任何性能基线 / 性能测试 | **P2** | 计划里的 `tests/perf/*`、`canvas-performance.mjs` 均不存在 | 优化不可度量 |
| 11 | 构建拆包配置失效 | **P2** | `react-vendor` 产物仅 177 字节 | 缓存命中 |
| 12 | 全量 JSON 写入仍是写热点 | **P2** | 每次节点写 = 重写整个 `payload_json`（最大 501KB） | 写 P95 |
| 13 | 无 CI，部署靠 100+ 个临时脚本 | **P2** | 无 `.github/`，根目录 102 个 `_tmp_deploy_*.sh` | 发布风险 |

---

## 三、详细发现

### P0-1 磁盘：9.4GB 数据目录里 8.8GB 是历史备份

```
data/                              9.4G
  canvas.db.bak-*（48 个）         8.8G   ← 单个 176MB，多为同一时刻快照
  tenants/                          284M
  uploads/                          192M
  canvas.db（当前）                 176M
backups/                            177M
recovered/                          214M
*.tgz（根目录 102 个）               40M
```

**影响**：备份与数据库同盘同目录，既没有隔离也没有轮转策略。真实故障时会被同一块盘一起带走，同时让 `git status`、磁盘监控、打包上传全部被污染。

**建议**：
1. 只保留最近 3 个备份（按日期），其余移出项目目录到独立磁盘/对象存储；
2. 备份改为 `VACUUM INTO 'backup/canvas-YYYYMMDD.db'`，体积直接降到 30MB 级别；
3. 加轮转：`保留 7 天 / 最多 5 份`，写进 `tools/backup_db.py`，由启动脚本调用。

**验收**：`du -sh data` < 800MB，`ls data/canvas.db.bak* | wc -l` ≤ 3。

---

### P0-2 数据库 83% 是空洞

- 文件：175.2MB（44,851 页 × 4KB）
- 活数据：`canvas_snapshots` 26.1MB + `canvas_projection_nodes` 1.7MB + 其他 < 1MB ≈ **30MB**
- `PRAGMA freelist_count` = 37,258 页 = **145.5MB 可回收**
- `PRAGMA auto_vacuum` = 0（关闭）

**根因**：`canvas_store.py` 至今对 `canvas_projects.payload_json` 做全量 `UPDATE`（L2616、L2944）。每次改一个节点，就把整份 500KB 的 JSON 重写一遍，旧页全部进 freelist，`auto_vacuum=0` 又永不回收。

**建议**：
1. 立即 `VACUUM`（服务停机窗口内），并 `PRAGMA auto_vacuum=INCREMENTAL` + 定时 `PRAGMA incremental_vacuum`；
2. 这才是 `docs/reports/2026-10-31-canvas-write-layer-migration.md` 里设计的 `canvas_nodes` / `canvas_edges` 关系表要解决的问题——**该迁移至今没有落地**（库里不存在这两张表），是当前最大的结构性技术债。

**验收**：`page_count × 4096` < 60MB；节点写入不再触发 `payload_json` 全量更新。

---

### P0-3 / P0-4 仓库卫生与测试入口

- 根目录 **764 个条目**，其中 `_tmp_*` 520 个、`*.log` 92 个、`*.tgz` 102 个（40MB）、`*.png` 30 个、孤儿文件 `$null`、`400`、空目录 `-p`。
- `.gitignore` 已忽略 `*.log`、`data/`，但 `_tmp_*`、`*.tgz`、根级备份目录 `.deploy-release-*`（7 份，各 1.4–2.5MB 全量源码副本）仍为 untracked，反复出现在 `git status`。
- `pytest` 在根目录直接失败：

```
ERROR deliverables/manju-laoli-skill-V7.4.29-skills-only/.../test_project_bundle.py
import file mismatch: imported module 'test_project_bundle' ...
```

同一套技能包在 `deliverables/` 下有 `V7.4.29` 与 `V7.4.29-skills-only` 两份，测试模块重名，pytest 默认 `rootdir` 收集直接中断。**没有 `conftest.py`、没有 `pytest.ini`**，测试只能靠 `cd` 到根目录手动挑文件名跑。

**建议**：
1. 新增 `pytest.ini`：
   ```ini
   [pytest]
   testpaths = .
   python_files = test_*.py
   norecursedirs = deliverables .venv node_modules data backups recovered .deploy-release-* .release-*
   ```
2. 新建 `archive/` 并一次性归档 `_tmp_*`、`*.tgz`、`*.png`、`.deploy-release-*`；`.gitignore` 补 `_tmp_*`、`*.tgz`、`.deploy-release-*`、`.release-*`；
3. 根目录只保留 `server.py` 等 12 个业务模块 + `test_*.py`。

**验收**：根目录条目 < 60；`pytest` 一条命令跑完全部后端测试。

---

### P0-5 真实 Bug：管理员「账号管理」弹窗必然崩溃

```tsx
// web-vimax/src/main.tsx:982
{isAdmin && adminAccountsOpen && <AdminAccountsDialog onClose={() => setAdminAccountsOpen(false)} />}

// 组件内部第一行状态初始化
const [accounts, setAccounts] = useState<ManagedAccount[]>(initialSnapshot.accounts);
```

`initialSnapshot` 是必填 prop，调用处没有传 → **打开即 `TypeError: Cannot read properties of undefined`**。`ErrorBoundary` 只包了画布（`main.tsx:947 scope="canvas"`），这个弹窗在 Shell 层，会直接白屏。

同一批类型错误还有一处契约漂移：`main.tsx:1371` 读 `session.preferences`，但 `api.ts` 的 `AuthSession` 类型没声明该字段（后端两种模式都确实返回了）。目前能跑，但类型检查已经不保护这条链路。

**建议**：`AdminAccountsDialog` 内部改为 `initialSnapshot?.accounts ?? []` 并在打开前 `await getManagedAccounts()`；`AuthSession` 补 `preferences?: TenantPreferences`。修完后 `npm run typecheck` 应为 0 错误。

**验收**：管理员点开「账号管理」正常列出账号；全量 `tsc --noEmit` 0 错误。

---

### P1-6 画布主文件：17,345 行 / 单组件 9,412 行 / 414 个 hooks

```
CanvasView.tsx                 17,345 行   884 KB
└─ CanvasViewInner              L7927–17339   9,412 行
   ├─ useState     91
   ├─ useEffect    56
   ├─ useCallback 137
   ├─ useRef      113
   └─ useMemo      17          → 合计 414 个 hooks
MediaNodeModal                  L3727–5747   2,021 行
MediaNode                       L2290–3647   1,358 行
```

**同时**：整个文件 `React.memo` 使用次数 = **0**。`CanvasViewInner` 里任意一个 `setState`（哪怕只是 1 秒一次的时钟 `busyClock`）都会重建 `flowNodes` 并重渲染全部节点组件（`ShotNode` / `SegmentNode` / `AssetNode` / `MediaNode`…）。当前 242 个投影节点还能撑住，整季画布（`EpisodeRows` + `LargeCanvasOverview`）节点上到 600+ 时，拖动帧率会明显掉。

**建议**（按风险从低到高）：
1. **先做 0 风险项**：给 10 个画布节点组件（`ShotNode` / `SegmentNode` / `AssetNode` / `AssetGroupNode` / `AssetCardNode` / `AssetColNode` / `EpBoxNode` / `NoteNode` / `MediaNode` / `LargeCanvasOverview`）加 `memo()`，并保证传入的 `data`/回调引用稳定（这一步不需要动 9,412 行主体）；
2. 把 1 秒时钟、生成同步轮询等高频状态从 `CanvasViewInner` 拆到独立 `<BusyIndicator>` / `<GenerationSync>`（它们只需要局部重渲染）；
3. 按弹窗边界切文件：`MediaNodeModal` / `BatchParamsDialog` / `BatchGenerateDialog` / `ProjectVersionsDialog` / `GenerationBudgetDialog` 各自成文件（合计可移出 ~3,500 行）；
4. 最后再考虑把 `CanvasViewInner` 拆成 `useCanvasNodes` / `useCanvasEdges` / `useCanvasSelection` / `useGeneration` 四个 hook。

**验收**：`CanvasView.tsx` < 8,000 行；600 节点整季画布拖动平均帧间隔 < 25ms。

> ⚠️ 注意：`docs/superpowers/plans/2026-10-29-industrial-canvas-upgrade-plan.md` 阶段 0.2 里规划的 `tests/perf/bench_canvas_store.py`、`tests/perf/bench_http.py`、`web-vimax/test/canvas-performance.mjs` **一个都不存在**（`tests/perf/` 目录未创建，`web-vimax/test/` 下只有 `fake-agent.mjs`）。也就是说这个系统**从未有过性能基线和性能回归闸门**——上面这条验收标准目前无法度量。做 P1-6 之前应先把 `bench_canvas_store.py`（100/300/600/1000 节点）落地，否则「优化了没有」全靠感觉。

---

### P1-7 CSS：600KB 产物、447 个重复选择器、659 个 `!important`

| 文件 | 行数 | `!important` |
|---|---|---|
| `styles.css` | 14,904 | 93 |
| `ui-refinements.css` | 11,369 | 94 |
| `theme-tokens.css` | 2,266 | **472** |

- 跨文件重复选择器 **447** 个（`theme-tokens` × `ui-refinements` 202 个、`styles` × `ui-refinements` 113 个、三文件同有 23 个）。
- 覆盖关系依赖 `main.tsx` 的 import 顺序：`theme-tokens.css` 排在最后（第 29 行），所以它必须用 472 个 `!important` 才能压住前面的旧规则（`MEMORY.md` 也记了「追加覆盖会被后面 2900 行旧规则翻盘」）。
- 仅 `.ledger*` / `.artifact-*`（对应已不可达组件）就占了 **35KB 源码、约 5.3%**。
- 构建后 CSS **599,532 字节 > 主 JS 578,261 字节**，是首屏渲染阻塞资源里最大的一块。

**建议**：
1. 先删死组件的样式（`.ledger*`、`.artifact-*`、`.primary-nav`、`chat-layout` 中已废弃段），预计 -40KB；
2. 把 `theme-tokens.css` 里的 472 个 `!important` 逐组降级：每条 `!important` 规则前移到它要覆盖的原始声明位置，改完删掉覆盖；按主题分批（一次一个主题，`theme.test.ts` 兜底）；
3. 拆分懒加载：`gdialog.css`、`error-boundary.css`、`immersive-theme.css` 改成组件级 `import`，让 Vite 自动分包；
4. 长期：确立「一个选择器只能在一个文件出现一次」的 lint 规则。

**验收**：CSS 产物 < 380KB（gz < 65KB）；`ui-refinements.css` 行数 < 5,000；重复选择器 < 100。

---

### P1-8 / P1-9 死代码：591KB + 85% 的前端测试时长

**前端不可达文件（从 `main.tsx` 静态导入图出发）**：14 个，共 **359KB**

```
App.tsx 126KB   ArtifactViews.tsx 77KB   events.ts 40KB
BudgetView.tsx 32KB   LedgerView.tsx 32KB   artifactPresentation.ts 15KB
ResultsView.tsx 9KB   CreationWizard.tsx 8KB   StylePicker.tsx 7KB
QualityReportPanel.tsx 6KB   types.ts 5KB   modelPresentation.ts / slashCommands.ts / vite-env.d.ts
```

它们对应的测试（`events.test.ts` 26KB、`artifactPresentation.test.ts` 8KB 等）仍在 `npm test` 里跑，测的是不会上线的代码。

**旧 Node 服务**：`web-vimax/server.mjs`（69KB）+ `server-lib.mjs`（163KB），**没有任何 `.bat` / `.sh` / `package.json` 脚本引用它**（前端已是纯 Vite + `server.py`）。但 `server-lib.test.mjs`（80KB / 74 个用例）耗时 **32.9 秒，占前端测试总时长 38.6 秒的 85%**。

**建议**：移到 `legacy/` 目录（不删，留可追溯），从 `tsconfig` 与 `vitest` 的 include 里摘掉；确认一周内无引用后再物理删除。

**验收**：`npm test` 从 38.6s 降到 < 8s；前端源码行数 -25%。

---

### P2-10 没有性能基线，也没有性能闸门

`docs/superpowers/plans/2026-10-29-industrial-canvas-upgrade-plan.md` 阶段 0.2 规划了 4 个度量工具：

```
tests/perf/bench_canvas_store.py     ← 不存在（tests/perf/ 目录都没有）
tests/perf/bench_http.py             ← 不存在
web-vimax/test/canvas-performance.mjs ← 不存在（该目录只有 fake-agent.mjs）
test_security_boundaries.py          ← 存在 ✅
```

规划里的 100/300/600/1000 节点基准、60 并发读取、24 并发节点更新、首屏 DOM 与长任务测量，**一项都没有落地**。这意味着后面所有性能类改动（`memo`、状态外提、存储迁移）都无法证明「是否变快」，也无法在回归时发现「变慢」。

**建议**：把 `bench_canvas_store.py`（纯 Python，直接打 `canvas_store` 的读写 API）作为第一优先补上，它不需要浏览器、几十行就能跑，能立刻量化 P0-2/P2-12 的写入代价。

**验收**：`python tests/perf/bench_canvas_store.py --nodes 100,300,600,1000` 输出 p50/p95 写入耗时表，并归档进 `docs/reports/`。

---

### P2-11 构建拆包配置没有生效

```js
// vite.config.ts
manualChunks: {'react-vendor': ['react','react-dom','react-dom/client'], ...}
```

实际产物：

```
react-vendor-c8fR0B8o.js     177 字节   ← 只剩 createRoot 的 re-export 壳
flow-vendor-Bc__75fb.js      319KB     ← react + react-dom 全在这里（@xyflow/react 依赖把它们拉进来）
index-Do-Z0ouJ.js            578KB
```

注释里写的「P2 批2：路由/组件级拆包，减少首屏单包体积」目标没有达到——react 跟着 `flow-vendor` 走，`react-vendor` 成了空壳。此外 `changelog.ts`（54KB 变更日志字符串）是静态 `import` 进主包，只在打开「更新日志」弹窗时用，应该 `React.lazy`。

**建议**：`manualChunks` 改成函数形式按 `node_modules/react` 真实路径归类；`ChangelogDialog` 改 `lazy(() => import('./changelog'))`。

**验收**：`react-vendor` 产物 > 100KB 且 `index-*.js` < 420KB。

---

### P2-12 写入模型仍是「整包读改写」

`canvas_repository.py` 的 revision 前置条件、按 episode 隔离等保护都做得不错，但底层仍是整份 `payload_json` 覆盖。`docs/reports/2026-10-31-canvas-write-layer-migration.md` 已经把目标表结构（`canvas_nodes` / `canvas_edges` / `canvas_node_versions` / `canvas_episode_state`）设计完整，**代码里一张都还没建**。

这是唯一属于「影响未来规模」的项：节点数上百后，拖一个节点要把 500KB 写进 SQLite 单写者事务，写 P95 随项目体积线性增长——同时也正是 P0-2 数据库膨胀的根因。

**建议**：按该报告的分阶段方案执行，先双写 + 读时合并（报告里已写明「旧 payload 保留可回滚」），再切读，最后停写。

---

### P2-13 发布流程没有护栏

- 无 `.github/workflows`、无 `Makefile`、无 CI 配置。
- 根目录 **102 个 `_tmp_deploy_*.sh`**（`_tmp_deploy_release_65eaf51.sh`、`_tmp_deploy_frontend_controlled_final.sh`…），每次发布都是复制上一版脚本改参数，无法复现、无法审计。
- 后端测试 8 分 21 秒且不并行（`pytest-xdist` 未装），本地全量校验成本高，进一步鼓励「跳过测试直接手工部署」。

**建议**：
1. 把 `_tmp_deploy_*.sh` 收敛成 1 个 `scripts/release.sh <tag>`（打包 → 上传 → 切换 → 健康检查 → 失败回滚），保留 `scripts/rollback.sh`；
2. 加一条最小 CI（GitHub Actions 或本地 `make check`）：`tsc -p tsconfig.canvas.json` + `vitest run` + `pytest -q -n auto` + `vite build`；
3. `requirements.txt` 目前 4/5 是 `>=` 无上界，建议锁死（`pip freeze` 生成 `requirements.lock`）。

**验收**：一条命令完成「校验 + 打包 + 发布 + 回滚」。

---

## 四、建议的升级路线

**第 1 周（止损，全部 0 功能风险）**
1. 备份轮转 + `VACUUM INTO`，清理 8.8GB 历史库；`auto_vacuum=INCREMENTAL`
2. 归档 `_tmp_*` / `*.tgz` / `*.png` / `.deploy-release-*`，补 `.gitignore`
3. 加 `pytest.ini`，让根目录 `pytest` 一条命令跑通
4. 修 `AdminAccountsDialog` 崩溃 + `AuthSession.preferences` 类型，全量 `tsc` 归零

**第 2–3 周（可维护性）**

5. 先落 `tests/perf/bench_canvas_store.py`（100/300/600/1000 节点）+ 首屏/拖动帧率脚本，建立可对比的基线数字
6. 10 个节点组件加 `memo()`，高频时钟/轮询状态外提（用第 5 步的基线验证收益）
7. 按弹窗边界把 `CanvasView.tsx` 切到 8,000 行以内
8. 死代码移入 `legacy/`：前端 359KB + 旧 Node 服务 232KB，测试时长回到 8 秒内
9. CSS 分批去 `!important` + 删死组件样式，产物降到 380KB 以内

**第 4–6 周（结构升级）**

10. 落地 `canvas_nodes` / `canvas_edges` 关系表（双写 → 切读 → 停写）
11. `manualChunks` 修正 + 变更日志懒加载
12. 统一发布脚本 + 最小 CI + 依赖锁定

---

## 五、明确不建议做的事

- **不要为了拆而拆** `CanvasViewInner` 的 9,412 行主体。`MEMORY.md` 已记录多条与弹窗结构、`.jim-*` 样式、hooks 顺序相关的硬约束，一次性大重构风险远大于收益。按「加 memo → 外提高频状态 → 切弹窗文件」三步走，每步都可单独验收、可回滚。
- **不要动 `!important` 的配色规则做「统一美化」**。`theme-tokens.css` 的 472 个 `!important` 是当前覆盖顺序下的必要产物，只能在原声明处就地改，删一条要验一条。
- **不要在没有 CI 的情况下启动存储迁移**。P2-12 是唯一会碰写入事务的改造，必须先在回归网里跑通。

---

## 附：本次审计的复现命令

```bash
# 类型
cd web-vimax && npx tsc -p tsconfig.canvas.json --noEmit     # 0 错误
npx tsc --noEmit                                              # 3 错误

# 测试
npm test                                                      # 395 通过 / 38.6s
../.venv/Scripts/python.exe -m pytest -q test_*.py             # 498 通过 / 501.7s
../.venv/Scripts/python.exe -m pytest -q                       # 3 个收集错误

# 数据库
python -c "import sqlite3;c=sqlite3.connect('data/canvas.db');print(c.execute('pragma freelist_count').fetchone(),c.execute('pragma page_count').fetchone())"

# 体积
du -sh data; ls data/canvas.db.bak* | wc -l; du -ch data/canvas.db.bak* | tail -1
```
