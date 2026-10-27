import fs from 'node:fs/promises';
import path from 'node:path';
import puppeteer from 'puppeteer-core';

const apiBase = 'http://127.0.0.1:8890';
const feedPath = 'C:\\Users\\liuch\\Doubao\\chats\\2026-10-15\\new-chat\\铁西硬汉_第01集_投喂载荷.json';
const tempDir = path.resolve(import.meta.dirname, '..', '..', '.screens');
const validLayoutPath = path.join(tempDir, 'valid-layout-smoke-20261025.json');
const title = '布局导入格式拦截测试';
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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
  body: JSON.stringify({title}),
});
const projectId = created.id;
await api(`/api/projects/${projectId}/nodes`, {
  method: 'PUT',
  body: JSON.stringify({
    nodes: [
      {
        id: 'layout-a', type: 'text', title: '布局节点 A', position: {x: 100, y: 100},
        metadata: {prompt: 'A', status: 'idle', refs: []},
      },
      {
        id: 'layout-b', type: 'text', title: '布局节点 B', position: {x: 400, y: 100},
        metadata: {prompt: 'B', status: 'idle', refs: []},
      },
    ],
  }),
});
await fs.mkdir(tempDir, {recursive: true});
await fs.writeFile(validLayoutPath, JSON.stringify({
  kind: 'drama-canvas-layout',
  version: 1,
  positions: {'media-layout-a': {x: 240, y: 260}},
  userEdges: [],
  deletedAuto: [],
}, null, 2), 'utf8');

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});
try {
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
  if (!selected) throw new Error('布局测试项目未出现在菜单');
  await page.waitForSelector('.react-flow', {timeout: 60000});

  const openLayoutImport = async () => {
    await page.evaluate(() => {
      const button = [...document.querySelectorAll('.canvas-header-tools button')]
        .find((item) => (item.textContent || '').trim().startsWith('布局'));
      if (button instanceof HTMLElement) button.click();
    });
    await page.waitForSelector('.canvas-header-menu', {timeout: 10000});
  };

  await openLayoutImport();
  const feedInput = await page.$('input[type="file"][accept="application/json,.json"]');
  if (!feedInput) throw new Error('布局文件输入不存在');
  await feedInput.uploadFile(feedPath);
  await page.waitForSelector('.canvas-toast', {timeout: 10000});
  let toast = await page.$eval('.canvas-toast', (node) => node.textContent || '');
  if (!toast.includes('这是投喂载荷') || !toast.includes('导入投喂载荷')) {
    throw new Error(`投喂载荷未被明确拦截：${toast}`);
  }
  await page.click('.canvas-toast');

  await openLayoutImport();
  const layoutInput = await page.$('input[type="file"][accept="application/json,.json"]');
  await layoutInput.uploadFile(validLayoutPath);
  await page.waitForSelector('.canvas-toast', {timeout: 10000});
  toast = await page.$eval('.canvas-toast', (node) => node.textContent || '');
  if (!toast.includes('布局已导入')) throw new Error(`有效布局未导入：${toast}`);
  await sleep(600);

  const move = await page.$eval('[data-id="media-layout-a"]', (node) => {
    const style = getComputedStyle(node);
    return {transform: style.transform, left: style.left, top: style.top};
  });
  console.log(JSON.stringify({ok: true, projectId, rejectedFeed: true, acceptedLayout: true, move}, null, 2));
} finally {
  await browser.close();
  await fs.unlink(validLayoutPath).catch(() => {});
  await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
}
