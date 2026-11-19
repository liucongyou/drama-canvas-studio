import puppeteer from 'puppeteer-core';

const base = process.env.CANVAS_BASE_URL || 'http://127.0.0.1:8890';
const token = process.env.CANVAS_SESSION_TOKEN || '';
const projectId = process.env.CANVAS_PROJECT_ID || '';
const screenshot = process.env.CANVAS_SCREENSHOT || 'artifacts/recipe-ui.png';
if (!token) throw new Error('CANVAS_SESSION_TOKEN required');
if (!projectId) throw new Error('CANVAS_PROJECT_ID required');

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});
try {
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 960, deviceScaleFactor: 1});
  const errors = [];
  const networkFailures = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error' && !message.text().includes('Failed to load resource')) errors.push(message.text());
  });
  page.on('requestfailed', (request) => {
    networkFailures.push({
      url: request.url(),
      error: request.failure()?.errorText || '',
    });
  });
  await page.setCookie({
    name: 'canvas_session',
    value: token,
    domain: '127.0.0.1',
    path: '/',
    httpOnly: true,
  });
  await page.goto(`${base}/?ep=EP001#${projectId}`, {
    waitUntil: 'domcontentloaded',
    timeout: 60_000,
  });
  await page.waitForSelector('.canvas-media-node', {timeout: 60_000});

  const fab = await page.$('.agent-fab');
  if (fab) await fab.click();
  await page.waitForSelector('.agent-panel', {timeout: 20_000});
  await page.click('[data-agent-trigger="recipe"]');
  await page.waitForSelector('.agent-recipe-picker input', {timeout: 20_000});
  await page.type('.agent-recipe-picker input', '群演聚焦接力');
  await page.waitForSelector('.agent-recipe-item', {timeout: 20_000});
  await page.waitForFunction(
    () => Array.from(document.querySelectorAll('.agent-recipe-item strong')).some(
      (node) => (node.textContent || '').includes('群演聚焦接力'),
    ),
    {timeout: 20_000},
  );
  const recipeText = await page.$eval('.agent-recipe-item strong', (node) => node.textContent || '');
  await page.click('.agent-recipe-item');
  await page.waitForSelector('.agent-recipe-chip', {timeout: 20_000});
  const selectedChip = await page.$eval('.agent-recipe-chip', (node) => node.textContent || '');
  await page.screenshot({path: screenshot.replace(/\.png$/i, '-agent.png'), fullPage: false});
  const agentLayout = await page.evaluate(() => {
    const panel = document.querySelector('.agent-panel');
    const chip = document.querySelector('.agent-recipe-chip');
    return {
      panel: panel ? panel.getBoundingClientRect().toJSON() : null,
      chip: chip ? chip.getBoundingClientRect().toJSON() : null,
      viewport: {width: innerWidth, height: innerHeight},
    };
  });

  await page.click('.agent-head [title="关闭"]');
  await page.waitForSelector('.agent-panel', {hidden: true, timeout: 20_000});
  const handles = await page.$$('.canvas-media-card');
  let videoHandle = null;
  for (const handle of handles) {
    const isVideo = await handle.evaluate((node) => Boolean(node.querySelector('.lucide-video')));
    if (isVideo) {
      videoHandle = handle;
      break;
    }
  }
  videoHandle ||= handles[handles.length - 1];
  if (!videoHandle) throw new Error('video node was not found');
  await videoHandle.click();
  await page.screenshot({path: screenshot.replace(/\.png$/i, '-node.png'), fullPage: false});
  await page.waitForSelector('.jim-recipe-row', {timeout: 20_000});
  await page.click('[data-pop-owner="tpl"]');
  await page.waitForSelector('.jim-tpl-recipe', {timeout: 20_000});
  await page.click('.jim-tpl-recipe');
  await page.waitForSelector('.canvas-recipe-overlay .canvas-recipe-popover', {timeout: 20_000});
  await page.click('.canvas-recipe-close');
  await page.waitForSelector('.canvas-recipe-overlay', {hidden: true, timeout: 20_000});
  const nodeRecipe = await page.$eval('.canvas-recipe-trigger', (node) => node.textContent || '');
  const layout = await page.evaluate(() => {
    const picker = document.querySelector('.canvas-recipe-trigger');
    return {
      nodeRecipe: picker ? picker.getBoundingClientRect().toJSON() : null,
      viewport: {width: innerWidth, height: innerHeight},
    };
  });
  await page.screenshot({path: screenshot, fullPage: false});
  if (!recipeText.includes('群演聚焦接力')) throw new Error(`unexpected recipe result: ${recipeText}`);
  if (!selectedChip.includes('群演聚焦接力')) throw new Error(`recipe chip missing: ${selectedChip}`);
  if (!nodeRecipe.includes('群演聚焦接力')) throw new Error(`node recipe missing: ${nodeRecipe}`);
  const share = await page.evaluate(async (targetProjectId) => {
    const response = await fetch(`/api/projects/${encodeURIComponent(targetProjectId)}/shares`, {
      method: 'POST',
      credentials: 'include',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({role: 'viewer', ttlDays: 1, label: 'recipe-e2e'}),
    });
    if (!response.ok) throw new Error(`share create failed: ${response.status}`);
    return response.json();
  }, projectId);
  await page.goto(`${base}/share/${share.token}`, {
    waitUntil: 'domcontentloaded',
    timeout: 60_000,
  });
  await page.waitForSelector('.canvas-media-card', {timeout: 60_000});
  const shareCards = await page.$$('.canvas-media-card');
  let shareVideo = null;
  for (const handle of shareCards) {
    if (await handle.evaluate((node) => Boolean(node.querySelector('.lucide-video')))) {
      shareVideo = handle;
      break;
    }
  }
  if (!shareVideo) throw new Error('shared video node was not found');
  await shareVideo.click();
  await page.waitForSelector('.canvas-recipe-readonly', {timeout: 20_000});
  const readOnlyRecipe = await page.$eval('.canvas-recipe-readonly', (node) => node.textContent || '');
  const readOnlyHasEditor = Boolean(await page.$('.canvas-recipe-trigger'));
  if (readOnlyHasEditor) throw new Error('read-only share exposed recipe editor');

  const mobile = await browser.newPage();
  await mobile.setViewport({width: 390, height: 844, deviceScaleFactor: 1});
  await mobile.goto(`${base}/?ep=EP001#${projectId}`, {
    waitUntil: 'domcontentloaded',
    timeout: 60_000,
  });
  await mobile.waitForSelector('.canvas-media-node', {timeout: 60_000});
  const mobileFab = await mobile.$('.agent-fab');
  if (mobileFab) await mobileFab.click();
  await mobile.waitForSelector('.agent-panel', {timeout: 20_000});
  await mobile.click('[data-agent-trigger="recipe"]');
  await mobile.waitForSelector('.agent-recipe-picker', {timeout: 20_000});
  const mobileLayout = await mobile.evaluate(() => {
    const picker = document.querySelector('.agent-recipe-picker');
    const rect = picker ? picker.getBoundingClientRect().toJSON() : null;
    return {
      rect,
      viewport: {width: innerWidth, height: innerHeight},
      overflow: Boolean(rect && (rect.left < 0 || rect.right > innerWidth)),
    };
  });
  await mobile.screenshot({
    path: screenshot.replace(/\.png$/i, '-mobile.png'),
    fullPage: false,
  });
  if (mobileLayout.overflow) throw new Error(`mobile recipe picker overflow: ${JSON.stringify(mobileLayout)}`);

  const apiFailures = networkFailures.filter((item) =>
    item.url.includes('/api/agent/recipes')
  );
  if (errors.length) throw new Error(`browser errors: ${errors.join(' | ')}`);
  if (apiFailures.length) throw new Error(`api failures: ${JSON.stringify(apiFailures)}`);
  console.log(JSON.stringify({
    recipeText,
    selectedChip,
    nodeRecipe,
    agentLayout,
    layout,
    readOnlyRecipe,
    readOnlyHasEditor,
    mobileLayout,
    networkFailures,
    screenshot,
  }, null, 2));
} finally {
  await browser.close();
}
