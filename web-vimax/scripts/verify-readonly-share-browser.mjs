import puppeteer from 'puppeteer-core';

const apiBase = 'http://127.0.0.1:8890';
const webBase = 'http://127.0.0.1:8891';
const password = process.env.CANVAS_PASSWORD || 'TmpCanvas2026!';
const image =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';

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

const project = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title: `只读分享浏览器回归-${Date.now()}`}),
});
const projectId = project.id;

try {
  await api(`/api/projects/${projectId}/nodes`, {
    method: 'PUT',
    body: JSON.stringify({
      episodeId: 'EP001',
      nodes: [{
        id: 'share-readonly-image',
        type: 'image',
        title: '角色｜只读分享',
        episodeId: 'EP001',
        position: {x: 240, y: 180},
        metadata: {
          prompt: '只读分享角色',
          status: 'done',
          content: image,
          episodeId: 'EP001',
        },
      }],
    }),
  });
  const share = await api(`/api/projects/${projectId}/shares`, {
    method: 'POST',
    body: JSON.stringify({role: 'viewer'}),
  });

  const browser = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--no-proxy-server'],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
    const errors = [];
    page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(`console: ${message.text()}`);
    });
    await page.goto(`${webBase}/share/${share.token}`, {
      waitUntil: 'domcontentloaded',
      timeout: 60_000,
    });
    await page.waitForSelector('.canvas-view.is-readonly-share', {timeout: 60_000});
    await page.waitForSelector('[data-id="media-share-readonly-image"]', {timeout: 60_000});
    await page.click('[data-id="media-share-readonly-image"]');
    await page.waitForSelector('.jim-bar', {timeout: 20_000});

    const state = await page.evaluate(() => {
      const fileInputs = [...document.querySelectorAll('input[type="file"]')];
      return {
        readonlyHost: Boolean(document.querySelector('.canvas-view.is-readonly-share')),
        shareBanner: Boolean(document.querySelector('.canvas-share-banner')),
        editorEditable: document.querySelector('.jim-prompt')?.getAttribute('contenteditable'),
        toolbarVisible: Boolean(document.querySelector('.jim-toolbar')),
        referenceEditorVisible: Boolean(document.querySelector('.jim-ref-row')),
        editButtonVisible: Boolean(document.querySelector('[title="编辑"]')),
        fileInputs: fileInputs.length,
        visibleFileInputs: fileInputs.filter((input) => (
          !input.disabled
          && input.getClientRects().length > 0
          && getComputedStyle(input).visibility !== 'hidden'
          && getComputedStyle(input).display !== 'none'
        )).length,
        connectControlVisible: Boolean(document.querySelector(
          'button[aria-label*="连接"], button[title*="连接"]',
        )),
      };
    });
    if (
      !state.readonlyHost
      || !state.shareBanner
      || state.editorEditable === 'true'
      || state.toolbarVisible
      || state.referenceEditorVisible
      || state.editButtonVisible
      || state.visibleFileInputs > 0
      || state.connectControlVisible
    ) {
      throw new Error(`read-only share exposed write controls: ${JSON.stringify(state)}`);
    }
    if (errors.length) throw new Error(errors.join('; '));

    console.log(JSON.stringify({
      ok: true,
      projectId,
      shareId: share.id,
      state,
      errors,
    }, null, 2));
  } finally {
    await browser.close();
  }
} finally {
  await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
}
