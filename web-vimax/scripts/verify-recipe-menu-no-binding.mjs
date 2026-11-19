import puppeteer from 'puppeteer-core';

const base = process.env.CANVAS_BASE_URL || 'http://127.0.0.1:8890';
const token = process.env.CANVAS_SESSION_TOKEN || '';
const projectId = process.env.CANVAS_PROJECT_ID || '';
const episode = process.env.CANVAS_EPISODE || 'EP001';
const screenshot = process.env.CANVAS_SCREENSHOT || 'artifacts/recipe-menu-no-binding.png';
if (!token || !projectId) throw new Error('session token and project id are required');

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});
try {
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 960, deviceScaleFactor: 1});
  await page.setCookie({
    name: 'canvas_session',
    value: token,
    domain: '127.0.0.1',
    path: '/',
    httpOnly: true,
  });
  await page.goto(`${base}/?ep=${encodeURIComponent(episode)}#${projectId}`, {
    waitUntil: 'domcontentloaded',
    timeout: 60_000,
  });
  await page.waitForSelector('.canvas-media-card', {timeout: 60_000});
  const cards = await page.$$('.canvas-media-card');
  let video = null;
  for (const card of cards) {
    if (await card.evaluate((node) => Boolean(node.querySelector('.lucide-video')))) {
      video = card;
      break;
    }
  }
  if (!video) throw new Error('video node was not found');
  await video.click();
  await page.waitForSelector('.jim-toolbar', {timeout: 20_000});
  const emptyRecipeRow = await page.$('.jim-recipe-row.is-empty');
  if (!emptyRecipeRow) throw new Error('recipe listener container is not mounted');
  await page.click('[data-pop-owner="tpl"]');
  await page.waitForSelector('.jim-tpl-recipe', {timeout: 20_000});
  await page.screenshot({path: screenshot.replace(/\.png$/i, '-menu.png'), fullPage: false});
  await page.click('.jim-tpl-recipe');
  await page.waitForSelector('.canvas-recipe-overlay .canvas-recipe-popover', {timeout: 20_000});
  const rect = await page.$eval('.canvas-recipe-popover', (node) => node.getBoundingClientRect().toJSON());
  const visible = await page.$eval('.canvas-recipe-popover', (node) => {
    const style = getComputedStyle(node);
    return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) > 0;
  });
  await page.screenshot({path: screenshot, fullPage: false});
  if (!visible) throw new Error('recipe dialog did not become visible');
  console.log(JSON.stringify({rect, screenshot}, null, 2));
} finally {
  await browser.close();
}
