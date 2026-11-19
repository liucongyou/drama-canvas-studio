import puppeteer from 'puppeteer-core';

const base = process.env.CANVAS_BASE_URL || 'https://yj.pduoduo.vip';
const token = process.env.CANVAS_SESSION_TOKEN || '';
const projectId = process.env.CANVAS_PROJECT_ID || '';
const episode = process.env.CANVAS_EPISODE || 'EP001';
const screenshot = process.env.CANVAS_SCREENSHOT || 'artifacts/recipe-online.png';
if (!token || !projectId) throw new Error('session token and project id are required');

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});
try {
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 960, deviceScaleFactor: 1});
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error' && !message.text().includes('Failed to load resource')) {
      errors.push(message.text());
    }
  });
  const host = new URL(base).hostname;
  await page.setCookie({
    name: 'canvas_session',
    value: token,
    domain: host,
    path: '/',
    httpOnly: true,
  });
  await page.goto(`${base}/?ep=${encodeURIComponent(episode)}#${projectId}`, {
    waitUntil: 'domcontentloaded',
    timeout: 60_000,
  });
  await page.waitForSelector('.canvas-media-card', {timeout: 60_000});
  const fab = await page.$('.agent-fab');
  if (fab) await fab.click();
  await page.waitForSelector('.agent-panel', {timeout: 20_000});
  await page.click('[data-agent-trigger="recipe"]');
  await page.waitForSelector('.agent-recipe-picker input', {timeout: 20_000});
  await page.type('.agent-recipe-picker input', '群演聚焦接力');
  await page.waitForFunction(
    () => Array.from(document.querySelectorAll('.agent-recipe-item strong')).some(
      (node) => (node.textContent || '').includes('群演聚焦接力'),
    ),
    {timeout: 20_000},
  );
  await page.click('.agent-recipe-item');
  await page.waitForSelector('.agent-recipe-chip', {timeout: 20_000});
  const selectedChip = await page.$eval('.agent-recipe-chip', (node) => node.textContent || '');
  await page.screenshot({path: screenshot.replace(/\.png$/i, '-agent.png'), fullPage: false});

  await page.click('.agent-head [title="关闭"]');
  await page.waitForSelector('.agent-panel', {hidden: true, timeout: 20_000});
  const cards = await page.$$('.canvas-media-card');
  let video = null;
  for (const card of cards) {
    if (await card.evaluate((node) => Boolean(node.querySelector('.lucide-video')))) {
      video = card;
      break;
    }
  }
  let nodeControl = 'not-available-in-project';
  if (video) {
    await video.click();
    await page.waitForSelector('.jim-recipe-row .canvas-recipe-trigger', {timeout: 20_000});
    nodeControl = await page.$eval('.jim-recipe-row .canvas-recipe-trigger', (node) => node.textContent || '');
  }
  await page.screenshot({path: screenshot, fullPage: false});
  if (!selectedChip.includes('群演聚焦接力')) throw new Error(`bad online recipe chip: ${selectedChip}`);
  if (errors.length) throw new Error(errors.join(' | '));
  console.log(JSON.stringify({selectedChip, nodeControl, screenshot}, null, 2));
} finally {
  await browser.close();
}
