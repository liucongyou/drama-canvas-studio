# 并发生成时完成节点被重新锁成“生成中”修复记录

日期：2026-11-14 17:57 +08:00

## 现象

段2与段3并发提交视频生成。段2完成并已有本地 MP4 后，卡片中央显示“已完成”，
底部却重新显示“生成中”，播放按钮不可用；段3仍在正常生成。

## 根因

- 后端任务和节点没有串号：
  - 段2：`node-a135hx820mtw-058a2d4f`，任务成功，结果 ready。
  - 段3：`node-7oeeodf7g05v-dc8213b7`，任务成功，结果 ready。
- 前端关闭任务中心时不再更新 `generationTasks` 快照。
- 节点完成事件触发局部刷新时，旧快照里两条任务仍都是 running。
- `preserveActiveGenerationStatus` 使用旧 `generationBusyIds`，把已经 done 的段2重新投影成 `generating`。
- 卡片媒体本身可播放，但 busy 投影锁住了播放控件，造成“已完成但不给播放”。

## 修复

- 新增统一的 `applyGenerationTaskSnapshot`：
  - 从当前任务台账重新计算 `activeNodeIds`；
  - 同步更新 `generationTasks`、`generationBusyIdsRef` 和组件 state；
  - 用同一份 busy 集合投影节点卡片。
- 工作区同步、任务中心轮询、页面初始化、完成事件局部刷新、提交后的任务刷新全部使用同一快照入口。
- 完成事件发生时，只要仍有任务在运行，也会重新读取当前集任务台账，不再依赖关闭状态的任务中心。
- 已完成节点只在自身仍有 active 任务时保持 busy；兄弟节点运行不会锁住它。

提交：`6d54ddd`
前端版本：`2026.11.14.04`

## 验证

- 完整前端测试：82 个测试文件、756 项通过。
- Canvas TypeScript 检查：通过。
- 干净提交 worktree 构建：通过。
- 线上入口：`/assets/index-BjDVe_fe.js`。
- 段2本地视频：HTTP 206，`video/mp4`。
- 段3本地视频：HTTP 206，`video/mp4`。
- 两条最新任务均为 `succeeded / ready`，没有残留 active 任务。
- `drama-canvas-yj.service`：active。
- `drama-canvas-agent.service`：active。

## 发布边界

工作区仍有未提交的批量弹窗改动。本次从提交 `6d54ddd` 的独立干净 worktree 构建，
只发布并发生成任务快照修复，没有把未确认改动带入线上。

回滚目录：

`/www/wwwroot/yj.pduoduo.vip/app/backups/frontend-concurrent-generation-6d54ddd-20261114-175706`

本次发布未触发图片、音频或视频付费生成。
