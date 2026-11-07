# Agent Studio 与创作技能集成架构设计

日期：2026-11-07

状态：待用户审阅

适用系统：`drama-canvas-studio`

## 1. 目标

在现有画布系统内增加一个受控的 Agent 创作中心：

```text
用户输入剧本或创作要求
→ Agent 按受控阶段调用创作技能
→ 生成结构化中间制作单
→ 后端确定性编译为固定 drama-canvas-feed/v2
→ 契约校验
→ 现有导入器预演
→ 用户确认
→ 导入现有画布
```

目标是提高创作自动化程度，同时保证：

- 最终 JSON 格式固定。
- 资产图号、音色、引用和连线不交给模型自由决定。
- Agent 中断、刷新或服务重启后可恢复。
- 用户确认前不修改正式项目。
- 默认关闭时，现有画布行为和性能保持不变。
- 不维护两套长期分叉的产品代码。

## 2. 当前系统事实

本设计基于当前仓库代码，不假设不存在的能力。

### 已存在

- 固定 Feed Schema：`schemas/canvas_feed.schema.json`。
- 业务契约校验：`canvas_feed_contract.py`。
- 导入预演和正式导入：
  - `POST /api/projects/{id}/import/preview`
  - `POST /api/projects/{id}/import`
- 分镜技能、资产技能、技能编译脚本和规则库。
- 文本模型配置。本地已有 `deepseek-flash-1017`，能力为 `text`。
- `provider_client.generate_text()`。
- 右侧 Agent 对话壳层和事件渲染组件。
- 画布媒体任务、唯一任务 ID、TOS 播放和多租户隔离。

### 现有限制

- 当前右侧 Agent 壳层与画布生成链不是同一个受控创作状态机。
- 现有 `build_board_lite.py` 主要从 Markdown 解析画布载荷，不适合作为 Agent 内部协议边界。
- `compile_feed.py` 能生成投喂稿，但没有覆盖从剧本到 Feed v2 的完整可恢复运行。
- 分镜稿九字段主要依靠 AI 自检，尚缺少独立硬校验。
- 缺少 Agent 运行的独立持久化、阶段重试、幂等和审计。

## 3. 设计原则

1. **业务整体，运行隔离**
   - 正式系统仍是一个产品、一个项目数据源。
   - Agent Worker 可以独立进程运行，但不拥有第二套画布业务真相。

2. **模型创作，代码定格式**
   - Agent 可以创作内容。
   - Agent 不得直接拼最终 Feed JSON。
   - 代码负责编号、引用、音色绑定、编译和校验。

3. **正式写入只走现有导入器**
   - 新模块不得另造第二套画布导入逻辑。
   - 预览与正式导入继续使用 `canvas_feed_contract.prepare_import()`。

4. **阶段失败就地停止**
   - 未通过当前阶段校验，不得进入下一阶段。
   - 用户确认前不得写正式项目。

5. **默认关闭与可回滚**
   - 新入口、API、Worker 和前端资源均受后端 Feature Flag 控制。
   - 开关关闭时不轮询、不创建运行、不加载 Agent 面板业务代码。

6. **单一数据来源**
   - 项目、节点、连线、资产和任务仍以现有 `canvas.db` 为权威。
   - Agent 数据库只保存创作运行、阶段输出和审计，不复制画布业务状态。

7. **不触发隐藏费用**
   - 影子模式不得调用图片、音频和视频生成。
   - Agent 文本模型调用必须有租户预算、并发和次数上限。

## 4. 非目标

第一版不包括：

- Agent 自动提交付费图片、音频、视频生成。
- Agent 自由修改现有画布节点。
- 多 Agent 自主无限循环。
- 任意 Shell、任意网络请求或任意文件系统访问。
- 自动发布或自动分享。
- 删除现有导入、生成、TOS 或画布链。
- 让浏览器状态成为 Agent 任务权威。

## 5. 总体架构

