# 07 · AIGC 架构（从 Agent 侧车升级为内容生产系统）

- 日期：2026-11-10
- 状态：架构提案（基于当前 `agent/` 侧车实现）
- 目标：不是“加一个聊天框”，而是把系统做成 **可编排、可生成、可交付、可观测** 的 AIGC 内容生产架构。

---

## 1. 一句话定义

**AIGC 架构 = 模型网关 + 技能/Agent 编排 + 内容管线 + 资产/生成任务 + 画布交付 + 观测/成本。**

聊天只是入口；真正的核心是“一个项目如何从一句话变成可生成的画布资产与视频”。

---

## 2. 分层架构

~~~text
┌──────────────────────────────────────────────────────────┐
│ 交互层：即梦式右侧面板 / 画布 / 任务中心 / 资产库          │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│ 编排层：Run / Stage DAG / Human Gate / Retry / Resume     │
│  - 锁题、阶段产物、人工确认、断点恢复、版本固定            │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│ 能力层：技能运行时 + 工具 + 上下文编译器                    │
│  - 技能包、检索、阶段提示词、项目/资产只读上下文            │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│ 模型层：统一 Model Gateway                                 │
│  - text / image / video / audio / embedding                │
│  - 供应商路由、fallback、限流、预算、重试、结果归一化       │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│ 资产与生成层：Asset Service + Generation Service            │
│  - 上传/TOS、版本、溯源、任务队列、租约、结果落盘           │
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│ 交付层：Canvas Feed v2 / 导入 / 导出 / 分享                 │
│  - 唯一契约；预检、预览、人工确认、原子提交                 │
└──────────────────────────────────────────────────────────┘
~~~

---

## 3. AIGC 领域模型

~~~text
Project（项目）
  └─ Episode（集）
       └─ Run（一次创作运行）
            ├─ StageRun（阶段：剧本/资产/分镜/投喂/质检/生成）
            │    ├─ Artifact（阶段产物：文本/JSON/图/视频/音频）
            │    └─ Gate（人工确认/自动门禁）
            ├─ Asset（角色/场景/道具/音色，跨集复用）
            ├─ GenerationTask（图片/视频/音频任务）
            └─ Delivery（导入画布/导出成片）
~~~

关键原则：

- **Run 不是聊天记录**，是有状态、可恢复、可审计的作业。
- **每个阶段都有输入/输出契约**，产物落盘，不靠上下文记忆。
- **Artifact 是版本化的**，下游只引用 artifactId/version，不引用“模型刚才说的话”。
- **Asset 是项目级身份**，跨集复用，不重复生成同一张脸。
- **Delivery 只走 Canvas Feed v2**，不直接改画布节点。

---

## 4. 短剧 AIGC 管线（第一个垂直）

~~~text
0. 锁题
   └─ 赛道 / 来源 / 模型 / 画幅 / 交付形态 / 段缝
1. 剧本
   └─ 一句话故事 → 人物小传 → 三幕 → 分集 → 台词
2. 资产
   └─ 角色 / 场景 / 道具 / 音色 → 资产卡 + 提示词 + 图号
3. 分镜
   └─ 段落 → 镜头 → 景别/机位/运动/时长/声音
4. 投喂
   └─ drama-canvas-feed/v2 JSON
5. 质检
   └─ 两道路由闸 + 节奏/台词/引用/资产一致性
6. 生成
   └─ 图片/视频/音频任务，结果回填资产/镜头
7. 交付
   └─ 导入画布 / 导出 / 分享
~~~

每个阶段都可以“人工确认后继续”，而不是一次 LLM 调用出全部。

---

## 5. 统一模型网关

### 5.1 接口

~~~python
class ModelGateway:
    def text(self, *, model, messages, schema=None, budget=None) -> TextResult: ...
    def image(self, *, model, prompt, refs, size, n) -> ImageResult: ...
    def video(self, *, model, prompt, refs, duration, ratio) -> VideoTask: ...
    def audio(self, *, model, text, voice, format) -> AudioResult: ...
~~~

### 5.2 职责

- 供应商适配：Ark / TeamRouter / Agnes / 百炼 / 4SToken / 本地模型。
- 统一参数：尺寸、比例、时长、参考图、音色。
- 统一结果：text / url / bytes / taskId / cost / raw。
- 失败语义：可重试 / 不可重试 / 结果待确认。
- 预算与限流：单次、每日、项目级。
- 结构化输出：JSON Schema 校验 + 修复循环。

### 5.3 当前状态

- 模型下拉已改为读取画布后台配置（`GET /api/agent/models`），不再写死 `Doubao 2.1 Lite`。
- Provider key 从租户加密配置解密，Agent 不维护第二套密钥。

- 文本：已接 TeamRouter `deepseek-flash-1017`，真实调用通过。
- 图片/视频/音频：已有画布供应商适配器，但 Agent 还没接。
- 下一步：把 `agent/app/llm.py` 升级为 `gateway`，文本/图片/视频/音频统一入口。

---

## 5.4 真实验证（2026-11-10）

- TeamRouter `deepseek-flash-1017` 已真实调用。
- 真实 `manju-laoli-skill@7.4.29` 已跑通：生成 → 预检（0 errors）→ 导入（6 节点）。
- 实现位置：`agent/app/runtime.py`、`agent/app/feed_normalizer.py`、`agent/app/db.py`。
- 结论：模型不能直接产出稳定契约，必须 **schema 约束 + 机械规范化 + 修复循环 + 人工门禁**。

## 6. 编排器（Orchestrator）

### 状态机

~~~text
queued → running → waiting_user → running → done
                    ↘ failed
                    ↘ cancelled
~~~

### 能力

