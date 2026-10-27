import path from 'node:path';
import puppeteer from 'puppeteer-core';

const apiBase = 'http://127.0.0.1:8890';
const projectTitle = `二次生成反馈 ${Date.now()}`;
const initialUrl = '/uploads/generated-1792951513-2263a277d7.png';
const generatedUrl = '/uploads/generated-1792927553-2f36faf33e.png';
const screenshot = path.resolve('../data/regenerate-feedback-20261026.png');

async function api(pathname, options = {}) {
  const response = await fetch(apiBase + pathname, {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${response.status} ${JSON.stringify(payload)}`);
  return payload;
}

const created = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title: projectTitle}),
});
const projectId = created.id;
const nodeId = `feedback-node-${Date.now()}`;
await api(`/api/projects/${projectId}/nodes`, {
  method: 'PUT',
  body: JSON.stringify({
    nodes: [{
      id: nodeId,
      type: 'image',
      title: '角色｜二次生成反馈（图99）',
      position: {x: 120, y: 120},
      metadata: {
        prompt: '测试二次生成即时反馈',
        status: 'done',
        content: initialUrl,
        model: 'doubao-seedream-5-0-pro',
        params: {
          provider: 'ark',
          model: 'doubao-seedream-5-0-pro',
          ratio: '1:1',
          clarity: '1.5K',
          size: '1536x1536',
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

const errors = [];
try {
  const page = await browser.newPage();
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(`console: ${message.text()}`);
  });
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.setRequestInterception(true);
  page.on('request', (request) => {
    const target = request.url();
    if (request.method() === 'POST' && target.includes(`/nodes/${nodeId}/generate`)) {
      void request.respond({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({url: generatedUrl}),
      });
      return;
    }
    void request.continue();
  });

  await page.goto(apiBase, {waitUntil: 'networkidle0', timeout: 60000});
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
  if (!selected) throw new Error('temporary project not found in menu');

  const mediaSelector = `[data-id="media-${nodeId}"] .canvas-media-card`;
  await page.waitForSelector(mediaSelector, {timeout: 60000});
  await page.click(mediaSelector);
  await page.waitForSelector('.jim-bar .jim-gen', {timeout: 30000});
  await page.click('.jim-bar .jim-gen');

  await page.waitForFunction(
    () => (document.querySelector('.canvas-toast')?.textContent || '').includes('重新生成已提交'),
    {timeout: 10000},
  );
  await page.waitForFunction(
    () => (document.querySelector('.canvas-toast')?.textContent || '').includes('图片生成完成'),
    {timeout: 15000},
  );
  await page.waitForFunction(
    (id) => {
      const node = document.querySelector(`[data-id="media-${id}"] .canvas-media-card`);
      return !!node && !node.querySelector('.canvas-media-busy-mask');
    },
    {timeout: 15000},
    nodeId,
  );

  const project = await api(`/api/projects/${projectId}`);
  const node = project.payload.nodes.find((item) => item.id === nodeId);
  if (node?.metadata?.status !== 'done' || node?.metadata?.content !== generatedUrl) {
    throw new Error(`node did not update without refresh: ${JSON.stringify(node?.metadata)}`);
  }
  await page.screenshot({path: screenshot, fullPage: false});
  if (errors.length) throw new Error(errors.join('\n'));
  console.log(JSON.stringify({
    ok: true,
    projectId,
    nodeId,
    status: node.metadata.status,
    content: node.metadata.content,
    versions: node.metadata.params?.mediaVersions?.length || 0,
    screenshot,
  }, null, 2));
} finally {
  await browser.close();
  await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
}