```text
┌──────────────────────────────────────────────┐
│ Browser                                      │
│                                              │
│ 现有画布 UI ──────────────┐                  │
│                           │                  │
│ Agent Studio 面板（受开关）│                  │
└───────────────┬───────────┴──────────────────┘
                │ HTTPS / SSE
┌───────────────▼──────────────────────────────┐
│ Existing Web/API Service                     │
│                                              │
│ 现有项目/画布/导入/生成 API                  │
│ Agent Studio API（受开关）                   │
│ Feature Flag + 租户白名单                    │
└───────┬───────────────────────┬──────────────┘
        │                       │
        │ existing import       │ publish jobs
        ▼                       ▼
┌──────────────────┐   ┌──────────────────────┐
│ canvas database  │   │ Agent Studio DB      │
│ project truth    │   │ run/stage/attempt    │
└──────────────────┘   └──────────┬───────────┘
                                  │
                       ┌──────────▼───────────┐
                       │ Agent Worker          │
                       │                      │
                       │ manifest router      │
                       │ stage orchestrator   │
                       │ model gateway        │
                       │ deterministic compile│
                       │ validators           │
                       └──────────┬───────────┘
                                  │
                  ┌───────────────▼────────────────┐
                  │ Read-only Skill Packages       │
                  │ manifest + stage slices        │
                  └───────────────┬────────────────┘
                                  │
                  ┌───────────────▼────────────────┐
                  │ Text Model via existing        │
                  │ provider_catalog + client      │
                  └────────────────────────────────┘
```

### 5.1 部署形态

开发阶段：

- 一个 Git 仓库。
- 一个正式本地实例。
- 一个 Agent 实验实例或独立 worktree。
- 两套端口、数据库、`uploads/`、配置和日志。

上线阶段：

- 现有一个 Web/API 服务。
- 一个可独立重启的 Agent Worker 服务。
- 两套进程职责，一个产品、一个登录体系、一个项目真相。

### 5.2 为什么需要独立 Worker

- Agent 阶段耗时远高于普通 API。
- 重试不应占满 Web 服务线程。
- Worker 故障不能导致画布服务不可用。
- 独立并发控制可防止 Agent 额度与媒体生成争抢资源。

## 6. 模块边界

新增模块：

```text
agent_studio/
  __init__.py
  config.py
  contracts.py
  manifest.py
  state_store.py
  orchestrator.py
  model_gateway.py
  skill_runner.py
  canonicalizer.py
  compiler.py
  validators.py
  import_bridge.py
  api.py
  worker.py
```

职责：

| 模块 | 职责 | 禁止事项 |
|---|---|---|
| `config.py` | 开关、白名单、预算、并发 | 不读取浏览器提交的权限标志 |
| `contracts.py` | 中间态数据模型和校验 | 不复制 Feed Schema |
| `manifest.py` | 技能版本、阶段、哈希和阶段文件路由 | 不执行技能中的任意脚本 |
| `state_store.py` | 运行、阶段、尝试和事件持久化 | 不保存画布业务副本 |
| `orchestrator.py` | 状态机、重试、取消和恢复 | 不直接写正式项目 |
| `model_gateway.py` | 只调用已启用文本模型 | 不调用媒体模型 |
| `skill_runner.py` | 读取技能切片，组装阶段请求，解析结构输出 | 不提供任意工具执行 |
| `canonicalizer.py` | 归一化资产身份和结构字段 | 不生成无法追溯的图号 |
| `compiler.py` | 中间制作单 → Feed v2 | 不调用模型，不让模型补字段 |
| `validators.py` | 结构、语义、Feed 和导入预演 | 失败不得自动放过 |
| `import_bridge.py` | 调用现有预览或正式导入 | 不复制导入逻辑 |
| `api.py` | Agent Studio HTTP/SSE | 不放业务状态机 |
| `worker.py` | 后台阶段运行 | 不承载现有画布 API |

## 7. 技能包契约

### 7.1 不可变技能版本

技能目录只读，按版本装载：

```text
skill_packages/short-drama-director/7.4.29/
  manifest.json
  stages/
  schemas/
  validators/
```

`manifest.json`：

```json
{
  "skillId": "short-drama-director",
  "version": "7.4.29",
  "packageRevision": "skill-sha256-prefix",
  "inputSchema": "production-input/v1",
  "stateSchema": "project-state/v1",
  "outputSchema": "drama-canvas-feed/v2",
  "stages": [
    {"id": "script", "required": true},
    {"id": "assets", "required": true},
    {"id": "storyboard", "required": true},
    {"id": "compile", "required": true}
  ],
  "allowedTools": [
    "read_skill_slice",
    "validate_stage",
    "compile_canvas_feed",
    "import_preview"
  ]
}
```

