import path from 'node:path';
import puppeteer from 'puppeteer-core';

const front = (process.env.CANVAS_FRONT || 'https://yj.pduoduo.vip').replace(/\/$/, '');
const api = (process.env.CANVAS_API || 'https://yjapi.pduoduo.vip').replace(/\/$/, '');
const username = process.env.CANVAS_USERNAME || '';
const password = process.env.CANVAS_PASSWORD || '';
const screenshot = path.resolve(
  process.env.DRAG_SCREENSHOT || '.tmp-online-video-node-drag.png',
);

if (!username || !password) {
  throw new Error('CANVAS_USERNAME and CANVAS_PASSWORD are required');
}

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--window-size=1600,1000'],
});

const page = await browser.newPage();
await page.setViewport({width: 1600, height: 1000});
const pageErrors = [];
const consoleErrors = [];
page.on('pageerror', (error) => pageErrors.push(error.message));
page.on('console', (message) => {
  if (message.type() === 'error') consoleErrors.push(message.text());
});

let projectId = '';

try {
  const loginResponse = await fetch(`${api}/api/auth/login`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username, password}),
  });
  const loginPayload = await loginResponse.json().catch(() => ({}));
  if (!loginResponse.ok) {
    throw new Error(`login failed: ${loginResponse.status} ${JSON.stringify(loginPayload)}`);
  }
  const setCookies = typeof loginResponse.headers.getSetCookie === 'function'
    ? loginResponse.headers.getSetCookie()
    : [loginResponse.headers.get('set-cookie') || ''];
  console.log('LOGIN_META', JSON.stringify({
    ok: loginResponse.ok,
    tenantId: loginPayload.tenantId || '',
    cookieAttributes: setCookies.map((value) => (
      value.split(';').slice(1).map((item) => item.trim()).join('; ')
    )),
  }, null, 2));
  const cookiePair = setCookies
    .map((value) => value.split(';', 1)[0])
    .find((value) => value.includes('='));
  if (!cookiePair) throw new Error('login did not return a session cookie');
  const [cookieName, ...cookieValueParts] = cookiePair.split('=');
  const cookieValue = cookieValueParts.join('=');
  for (const domain of new Set([new URL(api).hostname, new URL(front).hostname])) {
    await page.setCookie({
      name: cookieName,
      value: cookieValue,
      domain,
      path: '/',
      secure: (domain === new URL(api).hostname && api.startsWith('https://'))
        || (domain === new URL(front).hostname && front.startsWith('https://')),
      httpOnly: true,
      sameSite: 'Lax',
    });
  }

  await page.goto(front, {waitUntil: 'domcontentloaded', timeout: 60_000});
  const title = `视频拖拽验证 ${Date.now()}`;
  const created = await page.evaluate(async ({apiBase, projectTitle}) => {
    const response = await fetch(`${apiBase}/api/projects`, {
      method: 'POST',
      credentials: 'include',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({title: projectTitle}),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(`${response.status} ${JSON.stringify(payload)}`);
    return payload;
  }, {apiBase: api, projectTitle: title});
  projectId = String(created.id || '');
  if (!projectId) throw new Error('project id missing');

  const nodeId = `video-drag-${Date.now()}`;
  await page.evaluate(async ({apiBase, pid, id}) => {
    const response = await fetch(`${apiBase}/api/projects/${encodeURIComponent(pid)}/nodes`, {
      method: 'PUT',
      credentials: 'include',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        episodeId: 'EP001',
        nodes: [{
          id,
          type: 'video',
          title: '段1 · 视频画面拖拽验证',
          position: {x: 240, y: 180},
          metadata: {
            prompt: '视频节点整张画面可拖动',
            status: 'done',
            content: '/uploads/generated-1794580309-4ff49d9aca.mp4',
            params: {
              ratio: '9:16',
              resolution: '480P',
              duration: 12,
              count: 1,
            },
          },
        }],
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(`${response.status} ${JSON.stringify(payload)}`);
  }, {apiBase: api, pid: projectId, id: nodeId});

  await page.goto(
    `${front}/?ep=EP001#${encodeURIComponent(projectId)}`,
    {waitUntil: 'domcontentloaded', timeout: 60_000},
  );
  const selector = `.react-flow__node-media[data-id="media-${nodeId}"]`;
  await new Promise((resolve) => setTimeout(resolve, 3_000));
  const loadState = await page.evaluate(async ({apiBase, pid, nodeSelector}) => {
    const projectResponse = await fetch(
      `${apiBase}/api/projects/${encodeURIComponent(pid)}`,
      {credentials: 'include'},
    );
    const projectPayload = await projectResponse.json().catch(() => ({}));
    return {
      href: window.location.href,
      title: document.title,
      bodyText: (document.body.textContent || '').trim().slice(0, 1200),
      flowNodes: document.querySelectorAll('.react-flow__node').length,
      mediaNodes: document.querySelectorAll('.react-flow__node-media').length,
      targetNodes: document.querySelectorAll(nodeSelector).length,
      projectStatus: projectResponse.status,
      projectNodes: (projectPayload.payload?.nodes || []).length,
    };
  }, {apiBase: api, pid: projectId, nodeSelector: selector});
  console.log('LOAD_STATE', JSON.stringify(loadState, null, 2));
  if (!loadState.targetNodes) {
    await page.screenshot({path: screenshot, fullPage: false});
  }
  await page.waitForSelector(`${selector} video.canvas-media-preview.is-video`, {timeout: 60_000});
  await page.waitForFunction(
    (nodeSelector) => {
      const node = document.querySelector(nodeSelector);
      const rect = node?.getBoundingClientRect();
      return !!rect && rect.width > 40 && rect.height > 40;
    },
    {timeout: 30_000},
    selector,
  );
  await new Promise((resolve) => setTimeout(resolve, 400));

  const before = await page.$eval(selector, (node) => {
    const stage = node.querySelector('.canvas-video-stage');
    if (!(stage instanceof HTMLElement)) throw new Error('video stage missing');
    const nodeRect = node.getBoundingClientRect();
    const stageRect = stage.getBoundingClientRect();
    const x = stageRect.x + stageRect.width * 0.28;
    const y = stageRect.y + stageRect.height * 0.24;
    return {
      x,
      y,
      nodeX: nodeRect.x,
      nodeY: nodeRect.y,
      pointTag: document.elementFromPoint(x, y)?.tagName || '',
      pointClass: document.elementFromPoint(x, y)?.className || '',
      pointNodrag: Boolean(document.elementFromPoint(x, y)?.closest('.nodrag')),
    };
  });
  if (before.pointNodrag) {
    throw new Error(`video drag point is still nodrag: ${JSON.stringify(before)}`);
  }

  await page.mouse.move(before.x, before.y);
  await page.mouse.down();
  await page.mouse.move(before.x + 96, before.y + 58, {steps: 12});
  await page.mouse.up();
  await new Promise((resolve) => setTimeout(resolve, 650));

  const after = await page.$eval(selector, (node) => {
    const rect = node.getBoundingClientRect();
    return {
      nodeX: rect.x,
      nodeY: rect.y,
      modalOpen: Boolean(document.querySelector('.jim-bar:not(.is-closing)')),
    };
  });
  const moved = Math.hypot(after.nodeX - before.nodeX, after.nodeY - before.nodeY);
  await page.screenshot({path: screenshot, fullPage: false});

  const result = {
    projectId,
    nodeId,
    before,
    after,
    moved: Number(moved.toFixed(1)),
    screenshot,
    pageErrors,
    consoleErrors: consoleErrors.filter((item) => (
      !item.includes('favicon')
      && !item.includes('Failed to load resource: the server responded with a status of 404')
    )),
  };
  console.log(JSON.stringify(result, null, 2));

  if (moved < 20) throw new Error(`video surface did not drag node: moved=${moved}`);
  if (after.modalOpen) throw new Error('dragging video surface opened the node modal');
  if (result.pageErrors.length || result.consoleErrors.length) {
    throw new Error('browser errors detected');
  }
} finally {
  if (projectId) {
    await page.evaluate(async ({apiBase, pid}) => {
      await fetch(`${apiBase}/api/projects/${encodeURIComponent(pid)}`, {
        method: 'DELETE',
        credentials: 'include',
      }).catch(() => {});
    }, {apiBase: api, pid: projectId}).catch(() => {});
  }
  await browser.close();
}
