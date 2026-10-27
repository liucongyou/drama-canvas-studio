import puppeteer from 'puppeteer-core';

const base = 'http://127.0.0.1:8890';
const title = `依赖就绪界面 ${Date.now()}`;
const imageId = `readiness-image-${Date.now()}`;
const videoId = `readiness-video-${Date.now()}`;
const staleId = `readiness-stale-${Date.now()}`;
const newId = `readiness-new-${Date.now()}`;
const reusedId = `readiness-reused-${Date.now()}`;

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
await api(`/api/projects/${project.id}/nodes`, {
  method: 'PUT',
  body: JSON.stringify({
    nodes: [
      {
        id: imageId,
        type: 'image',
        title: '角色｜未生成测试（图1）',
        position: {x: 80, y: 80},
        metadata: {prompt: '测试角色', status: 'idle', content: '', params: {}},
      },
      {
        id: staleId,
        type: 'image',
        title: '角色｜提示词已更新（图2）',
        position: {x: 80, y: 520},
        metadata: {
          prompt: '更新后的源提示词',
          status: 'done',
          content: '/uploads/generated-1792927218-6f9b233e2d.png',
          promptStale: true,
          promptStaleReason: '源提示词已更新，当前复用图片仍是旧版本',
          params: {},
        },
      },
      {
        id: newId,
        type: 'image',
        title: '角色｜本集新资产（图3）',
        position: {x: 80, y: 820},
        metadata: {
          prompt: '本集新资产',
          status: 'done',
          content: '/uploads/generated-1792927218-6f9b233e2d.png',
          params: {},
        },
      },
      {
        id: reusedId,
        type: 'image',
        title: '角色｜跨集复用（图4）',
        position: {x: 420, y: 520},
        metadata: {
          prompt: '跨集复用资产',
          status: 'done',
          content: '/uploads/generated-1792927218-6f9b233e2d.png',
          reusedAsset: {
            sourceTitle: '角色｜跨集复用（图4）',
            sourceNodeId: 'source-reused-id',
          },
          params: {},
        },
      },
      {
        id: videoId,
        type: 'video',
        title: '段1 · 依赖未就绪',
        position: {x: 420, y: 80},
        metadata: {
          prompt: '参考@角色｜未生成测试（图1）。台词：无',
          status: 'idle',
          content: '',
          refs: [{nodeId: imageId, label: '角色｜未生成测试（图1）'}],
          params: {
            provider: 'ark',
            model: 'doubao-seedance-2-0',
            ratio: '9:16',
            resolution: '480P',
            duration: 5,
            refs: [{nodeId: imageId, label: '角色｜未生成测试（图1）'}],
          },
        },
      },
    ],
  }),
});

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

