# 第 28 轮：参考线三处手感打磨

日期：2026-11-02
版本：`2026.11.02.09`（产物 `index-p7JSRDS7.js`）
涉及文件：`web-vimax/src/canvasLayout.ts`、`web-vimax/src/CanvasView.tsx`、`web-vimax/src/styles.css`、`web-vimax/src/canvasLayout.test.ts`、`web-vimax/src/changelog.ts`、`web-vimax/src/changelogEntries.ts`

## 一、需求

用户问「细节方面还能再打磨打磨吗？」，我给了三个候选，用户回「可以」「三个一起做」：

1. 松手后参考线保留 120ms 再消失
2. 对齐时给被对齐卡片描 1px 强调色边
3. 缩放 < 35% 时线宽 1px → 1.5px

三条都做，且都不许动第 26/27 轮已经定下来的东西（虚线只画两个元素之间那一段、
同一条边只画一根、颜色必须纯色、不许做虚线相位对齐）。

## 二、实现

### 1. 松手驻留（`releaseAlignGuides`）

拖拽结束原本直接 `updateAlignGuides([])`，线在松手那一帧就没了 —— 用户来不及确认
「刚才到底吸到了哪几条边」。

新增 `releaseAlignGuides()`，只在 `onNodeDragStop` / `clearStuckDrag` 里调用：

```
松手 → 原样停 ALIGN_GUIDE_HOLD_MS(120) → 加 .is-fading 并摘 .is-on
      → CSS transition opacity 140ms → 再过 60ms 清掉 .is-fading
```

- `updateAlignGuides` 开头**无条件** `clearGuideTimers()`：拖动中任何一帧新数据都作废
  驻留计时器，否则用户又拖起来了，线却按上一轮的计时器在淡出。
- 拖动过程仍然 `transition: none`。只有 `.is-fading` 这一档开过渡 —— 逐帧显隐一旦有
  过渡就会采到半透明中间帧，肉眼是一闪一闪（第 26 轮踩过）。
- 本来就没线时直接走常规清空，不空等 120ms，避免无谓的 DOM 残留。

### 2. 配对描边（`AlignGuide.peerId` + `.is-align-peer`）

虚线只能表达「对上了」，表达不了「跟谁对上了」。给配对那张卡描同色 1px 边。

- `alignGuideSegments` 的每个聚合组记一个 `peerId`：**取最近的那张**。
  同一条边上常常同时压着好几张卡（项目里 x=0 就有 5 张），全描会闪成一片，等于没描。
- `updateAlignGuides` 每帧把 `peerIds` 交给 `syncAlignPeerMarks`，后者维护
  `Map<id, HTMLElement>`：稳定帧里 `querySelector` 次数为 0，只在新增/失效时才查。
- 描边写的是内层卡片的 `border-color` 而不是 `outline`/`box-shadow`：媒体卡与备注卡本来
  就有 `1px solid transparent` + 圆角（10px / 16px），改颜色天然贴合圆角、也不会跳 1px。

**坑（必须记住）**：`theme-tokens.css:1545` 有

```css
.canvas-node.canvas-media-node .canvas-media-card { border-color: transparent !important; }
```

只写普通规则会被它顶回去，卡片一点变化都没有。新规则必须是 `!important` **且**特异性
更高（本条 `.canvas-flow .react-flow__node.is-align-peer .canvas-media-card` 是 (0,4,0)）。

**坑二**：React Flow 每帧重算节点 `className` 并整体写回，会把挂上去的 class 冲掉。
`syncAlignPeerMarks` 对已在表里的元素每帧检查一次 `classList.contains`，掉了就补，
只在真的掉了时才写 DOM。

### 3. 低缩放加粗（`alignGuideStrokePx` / `alignGuideSnapQuantum`）

小地图视角（< 35%）下一根 1px 线会被同屏卡框和连线淹没。阈值取 35%，因为再低一档基本
只在看整体排布，需要更醒目的结构线。

加粗必须连带改像素栅格：1.5px 的线放在整像素栅格上，仍有 0.5px 溢到隔壁列、被背景冲淡
—— 正是第 27 轮修掉的「有深有浅」。所以线宽 1.5px 时 `snapToPixel` 的 quantum 降到 0.5，
DPR=2 屏幕上 1.5 CSS px = 整整 3 个物理像素，颜色回到纯色。

`snapToPixel(world, zoom, pan, quantum = 1)` 只加了第 4 个可选参数，默认行为与老调用完全一致
（老单测一行没改）。阈值以上一个像素都没变。

## 三、验证

| 项 | 结果 |
|---|---|
| `npx vitest run` | **455 passed**（新增 9 项：peerId 3 项、线宽/栅格 4 项、quantum 吸附 2 项） |
| `npx tsc --noEmit` | 0 错误 |
| `npx vite build` | 成功，主包 522.25 kB / gzip 154.13 kB（上一轮 522.15 / 154.13） |
| 隔离实例（8892 + `canvas.db` 副本）| 3 竖 + 1 横虚线；配对卡边框 `rgb(47, 111, 235)`= `--v-accent` 1px；被拖卡边框仍 `rgba(0,0,0,0)`；console **0 报错** |
| 松手 30ms | 3 条线仍在（驻留生效） |
| 松手 150ms | `is-on`=0、`is-fading`=3（正在淡出） |
| 松手 450ms | `is-on`=0、`is-fading`=0、配对卡边框回到 `rgba(0,0,0,0)`（无残留） |
| 缩放 24.1% 拖动 | 3 条线 `getBoundingClientRect` 宽度均为 **1.5px**，配对卡正确 |

时序测量的注意点：`onNodeDragStop` 里要写位置真源（会触发一次 PUT），主线程繁忙时
`setTimeout` 会被推迟，探针在 +475ms 才看到 `is-fading`。机制本身是对的，这里是
**主线程阻塞导致的延迟**，不是 bug —— 表现为「忙的时候线多停一会儿」，无副作用。

## 四、已知取舍

- 配对描边只在**拖动中**出现，松手随线一起收。理由：它不是选中态，不该在静态画面里
  留下第二套「选中」语义。
- 同一列多张卡时线会跨过中间那张（聚合取并集，第 27 轮的决定）。这是「一条边一根线」
  的必然结果，描边用「最近的一张」把注意力收回来。
- 被拖的卡不描边：拖动中它已经是 `selected`，再描一次会和选中态打架。