运行记录必须保存：

- `skillId`
- `version`
- 技能包哈希
- 阶段清单哈希
- 模型 ID
- 输入哈希

### 7.2 阶段切片

不得把整套技能一次性塞入上下文。

每个阶段只装载：

- 阶段目标。
- 输入结构 Schema。
- 输出结构 Schema。
- 当前阶段权威规则。
- 允许使用的工具。
- 失败示例和校验说明。

历史阶段只传结构化摘要，不重复传全文。

## 8. 数据契约

### 8.1 中间态

Agent 只允许输出经过 Schema 校验的结构化中间态：

```text
ProductionInput/v1
  projectName, episodeId, sourceText, locks, requirements

ProjectState/v1
  metadata
  characters[]
  scenes[]
  props[]
  voices[]
  segments[]
  shotPlan[]
  provenance
```

每个阶段声明自己可写字段。跨阶段字段由系统继承，模型不得覆盖未授权字段。

### 8.2 最终 Feed

唯一权威仍是：

```text
schemas/canvas_feed.schema.json
```

编译器输出 `drama-canvas-feed/v2`。技能包内如保留 schema 副本，只能用于构建时对照，运行时必须验证哈希，禁止形成双权威。

### 8.3 稳定资产身份

资产身份规则：

```text
semanticCode  稳定语义身份，如 CHR-001
seriesId      系列身份
variantKey    形态身份
code          画布槽位，只能是 图N
assetId       数据库稳定唯一 ID
```

`图N` 只由系统分配。Agent 输出资产语义身份，不输出未经系统确认的最终槽位号。

## 9. Agent 运行状态机

```text
queued
→ script_running
→ script_validated
→ assets_running
→ assets_validated
→ storyboard_running
→ storyboard_validated
→ compiling
→ compiled
→ preview_running
→ waiting_confirmation
→ importing
→ imported
```

终态：

```text
failed
cancelled
blocked
expired
```

规则：

- 只有前一阶段已进入对应的 `*_validated` 状态，后继阶段才能运行。
- 每个阶段最多自动尝试 2 次。
- 同一字段连续失败不无限重试。
- 用户取消后不再启动下一个模型调用。
- 服务重启后，从最后已提交阶段恢复。
- `preview_running` 不增加正式项目 revision。
- `imported` 只能在现有导入成功响应后写入。

## 10. 数据表

Agent Studio 使用独立数据库 `agent-studio.db`。

### `agent_runs`

| 字段 | 含义 |
|---|---|
| `id` | runId |
| `tenant_id` | 租户 |
| `project_id` | 现有项目 ID |
| `episode_id` | 集号 |
| `client_operation_id` | 幂等键 |
| `skill_id` / `skill_version` | 技能版本 |
| `model_id` | 文本模型 |
| `status` | 运行状态 |
| `current_stage` | 当前阶段 |
| `input_sha256` | 输入哈希 |
| `base_revision` | 项目初始 revision |
| `attempt_count` | 总尝试次数 |
| `created_at` / `updated_at` | 时间 |

唯一约束：

```text
(tenant_id, client_operation_id)
```

### `agent_stage_outputs`

保存每阶段成功结果：

- `run_id`
- `stage`
- `schema_version`
- `payload_json`
- `payload_sha256`
- `validator_version`
- `created_at`

### `agent_attempts`

保存每次模型尝试：

- 请求哈希
- 模型与参数
- prompt slice 哈希
- 原始响应摘要
- 解析错误
- 校验错误
- token 数和耗时
- 是否成功

不得保存明文密钥和不必要的私密原文副本。

### `agent_run_events`

保存 SSE 可回放事件：

- 递增 event id
- `run_id`
- `event_type`
- `public_payload_json`
- `created_at`

### `agent_artifacts`

可选保存：

- 中间态 JSON
- 编译后的 Feed JSON
- 校验报告
- 预览摘要

所有产物带哈希和 `run_id`。

## 11. API

新增独立命名空间：

```text
/api/agent-studio/v1
```

### 创建运行

```http
POST /api/agent-studio/v1/projects/{projectId}/runs
```

请求必须包含：

