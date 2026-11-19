import puppeteer from 'puppeteer-core';

const base = process.env.CANVAS_BASE_URL || 'http://127.0.0.1:8890';
const token = process.env.CANVAS_SESSION_TOKEN || '';
const projectId = process.env.CANVAS_PROJECT_ID || '';
const episode = process.env.CANVAS_EPISODE || 'EP001';
const screenshot = process.env.CANVAS_SCREENSHOT || 'artifacts/recipe-menu-no-binding.png';
const viewportWidth = Number(process.env.CANVAS_VIEWPORT_WIDTH || 1440);
const viewportHeight = Number(process.env.CANVAS_VIEWPORT_HEIGHT || 960);
if (!token || !projectId) throw new Error('session token and project id are required');

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});
try {
  const page = await browser.newPage();
  await page.setViewport({width: viewportWidth, height: viewportHeight, deviceScaleFactor: 1});
  const host = new URL(base).hostname;
  const cookieDomains = host.endsWith('pduoduo.vip')
    ? ['yj.pduoduo.vip', 'yjapi.pduoduo.vip']
    : [host];
  for (const domain of cookieDomains) {
    await page.setCookie({
      name: 'canvas_session',
      value: token,
      domain,
      path: '/',
      httpOnly: true,
      secure: base.startsWith('https://'),
    });
  }
  await page.goto(`${base}/?ep=${encodeURIComponent(episode)}#${projectId}`, {
    waitUntil: 'domcontentloaded',
    timeout: 60_000,
  });
  await page.waitForSelector('.canvas-media-card', {timeout: 60_000});
  const cards = await page.$$('.canvas-media-card');
  let video = null;
  for (const card of cards) {
    if (await card.evaluate((node) => Boolean(node.querySelector(
      'video, .canvas-video-shell, .canvas-video-cover-pending, .canvas-video-cover-fallback, .lucide-video',
    )))) {
      video = card;
      break;
    }
  }
  if (!video) {
    for (const card of cards) {
      await card.click();
      await page.waitForSelector('.jim-toolbar', {timeout: 5_000}).catch(() => null);
      if (await page.$('.jim-recipe-row')) {
        video = card;
        break;
      }
      await page.keyboard.press('Escape');
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
  const guide = await page.$eval('.canvas-recipe-guide', (node) => node.textContent || '');
  if (!guide.includes('简单对白') || !guide.includes('可以不选')) {
    throw new Error(`recipe selection guide missing: ${guide}`);
  }
  const categoryButtons = await page.$$('.canvas-recipe-categories button');
  let actionCategory = null;
  for (const button of categoryButtons) {
    if ((await button.evaluate((node) => node.textContent || '')).trim() === '动作打斗') {
      actionCategory = button;
      break;
    }
  }
  if (!actionCategory) throw new Error('action category button was not found');
  await actionCategory.click();
  await page.waitForFunction(() => {
    const results = document.querySelector('.canvas-recipe-results');
    return Boolean(
      results
      && results.querySelectorAll('button').length > 0
      && !(results.textContent || '').includes('正在检索配方'),
    );
  }, {timeout: 20_000});
  const firstRecipe = await page.$('.canvas-recipe-results button');
  if (!firstRecipe) throw new Error('categorized recipe result was not found');
  await firstRecipe.click();
  await page.waitForSelector('.canvas-recipe-form .canvas-recipe-slots input', {timeout: 20_000});
  const form = await page.evaluate(() => {
    const labels = Array.from(document.querySelectorAll('.canvas-recipe-slots label > span'))
      .map((node) => (node.textContent || '').trim());
    const hints = Array.from(document.querySelectorAll('.canvas-recipe-slots label > small'))
      .map((node) => (node.textContent || '').trim());
    const values = Array.from(document.querySelectorAll('.canvas-recipe-slots input'))
      .map((node) => node.value.trim());
    return {
      labels,
      hints,
      values,
      description: document.querySelector('.canvas-recipe-form > p')?.textContent || '',
    };
  });
  if (form.labels.some((value) => /[a-z]/i.test(value))) {
    throw new Error(`English slot labels remain: ${form.labels.join(' | ')}`);
  }
  if (form.hints.some((value) => !value)) {
    throw new Error('recipe slot help is incomplete');
  }
  if (!form.values.length || form.values.some((value) => !value)) {
    throw new Error(`recipe auto-fill left empty inputs: ${form.values.join(' | ')}`);
  }
  if (!/[\u4e00-\u9fff]/.test(form.description) || form.description.includes('Create original')) {
    throw new Error(`recipe description was not localized: ${form.description}`);
  }
  const overflow = await page.$eval('.canvas-recipe-popover', (node) => ({
    clientWidth: node.clientWidth,
    scrollWidth: node.scrollWidth,
  }));
  if (overflow.scrollWidth > overflow.clientWidth + 1) {
    throw new Error(`recipe popover overflows horizontally: ${JSON.stringify(overflow)}`);
  }
  const formScreenshot = screenshot.replace(/\.png$/i, '-form.png');
  await page.screenshot({path: formScreenshot, fullPage: false});
  console.log(JSON.stringify({rect, screenshot, formScreenshot, form, overflow}, null, 2));
} finally {
  await browser.close();
}
