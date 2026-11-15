import {mkdirSync} from 'node:fs';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import puppeteer from 'puppeteer-core';

const base = (
  process.env.CANVAS_BASE
  || process.env.CANVAS_BASE_URL
  || 'http://127.0.0.1:8890'
).replace(/\/$/, '');
const username = process.env.CANVAS_USER || process.env.CANVAS_USERNAME || 'admin';
const password = process.env.CANVAS_PASSWORD || '';
const tenantId = process.env.CANVAS_TENANT_ID || '';
const projectId = process.env.PROJECT_ID || 'gk6woLnGjh998UeNwvhqYG';
const episodeId = process.env.EPISODE_ID || 'EP001';
const minMediaNodes = Math.max(1, Number(process.env.MIN_MEDIA_NODES || 50));
const requireHistory = process.env.REQUIRE_VERSION_HISTORY !== '0';
const chromePath = process.env.CHROME_PATH
  || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const artifacts = fileURLToPath(new URL('../artifacts/', import.meta.url));
mkdirSync(artifacts, {recursive: true});

const browser = await puppeteer.launch({
  executablePath: chromePath,
  headless: true,
  args: [
    '--no-sandbox',
    '--disable-gpu',
    '--no-proxy-server',
    '--window-size=1440,900',
  ],
});

const page = await browser.newPage();
const pageErrors = [];
const consoleErrors = [];
page.on('pageerror', (error) => pageErrors.push(error.message));
page.on('console', (message) => {
  if (message.type() === 'error') {
    const text = message.text();
    const sourceUrl = message.location()?.url || '';
    if (
      !text.includes('favicon')
      && !sourceUrl.includes('/uploads/fixture-')
    ) {
      consoleErrors.push(text);
    }
  }
});

let mockTasks = [];
await page.setRequestInterception(true);
page.on('request', async (request) => {
  const url = new URL(request.url());
  if (
    url.pathname === '/api/generation-tasks'
    && mockTasks.length > 0
  ) {
    await request.respond({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify({tasks: mockTasks}),
    });
    return;
  }
  await request.continue();
});

async function loginIfNeeded() {
  const passwordInput = await page.$('input[type="password"]');
  if (!passwordInput) return;
  if (!password) {
    throw new Error(
      'CANVAS_PASSWORD is required because the target page shows a login form',
    );
  }
  await page.evaluate(async ({username, password}) => {
    const response = await fetch('/api/auth/login', {
      method: 'POST',
      credentials: 'include',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username, password}),
    });
    if (!response.ok) {
      throw new Error(
        `login failed HTTP ${response.status}: ${await response.text()}`,
      );
    }
  }, {username, password});
  const session = await page.evaluate(async () => {
    const response = await fetch('/api/auth/session', {
      credentials: 'include',
    });
    return {status: response.status, body: await response.json()};
  });
  if (session.status !== 200 || !session.body?.authenticated) {
    throw new Error(`login session not established: ${JSON.stringify(session)}`);
  }
}

async function openProject() {
  await page.goto(
    `${base}/?ep=${encodeURIComponent(episodeId)}#${encodeURIComponent(projectId)}`,
    {waitUntil: 'networkidle2', timeout: 60_000},
  );
  await loginIfNeeded();
  if (!page.url().includes(`#${projectId}`)) {
    await page.goto(
      `${base}/?ep=${encodeURIComponent(episodeId)}#${encodeURIComponent(projectId)}`,
      {waitUntil: 'networkidle2', timeout: 60_000},
    );
  }
  try {
    await page.waitForFunction(
      (minimum) => (
        document.querySelectorAll(
          '.react-flow__node-media',
        ).length >= minimum
      ),
      {timeout: 90_000},
      minMediaNodes,
    );
  } catch (error) {
    const diagnostic = await page.evaluate(() => ({
      url: location.href,
      title: document.title,
      passwordForm: Boolean(document.querySelector('input[type="password"]')),
      cards: document.querySelectorAll('.react-flow__node-media').length,
      flowNodes: document.querySelectorAll('.react-flow__node').length,
      errors: [...document.querySelectorAll(
        '[role="alert"],.error,.canvas-error,.save-error',
      )].map((node) => node.textContent?.trim()).filter(Boolean).slice(0, 10),
      body: (document.body.textContent || '').trim().slice(0, 1200),
    }));
    console.error('OPEN_PROJECT_DIAGNOSTIC', JSON.stringify(diagnostic, null, 2));
    await page.screenshot({
      path: join(artifacts, 'industrial-canvas-open-failure.png'),
      fullPage: false,
    });
    throw error;
  }
  await sleep(500);
}

