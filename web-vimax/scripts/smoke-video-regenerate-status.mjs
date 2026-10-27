import path from 'node:path';
import puppeteer from 'puppeteer-core';

const base = 'http://127.0.0.1:8890';
const title = `视频重生成状态 ${Date.now()}`;
const mediaUrl = '/uploads/generated-1792954217-8db1488463.mp4';
const screenshot = path.resolve('../data/video-regenerate-status-20261026.png');

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
const nodeId = `video-status-${Date.now()}`;
await api(`/api/projects/${project.id}/nodes`, {
  method: 'PUT',
  body: JSON.stringify({
    nodes: [{
      id: nodeId,
      type: 'video',
      title: '段1 · 视频状态测试',
      position: {x: 120, y: 120},
      metadata: {
        prompt: '测试视频重新生成状态',
        status: 'done',
        content: mediaUrl,
        model: 'doubao-seedance-2-0',
        params: {
          provider: 'ark',
          model: 'doubao-seedance-2-0',
          ratio: '9:16',
          resolution: '480P',
          duration: 5,
          count: 1,
          refs: [],
          voiceRefs: [],
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

let releaseGenerate;
try {
  const page = await browser.newPage();
  let generateSeen = false;
  const holdGenerate = new Promise((resolve) => { releaseGenerate = resolve; });
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.setRequestInterception(true);
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes(`/nodes/${nodeId}/generate`)) {
      generateSeen = true;
      void holdGenerate.then(() => request.respond({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({url: mediaUrl}),
      }));
      return;
    }
    void request.continue();
  });

  await page.goto(base, {waitUntil: 'domcontentloaded', timeout: 60000});
  await page.waitForSelector('.canvas-project-btn', {timeout: 30000});
  await page.click('.canvas-project-btn');
  await page.waitForSelector('.canvas-project-menu-list button', {timeout: 10000});
  await page.evaluate((projectTitle) => {
    const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
      .find((item) => (item.textContent || '').includes(projectTitle));
    if (button instanceof HTMLElement) button.click();
  }, title);
  const card = `[data-id="media-${nodeId}"] .canvas-media-card`;
  await page.waitForSelector(card, {timeout: 60000});
  await page.$eval(card, (node) => node.click());
  await page.waitForSelector('.jim-bar[data-kind="video"] .jim-gen', {timeout: 30000});
  const generateButtonState = await page.$eval('.jim-bar[data-kind="video"] .jim-gen', (node) => ({
    disabled: node.disabled,
    title: node.getAttribute('title') || '',
  }));
  await page.click('.jim-bar[data-kind="video"] .jim-gen');
  await new Promise((resolve) => setTimeout(resolve, 800));
  const busyExists = await page.evaluate(
    (id) => !!document.querySelector(`[data-id="media-${id}"] .canvas-media-busy-mask`),
    nodeId,
  );
  const busyState = await page.$eval(`[data-id="media-${nodeId}"]`, (node) => ({
    className: node.querySelector('.canvas-media-node')?.className || '',
    text: node.textContent || '',
  }));
  if (!busyExists || !busyState.className.includes('is-busy') || !busyState.text.includes('生成中')) {
    throw new Error(`video card busy state missing: ${JSON.stringify({
      busyExists,
      busyState,
      generateSeen,
      generateButtonState,
    })}`);
  }
  if (!generateSeen) throw new Error('generation request was not triggered');
  await page.screenshot({path: screenshot, fullPage: false});
  releaseGenerate();
  await page.waitForFunction(
    (id) => !document.querySelector(`[data-id="media-${id}"] .canvas-media-busy-mask`),
    {timeout: 15000},
    nodeId,
  );
  console.log(JSON.stringify({ok: true, nodeId, generateSeen, busyState, screenshot}, null, 2));
} finally {
  releaseGenerate?.();
  await browser.close();
  await api(`/api/projects/${project.id}`, {method: 'DELETE'}).catch(() => {});
}
