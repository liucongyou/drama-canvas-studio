import fs from 'node:fs/promises';
import path from 'node:path';
import puppeteer from 'puppeteer-core';

const apiBase = 'http://127.0.0.1:8890';
const root = path.resolve(import.meta.dirname, '..', '..');
const sourceImage = path.join(root, '.screens', 'model-settings-20261025.png');
const shotPath = path.join(root, '.screens', 'local-upload-ref-20261025.png');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let uploadedUrl = '';

async function api(pathname, options = {}) {
  const response = await fetch(apiBase + pathname, {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${response.status} ${payload.error || JSON.stringify(payload)}`);
  return payload;
}

const created = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title: '本地上传引用持久化测试'}),
});
const projectId = created.id;
await api(`/api/projects/${projectId}/nodes`, {
  method: 'PUT',
  body: JSON.stringify({
    nodes: [{
      id: 'upload-target-video',
      type: 'video',
      title: '上传引用目标',
      position: {x: 300, y: 100},
      metadata: {
        prompt: '测试上传图片引用',
        status: 'idle',
        refs: [],
        params: {
          provider: 'ark',
          model: 'doubao-seedance-2-0-fast',
          ratio: '9:16',
          resolution: '480P',
          duration: 5,
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

async function selectProject(page) {
  await page.waitForSelector('.canvas-project-btn', {timeout: 30000});
  await page.click('.canvas-project-btn');
  await page.waitForSelector('.canvas-project-menu', {timeout: 10000});
  const selected = await page.evaluate(() => {
    const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
      .find((item) => (item.textContent || '').includes('本地上传引用持久化测试'));
    if (!(button instanceof HTMLElement)) return false;
    button.click();
    return true;
  });
  if (!selected) throw new Error('测试项目未出现在菜单');
  await page.waitForSelector('[data-id="media-upload-target-video"] .canvas-media-card', {timeout: 60000});
  await sleep(600);
}

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.goto('http://127.0.0.1:8891', {waitUntil: 'networkidle0', timeout: 60000});
  await selectProject(page);
  await page.click('[data-id="media-upload-target-video"] .canvas-media-card');
  await page.waitForSelector('.jim-bar[data-kind="video"]', {timeout: 15000});

  const fileInput = await page.$('.jim-prompt-area input[accept="image/*"]');
  if (!fileInput) throw new Error('图片上传 input 不存在');
  await fileInput.uploadFile(sourceImage);
  await page.waitForFunction(
    () => [...document.querySelectorAll('.canvas-media-head-name')]
      .some((node) => (node.textContent || '').includes('model-settings-20261025.png')),
    {timeout: 30000},
  );
  await sleep(1500);

  const afterUpload = await api(`/api/projects/${projectId}`);
  const uploadedNode = afterUpload.payload.nodes.find((node) => node.title === 'model-settings-20261025.png');
  if (!uploadedNode || uploadedNode.metadata?.status !== 'done' || !uploadedNode.metadata?.content) {
    throw new Error(`上传节点未落库：${JSON.stringify(uploadedNode)}`);
  }
  uploadedUrl = uploadedNode.metadata.content;
  const targetBefore = afterUpload.payload.nodes.find((node) => node.id === 'upload-target-video');
  if (!(targetBefore.metadata?.params?.refs || []).some((ref) => ref.nodeId === uploadedNode.id)) {
    throw new Error('上传节点已落库，但目标引用未持久化');
  }

  await page.reload({waitUntil: 'networkidle0', timeout: 60000});
  await selectProject(page);
  await page.click('[data-id="media-upload-target-video"] .canvas-media-card');
  await page.waitForSelector('.jim-bar[data-kind="video"]', {timeout: 15000});
  const refImage = await page.$('.jim-bar .jim-ref-thumb img');
  if (!refImage) throw new Error('刷新后引用缩略图消失');
  const imageReady = await refImage.evaluate((node) => node.complete && node.naturalWidth > 0);
  if (!imageReady) throw new Error('刷新后引用缩略图无法加载');
  await page.hover('.jim-bar .jim-ref-thumb');
  await page.waitForSelector('.jim-mention-preview img', {timeout: 5000});
  const previewReady = await page.$eval('.jim-mention-preview img', (node) => node.complete && node.naturalWidth > 0);
  if (!previewReady) throw new Error('刷新后引用缩略图悬停原图预览失败');

  await page.click('.jim-bar .jim-tool[title="@ 引用素材"]');
  await page.waitForSelector('.jim-at-pop', {timeout: 10000});
  const candidateVisible = await page.evaluate(() =>
    [...document.querySelectorAll('.jim-at-pop .jim-at-item')]
      .some((item) => (item.textContent || '').includes('model-settings-20261025.png') && item.querySelector('img')),
  );
  if (!candidateVisible) throw new Error('刷新后 @ 候选里找不到本地上传图片');
  await page.screenshot({path: shotPath, fullPage: false});
  console.log(JSON.stringify({ok: true, projectId, uploadedUrl, imageReady, candidateVisible}, null, 2));
} finally {
  await browser.close();
  await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
  if (uploadedUrl.startsWith('/uploads/')) {
    await fs.unlink(path.join(root, 'data', 'uploads', path.basename(uploadedUrl))).catch(() => {});
  }
}