async function visibleControlAudit(scopeSelector) {
  return page.evaluate((selector) => {
    const scope = document.querySelector(selector);
    if (!scope) return {missing: true, overflow: [], overlapping: []};
    const nodes = [...scope.querySelectorAll(
      'button,input,textarea,[role="button"],[role="textbox"]',
    )].filter((node) => {
      const style = getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return (
        style.display !== 'none'
        && style.visibility !== 'hidden'
        && rect.width > 0
        && rect.height > 0
      );
    });
    const overflow = nodes
      .map((node) => {
        const rect = node.getBoundingClientRect();
        return {
          text: (node.textContent || node.getAttribute('aria-label') || '')
            .trim()
            .slice(0, 80),
          outsideViewport: (
            rect.left < -1
            || rect.top < -1
            || rect.right > innerWidth + 1
            || rect.bottom > innerHeight + 1
          ),
          textOverflow: (
            node.scrollWidth > node.clientWidth + 2
            && getComputedStyle(node).overflowX === 'visible'
          ),
        };
      })
      .filter((item) => item.outsideViewport || item.textOverflow);
    const overlapping = [];
    const actionable = nodes.filter((node) => {
      const rect = node.getBoundingClientRect();
      return rect.width >= 20 && rect.height >= 20;
    });
    for (let left = 0; left < actionable.length; left += 1) {
      for (let right = left + 1; right < actionable.length; right += 1) {
        const a = actionable[left].getBoundingClientRect();
        const b = actionable[right].getBoundingClientRect();
        const width = Math.min(a.right, b.right) - Math.max(a.left, b.left);
        const height = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
        if (width > 3 && height > 3) {
          overlapping.push({
            left: left,
            right: right,
            width: Number(width.toFixed(1)),
            height: Number(height.toFixed(1)),
          });
        }
      }
    }
    return {missing: false, overflow, overlapping};
  }, scopeSelector);
}

