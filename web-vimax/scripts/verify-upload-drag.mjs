import fs from 'node:fs/promises';
import path from 'node:path';
import puppeteer from 'puppeteer-core';


const apiBase = 'http://127.0.0.1:8890';
const webBase = 'http://127.0.0.1:8891';
const root = path.resolve(import.meta.dirname, '..', '..');
const password = process.env.CANVAS_PASSWORD || 'TmpCanvas2026!';
const loginResponse = await fetch(`${apiBase}/api/auth/login`, {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({username: 'admin', password, tenant: 'owner'}),
});
if (!loginResponse.ok) throw new Error(`login failed: ${loginResponse.status}`);
const sessionCookie = (loginResponse.headers.get('set-cookie') || '').split(';', 1)[0];
if (!sessionCookie) throw new Error('missing canvas session');

async function api(pathname, options = {}) {
  const response = await fetch(apiBase + pathname, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Cookie: sessionCookie,
      ...(options.headers || {}),
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(`${response.status} ${payload.error || JSON.stringify(payload)}`);
  }
  return payload;
}

const created = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title: '上传拖放回归测试'}),
});
const projectId = created.id;
const projectTitle = String(created.title || '上传拖放回归测试');
let uploadedUrl = '';

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

async function openProject(page) {
  await page.goto(
    `${webBase}/?ep=EP001#${encodeURIComponent(projectId)}`,
    {waitUntil: 'domcontentloaded', timeout: 60000},
  );
  await page.waitForSelector('.canvas-stage', {timeout: 60000});
}

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  const [cookieName, ...cookieValueParts] = sessionCookie.split('=');
  await page.setCookie({
    name: cookieName,
    value: cookieValueParts.join('='),
    domain: '127.0.0.1',
    path: '/',
    httpOnly: true,
    sameSite: 'Lax',
  });
  await openProject(page);

  const pngBase64 = (
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk'
    + '+A8AAQUBAScY42YAAAAASUVORK5CYII='
  );

  const internalDrag = await page.evaluate((base64) => {
    const stage = document.querySelector('.canvas-stage');
    if (!(stage instanceof HTMLElement)) return false;
    const bytes = Uint8Array.from(atob(base64), (value) => value.charCodeAt(0));
    const transfer = new DataTransfer();
    transfer.items.add(new File([bytes], 'internal-only.png', {type: 'image/png'}));
    transfer.setData('application/x-vimax-canvas', '1');
    const rect = stage.getBoundingClientRect();
    stage.dispatchEvent(new DragEvent('dragenter', {
      bubbles: true,
      cancelable: true,
      clientX: rect.left + rect.width / 2,
      clientY: rect.top + rect.height / 2,
      dataTransfer: transfer,
    }));
    return true;
  }, pngBase64);
  if (!internalDrag) throw new Error('找不到画布拖放区域');
  await new Promise((resolve) => setTimeout(resolve, 100));
  if (await page.$('.canvas-drop-overlay')) {
    throw new Error('内部拖拽错误触发了文件上传遮罩');
  }

  await page.evaluate((base64) => {
    const stage = document.querySelector('.canvas-stage');
    if (!(stage instanceof HTMLElement)) throw new Error('找不到画布拖放区域');
    const bytes = Uint8Array.from(atob(base64), (value) => value.charCodeAt(0));
    const transfer = new DataTransfer();
    transfer.items.add(new File([bytes], 'drag-upload-regression.png', {type: 'image/png'}));
    window.__uploadDragTransfer = transfer;
    const rect = stage.getBoundingClientRect();
    stage.dispatchEvent(new DragEvent('dragenter', {
      bubbles: true,
      cancelable: true,
      clientX: rect.left + rect.width / 2,
      clientY: rect.top + rect.height / 2,
      dataTransfer: transfer,
    }));
  }, pngBase64);
  await page.waitForSelector('.canvas-drop-overlay', {timeout: 5000});

  await page.evaluate(() => {
    const stage = document.querySelector('.canvas-stage');
    if (!(stage instanceof HTMLElement)) throw new Error('找不到画布拖放区域');
    stage.dispatchEvent(new DragEvent('dragleave', {
      bubbles: true,
      cancelable: true,
      dataTransfer: window.__uploadDragTransfer,
    }));
  });
  await page.waitForFunction(
    () => !document.querySelector('.canvas-drop-overlay'),
    {timeout: 5000},
  );

  await page.evaluate(() => {
    const stage = document.querySelector('.canvas-stage');
    if (!(stage instanceof HTMLElement)) throw new Error('找不到画布拖放区域');
    const rect = stage.getBoundingClientRect();
    stage.dispatchEvent(new DragEvent('dragenter', {
      bubbles: true,
      cancelable: true,
      clientX: rect.left + rect.width / 2,
      clientY: rect.top + rect.height / 2,
      dataTransfer: window.__uploadDragTransfer,
    }));
    stage.dispatchEvent(new DragEvent('drop', {
      bubbles: true,
      cancelable: true,
      clientX: rect.left + rect.width / 2,
      clientY: rect.top + rect.height / 2,
      dataTransfer: window.__uploadDragTransfer,
    }));
  });

  await new Promise((resolve) => setTimeout(resolve, 2000));
  const dropState = await page.evaluate(() => ({
    overlay: !!document.querySelector('.canvas-drop-overlay'),
    queue: !!document.querySelector('.canvas-upload-queue'),
    toast: document.querySelector('.canvas-toast')?.textContent || '',
    names: [...document.querySelectorAll('.canvas-media-head-name')]
      .map((node) => node.textContent || ''),
  }));
  console.log('DROP_STATE', JSON.stringify(dropState));

  await page.waitForFunction(
    () => !document.querySelector('.canvas-drop-overlay')
      && !document.querySelector('.canvas-upload-queue'),
    {timeout: 60000},
  );
  await page.waitForFunction(
    () => [...document.querySelectorAll('.canvas-media-head-name')]
      .some((node) => (node.textContent || '').includes('drag-upload-regression')),
    {timeout: 30000},
  );

  const project = await api(`/api/projects/${projectId}`);
  const uploadedNode = (project.payload?.nodes || []).find(
    (node) => node.title === 'drag-upload-regression',
  );
  if (!uploadedNode?.metadata?.content) {
    throw new Error('拖放上传节点未落库');
  }
  uploadedUrl = uploadedNode.metadata.content;
  console.log(JSON.stringify({
    ok: true,
    projectId,
    uploadedNodeId: uploadedNode.id,
    uploadedUrl,
  }, null, 2));
} finally {
  await browser.close();
  await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
  if (uploadedUrl.startsWith('/uploads/')) {
    await fs.unlink(path.join(
      root,
      'data',
      'tenants',
      'owner',
      'uploads',
      path.basename(uploadedUrl),
    )).catch(() => {});
  }
}
