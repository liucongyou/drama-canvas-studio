import path from 'node:path';
import puppeteer from 'puppeteer-core';

const base = 'http://127.0.0.1:8890';
const projectTitle = '铁西硬汉 · 画布工作流';
const nodeId = 'node-erwbzc2s7m34-e0f4d4e8';
const screenshot = path.resolve('../data/audio-prompt-refs-20261026.png');

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

try {
  const page = await browser.newPage();
  const errors = [];
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('pageerror', (error) => errors.push(error.message));
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.goto(base, {waitUntil: 'domcontentloaded', timeout: 60000});
  await page.waitForSelector('.canvas-project-btn', {timeout: 30000});
  await page.click('.canvas-project-btn');
  await page.waitForSelector('.canvas-project-menu', {timeout: 10000});
  await page.waitForSelector('.canvas-project-menu-list button', {timeout: 10000});
  const selected = await page.evaluate((title) => {
    const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
      .find((item) => (item.textContent || '').includes(title));
    if (!(button instanceof HTMLElement)) return false;
    button.click();
    return true;
  }, projectTitle);
  if (!selected) throw new Error('project not found');
  await page.waitForSelector('.canvas-ep-list-body', {timeout: 30000});
  await page.evaluate(() => {
    const button = [...document.querySelectorAll('.canvas-ep-list-body button')]
      .find((item) => (item.textContent || '').includes('第09集'));
    if (button instanceof HTMLElement) button.click();
  });
  const card = `[data-id="media-${nodeId}"] .canvas-media-card`;
  await page.waitForSelector(card, {timeout: 60000});
  await page.$eval(card, (node) => node.click());
  await page.waitForSelector('.jim-bar .jim-prompt', {timeout: 30000});
  await new Promise((resolve) => setTimeout(resolve, 800));
  const promptDebug = await page.$eval('.jim-bar .jim-prompt', (node) => ({
    text: node.textContent || '',
    html: node.innerHTML,
  }));
  if (!promptDebug.text.includes('音频｜赵铁柱')) {
    throw new Error(`audio reference not rendered in prompt editor: ${JSON.stringify(promptDebug)}`);
  }
  const audioMentions = await page.$$eval('.jim-bar .jim-prompt-mention.is-audio', (nodes) =>
    nodes.map((node) => ({
      text: (node.textContent || '').trim(),
      hasThumb: !!node.querySelector('img'),
    })));
  const expected = audioMentions.find((item) => item.text.includes('音频｜赵铁柱（图1）'));
  if (!expected) {
    throw new Error(`audio mention chip missing: ${JSON.stringify({audioMentions, promptDebug})}`);
  }
  await page.screenshot({path: screenshot, fullPage: false});
  if (errors.length) throw new Error(errors.join('\n'));
  console.log(JSON.stringify({ok: true, audioMentions, screenshot}, null, 2));
} finally {
  await browser.close();
}
