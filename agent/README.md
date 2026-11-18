# Canvas Agent · 画布内置智能体

> 位置：`agent/`。Agent 是当前画布系统的正式组成模块，与主服务同仓维护、统一测试和发布。
> 浏览器只访问主服务同源 `/api/agent/*`；主服务验证登录、租户和项目权限后，通过签名上下文把请求转给 Agent 侧车。唯一画布写路径仍然是现有 Feed 预览、用户确认和 `/import`。

## 启动

~~~bash
cd agent
uv venv .venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
copy .env.example .env
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8895
~~~

打开：http://127.0.0.1:8895/agent

前置：画布后端跑在 http://127.0.0.1:8890。

## 当前能力

- 同源登录、租户/账号隔离、项目只读工具
- 连续会话、消息、草稿刷新恢复和运行历史
- 技能库：内置 `demo-director`；上传 zip（`skill.json` 或旧 `PACKAGE-MANIFEST.json` 适配）
- 有界工具循环：项目索引、节点、依赖、资产台账和 Feed 预检
- 运行：项目 + 技能 + 目标 → 工具读取 → feed JSON → 画布预检
- 预检失败自动修复，最多 2 轮
- 产物预览、确认后导入
- SSE 事件、断线恢复 + 轮询兜底
- 没有配置 LLM 时自动 mock，用内置有效 feed 跑通链路

## API

- `GET /api/agent/health`
- `GET /api/agent/bootstrap`
- `POST /api/agent/conversations`
- `GET /api/agent/conversations/{id}`
- `PUT /api/agent/conversations/{id}/draft`
- `POST /api/agent/conversations/{id}/messages`
- `GET /api/agent/projects`
- `GET /api/agent/skills`
- `POST /api/agent/skills/upload`
- `POST /api/agent/runs`
- `GET /api/agent/runs/{id}`
- `GET /api/agent/runs/{id}/events`
- `POST /api/agent/runs/{id}/import`
- `POST /api/agent/runs/{id}/cancel`

## 模型来源

- 面板模型下拉来自画布后台 `/api/model-catalog` 对应的 provider/model 配置。
- Agent 通过 `backend_models.py` 读取同一份租户配置并解密 provider key；不维护第二套模型清单。
- `.env` 只作为脱离画布运行时的 fallback。

## 边界

- 不直接写 SQLite/PostgreSQL，不直接改 payload。
- 不执行上传技能里的脚本；只读 `SKILL.md` 和 manifest。
- 导入必须用户点“确认导入”；Agent 不会自动提交。
- 上传 zip 有路径穿越、符号链接、大小、文件数校验。
- 默认 mock 只用于演示；生产必须配置 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`。

## 集成

- 原生 React 面板：`web-vimax/src/agent/AgentDock.tsx`
- 同源 API 客户端：`web-vimax/src/agent/agentApi.ts`
- 主服务网关：`agent_gateway.py`
- 隔离进程：Agent sidecar 默认监听 `127.0.0.1:8895`，不直接暴露给浏览器


## 即梦风格面板

- 完整页：`http://127.0.0.1:8895/agent`
- 纯面板（可 iframe 嵌入）：`http://127.0.0.1:8895/agent?embed=1`
- 一键起服务 + 右侧面板窗口：`agent/start-panel.cmd`

面板顶部“创作设置”默认收起，点右下角参数图标选择技能、分支和模型。画布内使用原生 AgentDock，不再依赖 iframe。


## 真实 E2E 记录（2026-11-10）

- 模型：TeamRouter `deepseek-flash-1017`
- 技能：`manju-laoli-skill` @ `7.4.35`
- 镜头语法：`short-drama-director/references/agent-cinematic-grammar-contract.md`
- 结果：
  - 阶段：brief / generate / validate 全部 done
  - 预检：0 errors
  - 修复轮次：0
  - 导入：成功，revision 2，6 个节点
- 说明：LLM 原始输出仍会漏字段；`feed_normalizer.py` 会修复图号、角色名、音色引用、age 类型、generator 等机械问题，语义内容仍由模型/人工门禁负责。


## 嵌进画布（原生 AgentDock）

- 原生 React 面板：`web-vimax/src/agent/AgentDock.tsx`
- API 客户端：`web-vimax/src/agent/agentApi.ts`
- 设计系统：`web-vimax/src/agent-dock.css`
- **不再使用 iframe**；直接复用画布 CSS 变量与组件树。
- 面板从顶部 58px 开始，不覆盖画布右上角按钮。
- SSE 事件总线 + 轮询兜底；技能目录/版本/权限在原生弹窗中展示。
- 可用 `VITE_AGENT_URL` 指定 Agent 服务地址；默认当前主机 8895。

## 锁题卡 / 阶段产物 / 多技能路由

- 开始生成前先弹锁题卡：赛道、来源、模型版本、画幅、交付、段缝。
- 锁题写入 run；后端记录 `locks` 阶段和 artifact。
- 阶段：`brief → locks → generate → validate → deliver`。
- 技能分支下拉来自技能包内 `*/SKILL.md`，最多 13 个；选中的分支会替换系统提示词入口。
- 产物区展示 brief / locks / canvas_feed。


## 流式与交互升级（2026-11-10）

- `chat_stream()` 接收 OpenAI SSE 增量；运行时写入 assistant message 并推 `message.delta` 事件。
- 面板显示流式光标、阶段时间线、阶段产物、锁题卡。
- 技能库支持搜索、详情、13 个分支选择。
- `@` 打开画布素材引用菜单。
- 动画：面板滑入、消息淡入、阶段脉冲、按钮/卡片微动效。


## 技能选择（2026-11-10 二次调整）

- 点输入框左下角 ✦ 打开**技能选择弹层**：搜索、技能列表、新建技能、管理技能。
- 选中后技能变成输入框里的 **@ 技能 chip**，可点 × 移除；不再只藏在设置表单里。
- 「管理技能」打开管理弹窗：左侧列表 + 右侧 SKILL.md 详情 + 使用。
- 发送时用 chip 的技能 ID + 技能分支，不再依赖顶部下拉。
