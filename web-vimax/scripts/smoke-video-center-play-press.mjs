import path from 'node:path';
import puppeteer from 'puppeteer-core';

const base = 'http://127.0.0.1:8890';
const title = `播放按钮按压 ${Date.now()}`;
const mediaUrl = '/uploads/generated-1792954217-8db1488463.mp4';
const screenshot = path.resolve('../data/video-center-play-press-20261026.png');

async function api(pathname, options = {}) {
  const response = await fetch(base + pathname, {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${response.status} ${JSON.stringify(payload)}`);
  return payload;
}

const project = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title}),
});
const nodeId = `video-center-play-${Date.now()}`;
await api(`/api/projects/${project.id}/nodes`, {
  method: 'PUT',
  body: JSON.stringify({
    nodes: [{
      id: nodeId,
      type: 'video',
      title: '段1 · 播放按钮按压',
      position: {x: 160, y: 120},
      metadata: {
        prompt: '播放按钮按压稳定性',
        status: 'done',
        content: mediaUrl,
        params: {
          ratio: '9:16',
          resolution: '480P',
          duration: 5,
          count: 1,
        },
      },
    }],
  }),
});

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
  const selected = await page.evaluate((projectTitle) => {
    const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
      .find((item) => (item.textContent || '').includes(projectTitle));
    if (!(button instanceof HTMLElement)) return false;
    button.click();
    return true;
  }, title);
  if (!selected) throw new Error('temporary project not found');

  const card = `[data-id="media-${nodeId}"]`;
  await page.waitForSelector(`${card} .canvas-video-center-play`, {timeout: 60_000});
  const button = `${card} .canvas-video-center-play`;
  await page.hover(button);
  await new Promise((resolve) => setTimeout(resolve, 250));
  const before = await page.$eval(button, (element) => {
    const rect = element.getBoundingClientRect();
    return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
  });

  await page.mouse.down();
  await new Promise((resolve) => setTimeout(resolve, 250));
  const during = await page.$eval(button, (element) => {
    const rect = element.getBoundingClientRect();
    return {
      x: rect.x + rect.width / 2,
      y: rect.y + rect.height / 2,
      transform: getComputedStyle(element).transform,
    };
  });
  await page.screenshot({path: screenshot, fullPage: false});
  await page.mouse.up();

  const movement = Math.hypot(during.x - before.x, during.y - before.y);
  if (movement > 1) {
    throw new Error(`center play button moved while pressed: ${JSON.stringify({before, during, movement})}`);
  }

  console.log(JSON.stringify({ok: true, nodeId, before, during, movement, screenshot}, null, 2));
} finally {
  await browser.close();
  await api(`/api/projects/${project.id}`, {method: 'DELETE'}).catch(() => {});
}
