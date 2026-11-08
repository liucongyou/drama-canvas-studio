import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';
import {
  CANVAS_DOT_MAX_PITCH,
  CANVAS_DOT_MIN_PITCH,
  CANVAS_DOT_PITCH,
  canvasBackgroundCss,
  canvasBackgroundMetrics,
  isCanvasBackgroundMode,
} from './canvasBackground';

const source = readFileSync(
  resolve(__dirname, 'CanvasView.tsx'),
  'utf8',
);

describe('canvas background switch', () => {
  it('drives the dot layer from CSS vars, not from React Flow Background', () => {
    // 第 29 轮最终口径：底纹钉在屏幕上 —— 随缩放变密度、平移时不动（即梦的行为）。
    // 跟着 pan 走的 SVG pattern 是**错的**（用户实测「即梦拖动背景点阵是不动的」）。
    expect(source).toMatch(
      /<div\s+className="canvas-background-pattern"\s+data-bg=\{bgMode\}\s+aria-hidden="true"\s+\/>/s,
    );
    expect(source).not.toMatch(/<Background\b/);
    expect(source).toMatch(/'--canvas-pitch': bgVars\.pitch/);
    expect(source).toMatch(/'--canvas-dot': bgVars\.dot/);
  });

  it('paints the pattern with zoom-driven sizes fixed to the screen', () => {
    const css = readFileSync(resolve(__dirname, 'styles.css'), 'utf8');
    expect(css).toMatch(
      /\.canvas-background-pattern\s*\{[^}]*position:\s*absolute;[^}]*inset:\s*0;[^}]*pointer-events:\s*none;/s,
    );
    // 尺寸吃变量（随缩放）；不许写死 24px（那就是"不缩放"）。
    expect(css).toMatch(
      /\.canvas-background-pattern\[data-bg='dots'\]\s*\{[^}]*background-size:\s*var\(--canvas-pitch,[^)]*\)\s*var\(--canvas-pitch,[^)]*\)/s,
    );
    expect(css).not.toMatch(/\.canvas-background-pattern\[data-bg='dots'\]\s*\{[^}]*background-size:\s*24px/s);
    // 位置必须钉死：跟着 pan 写 background-position 会让点阵在拖动时游动。
    expect(css).toMatch(
      /\.canvas-background-pattern\[data-bg='dots'\]\s*\{[^}]*background-position:\s*0 0/s,
    );
    expect(css).toMatch(
      /\.canvas-stage \.canvas-flow,\s*\.canvas-stage \.react-flow\s*\{[^}]*background-color:\s*transparent\s*!important;/s,
    );
    const refinements = readFileSync(
      resolve(__dirname, 'ui-refinements.css'),
      'utf8',
    );
    expect(refinements).not.toMatch(
      /\.canvas-stage\[data-bg=['"]dots['"]\][^{]*\{[^}]*background-size/s,
    );
  });

  it('uses the database-provided mode as the first render value', () => {
    expect(source).toMatch(
      /isCanvasBackgroundMode\(initialBackgroundMode\)/,
    );
    expect(source).toMatch(/onBackgroundModeChange\?\.\(mode\)/);
    expect(source).not.toMatch(/vimax\.canvas\.background/);
  });

  it('derives every zoom-dependent canvas style from the single viewport state', () => {
    expect(source).toMatch(
      /const \[canvasViewport, setCanvasViewport\] = useState<CanvasViewport>/,
    );
    expect(source).toMatch(
      /const zoomPct = Math\.round\(canvasViewport\.zoom \* 100\);/,
    );
    expect(source).not.toMatch(/const \[zoomPct, setZoomPct\]/);
    expect(source).not.toMatch(/setZoomPct\s*\(/);
    expect(source).toMatch(/'--canvas-pitch': bgVars\.pitch/);
    expect(source).toMatch(/'--port-shrink':/);
    expect(source).toMatch(/'--card-control-visible':/);
    expect(source).toMatch(/\.\.\.edgeFlowVars\(zoomPct \/ 100\)/);
    expect(source).toMatch(/zoomPct < 35/);
    expect(source).toMatch(/zoomPct < 65/);
  });
});

