import puppeteer from 'puppeteer-core';

/**
 * Rapid same-card open/close stress gate.
 *
 * Sequence: click card -> click blank -> click the same card again while the
 * exit animation is still running. The reopened panel must survive every stale
 * close callback. This is read-only and never clicks generate.
 */
const base = (process.env.CANVAS_BASE || 'http://127.0.0.1:8891').replace(/\/$/, '');
const token = process.env.CANVAS_TOKEN || '';
const projectId = process.env.PROJECT_ID || '';
const episodeId = process.env.EPISODE_ID || 'EP003';
const rounds = Math.max(5, Number(process.env.RAPID_ROUNDS || 20));
const delays = (process.env.RAPID_DELAYS || '15,45,90')
  .split(',')
  .map((value) => Number(value.trim()))
  .filter((value) => Number.isFinite(value) && value >= 0);

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
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
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
  await page.waitForSelector('.react-flow__node-media .canvas-media-card', {
    timeout: 60_000,
  });
  await new Promise((resolve) => setTimeout(resolve, 400));

  const cardId = await page.$eval(
    '.react-flow__node-media .canvas-media-card',
    (element) => element.closest('.react-flow__node')?.getAttribute('data-id') || '',
  );
  if (!cardId) throw new Error('media card has no flow-node id');
  const cardSelector = `[data-id="${cardId.replace(/["\\]/g, '\\$&')}"] .canvas-media-card`;
  const cardBox = await page.$eval(cardSelector, (element) => {
    const rect = element.getBoundingClientRect();
    return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
  });
  const cardPoint = {
    x: cardBox.x + Math.min(24, cardBox.width / 3),
    y: cardBox.y + Math.min(24, cardBox.height / 3),
  };
  const blankPoint = {x: 90, y: 830};
  const samples = [];

  for (let round = 0; round < rounds; round += 1) {
    const delay = delays[round % Math.max(1, delays.length)];
    await page.mouse.click(cardPoint.x, cardPoint.y);
    await page.waitForFunction(() => {
      const panel = document.querySelector('.jim-bar');
      return !!panel && !panel.classList.contains('is-closing');
    }, {timeout: 5_000});

    await page.mouse.click(blankPoint.x, blankPoint.y);
    await page.waitForFunction(() => (
      !!document.querySelector('.jim-bar.is-closing')
      || !document.querySelector('.jim-bar')
    ), {timeout: 2_000});
    if (delay > 0) {
      await new Promise((resolve) => setTimeout(resolve, delay));
    }

    await page.mouse.click(cardPoint.x, cardPoint.y);
    await page.waitForFunction(() => {
      const panel = document.querySelector('.jim-bar');
      return !!panel && !panel.classList.contains('is-closing');
    }, {timeout: 5_000});
    await new Promise((resolve) => setTimeout(resolve, 260));
    const survived = await page.evaluate(() => {
      const panel = document.querySelector('.jim-bar');
      return {
        present: !!panel,
        closing: panel?.classList.contains('is-closing') ?? false,
        title: panel?.querySelector('.jim-head-title')?.textContent || '',
      };
    });
    samples.push({round, delay, ...survived});
    if (!survived.present || survived.closing) {
      throw new Error(`round ${round} at ${delay}ms lost the reopened modal`);
    }

    await page.mouse.click(blankPoint.x, blankPoint.y);
    await page.waitForFunction(() => !document.querySelector('.jim-bar'), {
      timeout: 3_000,
    });
  }

  console.log(JSON.stringify({
    cardId,
    rounds,
    delays,
    failures: samples.filter((sample) => !sample.present || sample.closing),
    pageErrors,
  }, null, 2));
  if (pageErrors.length) {
    throw new Error(`browser errors: ${pageErrors.join(' | ')}`);
  }
} finally {
  await browser.close();
}
