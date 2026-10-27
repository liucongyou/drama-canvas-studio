import path from 'node:path';
import puppeteer from 'puppeteer-core';

const apiBase = 'http://127.0.0.1:8890';
const projectTitle = '铁西硬汉 · 画布工作流';
const projectId = 'gk6woLnGjh998UeNwvhqYG';
const nodeId = 'node-2p76yrad1oas-bfce8b3d';
const screenshot = path.resolve('../data/ref-drag-tray-preview-20261026.png');

async function api(pathname, options = {}) {
  const response = await fetch(apiBase + pathname, {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${response.status} ${JSON.stringify(payload)}`);
  return payload;
}

const project = await api(`/api/projects/${projectId}`);
const originalNode = project.payload.nodes.find((node) => node.id === nodeId);
if (!originalNode) throw new Error('reference test node not found');
const originalParams = structuredClone(originalNode.metadata.params);

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.goto(apiBase, {waitUntil: 'domcontentloaded', timeout: 60000});
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
  await page.waitForSelector('.canvas-ep-list-body', {timeout: 30000});
  await page.evaluate(() => {
    const button = [...document.querySelectorAll('.canvas-ep-list-body button')]
      .find((item) => (item.textContent || '').includes('第09集'));
    if (button instanceof HTMLElement) button.click();
  });
  const card = `[data-id="media-${nodeId}"] .canvas-media-card`;
  await page.waitForSelector(card, {timeout: 60000});
  await page.click(card);
  await page.waitForSelector('.jim-ref-item', {timeout: 30000});
  await page.waitForFunction(
    () => document.querySelectorAll('.jim-ref-item').length >= 3,
    {timeout: 10000},
  );

  const orderBefore = await page.$$eval('.jim-ref-item .jim-ref-thumb', (nodes) =>
    nodes.map((node) => node.getAttribute('title') || ''));
  await page.evaluate(() => {
    window.__refDragDebug = [];
    for (const type of ['pointerdown', 'pointermove', 'pointerup']) {
      document.addEventListener(type, (event) => {
        const target = event.target instanceof Element ? event.target.closest('.jim-ref-item') : null;
        window.__refDragDebug.push({
          type,
          index: target instanceof HTMLElement ? target.dataset.refIndex : null,
          x: event.clientX,
          y: event.clientY,
        });
      }, true);
    }
  });
  const items = await page.$$('.jim-ref-item');
  const source = await items[0].boundingBox();
  const target = await items[2].boundingBox();
  if (!source || !target) throw new Error('reference drag boxes unavailable');
  await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
  await page.mouse.down();
  await new Promise((resolve) => setTimeout(resolve, 150));
  await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, {steps: 12});
  await new Promise((resolve) => setTimeout(resolve, 150));
  await page.waitForSelector('.jim-ref-drag-ghost', {timeout: 5000});
  const dragGhost = await page.$eval('.jim-ref-drag-ghost', (node) => ({
    text: node.textContent || '',
    imageLoaded: Array.from(node.querySelectorAll('img')).every(
      (image) => image.naturalWidth > 0 && image.naturalHeight > 0,
    ),
  }));
  await page.screenshot({path: path.resolve('../data/ref-drag-ghost-20261026.png'), fullPage: false});
  await page.mouse.up();
  await new Promise((resolve) => setTimeout(resolve, 600));
  const orderAfter = await page.$$eval('.jim-ref-item .jim-ref-thumb', (nodes) =>
    nodes.map((node) => node.getAttribute('title') || ''));
  if (orderAfter[0] === orderBefore[0] && orderAfter[2] === orderBefore[2]) {
    const debug = await page.evaluate(() => window.__refDragDebug || []);
    throw new Error(`reference drag did not reorder: ${JSON.stringify({orderBefore, orderAfter, debug})}`);
  }
  await new Promise((resolve) => setTimeout(resolve, 900));
  const persisted = await api(`/api/projects/${projectId}`);
  const persistedNode = persisted.payload.nodes.find((node) => node.id === nodeId);
  const persistedOrder = (persistedNode?.metadata?.params?.refs || [])
    .map((ref) => ref.label || ref.nodeId);
  const expectedPersistedOrder = orderAfter
    .map((title) => {
      const parts = title.split(' · ');
      return {role: parts[1] || '', label: parts[2] || ''};
    })
    .filter((item) => item.role !== '音色')
    .map((item) => item.label);
  if (JSON.stringify(persistedOrder) !== JSON.stringify(expectedPersistedOrder)) {
    throw new Error(
      `reference reorder was not persisted: ${JSON.stringify({persistedOrder, expectedPersistedOrder})}`,
    );
  }

  await page.mouse.click(10, 10);
  await page.waitForSelector('.jim-bar', {hidden: true, timeout: 10000}).catch(() => {});
  await page.click('.canvas-ep-list-rail');
  await page.waitForSelector('.canvas-ep-list.is-open', {timeout: 10000});
  await page.evaluate(() => {
    const button = [...document.querySelectorAll('.canvas-rail-tab')]
      .find((node) => (node.textContent || '').includes('资产'));
    if (button instanceof HTMLElement) button.click();
  });
  await page.waitForSelector('.canvas-asset-tray-body', {timeout: 10000});
  await page.waitForSelector('.canvas-tray-item img', {timeout: 10000});
  const trayImage = await page.$('.canvas-tray-item img');
  if (!trayImage) throw new Error('tray image not found');
  await trayImage.evaluate((node) => node.scrollIntoView({block: 'center'}));
  const trayBox = await trayImage.boundingBox();
  if (!trayBox) throw new Error('tray image box unavailable');
  await page.mouse.move(0, 0);
  await page.mouse.move(trayBox.x + trayBox.width / 2, trayBox.y + trayBox.height / 2, {steps: 8});
  await new Promise((resolve) => setTimeout(resolve, 500));
  const previewExists = await page.$('.jim-mention-preview img');
  if (!previewExists) {
    const debug = await page.evaluate((box) => ({
      box,
      pointTag: document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)?.outerHTML.slice(0, 300),
      trayCount: document.querySelectorAll('.canvas-tray-item').length,
      previewCount: document.querySelectorAll('.jim-mention-preview').length,
    }), trayBox);
    throw new Error(`tray hover preview did not open: ${JSON.stringify(debug)}`);
  }
  await page.waitForSelector('.jim-mention-preview img', {timeout: 10000});
  const preview = await page.$eval('.jim-mention-preview img', (image) => ({
    src: image.getAttribute('src') || '',
    loaded: image.naturalWidth > 0 && image.naturalHeight > 0,
  }));
  if (!preview.loaded) throw new Error(`tray preview failed: ${preview.src}`);
  await page.screenshot({path: screenshot, fullPage: false});
  console.log(JSON.stringify({
    ok: true,
    orderBefore,
    orderAfter,
    persistedOrder,
    dragGhost,
    preview,
    screenshot,
  }, null, 2));
  await new Promise((resolve) => setTimeout(resolve, 900));
} finally {
  await browser.close();
  await api(`/api/projects/${projectId}/nodes`, {
    method: 'PUT',
    body: JSON.stringify({
      nodes: [{
        id: nodeId,
        type: originalNode.type,
        title: originalNode.title,
        metadata: {params: originalParams},
      }],
    }),
  }).catch(() => {});
}