```json
{
  "clientOperationId": "uuid",
  "episodeId": "EP001",
  "source": {
    "type": "text",
    "text": "..."
  },
  "locks": {},
  "skillId": "short-drama-director",
  "skillVersion": "7.4.29",
  "modelId": "deepseek-flash-1017"
}
```

### 查询与事件

```text
GET  /api/agent-studio/v1/runs/{runId}
GET  /api/agent-studio/v1/runs/{runId}/events
POST /api/agent-studio/v1/runs/{runId}/cancel
```

### 阶段操作

```text
POST /api/agent-studio/v1/runs/{runId}/stages/{stage}/retry
POST /api/agent-studio/v1/runs/{runId}/compile
POST /api/agent-studio/v1/runs/{runId}/import-preview
POST /api/agent-studio/v1/runs/{runId}/import
```

### 权限

- 必须登录。
- 必须属于同一租户。
- viewer 只能查看自己的只读结果。
- editor 可以创建和导入，但导入仍受现有项目权限约束。
- admin 可以查看租户审计，不可跨租户读取私有内容。

### 幂等

- 同一 `clientOperationId` 重复创建，返回同一 `runId`。
- 同一 run 的 compile、preview、import 重复请求不得产生重复项目。
- 导入必须携带预演 revision。
- revision 变化时，必须重新预演，不得静默覆盖他人更新。

### SSE 事件

公共事件：

```text
run_queued
stage_started
stage_progress
stage_failed
stage_validated
compile_started
compile_completed
preview_started
preview_completed
waiting_confirmation
import_started
import_completed
run_failed
run_cancelled
```

事件只由后端发布，前端不得自行推断“已完成”。

## 12. 编译器

编译器是纯函数：

```text
compile(projectState, locks, manifest)
→ drama-canvas-feed/v2
```

要求：

- 不发起网络请求。
- 不调用模型。
- 不写数据库。
- 同样输入和同样版本必须产生同样输出。
- 所有排序稳定。
- 图号分配确定。
- 引用生成确定。
- 音色绑定确定。
- 无当前时间等非确定字段参与语义哈希。

编译阶段负责：

- 解析语义资产。
- 分配并复用 `图N`。
- 生成完整引用。
- 生成 `asset_prompts` 和 `voice_prompts`。
- 生成 `segments[].canvas` 声明。
- 生成素材引用身份契约。
- 生成来源与技能版本 metadata。

## 13. 校验门

### Gate 1：阶段契约

检查：

- JSON Schema。
- 必填字段。
- 台词说话人集合。
- 资产语义身份。
- 分镜九字段。
- 镜头编号和连续时码。

### Gate 2：生成器契约

检查：

- `code` 只能是 `图N`。
- 语义码、形态、图号唯一性。
- 场景只登记母版。
- 复用身份一致。
- 引用必须是完整 `@类型｜名称（图N）`。
- 所有引用和资产声明双向一致。

### Gate 3：现有导入预演

调用现有 `prepare_import()`：

- 构建候选项目。
- 校验音色和实际连线。
- 校验资产槽位。
- 不写正式项目。

### Gate 4：人工确认

显示：

- 集号。
- 分段数。
- 镜头数。
- 资产数和新增/复用数。
- 音色引用。
- 连线数量。
- 校验告警。

用户确认后才调用现有正式导入。

## 14. 错误与恢复

### 可恢复

- 文本模型超时。
- 429 限流。
- Worker 重启。
- SSE 断开。
- 单个阶段输出格式错误。

处理：

- 有界重试。
- 从最后成功阶段恢复。
- SSE 通过 event id 补发。
- 前端重新连接后读取后端状态。

### 不可自动放行

- Feed Schema 错误。
- 图号或语义身份冲突。
- 台词说话人缺音色。
- 引用和连线不一致。
- 项目 revision 冲突。

必须回到对应阶段修复或提示用户处理。

### Worker 崩溃

使用租约：

- Worker 启动运行前领取租约。
- 定期续租。
- 超过租约时间未续租，任务标记为可恢复。
- 恢复前先读取已提交阶段，不重放已完成阶段。

## 15. 安全边界

Agent 无权：

- 读取 `.env`、密钥、其他租户文件。
- 执行 Shell。
- 发起任意 URL。
- 访问现有数据库连接对象。
- 修改技能文件。
- 直接提交媒体生成。
- 绕过现有项目权限。

