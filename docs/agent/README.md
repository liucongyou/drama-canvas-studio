# Canvas Agent · 智能体 + 技能 + 画布导入 规划文档

- 版本：v1.0（规划稿）
- 日期：2026-11-09
- 适用系统：`drama-canvas-studio`（当前画布与 `drama-canvas-feed/v2` 导入链路）
- 状态：**架构设计 / 尚未实施**

> 目标：在**不改动现有画布编辑、生成、分享、导入能力**的前提下，加一个可选的智能体侧栏。
>
> **实现入口**：[agent/README.md](../../agent/README.md)（侧车模式，不改 `server.py` / `CanvasView.tsx` / `api.ts`，避免与 Codex++ 的系统优化冲突）。用户上传技能包，智能体按技能产出 **统一格式的 Canvas Feed JSON**，系统用现有导入管线预检、预览、确认后写入画布。

---

## 0. 一句话结论

**不要发明第二套画布 JSON。**

现有系统已经有一条成熟的“投喂载荷 → 两道路由闸 → 画布节点/连线”的导入管线：

- 契约：`drama-canvas-feed/v2`
- Schema：`schemas/canvas_feed.schema.json`
- 纯规划器：`canvas_feed_contract.prepare_import()`
- 原子提交：`prepare_import_locked()` / `commit_import()`
- HTTP：`POST /api/projects/{id}/import/preview` 与 `POST /api/projects/{id}/import`
- 前端手动导入：`main.tsx` 的 `runImportPlan()`

智能体只负责**生产** feed JSON；校验、预览、导入全部复用这条链路。这样画布功能零改动，JSON 不会分叉。

---

## 1. 推荐的整体形态

```text
┌──────────────── 前端（现有画布，不改核心） ────────────────┐
│  CanvasView（不动）        AgentDock（新增，右侧可折叠）   │
│                              │                              │
│  画布/节点/生成/分享          │ 选技能、对话、锁题、看产物     │
│                              │ 预检、预览、确认导入           │
└──────────────────────────────┼──────────────────────────────┘
                               │ /api/agent/*
┌──────────────────────────────▼──────────────────────────────┐
│  现有 Python 服务（同进程，新增 agent/ 模块）                │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │ Skill 注册  │  │ Agent 运行时 │  │ 工具层（只读/校验） │  │
│  │ 上传/校验   │  │ 状态机/SSE   │  │ 项目上下文/资产检索 │  │
│  └─────────────┘  └──────────────┘  └────────────────────┘  │
│          │                 │                    │            │
│          └────────── 产物（feed.json） ─────────┘            │
│                               │                              │
│             现有导入管线（不新增写路径）                      │
│   prepare_import → preview → commit_import → 画布刷新        │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. 七条不可妥协的设计原则

1. **Agent 不是画布写入方。** 它不能直接改 `payload.nodes` / `connections`；唯一写入口是现有导入接口。
2. **唯一契约 `drama-canvas-feed/v2`。** 不新建 Agent 专用 JSON。Agent 元数据只作为可选扩展字段。
3. **先预检、再预览、最后人工确认。** 任何一次导入都必须经过 `prepare_import()` 的两道闸；失败不写库。
4. **技能包是“可版本化的数据”，不是任意代码。** 默认 prompt-only；执行脚本必须显式开启并进沙箱。
5. **上下文按需取。** 不把 100 万字的技能包整体塞进模型；走 manifest + 检索/切片 + 阶段产物。
6. **全部租户隔离、全部审计。** 技能、运行、产物、导入归属到租户；关键动作写 audit。
7. **画布故障隔离。** Agent 服务挂掉、模型超时、技能损坏时，现有手动导入和画布编辑仍然完全可用。

---

## 3. 文档地图

| 文档 | 内容 |
|---|---|
| [01-architecture.md](01-architecture.md) | 现状、目标架构、组件职责、数据流、不改画布的边界、部署形态 |
| [02-canvas-feed-contract.md](02-canvas-feed-contract.md) | 统一 JSON 契约、版本策略、两道路由闸、校验/修复循环、溯源 |
| [03-skill-package-spec.md](03-skill-package-spec.md) | 技能包目录、`skill.json`、上传校验、版本/权限、沙箱、现有 V7.4.29 包适配 |
| [04-agent-runtime-and-api.md](04-agent-runtime-and-api.md) | 运行状态机、工具、API、SSE、数据库、并发、限流、安全、可观测性 |
| [05-ux-and-workflows.md](05-ux-and-workflows.md) | 右侧智能体面板、技能库、锁题卡、产物审阅、导入预览、异常与移动端 |
| [06-implementation-plan.md](06-implementation-plan.md) | 分期、任务拆分、测试矩阵、验收标准、风险、发布与回滚 |
| [07-aigc-architecture.md](07-aigc-architecture.md) | 从 Agent 侧车升级为 AIGC 内容生产架构：模型网关、编排、管线、资产、交付 |
| [schemas/skill-package.schema.json](schemas/skill-package.schema.json) | `skill.json` 的机器可读 Schema |
| [examples/skill.json](examples/skill.json) | 以现有“漫剧老李 V7.4.29”套件为例的 manifest |
| [examples/agent-events.json](examples/agent-events.json) | SSE 事件样例 |

---

## 4. 建议的 MVP 边界（先做这一版）

**做：**

- 上传一个技能包（先支持现有 V7.4.29 套件），校验 manifest、契约 hash、文件安全。
- 在项目里打开 Agent 侧栏，选择技能套件/子技能，输入目标。
- Agent 用项目只读上下文 + 技能文档，按“锁题 → 阶段产物 → feed JSON”推进。
- 对生成的 feed 做 **只读预检**，把错误用结构化 issue 回喂给 Agent 修复（MVP 先 1 轮，Phase 2 扩到 2 轮）。
- 用户在侧栏查看 feed、警告、导入预览；点“确认导入”后调用现有导入接口。
- 导入成功后画布走现有刷新逻辑；失败/取消不写库。
- 运行记录、技能版本、产物、导入结果可追溯。

**不做（第一版明确不做）：**

- 不让 Agent 直接改画布节点、连线、位置。
- 不自动执行用户上传的 Python 脚本（现有套件的 exporter 可先由服务端内置信任运行，见 03）。
- 不自动调用付费图片/视频生成；只产出导入载荷。
- 不做跨租户技能市场。
- 不替换现有手动导入入口。

---

## 4.1 从你现在的工作流怎么迁移

你现在：

```text
第三方智能体 + 技能包
  → 生成 drama-canvas-feed/v2 JSON
  → 手动「项目设置 → 导入投喂载荷」
  → 画布