try {
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.goto(base, {waitUntil: 'networkidle2', timeout: 60_000});
  await loginIfNeeded();
  await openProject();

  const nodeIds = await page.evaluate(() => (
    [...document.querySelectorAll('.react-flow__node-media')]
      .map((node) => (
        (node.getAttribute('data-id') || '').replace(/^media-/, '')
      ))
      .filter(Boolean)
      .slice(0, 4)
  ));
  if (nodeIds.length < 4) throw new Error('fewer than four media nodes');

  const now = new Date().toISOString();
  mockTasks = [
    {
      id: 'mock-recover',
      project_id: projectId,
      node_id: nodeIds[0],
      title: '恢复本地结果',
      kind: 'image',
      status: 'succeeded',
      delivery_status: 'unrecoverable',
      error: '结果无法恢复：本地文件和供应商结果地址均不存在',
      created_at: now,
      updated_at: now,
    },
    {
      id: 'mock-provider',
      project_id: projectId,
      node_id: nodeIds[1],
      title: '检查供应商',
      kind: 'image',
      status: 'failed',
      phase: '生成失败',
      error: '供应商账户欠费，余额不足',
      created_at: now,
      updated_at: now,
    },
    {
      id: 'mock-uncertain',
      project_id: projectId,
      node_id: nodeIds[2],
      title: '结果待确认',
      kind: 'video',
      status: 'interrupted',
      phase: '结果待确认',
      error: '提交未确认，等待供应商回执',
      outcome: {
        phase: 'unknown',
        regenerate: 'confirm',
        reason: 'provider_outcome_unknown',
      },
      created_at: now,
      updated_at: now,
    },
    {
      id: 'mock-local',
      project_id: projectId,
      node_id: nodeIds[3],
      title: '待补素材',
      kind: 'audio',
      status: 'failed',
      phase: '本地校验失败',
      error: '参考素材未就绪：角色图缺失',
      error_kind: 'local_validation',
      created_at: now,
      updated_at: now,
    },
  ];

  await page.reload({waitUntil: 'networkidle2', timeout: 60_000});
  await page.waitForSelector('.canvas-task-center-trigger', {timeout: 30_000});
  await page.click('.canvas-task-center-trigger');
  await page.waitForSelector('.canvas-task-center', {timeout: 10_000});
  const recoveryLabels = await page.$$eval(
    '.canvas-task-recovery-action',
    (buttons) => buttons.map((button) => button.textContent?.trim() || ''),
  );
  console.log('RECOVERY_LABELS', JSON.stringify(recoveryLabels));
  for (const expected of ['恢复本地结果', '检查供应商', '核对并结束', '重新生成']) {
    if (!recoveryLabels.includes(expected)) {
      throw new Error(`missing recovery action: ${expected}`);
    }
  }
  const taskLayout = await visibleControlAudit('.canvas-task-center');
  await page.screenshot({
    path: join(artifacts, 'industrial-canvas-desktop.png'),
    fullPage: false,
  });
  const closeTaskCenter = await page.$(
    '.canvas-task-center [aria-label="关闭任务中心"]',
  );
  if (closeTaskCenter) {
    await closeTaskCenter.click();
  } else {
    await page.keyboard.press('Escape');
  }
  await page.waitForFunction(
    () => !document.querySelector('.canvas-task-center'),
    {timeout: 5_000},
  );

  const imageCard = await page.$('.react-flow__node-media');
  if (!imageCard) throw new Error('media node missing');
  const cardPoint = await imageCard.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return {
      x: rect.x + Math.min(36, rect.width / 3),
      y: rect.y + Math.min(28, rect.height / 3),
    };
  });
  await page.mouse.click(cardPoint.x, cardPoint.y);
  await page.waitForSelector('.jim-prompt', {timeout: 10_000});
  const composition = await page.$eval('.jim-prompt', (editor) => {
    const original = (
      editor.dataset.promptValue
      || editor.textContent
      || ''
    );
    editor.focus();
    editor.dispatchEvent(new CompositionEvent('compositionstart', {bubbles: true}));
    editor.textContent = original;
    editor.dispatchEvent(new InputEvent('input', {
      bubbles: true,
      inputType: 'insertCompositionText',
      data: original,
    }));
    editor.dispatchEvent(new CompositionEvent('compositionend', {
      bubbles: true,
      data: original,
    }));
    return {
      original,
      final: editor.dataset.promptValue || editor.textContent || '',
      composing: false,
    };
  });
  const modalLayout = await visibleControlAudit('.jim-bar');
  await page.keyboard.press('Escape');

  let historyMenus = 0;
  const historyButtonCount = await page.$$eval(
    '.canvas-media-version-count',
    (buttons) => buttons.length,
  );
  for (const index of [0, 1].slice(0, historyButtonCount)) {
    const clicked = await page.evaluate((buttonIndex) => {
      const button = document.querySelectorAll(
        '.canvas-media-version-count',
      )[buttonIndex];
      if (!(button instanceof HTMLButtonElement)) return false;
      button.click();
      return true;
    }, index);
    if (!clicked) break;
    await page.waitForSelector('.canvas-media-version-pop', {timeout: 5_000});
    historyMenus += 1;
    await page.keyboard.press('Escape');
  }
  if (requireHistory && historyMenus === 0) {
    throw new Error('no image/video version history control found');
  }

  const pane = await page.$('.react-flow__pane');
  if (!pane) throw new Error('canvas pane missing');
  const paneBox = await pane.boundingBox();
  if (!paneBox) throw new Error('canvas pane has no box');
  await page.keyboard.down('Space');
  const panStarted = Date.now();
  for (let index = 0; index < 10; index += 1) {
    const x = paneBox.x + paneBox.width / 2;
    const y = paneBox.y + paneBox.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(
      x + (index % 2 === 0 ? 48 : -48),
      y + (index % 3 === 0 ? 22 : -18),
      {steps: 4},
    );
    await page.mouse.up();
    await page.mouse.wheel({deltaY: index % 2 === 0 ? -90 : 90});
    await sleep(1000);
  }
  await page.keyboard.up('Space');
  const panSeconds = (Date.now() - panStarted) / 1000;

  await page.setViewport({width: 390, height: 844, deviceScaleFactor: 1});
  await page.reload({waitUntil: 'networkidle2', timeout: 60_000});
  await page.waitForFunction(
    (minimum) => (
      document.querySelectorAll(
        '.react-flow__node-media',
      ).length >= minimum
    ),
    {timeout: 90_000},
    Math.min(5, minMediaNodes),
  );
  const mobileLayout = await page.evaluate(() => ({
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: innerWidth,
    horizontalOverflow: document.documentElement.scrollWidth > innerWidth + 1,
  }));
  await page.screenshot({
    path: join(artifacts, 'industrial-canvas-mobile.png'),
    fullPage: false,
  });

  const result = {
    projectId,
    episodeId,
    mediaNodes: await page.$$eval(
      '.react-flow__node-media',
      (items) => items.length,
    ),
    recoveryLabels,
    historyMenus,
    composition,
    panSeconds: Number(panSeconds.toFixed(2)),
    taskLayout,
    modalLayout,
    mobileLayout,
    pageErrors,
    consoleErrors,
  };
  console.log(JSON.stringify(result, null, 2));

  if (!composition.final.includes(composition.original)) {
    throw new Error('IME composition changed prompt content');
  }
  if (panSeconds < 9.5) throw new Error('pan performance window was too short');
  if (taskLayout.missing || modalLayout.missing) {
    throw new Error('layout audit target missing');
  }
  if (
    taskLayout.overflow.length
    || modalLayout.overflow.length
    || taskLayout.overlapping.length
    || modalLayout.overlapping.length
  ) {
    throw new Error('overlapping or overflowing controls detected');
  }
  if (mobileLayout.horizontalOverflow) {
    throw new Error('mobile page has horizontal overflow');
  }
  if (pageErrors.length || consoleErrors.length) {
    throw new Error('browser errors detected');
  }
} finally {
  await browser.close();
}
