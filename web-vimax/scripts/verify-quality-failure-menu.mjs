import puppeteer from 'puppeteer-core';

/**
 * Read-only verification for failed gate-check details.
 *
 * The default project/episode intentionally has two real failed segments in
 * the local workspace fixture. Clicking the status menu must expose both the
 * segment title and the exact reference mismatch, without triggering generate.
 */
const base = (process.env.CANVAS_BASE || 'http://127.0.0.1:8891').replace(/\/$/, '');
const token = process.env.CANVAS_TOKEN || '';
const projectId = process.env.PROJECT_ID || 'RyFNSHbwwgMX3ZtvwTxx0q';
const episodeId = process.env.EPISODE_ID || 'EP002';
const shotPath = process.env.SHOT_PATH || 'scripts/_quality-failure-menu.png';

if (!token) throw new Error('CANVAS_TOKEN is required');

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
  await page.waitForSelector('.canvas-status-trigger', {timeout: 60_000});
  await page.click('.canvas-status-trigger');
  await page.waitForSelector('.canvas-status-failure', {timeout: 10_000});

  const result = await page.evaluate(() => ({
    summary: document.querySelector('.canvas-status-quality')?.textContent || '',
    rows: [...document.querySelectorAll('.canvas-status-failure')].map((element) => ({
      text: element.textContent || '',
      title: element.getAttribute('title') || '',
      disabled: element.disabled,
    })),
  }));

  console.log(JSON.stringify({...result, pageErrors}, null, 2));
  await page.screenshot({path: shotPath});
  if (!result.summary.includes('段未通过')) {
    throw new Error(`expected failed gate summary, got "${result.summary}"`);
  }
  if (result.rows.length < 1) {
    throw new Error('expected at least one failed segment row');
  }
  if (!result.rows.every((row) => /段\d+ · /.test(row.text) && row.text.includes('不一致'))) {
    throw new Error('each failed row must name the segment and mismatch reason');
  }
  if (result.rows.some((row) => row.disabled)) {
    throw new Error('failed segment row is not navigable');
  }
  if (pageErrors.length) {
    throw new Error(`browser errors: ${pageErrors.join(' | ')}`);
  }
} finally {
  await browser.close();
}
