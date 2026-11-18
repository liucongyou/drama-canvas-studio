import puppeteer from 'puppeteer-core';

const apiBase = (process.env.CANVAS_API_BASE || 'https://yjapi.pduoduo.vip').replace(/\/$/, '');
const webBase = (process.env.CANVAS_WEB_BASE || 'https://yj.pduoduo.vip').replace(/\/$/, '');
const projectId = process.env.PROJECT_ID || 'CC7nAazDAMK31EW9eJGw5b';
const episodeId = process.env.EPISODE_ID || 'EP001';
const password = process.env.CANVAS_PASSWORD;
if (!password) throw new Error('CANVAS_PASSWORD is required');

const loginResponse = await fetch(`${apiBase}/api/auth/login`, {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({username: 'admin', password, tenant: 'owner'}),
});
if (!loginResponse.ok) throw new Error(`login failed: ${loginResponse.status}`);
const sessionCookie = (loginResponse.headers.get('set-cookie') || '').split(';', 1)[0];
if (!sessionCookie) throw new Error('missing canvas session');

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--no-proxy-server', '--autoplay-policy=no-user-gesture-required'],
});

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 1000, deviceScaleFactor: 1});
  const errors = [];
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(`console: ${message.text()}`);
  });
  const [cookieName, ...cookieValueParts] = sessionCookie.split('=');
  await page.setCookie({
    name: cookieName,
    value: cookieValueParts.join('='),
    domain: new URL(webBase).hostname,
    path: '/',
    secure: webBase.startsWith('https://'),
    httpOnly: true,
    sameSite: 'Lax',
  });
  await page.setRequestInterception(true);
  page.on('request', (request) => {
    if (/\/generate(?:[/?]|$)|\/config\/test(?:[/?]|$)|\/recover-media(?:[/?]|$)/.test(request.url())) {
      void request.abort('blockedbyclient');
      return;
    }
    void request.continue();
  });

  await page.goto(`${webBase}/?ep=${episodeId}#${projectId}`, {
    waitUntil: 'domcontentloaded',
    timeout: 60_000,
  });
  await page.waitForSelector('.canvas-stage', {timeout: 60_000});
  await page.evaluate(() => {
    const tab = [...document.querySelectorAll('.canvas-rail-tabs button')]
      .find((button) => (button.textContent || '').includes('资产'));
    if (!(tab instanceof HTMLElement)) throw new Error('asset tab missing');
    tab.click();
  });
  await page.waitForSelector('.canvas-tray-group.is-video', {timeout: 20_000});
  await page.evaluate(() => {
    document.querySelector('.canvas-tray-group.is-video')
      ?.scrollIntoView({block: 'start'});
  });
  await new Promise((resolve) => setTimeout(resolve, 8000));

  const state = await page.evaluate(() => {
    const items = [...document.querySelectorAll('.canvas-tray-group.is-video .canvas-tray-item')]
      .filter((item) => {
        const rect = item.getBoundingClientRect();
        return rect.bottom > 0 && rect.top < window.innerHeight;
      });
    return items.map((item) => {
      const image = item.querySelector('img.canvas-tray-thumb');
      const video = item.querySelector('video.canvas-tray-thumb');
      return {
        nodeId: item.getAttribute('data-node-id'),
        title: (item.querySelector('.canvas-tray-name')?.textContent || '').trim(),
        imageReady: Boolean(image?.complete && image.naturalWidth > 0),
        videoReady: Boolean(
          video
          && (video.readyState >= 2 || video.classList.contains('is-thumb-ready'))
        ),
        placeholder: Boolean(item.querySelector('.canvas-tray-thumb.is-placeholder')),
        loading: Boolean(item.querySelector('.canvas-tray-thumb-loading')),
      };
    });
  });
  const failed = state.filter((item) => (
    item.placeholder
    || (!item.imageReady && !item.videoReady)
  ));
  if (state.length === 0) throw new Error('no visible video items in asset tray');
  if (failed.length > 0) {
    throw new Error(`visible video covers failed: ${JSON.stringify({state, failed, errors})}`);
  }
  if (errors.length) throw new Error(errors.join('; '));
  console.log(JSON.stringify({
    ok: true,
    projectId,
    episodeId,
    visibleVideoCount: state.length,
    posterImages: state.filter((item) => item.imageReady).length,
    firstFrames: state.filter((item) => item.videoReady).length,
    state,
    errors,
  }, null, 2));
} finally {
  await browser.close();
}
