import path from 'node:path';
import puppeteer from 'puppeteer-core';

const base = 'http://127.0.0.1:8890';
const projectTitle = '铁西硬汉 · 画布工作流';
const videoNodeId = 'node-erwbzc2s7m34-e0f4d4e8';
const audioNodeId = 'audio-1792926886504-v5jnn';
const screenshot = path.resolve('../data/audio-focus-alignment-20261026.png');

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.goto(base, {waitUntil: 'domcontentloaded', timeout: 60000});
  await page.waitForSelector('.canvas-project-btn', {timeout: 30000});
  await page.click('.canvas-project-btn');
  await page.waitForSelector('.canvas-project-menu-list button', {timeout: 10000});
  await page.evaluate((title) => {
    const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
      .find((item) => (item.textContent || '').includes(title));
    if (button instanceof HTMLElement) button.click();
  }, projectTitle);
  await page.waitForSelector('.canvas-ep-list-body', {timeout: 30000});
  await page.evaluate(() => {
    const button = [...document.querySelectorAll('.canvas-ep-list-body button')]
      .find((item) => (item.textContent || '').includes('第09集'));
    if (button instanceof HTMLElement) button.click();
  });
  await page.waitForSelector(`[data-id="media-${videoNodeId}"] .canvas-media-card`, {timeout: 60000});
  await page.$eval(`[data-id="media-${videoNodeId}"] .canvas-media-card`, (node) => node.click());
  await page.waitForSelector('.jim-bar .jim-prompt', {timeout: 30000});
  await new Promise((resolve) => setTimeout(resolve, 500));

  const focusState = await page.evaluate(() => {
    const active = document.activeElement;
    return {
      tag: active?.tagName || '',
      className: active instanceof HTMLElement ? active.className : '',
      isPrompt: active instanceof HTMLElement && (
        active.classList.contains('jim-prompt')
        || active.closest('.jim-prompt') !== null
      ),
    };
  });
  if (focusState.isPrompt) throw new Error(`prompt stole focus: ${JSON.stringify(focusState)}`);

  const alignment = await page.evaluate(() => {
    const thumb = document.querySelector('.jim-ref-thumb');
    const add = document.querySelector('.jim-ref-row .jim-add');
    if (!(thumb instanceof HTMLElement) || !(add instanceof HTMLElement)) return null;
    const a = thumb.getBoundingClientRect();
    const b = add.getBoundingClientRect();
    return {thumbTop: a.top, addTop: b.top, delta: Math.abs(a.top - b.top)};
  });
  if (!alignment || alignment.delta > 1) {
    throw new Error(`reference add button misaligned: ${JSON.stringify(alignment)}`);
  }
  await page.click('.jim-bar .jim-prompt');
  const editorFocused = await page.evaluate(() =>
    document.activeElement instanceof HTMLElement
    && document.activeElement.classList.contains('jim-prompt'));
  if (!editorFocused) throw new Error('clicking prompt editor did not focus it');
  await page.click('.jim-bar .jim-head-title');
  await new Promise((resolve) => setTimeout(resolve, 120));
  const blankFocusState = await page.evaluate(() => ({
    activeClass: document.activeElement instanceof HTMLElement ? document.activeElement.className : '',
    promptFocused: document.activeElement instanceof HTMLElement
      && document.activeElement.classList.contains('jim-prompt'),
    selectionRanges: window.getSelection()?.rangeCount || 0,
  }));
  if (blankFocusState.promptFocused || blankFocusState.selectionRanges !== 0) {
    throw new Error(`clicking modal blank kept editor caret: ${JSON.stringify(blankFocusState)}`);
  }

  const quality = await page.evaluate(() => {
    const label = document.querySelector('.canvas-quality-pill-label');
    return {
      text: label?.textContent?.trim() || '',
      full: document.querySelector('.canvas-quality-pill')?.textContent?.trim() || '',
    };
  });
  if (!quality.text.startsWith('挂接') || quality.full.includes('40未达标')) {
    throw new Error(`quality badge is still misleading: ${JSON.stringify(quality)}`);
  }
  await page.screenshot({path: screenshot, fullPage: false});

  await page.mouse.click(10, 10);
  await page.waitForSelector('.jim-bar', {hidden: true, timeout: 10000}).catch(() => {});
  await page.waitForSelector(`[data-id="media-${audioNodeId}"] .canvas-media-card`, {timeout: 30000});
  await page.$eval(`[data-id="media-${audioNodeId}"] .canvas-media-card`, (node) => node.click());
  await page.waitForSelector('.jim-bar[data-kind="audio"]', {timeout: 30000});
  const audioBarText = await page.$eval('.jim-bar[data-kind="audio"]', (node) => node.textContent || '');
  if (!audioBarText.includes('MP3')) {
    throw new Error(`audio design default is not MP3: ${audioBarText.slice(0, 500)}`);
  }
  console.log(JSON.stringify({
    ok: true,
    focusState,
    alignment,
    editorFocused,
    blankFocusState,
    quality,
    audioDefault: 'MP3',
    screenshot,
  }, null, 2));
} finally {
  await browser.close();
}