try {
  const page = await browser.newPage();
  page.on('pageerror', (error) => console.error('[pageerror]', error.message));
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.goto(base, {waitUntil: 'domcontentloaded', timeout: 60_000});
  await page.waitForSelector('.canvas-project-btn', {timeout: 30_000});
  await page.click('.canvas-project-btn');
  await page.waitForSelector('.canvas-project-menu-list button', {timeout: 10_000});
  await page.evaluate((projectTitle) => {
    const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
      .find((item) => (item.textContent || '').includes(projectTitle));
    if (!(button instanceof HTMLElement)) throw new Error('project entry missing');
    button.click();
  }, title);

  const card = `[data-id="media-${videoId}"] .canvas-media-card`;
  await page.waitForSelector(card, {timeout: 60_000});
  await page.waitForSelector(`[data-id="media-${staleId}"] .canvas-media-reuse-badge.is-stale`, {timeout: 30_000});
  await page.waitForSelector(`[data-id="media-${imageId}"] .canvas-media-reuse-badge.is-pending`, {timeout: 30_000});
  await page.waitForSelector(`[data-id="media-${newId}"] .canvas-media-reuse-badge.is-new`, {timeout: 30_000});
  await page.waitForSelector(`[data-id="media-${reusedId}"] .canvas-media-reuse-badge.is-shared`, {timeout: 30_000});
  const staleBadge = await page.$eval(
    `[data-id="media-${staleId}"] .canvas-media-reuse-badge.is-stale`,
    (element) => ({text: element.textContent?.trim() || '', title: element.getAttribute('title') || ''}),
  );
  const point = await page.$eval(card, (element) => {
    const rect = element.getBoundingClientRect();
    return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
  });
  await page.mouse.click(point.x, point.y);
  await page.waitForSelector('.jim-bar', {timeout: 30_000});

  const state = await page.evaluate(() => {
    const bar = document.querySelector('.jim-bar');
    if (!(bar instanceof HTMLElement)) throw new Error('modal missing');
    const generate = bar.querySelector('.jim-gen');
    const notes = [...bar.querySelectorAll('.jim-validation-notes div')]
      .map((item) => item.textContent?.trim() || '');
    return {
      generateDisabled: generate instanceof HTMLButtonElement && generate.disabled,
      generateTitle: generate instanceof HTMLElement ? generate.getAttribute('title') : '',
      notes,
    };
  });
  if (
    !state.generateDisabled
    || !state.generateTitle.includes('缺少')
    || !state.generateTitle.includes('角色｜未生成测试')
    || !state.notes.some((note) => note.includes('引用未就绪'))
  ) {
    throw new Error(`dependency readiness UI missing: ${JSON.stringify(state)}`);
  }
  await page.click('.jim-dependency-prepare-trigger');
  await page.waitForSelector('.jim-dependency-prepare-panel', {timeout: 5_000});
  const prepare = await page.evaluate(() => {
    const panel = document.querySelector('.jim-dependency-prepare-panel');
    if (!(panel instanceof HTMLElement)) throw new Error('prepare panel missing');
    const inputs = [...panel.querySelectorAll('input[type="checkbox"]')];
    const primary = panel.querySelector('.jim-dependency-prepare-foot .is-primary');
    return {
      options: inputs.length,
      checked: inputs.filter((input) => input instanceof HTMLInputElement && input.checked).length,
      primaryDisabled: primary instanceof HTMLButtonElement && primary.disabled,
      text: panel.textContent?.replace(/\s+/g, ' ').trim() || '',
    };
  });
  if (prepare.options < 1 || prepare.checked !== 0 || !prepare.primaryDisabled || !prepare.text.includes('默认不勾选')) {
    throw new Error(`prepare resolver must default to no paid selection: ${JSON.stringify(prepare)}`);
  }
  await page.$eval('.jim-dependency-prepare-item input[type="checkbox"]', (input) => input.click());
  const prepareSelected = await page.evaluate(() => {
    const panel = document.querySelector('.jim-dependency-prepare-panel');
    if (!(panel instanceof HTMLElement)) {
      return {
        panelMissing: true,
        barExists: !!document.querySelector('.jim-bar'),
        triggerExists: !!document.querySelector('.jim-dependency-prepare-trigger'),
        bodyText: document.body.innerText.slice(0, 320),
      };
    }
    const inputs = [...panel.querySelectorAll('input[type="checkbox"]')];
    const primary = panel.querySelector('.jim-dependency-prepare-foot .is-primary');
    return {
      checked: inputs.filter((input) => input instanceof HTMLInputElement && input.checked).length,
      primaryDisabled: primary instanceof HTMLButtonElement && primary.disabled,
      text: panel.textContent?.replace(/\s+/g, ' ').trim() || '',
      panelMissing: false,
    };
  });
  if (prepareSelected.checked !== 1 || prepareSelected.primaryDisabled || !prepareSelected.text.includes('已选 1 项')) {
    throw new Error(`explicit dependency selection did not update cost action: ${JSON.stringify(prepareSelected)}`);
  }

  await page.keyboard.press('Escape');
  await page.waitForSelector('.jim-bar', {hidden: true, timeout: 3_000});

  const dependencyChip = `[data-id="media-${videoId}"] .canvas-media-dependency-chip`;
  await page.waitForSelector(dependencyChip, {timeout: 10_000});
  const viewportBefore = await page.$eval('.react-flow__viewport', (element) => getComputedStyle(element).transform);
  const chipPoint = await page.$eval(dependencyChip, (element) => {
    const rect = element.getBoundingClientRect();
    return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
  });
  await page.mouse.click(chipPoint.x, chipPoint.y);
  await new Promise((resolve) => setTimeout(resolve, 520));
  const viewportAfter = await page.$eval('.react-flow__viewport', (element) => getComputedStyle(element).transform);
  if (viewportBefore === viewportAfter) {
    throw new Error(`dependency chip did not locate missing node: ${viewportBefore}`);
  }

  console.log(JSON.stringify({ok: true, staleBadge, dependencyLocated: true, prepare, prepareSelected, ...state}, null, 2));
} finally {
  await browser.close();
  await api(`/api/projects/${project.id}`, {method: 'DELETE'}).catch(() => {});
}
