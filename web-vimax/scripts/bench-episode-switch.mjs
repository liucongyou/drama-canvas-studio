import puppeteer from 'puppeteer-core';

/**
 * Canvas episode-switch performance gate.
 *
 * Usage:
 *   CANVAS_TOKEN=ses_... PROJECT_ID=... \
 *   CANVAS_BASE=https://yj.pduoduo.vip EPISODE_ID=EP002 \
 *   node scripts/bench-episode-switch.mjs
 *
 * The script is read-only and never starts a generation.
 */
const base = (process.env.CANVAS_BASE || 'http://127.0.0.1:8890').replace(/\/$/, '');
const token = process.env.CANVAS_TOKEN || '';
const projectId = process.env.PROJECT_ID || '';
const episodes = String(
  process.env.SWITCH_EPISODES || 'EP001,EP003,EP002,EP001,EP003,EP002',
).split(',').map((item) => item.trim()).filter(Boolean);
const burst = sequence(process.env.BURST_EPISODES || 'EP001,EP003,EP002,EP001');

if (!token || !projectId) {
  throw new Error('CANVAS_TOKEN and PROJECT_ID are required');
}

function sequence(value) {
  return String(value || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});
const page = await browser.newPage();
const errors = [];
const responses = [];
page.on('pageerror', (error) => errors.push(error.message));
page.on('response', (response) => {
  const url = response.url();
  if (url.includes('/workspace-') || url.includes('/generation-tasks')) {
    responses.push({url, status: response.status()});
  }
});

const cookieHost = new URL(base).hostname;

try {
  await page.setViewport({width: 1920, height: 1080});
  await page.setCookie({
    name: process.env.CANVAS_COOKIE || 'canvas_session',
    value: token,
    domain: cookieHost,
    path: '/',
    httpOnly: true,
    sameSite: 'Lax',
  });

  const startedAt = Date.now();
  await page.goto(
    `${base}/?ep=${encodeURIComponent(episodes[0] || 'EP001')}#${encodeURIComponent(projectId)}`,
    {waitUntil: 'domcontentloaded', timeout: 60_000},
  );
  await page.waitForFunction(
    () => document.querySelectorAll('.react-flow__node').length > 0,
    {timeout: 60_000},
  );
  const firstNodeMs = Date.now() - startedAt;
  await new Promise((resolve) => setTimeout(resolve, 500));

  async function switchTo(episode) {
    await page.evaluate(() => {
      document.querySelector('.canvas-ep-list-rail')?.click();
    });
    await page.waitForSelector(
      `.canvas-ep-list.is-open .canvas-ep-row[data-episode-id="${episode}"]`,
      {timeout: 10_000},
    );
    const start = Date.now();
    await page.evaluate((target) => {
      const row = document.querySelector(
        `.canvas-ep-list.is-open .canvas-ep-row[data-episode-id="${target}"]`,
      );
      row?.querySelector('.canvas-ep-select')?.click();
    }, episode);
    await new Promise((resolve) => setTimeout(resolve, 25));
    const transient = await page.evaluate(() => ({
      skeleton: Boolean(document.querySelector('.canvas-skeleton')),
      nodes: document.querySelectorAll('.react-flow__node').length,
    }));
    await page.waitForFunction(
      (target) => (
        [...document.querySelectorAll('.canvas-ep-row.is-active')]
          .some((row) => row.dataset.episodeId === target)
        && !document.querySelector('.canvas-skeleton')
      ),
      {timeout: 60_000},
      episode,
    );
    return {
      episode,
      elapsedMs: Date.now() - start,
      transient,
      rows: await page.evaluate(
        () => document.querySelectorAll('.canvas-ep-row').length,
      ),
      nodes: await page.evaluate(
        () => document.querySelectorAll('.react-flow__node').length,
      ),
    };
  }

  const switches = [];
  for (const episode of episodes) switches.push(await switchTo(episode));
  const timings = switches.map((item) => item.elapsedMs).sort((a, b) => a - b);
  const percentile = (p) => (
    timings[Math.min(timings.length - 1, Math.floor(timings.length * p))] || 0
  );

  await page.evaluate(() => {
    document.querySelector('.canvas-ep-list-rail')?.click();
  });
  await page.waitForSelector('.canvas-ep-list.is-open .canvas-ep-row', {
    timeout: 10_000,
  });
  const burstStartedAt = Date.now();
  await page.evaluate((targets) => {
    targets.forEach((target) => {
      const row = document.querySelector(
        `.canvas-ep-list.is-open .canvas-ep-row[data-episode-id="${target}"]`,
      );
      row?.querySelector('.canvas-ep-select')?.click();
    });
  }, burst);
  const burstTarget = burst.at(-1);
  await page.waitForFunction(
    (target) => (
      [...document.querySelectorAll('.canvas-ep-row.is-active')]
        .some((row) => row.dataset.episodeId === target)
      && !document.querySelector('.canvas-skeleton')
    ),
    {timeout: 60_000},
    burstTarget,
  );
  const burstMs = Date.now() - burstStartedAt;

  const frameSample = await page.evaluate(() => new Promise((resolve) => {
    const gaps = [];
    let last = performance.now();
    const start = last;
    const tick = (now) => {
      gaps.push(now - last);
      last = now;
      if (now - start < 1_000) {
        requestAnimationFrame(tick);
        return;
      }
      const usable = gaps.slice(1);
      const sorted = [...usable].sort((a, b) => a - b);
      resolve({
        frames: usable.length,
        avgGap: Number(
          (usable.reduce((sum, gap) => sum + gap, 0) / usable.length)
            .toFixed(2),
        ),
        p95Gap: Number(
          (sorted[Math.floor(sorted.length * 0.95)] || 0).toFixed(2),
        ),
        janky: usable.filter((gap) => gap > 32).length,
      });
    };
    requestAnimationFrame(tick);
  }));

  const finalState = await page.evaluate(() => ({
    active: [...document.querySelectorAll('.canvas-ep-row.is-active')]
      .map((row) => row.dataset.episodeId),
    rows: document.querySelectorAll('.canvas-ep-row').length,
    nodes: document.querySelectorAll('.react-flow__node').length,
  }));
  const result = {
    firstNodeMs,
    switches,
    p50Ms: percentile(0.5),
    p95Ms: percentile(0.95),
    maxMs: timings.at(-1) || 0,
    skeletonFrames: switches.filter((item) => item.transient.skeleton).length,
    burstMs,
    burstTarget,
    finalState,
    frameSample,
    workspaceIndexRequests: responses.filter(
      (item) => item.url.includes('/workspace-index'),
    ).length,
    errors,
  };
  console.log(JSON.stringify(result, null, 2));
  if (result.skeletonFrames > 0) throw new Error('episode switch showed skeleton');
  if (result.finalState.rows < 2) throw new Error('episode list lost sibling rows');
  if (result.workspaceIndexRequests > 1) {
    throw new Error('episode switches re-read workspace-index');
  }
  if (errors.length > 0) throw new Error(`browser errors: ${errors.join(' | ')}`);
} finally {
  await browser.close();
}
