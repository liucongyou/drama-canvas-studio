import puppeteer from 'puppeteer-core';

/**
 * 画布性能基线。
 *
 * 目的：任何一次「性能优化」都要有可比数字，否则改完只能说"感觉快了"。
 * 之前升级计划里规划的 tests/perf 一直不存在，导致历次重构都无法证明收益。
 *
 * 采三组数据：
 *   ① 首屏：navigateStart → 第一个画布节点出现在 DOM 的耗时；
 *   ② 渲染规模：节点/连线数量、DOM 节点总数、JS 堆占用；
 *   ③ 交互帧：静止 1s 与连续缩放 1s 的平均帧间隔、掉帧数（>32ms 记一次）。
 *
 * 只读：不发任何写请求，绝不触发生成。
 *
 * 用法：
 *   CANVAS_TOKEN=ses_xxx PROJECT_ID=xxx node scripts/bench-canvas.mjs
 *   可选 CANVAS_BASE（默认 8891）、BENCH_JSON（把结果写到文件）
 */
const base = process.env.CANVAS_BASE || 'http://127.0.0.1:8891';
const token = process.env.CANVAS_TOKEN || '';
const projectId = process.env.PROJECT_ID || '';
const outPath = process.env.BENCH_JSON || '';

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--window-size=1680,1050'],
});

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const requestTiming = [];
const requestStartedAt = new Map();

/** 在页面里采样若干帧的间隔。 */
async function sampleFrames(page, ms) {
  return page.evaluate((duration) => new Promise((resolve) => {
    const gaps = [];
    let last = performance.now();
    const start = last;
    const tick = (now) => {
      gaps.push(now - last);
      last = now;
      if (now - start < duration) requestAnimationFrame(tick);
      else {
        const usable = gaps.slice(1);
        const total = usable.reduce((sum, gap) => sum + gap, 0) || 1;
        const sorted = [...usable].sort((a, b) => a - b);
        resolve({
          frames: usable.length,
          avgGap: Number((total / usable.length).toFixed(2)),
          p95Gap: Number((sorted[Math.floor(sorted.length * 0.95)] || 0).toFixed(2)),
          janky: usable.filter((gap) => gap > 32).length,
        });
      }
    };
    requestAnimationFrame(tick);
  }), ms);
}

try {
  const page = await browser.newPage();
  page.on('request', (request) => {
    requestStartedAt.set(request, Date.now());
  });
  page.on('requestfinished', (request) => {
    const response = request.response();
    const startedAt = requestStartedAt.get(request);
    requestTiming.push({
      url: request.url(),
      status: response?.status() || 0,
      responseMs: startedAt ? Date.now() - startedAt : null,
      resource: request.resourceType(),
      encodedBytes: Number(
        response?.headers?.()['content-length'] || 0,
      ),
    });
  });
  await page.setViewport({width: 1680, height: 1050, deviceScaleFactor: 1});
  if (token) {
    await page.setCookie({
      name: process.env.CANVAS_COOKIE || 'canvas_session',
      value: token,
      domain: '127.0.0.1',
      path: '/',
      httpOnly: true,
    });
  }

  const url = projectId ? `${base}/#${projectId}` : base;
  const startedAt = Date.now();
  await page.goto(url, {waitUntil: 'domcontentloaded', timeout: 60_000});

  // 第一个节点出现 = 用户真正"看见画布"的时刻
  await page.waitForFunction(() => !!document.querySelector('.react-flow__node'), {timeout: 60_000});
  const firstNodeMs = Date.now() - startedAt;

  // 等布局稳定（不等待无限渲染）
  await sleep(2500);

  const scale = await page.evaluate(() => ({
    nodes: document.querySelectorAll('.react-flow__node').length,
    mediaNodes: document.querySelectorAll('.react-flow__node-media').length,
    edges: document.querySelectorAll('.react-flow__edge').length,
    domNodes: document.querySelectorAll('*').length,
  }));

  const metricsBefore = await page.metrics();
  const idle = await sampleFrames(page, 1000);

  // 连续缩放（模拟真实操作中最吃渲染的交互）
  const box = await page.evaluate(() => {
    const el = document.querySelector('.react-flow') || document.body;
    const rect = el.getBoundingClientRect();
    return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
  });
  await page.mouse.move(box.x, box.y);
  const zoomSampling = sampleFrames(page, 1000);
  for (let i = 0; i < 20; i += 1) {
    await page.mouse.wheel({deltaY: i % 2 === 0 ? -60 : 60});
    await sleep(40);
  }
  const zoom = await zoomSampling;
  const expectedZoomLabel = await page.evaluate(() => {
    const value = document.querySelector('.react-flow__viewport')?.style.transform || '';
    const match = /scale\(([^)]+)\)/.exec(value);
    const zoomValue = Number(match?.[1] || 1);
    return zoomValue > 0 ? `${Math.round(zoomValue * 100)}%` : '';
  });
  const zoomLabel = await page.evaluate(
    () => document.querySelector('.canvas-toolbar-zoom span')?.textContent || '',
  );
  const zoomCssVars = await page.evaluate(() => {
    const flow = document.querySelector('.react-flow');
    const pattern = document.querySelector('.canvas-background-pattern');
    return {
      pitch: pattern
        ? getComputedStyle(pattern).getPropertyValue('--canvas-pitch').trim()
        : '',
      portShrink: flow
        ? getComputedStyle(flow).getPropertyValue('--port-shrink').trim()
        : '',
      edgeFlowWidth: flow
        ? getComputedStyle(flow).getPropertyValue('--edge-flow-width').trim()
        : '',
    };
  });
  const metricsAfter = await page.metrics();

  const result = {
    url,
    at: new Date().toISOString(),
    firstNodeMs,
    scale,
    idle,
    zoom,
    zoomLabel,
    expectedZoomLabel: expectedZoomLabel || zoomLabel,
    zoomCssVars,
    heapMB: Number((metricsAfter.JSHeapUsedSize / 1048576).toFixed(1)),
    heapDeltaMB: Number(
      ((metricsAfter.JSHeapUsedSize - metricsBefore.JSHeapUsedSize) / 1048576).toFixed(1),
    ),
    layoutCountDelta: metricsAfter.LayoutCount - metricsBefore.LayoutCount,
    slowRequests: requestTiming
      .filter((item) => (item.responseMs || 0) > 300 || item.status >= 400)
      .sort((left, right) => (right.responseMs || 0) - (left.responseMs || 0))
      .slice(0, 12),
    transferBytes: requestTiming.reduce(
      (sum, item) => sum + Number(item.encodedBytes || 0),
      0,
    ),
  };

  console.log(JSON.stringify(result, null, 2));
  if (outPath) {
    const {writeFileSync} = await import('node:fs');
    writeFileSync(outPath, `${JSON.stringify(result, null, 2)}\n`);
  }
  if (!zoomCssVars.pitch || !zoomCssVars.portShrink || !zoomCssVars.edgeFlowWidth) {
    throw new Error(`zoom CSS vars missing: ${JSON.stringify(zoomCssVars)}`);
  }
} finally {
  await browser.close();
}