允许：

- 读取当前 manifest 指定的技能切片。
- 调用文本模型。
- 写入自己的运行数据库。
- 调用现有导入预演。
- 在用户确认后调用现有正式导入。

文件访问：

- 只允许技能包根目录下的 manifest 声明路径。
- 拒绝绝对路径、`..`、符号链接逃逸。
- 输入文本只在当前 run 作用域使用。

日志：

- 不记录 API Key。
- 不记录完整 Cookie。
- 对原文只记录哈希和必要摘要。

## 16. 功能开关与灰度

配置：

```text
CANVAS_AGENT_STUDIO_V1=off
CANVAS_AGENT_STUDIO_TENANTS=
CANVAS_AGENT_SHADOW_MODE=on
CANVAS_AGENT_MAX_CONCURRENCY=2
CANVAS_AGENT_MAX_ATTEMPTS=2
CANVAS_AGENT_RUN_TTL_SECONDS=604800
```

阶段：

1. `off`：所有新路径关闭，现有行为不变。
2. `shadow`：只对白名单租户生成、校验和记录，不显示导入按钮。
3. `preview`：白名单租户可以查看预览和问题。
4. `canary`：白名单租户可以确认导入。
5. `on`：指定租户启用。

浏览器不决定租户资格，后端根据租户上下文返回能力。

## 17. 前端

新增受控面板，不修改原画布业务组件。

面板内容：

- 技能和版本。
- 文本模型。
- 锁定项。
- 阶段时间线。
- 当前阶段与等待原因。
- 结构化产物摘要。
- 校验问题列表。
- 预览摘要。
- 取消、重试、确认导入。

开关关闭：

- 不显示新入口。
- 不请求 `/api/agent-studio/v1`。
- 不加载新 chunk。

开关开启但影子模式：

- 不显示正式导入按钮。
- 明确显示“影子运行，不写画布”。

前端不本地判断运行成功；只渲染后端 `status`。

## 18. 与现有系统的接入

### 保留

- 现有画布前端。
- 现有项目 API。
- 现有导入器。
- 现有媒体任务。
- 现有 TOS 和分享。

### 增量修改

仅允许：

- 新模块导入。
- 新 API 路由，受开关保护。
- 新静态资源 chunk，受开关保护。
- 新配置读取。
- 新服务文件。
- `schemas/` 中的 schema 哈希登记。

### 禁止

第一批不修改：

- `CanvasView.tsx` 主画布逻辑。
- 现有生成提交和任务状态投影。
- `provider_client.py` 的媒体生成行为。
- 现有导入语义。
- 主题、视频控件和 TOS 播放。
- 现有技能包规则原文。

## 19. 测试策略

### 单元

- 中间态 Schema。
- 确定性编译器。
- 图号分配和复用。
- 台词说话人解析。
- 音色绑定。
- 引用双向一致。
- 错误映射。
- 状态机迁移。

### 契约

- 编译器产物过 `schemas/canvas_feed.schema.json`。
- 过 `canvas_feed_contract.validate_declared_feed()`。
- 过 `prepare_import()`。
- 现有 valid fixture 全部仍通过。

### 集成

- 创建运行。
- SSE 断开重连。
- Worker 重启恢复。
- 阶段重试。
- 同 `clientOperationId` 幂等。
- 预览 revision 不变。
- 正式确认后只增长一次 revision。
- 并发租户隔离。

### 负向

- 缺音色。
- 错误图号。
- 同一资产双语义码。
- 引用与连线不一致。
- 模型输出 Markdown 或非 JSON。
- 模型尝试篡改系统锁定字段。
- 路径穿越。
- 跨租户读取。
- 用户取消后继续调用模型。

### 浏览器

- 开关关闭时新资源不请求。
- 开关开启时面板可用。
- 预约、重试、取消、预览和确认流程。
- 刷新后恢复状态。
- 控制台零未处理错误。

### 回归

- 现有后端测试。
- 现有前端测试。
- 现有构建。
- 本地画布真实打开、切换集、拖拽、导入和播放。
- 性能没有实质退化。

## 20. 性能与容量

要求：

