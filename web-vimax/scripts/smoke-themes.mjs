import path from 'node:path';
import puppeteer from 'puppeteer-core';

const base = 'http://127.0.0.1:8890';
const screenshots = path.resolve('../data');
const themes = [
  {label: '石墨蓝', key: 'immersive', theme: 'dark', variant: 'immersive'},
  {label: '松石绿', key: 'forest', theme: 'dark', variant: 'forest'},
  {label: '暖铜灰', key: 'copper', theme: 'dark', variant: 'copper'},
  {label: 'AIGC 暗色', key: 'dark', theme: 'dark', variant: 'aigc'},
  {label: '白色主题', key: 'light', theme: 'light', variant: 'light'},
];

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.goto(base, {waitUntil: 'domcontentloaded', timeout: 60_000});
  await page.waitForSelector('.canvas-project-btn', {timeout: 30_000});
  await page.click('.canvas-project-btn');
  await page.waitForSelector('.canvas-project-menu-list button', {timeout: 10_000});
  await page.evaluate(() => {
    const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
      .find((item) => (item.textContent || '').includes('铁西硬汉 · 画布工作流'));
    if (button instanceof HTMLElement) button.click();
  });
  await page.waitForSelector('.canvas-minimap .react-flow__minimap-node', {timeout: 60_000});

  const results = [];
  for (const item of themes) {
    await page.evaluate(() => {
      const button = [...document.querySelectorAll('.shell-topbar-btn')]
        .find((element) => (element.textContent || '').includes('主题'));
      if (button instanceof HTMLElement) button.click();
    });
    await page.waitForSelector('.shell-theme-option', {timeout: 10_000});
    await page.evaluate((label) => {
      const button = [...document.querySelectorAll('.shell-theme-option')]
        .find((element) => (element.textContent || '').trim() === label);
      if (!(button instanceof HTMLElement)) throw new Error(`theme option missing: ${label}`);
      button.click();
    }, item.label);
    await new Promise((resolve) => setTimeout(resolve, 180));

    const state = await page.evaluate(() => {
      const root = document.documentElement;
      const rootStyle = getComputedStyle(root);
      const minimap = document.querySelector('.canvas-minimap');
      const minimapStyle = minimap ? getComputedStyle(minimap) : null;
      const minimapNode = document.querySelector('.canvas-minimap .react-flow__minimap-node');
      const minimapNodeStyle = minimapNode ? getComputedStyle(minimapNode) : null;
      const textCard = document.querySelector('.canvas-media-node.is-text-node .canvas-media-card');
      const textCardStyle = textCard ? getComputedStyle(textCard) : null;
      return {
        theme: root.dataset.theme || '',
        variant: root.dataset.variant || '',
        canvas: rootStyle.getPropertyValue('--v-canvas').trim(),
        surface: rootStyle.getPropertyValue('--v-surface').trim(),
        accent: rootStyle.getPropertyValue('--v-accent').trim(),
        minimapBg: minimapStyle?.backgroundColor || '',
        minimapBorder: minimapStyle?.borderColor || '',
        minimapNodeFill: minimapNodeStyle?.fill || '',
        textCardTone: textCardStyle?.getPropertyValue('--card-c1').trim() || '',
        stored: localStorage.getItem('vimax-web-theme'),
      };
    });
    if (state.theme !== item.theme || state.variant !== item.variant || state.stored !== item.key) {
      throw new Error(`theme state mismatch: ${JSON.stringify({expected: item, state})}`);
    }
    if (state.minimapNodeFill === 'rgb(0, 0, 0)') {
      throw new Error(`minimap node color fell back to black: ${JSON.stringify({item, state})}`);
    }
    const shot = path.join(screenshots, `theme-${item.key}.png`);
    await page.screenshot({path: shot, fullPage: false});
    results.push({...item, ...state, screenshot: shot});
  }

  const topbarOrder = await page.evaluate(() =>
    [...document.querySelectorAll('.shell-topbar-right .shell-topbar-btn')]
      .map((element) => (element.textContent || '').trim())
      .filter(Boolean));
  if (topbarOrder[0] !== '主题' || topbarOrder[1] !== '模型接口') {
    throw new Error(`theme button order mismatch: ${JSON.stringify(topbarOrder)}`);
  }

  await page.reload({waitUntil: 'domcontentloaded', timeout: 60_000});
  await page.waitForSelector('.canvas-minimap', {timeout: 60_000});
  const persisted = await page.evaluate(() => ({
    theme: document.documentElement.dataset.theme || '',
    variant: document.documentElement.dataset.variant || '',
  }));
  if (persisted.theme !== 'light' || persisted.variant !== 'light') {
    throw new Error(`theme did not persist: ${JSON.stringify(persisted)}`);
  }

  // Leave the shared workspace on its default theme after the probe.
  await page.evaluate(() => {
    const button = [...document.querySelectorAll('.shell-topbar-btn')]
      .find((element) => (element.textContent || '').includes('主题'));
    if (button instanceof HTMLElement) button.click();
  });
  await page.waitForSelector('.shell-theme-option', {timeout: 10_000});
  await page.evaluate(() => {
    const button = [...document.querySelectorAll('.shell-theme-option')]
      .find((element) => (element.textContent || '').trim() === '石墨蓝');
    if (button instanceof HTMLElement) button.click();
  });

  console.log(JSON.stringify({ok: true, persisted, topbarOrder, results}, null, 2));
} finally {
  await browser.close();
}
