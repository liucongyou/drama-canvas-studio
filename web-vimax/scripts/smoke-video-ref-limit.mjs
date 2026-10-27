import path from 'node:path';
import puppeteer from 'puppeteer-core';

const base = 'http://127.0.0.1:8890';
const projectTitle = '铁西硬汉 · 画布工作流';
const projectId = 'gk6woLnGjh998UeNwvhqYG';
const nodeId = 'node-2p76yrad1oas-bfce8b3d';
const screenshot = path.resolve('../data/video-ref-limit-20261026.png');

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

try {
  const page = await browser.newPage();
  let generateRequests = 0;
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.setRequestInterception(true);
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes(`/nodes/${nodeId}/generate`)) {
      generateRequests += 1;
    }
    void request.continue();
  });
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
  await page.waitForSelector('.canvas-ep-list-body', {timeout: 30000});
  await page.evaluate(() => {
    const button = [...document.querySelectorAll('.canvas-ep-list-body button')]
      .find((item) => (item.textContent || '').includes('第09集'));
    if (button instanceof HTMLElement) button.click();
  });
  const card = `[data-id="media-${nodeId}"] .canvas-media-card`;
  await page.waitForSelector(card, {timeout: 60000});
  await page.click(card);
  await page.waitForSelector('.jim-bar .jim-gen', {timeout: 30000});
  await page.click('.jim-bar .jim-gen');
  await page.waitForFunction(
    () => (document.querySelector('.canvas-toast')?.textContent || '')
      .includes('最多 5 张参考图，当前 6 张'),
    {timeout: 10000},
  );
  if (generateRequests !== 0) {
    throw new Error(`reference limit should block before POST, got ${generateRequests}`);
  }
  await page.screenshot({path: screenshot, fullPage: false});
  console.log(JSON.stringify({
    ok: true,
    projectId,
    nodeId,
    generateRequests,
    toast: await page.$eval('.canvas-toast', (node) => node.textContent || ''),
    screenshot,
  }, null, 2));
} finally {
  await browser.close();
}
