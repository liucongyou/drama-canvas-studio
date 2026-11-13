# 提示词滚动锚定与删除按钮优化

日期：2026-11-13

## 问题

- 卡片右上角删除节点按钮在悬停时使用整块危险色填充，视觉上像一大块粉色方块，与铅笔、重新生成按钮不协调。
- 长提示词滚动到底部后，每删除一个字符都会触发编辑框高度重测；`calcTaHeight()` 临时切换 `height:auto`，导致 `scrollTop` 被浏览器重排重置，内容自动跳回上方。

## 修复

- 新增 `promptScrollAnchor.ts`：重测高度前保存 `scrollTop` 和是否位于底部；重测后恢复原锚点。
- 位于底部时始终恢复到新的最大 `scrollTop`，底部删除字符不再跳顶。
- 阅读中间内容时恢复原 `scrollTop`，不会因为输入或删除改变阅读位置。
- 删除节点按钮改为紧凑图标按钮：默认与其它操作同色，悬停仅使用浅红底、红色图标和红色描边，不再整块实心粉。
- 为删除按钮补充 `aria-label`，并增加滚动锚点和按钮样式回归测试。

## 验证

- `promptScrollAnchor.test.ts`：3 项通过。
- `promptScrollbarCss.test.ts`：8 项通过。
- `mediaCardInteraction.test.ts`：4 项通过。
- 前端完整测试：80 个测试文件、747 项通过。
- Canvas 类型检查：通过。
- 生产构建：通过，入口为 `index-DBoarY-j.js`。

本次不触发生成任务，不产生供应商费用。

## 线上发布

- 代码提交：`40ea918 fix: stabilize prompt scroll and soften delete action`
- 线上发布号：`40ea918`
- 线上入口：`/assets/index-DBoarY-j.js`
- 线上备份：`/www/wwwroot/yj.pduoduo.vip/app/backups/frontend-prompt-scroll-delete-40ea918-20261113-222827`
- `index.html`、入口 JS、CSS 本地与线上 SHA-256 一致。
- 画布服务与 Agent 服务均为 `active`。
