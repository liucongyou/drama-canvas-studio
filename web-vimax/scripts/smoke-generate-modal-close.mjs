import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import puppeteer from 'puppeteer-core';

const apiBase = 'http://127.0.0.1:8890';
const root = path.resolve(import.meta.dirname, '..', '..');
const suffix = Date.now().toString(36);
const providerId = `modal-provider-${suffix}`;
const modelId = `modal-video-${suffix}`;
const title = '生成按钮关闭弹窗测试';
let mockBase = '';

const mock = http.createServer((request, response) => {
  const url = new URL(request.url || '/', 'http://127.0.0.1');
  if (request.method === 'POST' && url.pathname === '/api/v1/contents/generations/tasks') {
    request.resume();
    response.writeHead(200, {'Content-Type': 'application/json'});
    response.end(JSON.stringify({id: 'modal-task-1'}));
    return;
  }
  if (request.method === 'GET' && url.pathname === '/api/v1/contents/generations/tasks/modal-task-1') {
    response.writeHead(200, {'Content-Type': 'application/json'});
    response.end(JSON.stringify({status: 'succeeded', content: {video_url: `${mockBase}/result.mp4`}}));
    return;
  }
  if (request.method === 'GET' && url.pathname === '/result.mp4') {
    response.writeHead(200, {'Content-Type': 'video/mp4'});
    response.end(Buffer.from('modal-mp4'));
    return;
  }
  response.writeHead(404, {'Content-Type': 'application/json'});
  response.end('{}');
});
await new Promise((resolve) => mock.listen(0, '127.0.0.1', resolve));
mockBase = `http://127.0.0.1:${mock.address().port}`;

async function api(pathname, options = {}) {
  const response = await fetch(apiBase + pathname, {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${response.status} ${payload.error || JSON.stringify(payload)}`);
  return payload;
}

let projectId = '';
let generatedUrl = '';
let browser;
try {
  await api('/api/providers', {
    method: 'POST',
    body: JSON.stringify({provider: {
      id: providerId, name: '弹窗关闭本地供应商', protocol: 'openai',
      base_url: `${mockBase}/api/v1`, api_key: 'local-test', enabled: true,
    }}),
  });
  await api('/api/models', {
    method: 'POST',
    body: JSON.stringify({model: {
      id: modelId, providerId, capability: 'video', adapter: 'ark_video',
      remoteModel: 'modal-local-video', label: '弹窗关闭测试模型', badge: 'TEST',
      enabled: true,
      defaultParams: {ratio: '9:16', resolution: '480P', duration: 4},
      params: {
        ratio: {options: ['9:16']},
        resolution: {options: ['480P']},
        duration: {min: 4, max: 15, step: 1},
      },
      limits: {maxRefs: 9, maxImages: 5, maxVideos: 3, maxAudios: 3, maxPromptChars: 8000},
    }}),
  });
  const project = await api('/api/projects', {
    method: 'POST',
    body: JSON.stringify({title}),
  });
  projectId = project.id;
  await api(`/api/projects/${projectId}/nodes`, {
    method: 'PUT',
    body: JSON.stringify({nodes: [{
      id: 'modal-video-node', type: 'video', title: '生成后关闭弹窗',
      position: {x: 300, y: 120},
      metadata: {
        prompt: '镜头缓慢推进，人物转头。',
        status: 'idle',
        refs: [],
        params: {
          provider: providerId, model: modelId,
          ratio: '9:16', resolution: '480P', duration: 4,
          generateAudio: false, watermark: false,
        },
      },
    }]}),
  });

  browser = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true,
    args: ['--no-sandbox', '--disable-gpu'],
  });
  const page = await browser.newPage();
  await page.setViewport({width: 1280, height: 820, deviceScaleFactor: 1});
  await page.goto('http://127.0.0.1:8891', {waitUntil: 'networkidle0', timeout: 60000});
  await page.waitForSelector('.canvas-project-btn', {timeout: 30000});
  await page.click('.canvas-project-btn');
  await page.waitForSelector('.canvas-project-menu', {timeout: 10000});
  const selected = await page.evaluate((projectTitle) => {
    const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
      .find((item) => (item.textContent || '').includes(projectTitle));
    if (!(button instanceof HTMLElement)) return false;
    button.click();
    return true;
  }, title);
  if (!selected) throw new Error('生成弹窗测试项目未出现在菜单');
  await page.waitForSelector('[data-id="media-modal-video-node"] .canvas-media-card', {timeout: 60000});
  await page.click('[data-id="media-modal-video-node"] .canvas-media-card');
  await page.waitForSelector('.jim-bar[data-kind="video"]', {timeout: 15000});
  const generateButton = await page.$('.jim-gen');
  if (!generateButton) throw new Error('生成按钮不存在');
  await generateButton.click();
  await page.waitForSelector('.jim-bar', {hidden: true, timeout: 5000});
  await new Promise((resolve) => setTimeout(resolve, 600));
  const saved = await api(`/api/projects/${projectId}`);
  const node = saved.payload.nodes.find((item) => item.id === 'modal-video-node');
  generatedUrl = String(node?.metadata?.content || '');
  if (node?.metadata?.status !== 'generating') throw new Error(`生成状态未提交：${JSON.stringify(node?.metadata)}`);

  console.log(JSON.stringify({
    ok: true,
    projectId,
    modalClosedAfterClick: true,
    generationSubmitted: node.metadata.status === 'generating',
  }, null, 2));
} finally {
  await browser?.close().catch(() => {});
  if (projectId) await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
  await api(`/api/providers/${encodeURIComponent(providerId)}`, {method: 'DELETE'}).catch(() => {});
  if (generatedUrl.startsWith('/uploads/')) {
    await fs.unlink(path.join(root, 'data', 'uploads', path.basename(generatedUrl))).catch(() => {});
  }
  await new Promise((resolve) => mock.close(resolve));
}
