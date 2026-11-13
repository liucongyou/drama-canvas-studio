# 供应商版权拦截错误中文化

日期：2026-11-13

## 问题

火山方舟视频任务失败时返回英文错误，例如：

`OutputVideoSensitiveContentDetected.PolicyViolation: The request failed because the output video may be related to copyright restrictions. Request id: ...`

前端直接展示原始错误，用户无法快速判断是提示词、参考图还是平台版权策略问题。

## 改动

- 后端 `provider_client.py` 增加供应商错误中文化：识别 `OutputVideoSensitiveContentDetected`、`PolicyViolation` 和 `copyright restrictions`。
- 中文化后的错误明确区分失败类型、可能原因、处理建议和供应商请求编号。
- `check_video_task()` 和 `ProviderTaskFailed` 都经过同一层错误规范化，避免任务提交失败与恢复查询失败显示两套文案。
- 前端 `friendlyMediaError()` 增加历史数据兼容映射，已落库的英文错误刷新后也会显示中文。
- 前端发布版本更新为 `2026.11.13.03`，并写入应用更新记录。

## 验证

- 后端 `test_provider_stack`：87 项通过。
- 前端 `src/api.test.ts`：75 项通过。
- 前端 Canvas 类型检查：通过。
- 前端生产构建：通过。

## 发布

- 后端：更新 `provider_client.py` 并重启 `drama-canvas-yj.service`。
- 前端：更新 `web-vimax/dist` 静态资源，保留上一版用于回滚。
- 本次发布不触发图片、音频或视频供应商生成，不产生生成费用。
