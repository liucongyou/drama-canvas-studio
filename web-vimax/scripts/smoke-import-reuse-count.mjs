import path from 'node:path';
import puppeteer from 'puppeteer-core';

const base = 'http://127.0.0.1:8890';
const projectTitle = '铁西硬汉 · 画布工作流';
const feedPath = path.resolve('../data/ep09-source-fixed.json');
const screenshot = path.resolve('../data/import-reuse-count-20261026.png');

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.goto(base, {waitUntil: 'networkidle0', timeout: 60000});
  await page.waitForSelector('.canvas-project-btn', {timeout: 30000});
  await page.click('.canvas-project-btn');
  await page.waitForSelector('.canvas-project-menu', {timeout: 10000});
  const selected = await page.evaluate((title) => {
    const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
      .find((item) => (item.textContent || '').includes(title));
    if (!(button instanceof HTMLElement)) return false;
    button.click();
    return true;
  }, projectTitle);
  if (!selected) throw new Error('project not found in menu');

  await page.waitForSelector('.react-flow', {timeout: 60000});
  await page.click('button[title^="项目维护"]');
  await page.waitForSelector('.shell-tools-menu', {timeout: 10000});
  const fileInput = await page.$('.shell-tools-menu input[type="file"][accept="application/json,.json"]');
  if (!fileInput) throw new Error('import file input not found');
  await fileInput.uploadFile(feedPath);
  await page.waitForSelector('.gdialog', {timeout: 30000});
  const message = await page.$eval('.gdialog', (node) => node.textContent || '');
  if (!message.includes('复用已有资产 15 个') || !message.includes('其中已生成 9 个')) {
    throw new Error(`reuse summary mismatch: ${message}`);
  }
  await page.screenshot({path: screenshot, fullPage: false});
  console.log(JSON.stringify({ok: true, message: message.trim(), screenshot}, null, 2));
} finally {
  await browser.close();
}
