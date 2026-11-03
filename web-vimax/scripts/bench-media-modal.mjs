import puppeteer from 'puppeteer-core';

/**
 * Media-node modal interaction gate (read-only; no generation).
 *
 * CANVAS_TOKEN=... PROJECT_ID=... CANVAS_BASE=http://127.0.0.1:8891 \
 * EPISODE_ID=EP003 node scripts/bench-media-modal.mjs
 */
const base = (process.env.CANVAS_BASE || 'http://127.0.0.1:8891').replace(/\/$/, '');
const token = process.env.CANVAS_TOKEN || '';
const projectId = process.env.PROJECT_ID || '';
const episodeId = process.env.EPISODE_ID || 'EP003';
const samples = Math.max(3, Number(process.env.MODAL_SAMPLES || 5));

if (!token || !projectId) {
  throw new Error('CANVAS_TOKEN and PROJECT_ID are required');
}

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.setViewport({width: 1920, height: 1080});
  await page.setCookie({
    name: process.env.CANVAS_COOKIE || 'canvas_session',
    value: token,
    domain: new URL(base).hostname,
    path: '/',
    httpOnly: true,
    sameSite: 'Lax',
  });
  await page.goto(
    `${base}/?ep=${encodeURIComponent(episodeId)}#${encodeURIComponent(projectId)}`,
    {waitUntil: 'networkidle2', timeout: 60_000},
  );
  await page.waitForFunction(
    () => document.querySelectorAll('.react-flow__node-media .canvas-media-card').length >= 2,
    {timeout: 60_000},
  );
  await new Promise((resolve) => setTimeout(resolve, 500));

  const cardIds = await page.$$eval(
    process.env.CARD_SELECTOR || '.react-flow__node-media .canvas-media-card',
    (elements) => elements.map((element) => (
      element.closest('.react-flow__node')?.getAttribute('data-id') || ''
    )).filter(Boolean),
  );
  if (cardIds.length < 2) throw new Error('need at least two media cards');
  const boxOf = async (index) => {
    const cardId = cardIds[index % cardIds.length];
    const card = await page.$(`[data-id="${cardId.replace(/["\\]/g, '\\$&')}"] .canvas-media-card`);
    const box = await card?.boundingBox();
    if (!box) throw new Error(`media card ${index} has no bounding box`);
    return box;
  };

  const openOne = async (index) => {
    const box = await boxOf(index);
    const started = performance.now();
    await page.mouse.click(box.x + Math.min(24, box.width / 3), box.y + Math.min(24, box.height / 3));
    await page.waitForSelector('.jim-bar', {timeout: 5_000});
    const domMs = performance.now() - started;
    const timing = await page.$eval('.jim-bar', (element) => {
      const animation = element.getAnimations()
        .find((item) => item.animationName === 'jim-pop-anchored'
          || item.animationName === 'jim-pop-anchored-up'
          || item.animationName === 'jim-pop-center');
      const styles = getComputedStyle(element);
      return {
        animationName: animation?.animationName || '',
        animationPlayState: animation?.playState || '',
        animationCurrentTime: animation?.currentTime ?? null,
        animationDuration: animation?.effect?.getTiming().duration ?? null,
        transformOrigin: styles.transformOrigin,
        dataFlip: element.getAttribute('data-flip'),
        dataCenter: element.getAttribute('data-center'),
      };
    });
    return {
      domMs: Number(domMs.toFixed(1)),
      ...timing,
    };
  };
  const closeOne = async () => {
    const started = performance.now();
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('.jim-bar'), {
      timeout: 5_000,
    });
    return performance.now() - started;
  };

  const runs = [];
  for (let index = 0; index < samples; index += 1) {
    const openMs = await openOne(index);
    const closeMs = await closeOne();
    runs.push({
      card: cardIds[index % cardIds.length],
      openMs: openMs.domMs,
      openAnimation: openMs.animationName,
      openAnimationState: openMs.animationPlayState,
      openAnimationCurrentTime: openMs.animationCurrentTime,
      openAnimationDuration: openMs.animationDuration,
      openTransformOrigin: openMs.transformOrigin,
      openDataFlip: openMs.dataFlip,
      openDataCenter: openMs.dataCenter,
      closeMs: Number(closeMs.toFixed(1)),
    });
  }

  await openOne(0);
  const switchStarted = performance.now();
  const secondBox = await boxOf(1);
  await page.mouse.click(
    secondBox.x + Math.min(24, secondBox.width / 3),
    secondBox.y + Math.min(24, secondBox.height / 3),
  );
  await page.waitForFunction(
    () => Boolean(document.querySelector('.jim-bar')),
    {timeout: 5_000},
  );
  const switchMs = performance.now() - switchStarted;
  await closeOne();

  const frames = await page.evaluate(() => new Promise((resolve) => {
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
          (usable.reduce((sum, gap) => sum + gap, 0) / usable.length).toFixed(2),
        ),
        p95Gap: Number(
          (sorted[Math.floor(sorted.length * 0.95)] || 0).toFixed(2),
        ),
        janky: usable.filter((gap) => gap > 32).length,
      });
    };
    requestAnimationFrame(tick);
  }));

  const sortedOpen = runs.map((item) => item.openMs).sort((a, b) => a - b);
  const sortedClose = runs.map((item) => item.closeMs).sort((a, b) => a - b);
  const percentile = (values, p) => (
    values[Math.min(values.length - 1, Math.floor(values.length * p))] || 0
  );
  const result = {
    runs,
    openP50Ms: percentile(sortedOpen, 0.5),
    openP95Ms: percentile(sortedOpen, 0.95),
    closeP50Ms: percentile(sortedClose, 0.5),
    closeP95Ms: percentile(sortedClose, 0.95),
    switchMs: Number(switchMs.toFixed(1)),
    frames,
    errors,
  };
  console.log(JSON.stringify(result, null, 2));
  if (errors.length) throw new Error(`browser errors: ${errors.join(' | ')}`);
} finally {
  await browser.close();
}
