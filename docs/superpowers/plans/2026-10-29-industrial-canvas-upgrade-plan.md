# 工业级自由画布升级实施计划

日期：2026-10-29

依据：

- `docs/superpowers/specs/2026-10-29-industrial-canvas-upgrade-design.md`

执行规则：

- 先写失败测试，再修改实现。
- 每个逻辑主题单独提交本地 Git。
- 全部本地测试和浏览器验证通过后才交给用户验收。
- 未经用户明确授权，不部署线上。
- 不调用真实付费生成接口。

## 阶段 0：测试与审计基线

### 0.1 固化审计样例

文件：

- `tests/fixtures/canvas-feed-r10/`
- `tests/fixtures/canvas-feed-workbuddy/`
- `test_canvas_feed_roundtrip.py`

内容：

- r10 EP001 至 EP003。
- WorkBuddy 可信载荷。
- 缺 `voice_prompts` 的旧载荷负例。
- 同集重导、连续多集导入和删项目后重导。

验证：

- 先断言当前重导行为失败。
- 记录节点 ID、语义字段、引用集合和提示词的变化。

提交：

- `test:fixture-real-skill-roundtrip`

### 0.2 性能与安全审计脚本

文件：

- `tests/perf/bench_canvas_store.py`
- `tests/perf/bench_http.py`
- `web-vimax/test/canvas-performance.mjs`
- `test_security_boundaries.py`

验证：

- 100、300、600、1000 节点基准。
- 60 并发读取、24 并发节点更新。
- 1000 节点首屏、DOM、内存、长任务和拖动帧间隔。
- SSRF、请求体和安全头负例。

提交：

- `test:add-canvas-performance-security-probes`

## 阶段 1：P0 契约和数据一致性

### 1.1 canonical 图号

文件：

- `canvas_feed_contract.py`
- `test_canvas_feed_negative_contract.py`

实现：

- 统一 `图9`、`图09`、`图 9`。
- 资产、音色、正文和连线比较全部使用 canonical code。
- 零填充重复报错并给 JSON path。

验证：

- `图9` 与 `图09` 同时出现必须被拒绝。
- r10 合法样例保持通过。

提交：

- `contract:canonicalize-asset-codes`

### 1.2 运行时 JSON Schema

文件：

- `canvas_feed_contract.py`
- `schemas/canvas_feed.schema.json`
- `test_canvas_feed_schema_runtime.py`

实现：

- 增加无额外依赖的 Schema 读取与 Draft 2020-12 子集校验。
- 保留语义层处理跨数组规则。
- Schema 只作为结构闸，不复制业务语义。

验证：

- 非法类型、缺必填、错误枚举直接拒绝。
- schema 文档和运行时版本一致。

提交：

- `contract:enforce-runtime-feed-schema`

### 1.3 provenance 和 generator

文件：

- `canvas_feed_contract.py`
- `test_canvas_feed_negative_contract.py`

实现：

- 缺 generator 时产生 warning。
- provenance 记录 canonical feed hash。
- 导入 warning 写入集级 provenance。

验证：

- WorkBuddy 无 generator 载荷可导入但带 warning。
- warning 在 preview 和 commit 中一致。

提交：

- `contract:track-generator-provenance`

### 1.4 重导幂等

文件：

- `import_feed.py`
- `canvas_feed_contract.py`
- `test_canvas_feed_roundtrip.py`
- `test_import_feed.py`

实现：

- 资产身份以 canonical code、类型、角色名和 variant 组成。
- 集级节点 ID 由稳定逻辑键生成。
- 占位提示词永不覆盖已有详细提示词。
- 同集重导不产生 reusedAsset、promptStale 或引用顺序变化。
- 跨集共享资产只更新 source prompt 状态，不换节点 ID。

验证：

- 同一 feed 连续两次导入语义快照相同。
- 连续导入 EP001 至 EP003 后再重导 EP002，EP001/EP003 不受影响。
- 占位节点、详细提示词、refs、voiceRefs、assetKey 和 seriesId 稳定。

提交：

- `import:make-episode-reimport-idempotent`

### 1.5 Gate 2 错误定位

文件：

- `canvas_feed_contract.py`
- `gate_check.py`

实现：

- 重复段号在 Gate 1 报单个根因。
- Gate 2 复用共享解析和期望值。
- 避免一个根因扩散成多条引用错误。

验证：

- 重复段号只报 `SEGMENT_NO_DUPLICATE`。
- 缺音色、缺图、错连线分别只报对应错误。

提交：

- `import:deduplicate-derivation-errors`

## 阶段 2：P0 安全边界

### 2.1 出站 URL 安全策略

文件：

- `network_security.py`
- `provider_catalog.py`
- `provider_client.py`
- `tos_storage.py`
- `imgbb_storage.py`
- `test_security_boundaries.py`

实现：

- 拒绝回环、私网、链路本地、组播和保留地址。
- DNS 解析后检查全部地址。
- 重定向重新检查。
- 供应商测试和实际请求共用策略。

验证：

- `127.0.0.1`、`localhost`、`10.0.0.0/8`、`169.254.169.254` 全部拒绝。
- 正常公网提供商地址通过。

提交：

