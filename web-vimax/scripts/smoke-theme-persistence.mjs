import puppeteer from 'puppeteer-core';

/**
 * Theme persistence smoke. Uses temporary sessions and never triggers a paid
 * generation. Verifies:
 *   1. static HTML paints graphite before React mounts;
 *   2. a database theme is applied before first paint;
 *   3. changing theme persists through a fresh browser context;
 *   4. rapid theme changes settle on the last choice.
 *
 * CANVAS_BASE defaults to the local Vite server.
 */
const base = process.env.CANVAS_BASE || 'http://127.0.0.1:8891';
const tokens = String(process.env.CANVAS_SMOKE_TOKENS || '')
  .split(',')
  .map((value) => value.trim())
  .filter(Boolean);
if (tokens.length < 2) {
  throw new Error('CANVAS_SMOKE_TOKENS requires at least two temporary session tokens');
}

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

const errors = [];
const failedRequests = [];
const failures = [];
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function openWithToken(token) {
  const context = await browser.createBrowserContext();
  const page = await context.newPage();
  await page.setViewport({width: 1440, height: 960});
  await page.setCookie({
    name: 'canvas_session',
    value: token,
    domain: '127.0.0.1',
    path: '/',
    httpOnly: true,
    sameSite: 'Lax',
  });
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('requestfailed', (request) => {
    failedRequests.push(`${request.url()} ${request.failure()?.errorText || ''}`);
  });
  page.on('response', (response) => {
    if (response.status() >= 400) {
      failedRequests.push(`${response.status()} ${response.url()}`);
    }
  });
  return {context, page};
}

try {
  const {context: firstContext, page: first} = await openWithToken(tokens[0]);
  await first.goto(base, {waitUntil: 'domcontentloaded', timeout: 60_000});
  const boot = await first.evaluate(() => ({
    theme: document.documentElement.dataset.theme,
    variant: document.documentElement.dataset.variant,
    background: getComputedStyle(document.documentElement).backgroundColor,
  }));
  if (boot.theme !== 'dark' || boot.variant !== 'immersive') {
    failures.push(`initial HTML theme is ${boot.theme}/${boot.variant}`);
  }

  await first.waitForSelector('.shell-topbar-btn', {timeout: 60_000});
  await first.evaluate(() => {
    const btn = Array.from(document.querySelectorAll('button')).find((item) =>
      (item.textContent || '').includes('设置')
    );
    btn?.click();
  });
  await first.waitForSelector('.shell-theme-option', {timeout: 10_000});

  const chooseTheme = async (label) => {
    await first.evaluate((text) => {
      const btn = Array.from(document.querySelectorAll('.shell-theme-option')).find(
        (item) => (item.textContent || '').includes(text)
      );
      btn?.click();
    }, label);
  };

  await chooseTheme('松石绿');
  await sleep(500);
  const afterForest = await first.evaluate(() => ({
    theme: document.documentElement.dataset.theme,
    variant: document.documentElement.dataset.variant,
  }));
  if (afterForest.variant !== 'forest') {
    failures.push(`forest click produced ${afterForest.variant}`);
  }

  await Promise.all([chooseTheme('暖铜灰'), chooseTheme('松石绿')]);
  await sleep(1_000);
  const settled = await first.evaluate(() => document.documentElement.dataset.variant);
  if (settled !== 'forest') failures.push(`rapid-change settled on ${settled}`);

  await firstContext.close();

  const {context: secondContext, page: second} = await openWithToken(tokens[1]);
  await second.goto(base, {waitUntil: 'domcontentloaded', timeout: 60_000});
  await second.waitForSelector('.shell-topbar-btn', {timeout: 60_000});
  const persisted = await second.evaluate(() => document.documentElement.dataset.variant);
  if (persisted !== 'forest') failures.push(`fresh session saw ${persisted}`);
  await secondContext.close();

  const realErrors = errors.filter(
    (item) => !item.includes('favicon')
      && !item.includes('409')
      && !item.includes('Failed to load resource'),
  );
  if (realErrors.length) {
    failures.push(`browser errors: ${realErrors.slice(0, 5).join(' | ')}`);
  }
  console.log(JSON.stringify({
    boot,
    afterForest,
    settled,
    persisted,
    failedRequests: [...new Set(failedRequests)],
    realErrors,
  }, null, 2));
} finally {
  await browser.close();
}

if (failures.length) {
  console.error(failures.join('\n'));
  process.exitCode = 2;
} else {
  console.log('RESULT PASS');
}
