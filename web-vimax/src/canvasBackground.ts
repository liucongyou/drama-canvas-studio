export type CanvasBackgroundMode = 'dots' | 'lines' | 'blank';

export function isCanvasBackgroundMode(
  value: unknown,
): value is CanvasBackgroundMode {
  return value === 'dots' || value === 'lines' || value === 'blank';
}

/** 点阵的世界坐标间距（画布 px）。100% 缩放下屏幕上就是 24px。 */
export const CANVAS_DOT_PITCH = 24;
/** 点阵圆点的世界坐标直径（画布 px）。 */
export const CANVAS_DOT_SIZE = 2;
/**
 * 点阵间距在**屏幕上**的下限（px）。再密就糊成一片灰膜、还容易出摩尔纹。
 * 取 8：刚好让 33% ~ 267% 这段常用缩放落在"完全等比"的区间里。
 */
export const CANVAS_DOT_MIN_PITCH = 8;
/** 点阵间距在**屏幕上**的上限（px）。再稀一屏就只剩两三个点，失去参考意义。 */
export const CANVAS_DOT_MAX_PITCH = 64;

/**
 * 底纹随缩放变化（用户诉求：「即梦的点阵背景是可以缩放的」）。
 *
 * 旧实现是画在 `.canvas-background-pattern` 上的 CSS 背景图：
 * `background-size: 24px` 是**屏幕像素**，缩放时点阵纹丝不动、平移时也不跟着走 ——
 * 画布在动、底纹是贴死的壁纸，放大到 400% 还是一屏几十个点，空间感全靠脑补。
 *
 * ★★ 曾经在这里踩过一个坑：为了把屏幕间距压在合理区间，用的是
 *   「世界间距翻倍 / 减半」的**离散档位**。档位一跳，视觉密度就突变，而且方向是反的：
 *     36% → 24×0.36 = 8.6px 太密 → 翻倍成 48 → 屏幕 17.3px
 *     42% → 24×0.42 = 10.1px 达标 → 不翻倍 → 屏幕 10.1px
 *   于是「放大反而变密」（用户实测反馈：36% 间距大、42% 反而小）。
 *   根因是**离散档位破坏了单调性**：约束是"屏幕间距 ≥ 下限"，但用"世界间距翻倍"来满足它，
 *   跨过临界点时屏幕间距会从 ~2×下限 直接掉回 1×下限。
 *
 * 现在改成**连续钳制**：先算屏幕间距，钳到 [下限, 上限]，再换回世界坐标。
 * 这样屏幕间距 = clamp(24 × zoom, 8, 64)，对 zoom 单调不减，且不会再突变。
 *
 * ★ 点径按 1/zoom 反向补偿，屏幕上恒为 ~2px：
 *   否则 19% 时圆点直径 0.4px，抗锯齿后直接消失。
 */
export function canvasBackgroundMetrics(zoom: number): {gap: number; size: number} {
  const z = Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
  // 屏幕间距（px）：等比缩放 + 两端钳制。单调、连续、无档位跳变。
  const pitchScreen = Math.min(
    CANVAS_DOT_MAX_PITCH,
    Math.max(CANVAS_DOT_MIN_PITCH, CANVAS_DOT_PITCH * z),
  );
  const gap = pitchScreen / z;
  const size = Math.max(0.2, Math.min(10, CANVAS_DOT_SIZE / z));
  return {gap, size};
}

/**
 * 底纹的 CSS 变量（**屏幕像素**）。
 *
 * 即梦的底纹是「钉在屏幕上」的：拖动画布时点阵**不动**，只有缩放时点和间距才变。
 * （用户原话：「即梦拖动背景点阵是不动的」。）所以这里给的是屏幕单位，
 * 由 `.canvas-background-pattern` 用 `background-size` 铺 —— 平移期间一个样式都不用写，
 * 连重绘都没有；换成 SVG pattern（跟着 pan 走）反而是错的。
 *
 * 换算：世界间距 × zoom = 屏幕间距；点径同样 × zoom（canvasBackgroundMetrics 已按 1/zoom
 * 反向补偿过，所以屏幕上恒为 ~2px，不会在 19% 时缩成一个点、也不会在 800% 时变成大圆斑）。
 */
export function canvasBackgroundCss(zoom: number): {pitch: string; dot: string} {
  const z = Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
  const {gap, size} = canvasBackgroundMetrics(z);
  const round = (v: number) => Math.round(v * 100) / 100;
  return {
    pitch: `${round(gap * z)}px`,
    dot: `${round((size * z) / 2)}px`,
  };
}