- `security:block-private-provider-urls`

### 2.2 JSON 请求边界

文件：

- `server.py`
- `test_tenant_http.py`
- `test_security_boundaries.py`

实现：

- 普通 JSON 上限 8MB。
- 超限返回 413 且不读取完整 body。
- 限制请求头、嵌套深度和 Content-Length 异常。

验证：

- 8MB 内正常。
- 超限请求返回 413。
- 负数、非数字和分块异常安全拒绝。

提交：

- `security:limit-json-request-bodies`

### 2.3 路径和响应头

文件：

- `server.py`
- `deploy/nginx*.conf`
- `test_tenant_http.py`

实现：

- 账号模式禁用任意 `feed_path`。
- 增加 CSP、Frame、Referrer、Permissions Policy。
- 保留上传文件 `nosniff` 和只读缓存。

验证：

- 任意服务器路径不可作为导入来源。
- 首页和 API 安全头符合清单。

提交：

- `security:harden-import-paths-and-headers`

## 阶段 3：P1 存储与任务底座

### 3.1 SQLite WAL 和项目锁

文件：

- `canvas_store.py`
- `server.py`
- `test_canvas_store_concurrency.py`

实现：

- 开启 WAL。
- 项目级锁替代跨租户全局项目写锁。
- 保持 revision 前置条件。

验证：

- 不同项目并发写不互相阻塞。
- 同一节点并发编辑有确定结果或 409。

提交：

- `data:add-wal-and-project-locks`

### 3.2 Repository 边界

文件：

- `canvas_repository.py`
- `canvas_store.py`
- `server.py`

实现：

- 项目、节点、连线、布局和快照接口。
- 上层不再直接操作 payload 结构。
- 保留旧数据迁移适配。

验证：

- Repository 契约测试覆盖所有写接口。
- 旧 payload round-trip 不变。

提交：

- `data:introduce-canvas-repository`

### 3.3 增量保存与快照节流

文件：

- `canvas_repository.py`
- `server.py`
- `test_canvas_store_concurrency.py`

实现：

- 节点 patch 只更新变更节点。
- 自动快照按逻辑操作节流。
- 手动项目版本不参与自动裁剪。

验证：

- 1000 节点单节点修改目标中位低于 100ms。
- 快照数量受控。

提交：

- `data:add-incremental-saves`

### 3.4 项目版本恢复

文件：

- `canvas_repository.py`
- `server.py`
- `web-vimax/src/CanvasView.tsx`

实现：

- 版本列表、预览和恢复。
- 恢复前自动创建保护快照。

验证：

- 恢复节点、连线、提示词和媒体版本。
- 恢复失败不修改当前项目。

提交：

- `ux:add-project-version-restore`

## 阶段 4：P1 画布性能

### 4.1 分级渲染

文件：

- `web-vimax/src/CanvasView.tsx`
- `web-vimax/src/canvasPerformance.ts`
- 对应测试

实现：

- 远视图骨架。
- 中视图基础操作。
- 近视图完整控件。
- 视口外媒体不激活。

验证：

- 1000 节点 DOM 小于 20000。
- 首屏低于 3 秒。
- 长任务小于 200ms。

提交：

- `perf:add-canvas-level-of-detail`

### 4.2 媒体延迟激活

文件：

- `CanvasView.tsx`
- `api.ts`

实现：

- 图片缩略图。
- 视频播放器可见时激活。
- 音频波形可见时计算。

验证：

- 1000 节点 JS 堆低于 100MB。
- 拖动 p95 小于 33ms。

提交：

- `perf:defer-heavy-media`

## 阶段 5：P1/P2 体验与工业化

### 5.1 导入 diff 和任务体验

- 导入 preview 展示语义 diff。
- 任务中心区分本地错误和供应商错误。
- 费用预估与预算上限进入提交前检查。

提交：

- `ux:improve-import-and-task-operations`

### 5.2 键盘、焦点和无障碍

- 对话框焦点陷阱。
- 关闭后焦点恢复。
- 完整快捷键和屏幕阅读器标签。

提交：

- `ux:complete-keyboard-and-accessibility`

### 5.3 多标签同步与冲突

- BroadcastChannel 同步扩展。
- 冲突提示和重载动作。
- stale revision 不静默覆盖。

提交：

- `data:complete-cross-tab-conflict-handling`

### 5.4 监控和审计

- 导入、生成、配置和账号审计日志。
- 性能指标和任务指标。
- 敏感字段脱敏。

提交：

- `ops:add-audit-and-runtime-metrics`

## 最终验收

必须全部通过：

- 后端全量测试。
- 前端全量测试。
- `tsc --noEmit`。
- `npm run build`。
- `python -m py_compile`。
- r10 和历史智能体 JSON 兼容矩阵。
- 1000 节点性能预算。
- 并发读写和租户隔离。
- SSRF、请求体、安全头和路径负例。
- Chromium 桌面与移动浏览器流程。
- `git diff --check`。

最终交付：

- 本地可运行版本。
- 完整测试报告和指标。
- 本地 Git 提交清单。
- 线上部署包和回滚包。
- 等待用户本地验收。

用户明确说“可以提交线上”后，才允许部署线上。