```

接入后：

```text
同一技能包上传到「技能库」
  → 内置 Agent 用同一套技能规则
  → 生成同一个 drama-canvas-feed/v2 JSON
  → 侧栏里预检/预览/点一次导入
  → 同一个现有导入接口 → 画布
```

兼容策略：

- 现有手动导入**保留**，不删不改；老 JSON 照样能导。
- 现有技能包**不需要重写**；先通过 legacy manifest adapter 接入。
- 现有第三方智能体仍可继续用；内置 Agent 是第二条路径，不是替代。
- 第一版不追求“全自动”，先把“技能 → feed → 预检 → 导入”跑稳。

---

## 5. 需要你拍板的 5 个决策

在开工前建议确认这 5 项；文档里已给出推荐值：

| # | 决策 | 推荐 |
|---|---|---|
| 1 | Agent 对话模型 | 复用现有 provider 配置里的文本模型；优先支持 tool calling 的模型，另留 JSON 降级 |
| 2 | 技能脚本执行 | MVP 只跑“内置信任”的 exporter；用户上传脚本默认不执行，Phase 3 再开沙箱 |
| 3 | 导入是否自动 | **不自动**。必须用户看预览后确认；避免误写和误付费 |
| 4 | 目标集 | 默认新建一集（`episode_title` 决定）；也支持显式“追加到现有集”，但必须二次确认 |
| 5 | 技能共享范围 | 默认租户内私有；跨租户导出/导入后续再做 |

---

## 6. 现有系统里可直接复用的东西

- 资产台账导出：`GET /api/projects/{id}/assets/export`（`drama-canvas-assets/v1`）
- 工作区只读投影：`/workspace-index`、`/workspace-nodes`
- 项目快照/回滚：现有 `canvas_snapshots` 与版本恢复
- 审计：`record_audit_event`
- 模型/供应商：`provider_catalog`、`provider_client._openai_chat`
- 生成任务的租约/队列/限流模式（Agent 运行可复用同一套工程约束）
- 现有前端对话框/Toast/错误边界

---

## 7. 读完后的下一步

1. 确认第 5 节的 5 个决策。
2. 先做 [06-implementation-plan.md](06-implementation-plan.md) 的 Phase 0（1 周技术验证）：
   - 用现有 V7.4.29 技能包手工跑一次“技能 → feed.json → 现有导入”；
   - 用真实项目验证资产复用、音色、素材引用契约；
   - 用 1 个文本模型跑通“读技能 → 产出 feed”的最小闭环。
3. Phase 0 通过后再进入 MVP 开发，避免先写大量 UI 才发现契约/模型不匹配。