- Stage DAG：阶段依赖、并行、跳过、重跑。
- Human Gate：锁题、资产确认、分镜确认、导入确认。
- Artifact：每个阶段产物落盘 + 版本。
- Resume：进程重启后从最后一个 artifact 继续。
- Idempotency：同一 stage + 输入 hash 不重复扣费。
- Trace：每次模型调用记录 provider/model/tokens/cost/latency。

### 当前状态

- `agent/app/runtime.py` 已有 run/messages/steps/artifacts/events；
- 目前是“一次生成 + 预检 + 导入”的单段管线；
- 下一步拆成 Stage DAG，先做 剧本/资产/分镜/投喂 四段。

---

## 7. 数据与存储

| 数据 | 存储 | 说明 |
|---|---|---|
| Run / Stage / Artifact | PostgreSQL | 运行状态与版本，支持查询/恢复 |
| 项目/节点/连线 | PostgreSQL JSONB + 投影 | 画布现有模型 |
| 图片/视频/音频 | TOS / 本地 uploads | 版本 + 溯源 + 签名 URL |
| 技能包 | 对象存储 + DB | 版本、manifest、契约 hash |
| 事件 | PostgreSQL / outbox | SSE、补偿、审计 |
| 成本 | PostgreSQL | 模型调用与生成任务 |

当前 Agent 用独立 SQLite 是 MVP；AIGC 正式版应并入 PostgreSQL（可复用 `drama-canvas-studio-pg` 的 PG 骨架）。

---

## 8. API 设计

~~~text
POST /api/agent/runs                 创建运行
GET  /api/agent/runs/{id}            运行详情
GET  /api/agent/runs/{id}/events     SSE
POST /api/agent/runs/{id}/resume     恢复
POST /api/agent/runs/{id}/cancel     取消

GET  /api/agent/runs/{id}/stages     阶段列表
POST /api/agent/runs/{id}/stages/{s}/confirm  人工确认
GET  /api/agent/runs/{id}/artifacts  产物
GET  /api/agent/artifacts/{id}       单产物

POST /api/agent/assets               资产登记
POST /api/agent/generations          图片/视频/音频生成
GET  /api/agent/generations/{id}     任务状态

POST /api/agent/deliveries/import    导入画布（复用现有 /import）
~~~

---

## 9. 前端（即梦式面板 + 画布嵌入）

- 画布已挂**原生** `AgentDock`：右下角「与 AI 对话」→ 右侧 React 面板，不再使用 iframe。
- 设计系统复用画布 CSS token；SSE 事件总线 + 轮询兜底；技能目录/版本/权限在弹窗中展示。
- 面板内有锁题卡、阶段时间线、阶段产物、技能分支、后台模型下拉。

当前已实现：

- 右侧 480px 面板 / 新会话 / 专业模式 chips / 输入框 / 模型选择。
- 项目/技能设置收起在“参数”里。
- 运行步骤、产物卡片、确认导入。

下一步：

- 阶段时间线：剧本 → 资产 → 分镜 → 投喂 → 质检。
- 资产卡：角色/场景/道具/音色，支持确认/重生成/锁定。
- 分镜卡：镜头列表、时长、参考图、生成状态。
- 成本/模型/版本固定显示。
- 与画布双向定位（点产物跳到对应节点）。

---

## 9.1 流式与交互

- 模型 SSE 增量进入 run message；UI 以流式光标和阶段事件呈现。
- 锁题卡是 generate 前的人工 Gate。
- 技能库/搜索/详情/分支选择与 `@` 素材引用已接入面板。

## 10. 与现有代码的关系

| 现有 | AIGC 角色 |
|---|---|
| `server.py` / `canvas_store.py` | 画布与生成基础设施，不改 |
| `provider_client.py` / `provider_catalog.py` | 模型网关的供应商适配器，复用 |
| `media_storage.py` | 资产存储，复用 |
| `canvas_feed_contract.py` | 交付契约与两闸，复用 |
| `agent/app/llm.py` | 文本网关 MVP，升级为 gateway |
| `agent/app/runtime.py` | 编排器 MVP，升级为 Stage DAG |
| `agent/app/skills.py` | 技能运行时，保留 |
| `agent/app/canvas_client.py` | 交付适配器，保留 |

---

## 11. 落地路线

### Phase 1（1–2 周）· 编排骨架

- Run/Stage/Artifact 数据模型。
- Stage DAG：剧本 → 投喂 → 预检 → 导入。
- 人工确认 Gate。
- 结构化输出 + 修复循环。

### Phase 2（2–3 周）· 模型网关

- 文本/图片/视频/音频统一接口。
- Ark/TeamRouter/Agnes/百炼适配。
- 预算、限流、失败语义、成本记录。

### Phase 3（3–4 周）· 短剧全流程

- 资产卡（角色/场景/道具/音色）。
- 分镜卡。
- 图/视频生成回填。
- 质检门。

### Phase 4（2–3 周）· 生产化

- PostgreSQL 状态库。
- 断点恢复、幂等、审计。
- 成本看板、评测集、灰度。
- 与画布双向定位/分享。

---

## 12. 需要确认的 3 个架构选择

1. **AIGC 范围**：先做“短剧全流程”，还是先做“通用图文/视频生成平台”？
   - 建议：短剧全流程，因为现有技能包和画布都是这个方向。
2. **生成执行**：Agent 只产出 feed，还是也直接调用图片/视频生成？
   - 建议：Phase 2 接生成，但保留人工确认与预算门。
3. **状态库**：Agent 独立 SQLite，还是并入 PostgreSQL？
   - 建议：Phase 4 前并入 PostgreSQL；先保留 SQLite 快速迭代。
