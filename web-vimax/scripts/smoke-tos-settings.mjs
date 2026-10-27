import fs from 'node:fs/promises';
import path from 'node:path';
import puppeteer from 'puppeteer-core';
import {fileURLToPath} from 'node:url';

const base = 'http://127.0.0.1:8890';
const theme = process.env.THEME || 'immersive';
const before = await fetch(`${base}/api/storage`).then((response) => response.json());
const invalid = await fetch(`${base}/api/storage`, {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({
    storage: {
      enabled: true,
      bucket: 'test-bucket',
      region: 'us-east-1',
      endpoint: 'tos-cn-beijing.volces.com',
      internal_endpoint: 'tos-cn-beijing.ivolces.com',
    },
  }),
});
if (invalid.status !== 400) throw new Error(`invalid TOS config should be rejected: ${invalid.status}`);

const afterInvalid = await fetch(`${base}/api/storage`).then((response) => response.json());
if (
  afterInvalid.storage.enabled !== before.storage.enabled
  || afterInvalid.storage.bucket !== before.storage.bucket
  || afterInvalid.storage.region !== before.storage.region
) {
  throw new Error('invalid config changed the live TOS state');
}

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1024, height: 760, deviceScaleFactor: 1});
  await page.evaluateOnNewDocument((value) => localStorage.setItem('vimax-web-theme', value), theme);
  await page.goto(base, {waitUntil: 'domcontentloaded', timeout: 60_000});
  await page.waitForSelector('.shell-topbar-right .shell-topbar-btn', {timeout: 30_000});
  await page.click('.shell-topbar-right .shell-topbar-btn');
  await page.waitForSelector('.shell-settings-menu', {timeout: 10_000});
  await page.evaluate(() => {
    const button = [...document.querySelectorAll('.shell-settings-menu button')]
      .find((item) => (item.textContent || '').includes('对象存储 TOS'));
    if (!(button instanceof HTMLElement)) throw new Error('TOS settings entry missing');
    button.click();
  });
  await page.waitForSelector('.tos-settings-dialog', {timeout: 10_000});
  await page.waitForFunction(() => {
    const input = document.querySelector('.tos-settings-dialog input[placeholder="tos-cn-beijing.ivolces.com"]');
    return input instanceof HTMLInputElement && input.value === 'tos-cn-beijing.ivolces.com';
  });

  const summary = await page.evaluate(() => {
    const dialog = document.querySelector('.tos-settings-dialog');
    const secretValues = [...document.querySelectorAll('.tos-settings-dialog input[type="password"]')]
      .map((input) => input instanceof HTMLInputElement ? input.value : '');
    const testButton = [...document.querySelectorAll('.tos-settings-actions button')]
      .find((button) => (button.textContent || '').includes('测试连接'));
    return {
      theme: document.documentElement.dataset.theme,
      variant: document.documentElement.dataset.variant,
      text: dialog?.textContent || '',
      inputs: dialog?.querySelectorAll('input').length || 0,
      switches: dialog?.querySelectorAll('[role="switch"]').length || 0,
      secretValues,
      testDisabled: testButton instanceof HTMLButtonElement ? testButton.disabled : false,
      overflowX: dialog ? dialog.scrollWidth > dialog.clientWidth + 1 : true,
      width: dialog?.getBoundingClientRect().width || 0,
      viewport: window.innerWidth,
    };
  });
  const expectedTheme = theme === 'light' ? ['light', 'light'] : ['dark', theme === 'dark' ? 'aigc' : theme];
  if (summary.theme !== expectedTheme[0] || summary.variant !== expectedTheme[1]) {
    throw new Error(`TOS theme mismatch: ${JSON.stringify(summary)}`);
  }
  if (!summary.text.includes('对象存储 TOS') || summary.inputs < 8 || summary.switches < 3) {
    throw new Error(`TOS dialog fields missing: ${JSON.stringify(summary)}`);
  }
  if (summary.secretValues.some(Boolean) || summary.testDisabled === before.storage.ready) {
    throw new Error(`TOS secrets or disabled state mismatch: ${JSON.stringify(summary)}`);
  }
  if (summary.overflowX || summary.width > summary.viewport) {
    throw new Error(`TOS dialog overflow: ${JSON.stringify(summary)}`);
  }

  let liveTest = '';
  if (before.storage.ready) {
    await page.evaluate(() => {
      const button = [...document.querySelectorAll('.tos-settings-actions button')]
        .find((item) => (item.textContent || '').includes('测试连接'));
      if (button instanceof HTMLElement) button.click();
    });
    await page.waitForSelector('.tos-test-result', {timeout: 30_000});
    liveTest = await page.$eval('.tos-test-result', (element) => element.textContent?.trim() || '');
    if (!liveTest.includes('已连接') && !liveTest.includes('欠费')) {
      throw new Error(`unexpected TOS live test result: ${liveTest}`);
    }
  }

  const shots = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'data');
  await fs.mkdir(shots, {recursive: true});
  const screenshotPath = path.join(shots, 'tos-settings-20261027.png');
  await page.screenshot({path: screenshotPath, fullPage: false});

  await page.setViewport({width: 560, height: 720, deviceScaleFactor: 1});
  await new Promise((resolve) => setTimeout(resolve, 220));
  const compact = await page.evaluate(() => {
    const dialog = document.querySelector('.tos-settings-dialog');
    const rect = dialog?.getBoundingClientRect();
    return {
      width: rect?.width || 0,
      height: rect?.height || 0,
      overflowX: dialog ? dialog.scrollWidth > dialog.clientWidth + 1 : true,
      withinViewport: !!rect && rect.left >= 0 && rect.right <= window.innerWidth + 1,
    };
  });
  if (compact.overflowX || !compact.withinViewport) {
    throw new Error(`compact TOS dialog overflow: ${JSON.stringify(compact)}`);
  }

  console.log(JSON.stringify({ok: true, summary, liveTest, compact, screenshotPath}, null, 2));
} finally {
  await browser.close();
}
