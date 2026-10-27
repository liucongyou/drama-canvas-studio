import path from 'node:path';
import puppeteer from 'puppeteer-core';

const base = 'http://127.0.0.1:8890';
const projectTitle = '铁西硬汉 · 画布工作流';
const screenshot = path.resolve('../data/task-center-20261026.png');

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
  if (!selected) throw new Error('project not found');
  await page.waitForSelector('.react-flow', {timeout: 60000});
  await page.click('.canvas-task-center-trigger');
  await page.waitForSelector('.canvas-task-center', {timeout: 10000});
  const taskText = await page.$eval('.canvas-task-item', (node) => node.textContent || '');
  if (!taskText.includes('第 1 次')) throw new Error(`attempt label missing: ${taskText}`);
  const metaText = await page.$eval('.canvas-task-item-meta', (node) => node.textContent || '');
  if (!/(等待|运行) \d+:\d{2}/.test(metaText)) throw new Error(`elapsed label missing: ${metaText}`);
  await page.screenshot({path: screenshot, fullPage: false});
  console.log(JSON.stringify({ok: true, taskText: taskText.trim(), metaText: metaText.trim(), screenshot}, null, 2));
} finally {
  await browser.close();
}
