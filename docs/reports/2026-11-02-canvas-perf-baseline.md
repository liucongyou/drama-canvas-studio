# 画布性能基线（2026-11-02）

> 目的：给后续任何「性能优化」一个可比数字。此前 `tests/perf/` 一直是空的，
> 历次重构都无法证明自己变快还是变慢。

## 怎么跑

```bash
# 先用 tenant_registry.create_session 造一个临时会话（user_agent='smoke-audit'，用完删除）
CANVAS_TOKEN=ses_xxx PROJECT_ID=<项目id> node web-vimax/scripts/bench-canvas.mjs
# 可选：CANVAS_BASE=http://127.0.0.1:8890（默认 8891 dev）
```

脚本只读：不发写请求，**绝不触发生成**（不花钱）。

## 本次数据

项目 `gk6woLnGjh998UeNwvhqYG`，23 个媒体节点 / 51 条连线 / 2,034 个 DOM 节点。
环境：headless Chrome + `--disable-gpu`，1680×1050，本地 `8890`（生产产物）。

| 指标 | 数值 | 判读 |
|---|---|---|
| 首屏到第一个节点出现 | **1,352 ms** | 含页面加载 + 两次接口往返，正常 |
| 静止帧间隔 avg / p95 | **16.67 / 16.7 ms** | 满帧，0 掉帧 |
| 缩放帧间隔 avg / p95 | 31.25 / 66.7 ms | 见下方对照结论 |
| JS 堆占用 | 18.7 MB | 健康 |
| 单次测量期间 Layout 次数 | 36 | 没有布局抖动迹象 |

## 缩放掉帧不是代码问题（对照实验）

同一环境、同一 headless 配置，把一个**不含 React、只有 23 张带阴影卡片**的
`transform: scale()` 动画作为对照：

| | avg | p95 | 掉帧 |
|---|---|---|---|
| 纯 CSS 对照 | 62.5 ms | 133.3 ms | 14 / 16 |
| 本画布 | 31.25 ms | 66.7 ms | 17 / 32 |

**纯 CSS 比画布还慢一倍**，说明掉帧来自 headless 没有 GPU、走软件光栅化，
而不是 React 状态更新或 memo 失效。`onMove` 里只有 `setZoomPct` 一处 setState，
且大画布分支（>400 节点）另有 90ms 防抖，不构成瓶颈。

结论：**不要为了这个数字去改端口/连线的缩放曲线**（`--edge-ui-scale` /
`--port-shrink`）。那套曲线是用户逐轮反馈调出来的，改错会直接被看见。

## 已知的、值得后续观察的点

- 首屏 1,352ms 里主要是两次接口往返（`workspace-index` → `nodes`+`tasks` 并行）。
  `getCanvas` / `getCanvasMediaNodes` 在传入 `pseudoProject` 后是纯本地转换，不再发请求。
  要再快只能动 `workspace-index` 与 `nodes` 的合并，属于后端契约变更，收益约 1 个 RTT（本地 <50ms）。
- 400 节点以上会切 `largeCanvasOverview`（概览模式）+ 可见区域裁剪。基线项目只有 23 个节点，
  **尚未测过大画布**。有 400+ 节点的项目时应重跑本脚本，把数字补进本文件。
