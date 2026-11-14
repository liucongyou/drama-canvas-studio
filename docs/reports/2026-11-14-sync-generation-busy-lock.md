# 同步生成完成后“生成中 1 项”修复记录

日期：2026-11-14 13:51 +08:00

## 根因

线上任务台账和持久化媒体节点都没有 active 生成记录，因此不是供应商任务未结束，
也不是节点状态没有落库。

前端在同步生成成功后：

- 请求函数自身清除了 `inflight`；
- 后端已经返回完成结果；
- 但 `CanvasView` 的本地 `optimisticGenerationStartsRef` 没有释放；
- 底部“生成中 N 项”仍把该 optimistic 节点计算为 busy，最长持续到 5 分钟 TTL。

## 修复

- 新增 `releaseOptimisticGenerationSubmission`。
- 同步生成返回 `deferred=false` 后，只在本次 `operationId` 精确匹配时释放本地锁。
- 同一节点后来发起的新任务不会被旧请求误清。
- 释放本地锁时同步移除 `generationBusyIds`，底部计数立即归零。

提交：`1134a86`
前端版本：`2026.11.14.03`

## 验证

- `generationPresentation.test.ts` 与 `changelog.test.ts`：30 项通过。
- 完整前端测试：82 个测试文件、755 项通过。
- `typecheck:canvas`：通过。
- 干净提交 worktree 构建：通过。
- 线上入口：`/assets/index-kRh4Nbjf.js`。
- `https://yj.pduoduo.vip`：HTTP 200。
- 登录与项目列表接口：HTTP 200。
- `drama-canvas-yj.service`：active。
- `drama-canvas-agent.service`：active。

## 发布边界

工作区中另有未提交的批量生成弹窗改动。本次从提交 `1134a86` 的独立干净 worktree
构建，只发布“同步生成完成后释放本地锁”的修复，没有把未确认改动带入线上。

回滚目录：

`/www/wwwroot/yj.pduoduo.vip/app/backups/frontend-sync-generation-lock-1134a86-20261114-135109`

本次发布未触发图片、音频或视频付费生成。
