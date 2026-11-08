import path from 'node:path';
import fs from 'node:fs/promises';
import puppeteer from 'puppeteer-core';


const apiBase = 'http://127.0.0.1:8890';
const webBase = 'http://127.0.0.1:8891';
const root = path.resolve(import.meta.dirname, '..', '..');
const projectTitle = `图片版本切换回归-${Date.now()}`;
const nodeId = 'image-version-regression';
const imageAName = `version-regression-a-${Date.now()}.png`;
const imageBName = `version-regression-b-${Date.now()}.png`;
const imageA = `/uploads/${imageAName}`;
const imageB = `/uploads/${imageBName}`;
const uploadsDir = path.join(root, 'data', 'uploads');
const png = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk'
  + '+A8AAQUBAScY42YAAAAASUVORK5CYII=',
  'base64',
);
await fs.mkdir(uploadsDir, {recursive: true});
await fs.writeFile(path.join(uploadsDir, imageAName), png);
await fs.writeFile(path.join(uploadsDir, imageBName), png);

async function api(pathname, options = {}) {
  const response = await fetch(apiBase + pathname, {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(`${response.status} ${payload.error || JSON.stringify(payload)}`);
  }
  return payload;
}

const created = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title: projectTitle}),
});
const projectId = created.id;

await api(`/api/projects/${projectId}/nodes`, {
  method: 'PUT',
  body: JSON.stringify({
    episodeId: 'EP001',
    nodes: [{
      id: nodeId,
      type: 'image',
      title: '段3 · 同 URL 历史版本',
      metadata: {
        prompt: '测试图片历史版本',
        status: 'done',
        content: imageA,
        source: {type: 'generated'},
        params: {
          ratio: '16:9',
          activeVersionId: 'image:v2',
          mediaVersionIndex: 1,
          mediaVersions: [
            {versionId: 'image:v1', url: imageA, sourceType: 'generated'},
            {versionId: 'image:v2', url: imageA, sourceType: 'generated'},
            {versionId: 'image:v3', url: imageB, sourceType: 'generated'},
          ],
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
  await page.waitForSelector('.canvas-project-btn', {timeout: 30_000});
  await page.click('.canvas-project-btn');
  await page.waitForSelector('.canvas-project-menu', {timeout: 10_000});
  const selected = await page.evaluate((title) => {
    const row = [...document.querySelectorAll('.canvas-project-row')]
      .find((item) => (item.textContent || '').includes(title));
    const button = row?.querySelector('.canvas-project-item-main');
    if (!(button instanceof HTMLElement)) return false;
    button.click();
    return true;
  }, projectTitle);
  if (!selected) {
    const visible = await page.evaluate(() => [...document.querySelectorAll('.canvas-project-row')]
      .map((item) => (item.textContent || '').trim())
      .slice(0, 60));
    throw new Error(`回归项目未出现在项目菜单；可见：${visible.join(' | ')}`);
  }
  await page.waitForSelector(`[data-id="media-${nodeId}"] .canvas-media-version-nav`, {
    timeout: 60_000,
  });
  await new Promise((resolve) => setTimeout(resolve, 500));
}

async function readVersion(page) {
  return page.evaluate((id) => {
    const shell = document.querySelector(`[data-id="media-${id}"]`);
    const card = shell?.querySelector('.canvas-media-card');
    const img = card?.querySelector('img');
    return {
      label: shell?.querySelector('.canvas-media-version-nav')?.textContent?.trim() || '',
      src: img?.getAttribute('src') || img?.currentSrc || card?.getAttribute('data-media-src') || '',
      srcAttr: img?.getAttribute('src') || '',
      naturalWidth: img?.naturalWidth || 0,
      cardHtml: card?.innerHTML?.slice(0, 1200) || '',
    };
  }, nodeId);
}

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.goto(webBase, {waitUntil: 'domcontentloaded', timeout: 60_000});
  await selectProject(page);

  const before = await readVersion(page);
  if (before.label !== '2/3') {
    throw new Error(`初始版本应为 2/3，实际 ${before.label}`);
  }
  const nextSelector = `[data-id="media-${nodeId}"] [aria-label="下一个生成版本"]`;
  await page.click(nextSelector);
  await page.waitForFunction(
    (id) => document.querySelector(`[data-id="media-${id}"] .canvas-media-version-nav`)
      ?.textContent?.includes('3/3'),
    {timeout: 10_000},
    nodeId,
  );
  await page.waitForFunction(
    (id) => {
      const img = document.querySelector(`[data-id="media-${id}"] .canvas-media-card img`);
      return !!img?.getAttribute('src');
    },
    {timeout: 10_000},
    nodeId,
  );
  const afterFirst = await readVersion(page);
  if (!afterFirst.srcAttr.includes('version-regression-b')) {
    console.log('AFTER_FIRST_DEBUG', JSON.stringify(afterFirst, null, 2));
    throw new Error(`下一版未切到第三张图：${afterFirst.src || afterFirst.cardHtml}`);
  }

  await new Promise((resolve) => setTimeout(resolve, 1200));
  await page.reload({waitUntil: 'domcontentloaded', timeout: 60_000});
  await selectProject(page);
  const afterRefresh = await readVersion(page);
  if (
    afterRefresh.label !== '3/3'
    || !afterRefresh.src.includes('version-regression-b')
  ) {
    throw new Error(`刷新后版本未持久化：${JSON.stringify(afterRefresh)}`);
  }

  const persisted = await api(`/api/projects/${projectId}`);
  const savedNode = persisted.payload.nodes.find((node) => node.id === nodeId);
  if (
    savedNode?.metadata?.params?.activeVersionId !== 'image:v3'
    || savedNode?.metadata?.params?.mediaVersionIndex !== 2
  ) {
    throw new Error(`后端版本指针未落库：${JSON.stringify(savedNode?.metadata?.params)}`);
  }
  console.log(JSON.stringify({ok: true, projectId, before, afterFirst, afterRefresh}, null, 2));
} finally {
  await browser.close();
  await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
  await fs.unlink(path.join(uploadsDir, imageAName)).catch(() => {});
  await fs.unlink(path.join(uploadsDir, imageBName)).catch(() => {});
}
