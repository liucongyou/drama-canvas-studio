import puppeteer from 'puppeteer-core';

const apiBase = 'http://127.0.0.1:8890';
const webBase = 'http://127.0.0.1:8891';
const password = process.env.CANVAS_PASSWORD || 'TmpCanvas2026!';
const videoUrl = '/uploads/generated-1793362871-a555b1e3b7.mp4';
const poster =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';

const loginResponse = await fetch(`${apiBase}/api/auth/login`, {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({username: 'admin', password, tenant: 'owner'}),
});
if (!loginResponse.ok) throw new Error(`login failed: ${loginResponse.status}`);
const sessionCookie = (loginResponse.headers.get('set-cookie') || '').split(';', 1)[0];

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
  body: JSON.stringify({title: `资产托盘视频封面回归-${Date.now()}`}),
});
const projectId = project.id;

try {
  await api(`/api/projects/${projectId}/nodes`, {
    method: 'PUT',
    body: JSON.stringify({
      episodeId: 'EP001',
      nodes: [
        {
          id: 'tray-video-with-poster',
          type: 'video',
          title: '视频｜有持久封面',
          episodeId: 'EP001',
          position: {x: 100, y: 100},
          metadata: {
            prompt: '有封面',
            status: 'done',
            content: videoUrl,
            params: {thumbUrl: poster},
            episodeId: 'EP001',
          },
        },
        {
          id: 'tray-video-first-frame',
          type: 'video',
          title: '视频｜自动首帧',
          episodeId: 'EP001',
          position: {x: 460, y: 100},
          metadata: {
            prompt: '无持久封面',
            status: 'done',
            content: videoUrl,
            episodeId: 'EP001',
          },
        },
      ],
    }),
  });

  const browser = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--autoplay-policy=no-user-gesture-required'],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport({width: 1280, height: 900, deviceScaleFactor: 1});
    const errors = [];
    page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(`console: ${message.text()}`);
    });
    const [cookieName, ...cookieValueParts] = sessionCookie.split('=');
    await page.setCookie({
      name: cookieName,
      value: cookieValueParts.join('='),
      domain: '127.0.0.1',
      path: '/',
      httpOnly: true,
      sameSite: 'Lax',
    });
    await page.goto(`${webBase}/?ep=EP001#${projectId}`, {
      waitUntil: 'domcontentloaded',
      timeout: 60_000,
    });
    await page.waitForSelector('.canvas-stage', {timeout: 60_000});
    await page.evaluate(() => {
      const tab = [...document.querySelectorAll('.canvas-rail-tabs button')]
        .find((button) => (button.textContent || '').includes('资产'));
      if (!(tab instanceof HTMLElement)) throw new Error('资产托盘页签不存在');
      tab.click();
    });
    await page.waitForSelector('.canvas-tray-group.is-video', {timeout: 20_000});
    await page.waitForFunction(() => (
      document.querySelectorAll('.canvas-tray-group.is-video .canvas-tray-item').length === 2
    ), {timeout: 10_000});
    await new Promise((resolve) => setTimeout(resolve, 3000));

    const state = await page.evaluate(() => {
      const items = [...document.querySelectorAll('.canvas-tray-group.is-video .canvas-tray-item')];
      return items.map((item) => {
        const image = item.querySelector('img.canvas-tray-thumb');
        const video = item.querySelector('video.canvas-tray-thumb');
        return {
          nodeId: item.getAttribute('data-node-id'),
          image: image ? {
            complete: image.complete,
            naturalWidth: image.naturalWidth,
            ready: image.classList.contains('is-thumb-ready'),
          } : null,
          video: video ? {
            readyState: video.readyState,
            currentTime: video.currentTime,
            ready: video.classList.contains('is-thumb-ready'),
          } : null,
          placeholder: Boolean(item.querySelector('.canvas-tray-thumb.is-placeholder')),
          loading: Boolean(item.querySelector('.canvas-tray-thumb-loading')),
        };
      });
    });
    if (state.length !== 2) throw new Error(`expected 2 video tray items, got ${state.length}`);
    const posterItem = state.find((item) => item.nodeId === 'tray-video-with-poster');
    const firstFrameItem = state.find((item) => item.nodeId === 'tray-video-first-frame');
    if (!posterItem?.image?.complete || posterItem.image.naturalWidth <= 0) {
      throw new Error(`persisted poster did not render: ${JSON.stringify(posterItem)}`);
    }
    if (
      !firstFrameItem?.video
      || (!firstFrameItem.video.ready && firstFrameItem.video.readyState < 2)
      || firstFrameItem.placeholder
    ) {
      throw new Error(`video first frame did not render: ${JSON.stringify(firstFrameItem)}`);
    }
    if (errors.length) throw new Error(errors.join('; '));
    console.log(JSON.stringify({ok: true, projectId, state, errors}, null, 2));
  } finally {
    await browser.close();
  }
} finally {
  await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
}