- Agent 阶段不阻塞现有 API 线程。
- 独立并发上限，默认 2。
- 单租户并发上限可配。
- 事件和尝试记录有保留期限。
- 运行产物有 TTL。
- SSE 只传增量事件。
- 不在前端一次加载所有历史尝试。

建议指标：

- 现有首屏加载变化小于 5%。
- 现有项目 API P95 变化小于 10%。
- 开关关闭时新增网络请求为 0。
- 影子运行不增加正式项目 revision。

## 21. 可观测性

每个运行提供：

- runId、projectId、episodeId。
- 阶段耗时。
- 模型调用次数。
- 模型重试次数。
- 校验失败码。
- 编译哈希。
- 预览结果。
- 导入结果。

审计事件：

- 创建运行。
- 阶段失败。
- 编译。
- 预览。
- 用户确认。
- 导入。
- 取消。

## 22. 发布与回滚

### 发布顺序

1. 发布新模块、迁移和默认关闭开关。
2. 启动 Agent Worker。
3. 白名单租户开启 shadow。
4. 对比真输入的结构和校验结果。
5. 开启 preview。
6. 开启小范围 canary 导入。
7. 逐步扩大租户。

### 回滚

立即：

- 将 `CANVAS_AGENT_STUDIO_V1=off`。
- 停用 Agent Worker。
- 保留 Agent 数据库和产物，不删除审计证据。

不影响：

- 现有画布。
- 现有图片/音频/视频任务。
- 现有 TOS 播放。
- 现有分享和权限。

## 23. 实施分解

### Phase 0：只新增基础设施

- Agent Studio DB 迁移。
- Feature Flag。
- Run、Stage、Attempt、Event Store。
- 只读状态 API。

验收：开关关闭时现有路径和请求完全不变。

### Phase 1：中间态和编译器

- 增加中间态 Schema。
- 增加纯函数编译器。
- 生成 Feed v2。
- 接入现有 Feed Schema 和业务契约测试。

验收：固定输入多次编译，语义输出稳定。

### Phase 2：技能运行与影子模式

- 技能 manifest。
- 阶段切片加载。
- 文本模型网关。
- 阶段状态机与重试。
- shadow 运行。

验收：完整生成、校验和审计，不写正式项目。

### Phase 3：预演和前端面板

- 导入预演桥化。
- SSE。
- Agent Studio 面板。
- 刷新恢复。

验收：用户能查看结构、校验和预览，仍不能写画布。

### Phase 4：受控正式导入

- 用户确认。
- revision 检查。
- 调用现有导入。
- 审计和回滚验证。

验收：失败不写入，成功只导入一次，可追溯。

### Phase 5：灰度

- 内部租户。
- 小范围租户。
- 性能与错误率门禁。
- 全量开关。

## 24. 第一份实施计划的范围

第一份实施计划只覆盖 Phase 0 与 Phase 1：

- 独立数据库。
- Feature Flag。
- 中间态 Schema。
- 确定性编译器。
- 不接模型、不接前端、不写画布。

完成后再根据真实产物决定 Phase 2 的模型编排，不一开始同时改 Agent、前端、导入和生成。

## 25. 验收定义

本设计落地成功必须满足：

- 现有功能测试与浏览器回归通过。
- 新开关关闭时新增请求、写入和资源加载均为 0。
- 同一中间态多次编译结果稳定。
- 编译器产物通过现有 Schema、业务契约和导入预演。
- 所有编号、音色和引用可追溯。
- 失败不修改正式项目。
- 用户确认前不写画布。
- Agent 中断后可恢复。
- 不需要维护两套产品或两套画布数据。
- 正式环境只有一个项目列表、一个项目详情入口、一个资产真相和一个导入入口。
- Agent Worker 的部署拆分不改变用户看到的产品边界；关闭 Worker 后现有画布仍可完整使用。

## 26. 待审阅决策

以下决策已在本设计中给出推荐值，等待用户确认：

1. 第一版只做到“生成并导入画布”，不自动触发媒体生成。
2. Agent 使用中间态，不直接生成 Feed JSON。
3. Agent Worker 独立进程，产品仍是整体系统。
4. 新功能默认关闭，先 shadow，再 preview，再 canary。
5. 第一份实施计划只做 Phase 0 与 Phase 1。
6. 正式画布写入只使用现有 `prepare_import()`。