describe('canvasBackgroundMetrics（间距/点径的世界坐标换算）', () => {
  it('100% 就是 24px 间距 / 2px 点径', () => {
    expect(canvasBackgroundMetrics(1)).toEqual({gap: CANVAS_DOT_PITCH, size: 2});
  });

  it('全览时屏幕间距被钳到下限，不会糊成灰膜/摩尔纹', () => {
    const {gap, size} = canvasBackgroundMetrics(0.19);
    // 24 × 0.19 = 4.56px 太密 → 钳到下限时屏幕间距 = 8px
    expect(gap * 0.19).toBeCloseTo(CANVAS_DOT_MIN_PITCH, 6);
    expect(gap).toBeGreaterThan(0);
    // 点径屏幕上仍 ~2px（极小缩放时被上限钳了一点点，是 1.9 不是 2，属预期）
    expect(size * 0.19).toBeGreaterThan(1.5);
  });

  it('放大到 800% 时屏幕间距被钳到上限，一屏不会只剩两三个点', () => {
    const {gap, size} = canvasBackgroundMetrics(8);
    expect(gap * 8).toBeCloseTo(CANVAS_DOT_MAX_PITCH, 6);
    expect(size * 8).toBeCloseTo(2, 1);
  });

  it('正常编辑档（57%）保持原始间距', () => {
    const {gap} = canvasBackgroundMetrics(0.57);
    expect(gap).toBe(24);
    expect(gap * 0.57).toBeCloseTo(13.68, 2);
  });

  it('非法缩放下不产生 0/NaN（否则 pattern 宽高为 0，底纹整个消失）', () => {
    for (const z of [0, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
      const {gap, size} = canvasBackgroundMetrics(z);
      expect(Number.isFinite(gap)).toBe(true);
      expect(Number.isFinite(size)).toBe(true);
      expect(gap).toBeGreaterThan(0);
      expect(size).toBeGreaterThan(0);
    }
  });
});

describe('canvasBackgroundCss（屏幕坐标尺寸）', () => {
  it('100% 是 24px 间距 / 1px 半径', () => {
    expect(canvasBackgroundCss(1)).toEqual({pitch: '24px', dot: '1px'});
  });

  it('缩放时间距跟着放大（这才是「可缩放」）', () => {
    const at200 = canvasBackgroundCss(2);
    expect(at200.pitch).toBe('48px');
    // 点径屏幕恒定 ~2px → 半径 1px
    expect(at200.dot).toBe('1px');
  });

  it('全览时间距落在可用区间，不会密成灰膜', () => {
    const low = canvasBackgroundCss(0.19);
    const pitch = Number.parseFloat(low.pitch);
    expect(pitch).toBeGreaterThanOrEqual(CANVAS_DOT_MIN_PITCH);
    expect(pitch).toBeLessThanOrEqual(CANVAS_DOT_MAX_PITCH);
    expect(Number.parseFloat(low.dot)).toBeGreaterThan(0.8);
  });

  // ★★ 第 30 轮补丁的核心：用户实测「缩放到 36% 反而点间距大了、到 42% 反而小了」。
  // 原因是旧实现用「世界间距翻倍 / 减半」的离散档位来满足屏幕下限，
  // 跨过临界点时屏幕间距会从 ~2×下限 掉回 1×下限 —— 放大反而变密。
  it('屏幕间距随缩放单调不减（修的就是 36% → 42% 反向变小）', () => {
    let prev = 0;
    for (let z = 0.12; z <= 8.0001; z += 0.01) {
      const p = Number.parseFloat(canvasBackgroundCss(z).pitch);
      expect(p).toBeGreaterThanOrEqual(prev - 1e-9);
      prev = p;
    }
  });

  it('36% 的间距必须 ≤ 42% 的间距（用户反馈的那个 case）', () => {
    const at36 = Number.parseFloat(canvasBackgroundCss(0.36).pitch);
    const at42 = Number.parseFloat(canvasBackgroundCss(0.42).pitch);
    expect(at36).toBeLessThanOrEqual(at42);
  });

  it('相邻缩放之间没有档位跳变（旧的翻倍/减半一次差 2 倍）', () => {
    for (let z = 0.12; z <= 8; z += 0.02) {
      const a = Number.parseFloat(canvasBackgroundCss(z).pitch);
      const b = Number.parseFloat(canvasBackgroundCss(z + 0.02).pitch);
      expect(b / a).toBeLessThan(1.2);
    }
  });

  it('非法缩放不产出 NaN（写进 style 会让整条规则失效）', () => {
    for (const z of [0, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
      const {pitch, dot} = canvasBackgroundCss(z);
      expect(Number.isFinite(Number.parseFloat(pitch))).toBe(true);
      expect(Number.isFinite(Number.parseFloat(dot))).toBe(true);
    }
  });
});

describe('canvas background preference', () => {
  it('accepts only the three supported values', () => {
    expect(isCanvasBackgroundMode('dots')).toBe(true);
    expect(isCanvasBackgroundMode('lines')).toBe(true);
    expect(isCanvasBackgroundMode('blank')).toBe(true);
    expect(isCanvasBackgroundMode('grid')).toBe(false);
    expect(isCanvasBackgroundMode(null)).toBe(false);
  });
});
