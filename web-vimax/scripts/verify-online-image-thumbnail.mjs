import path from 'node:path';
import puppeteer from 'puppeteer-core';

const front = (process.env.CANVAS_FRONT || 'https://yj.pduoduo.vip').replace(/\/$/, '');
const api = (process.env.CANVAS_API || 'https://yjapi.pduoduo.vip').replace(/\/$/, '');
const username = process.env.CANVAS_USERNAME || '';
const password = process.env.CANVAS_PASSWORD || '';
const projectId = process.env.CANVAS_PROJECT_ID || 'Wz1S771JMitvStPUYZtjRn';
const episodeId = process.env.CANVAS_EPISODE_ID || 'EP001';
const nodeId = process.env.CANVAS_IMAGE_NODE_ID || 'node-zs1t6oarziuc-01cddf84';
const screenshot = path.resolve(
  process.env.THUMBNAIL_SCREENSHOT || '.tmp-online-image-thumbnail.png',
);

if (!username || !password) {
  throw new Error('CANVAS_USERNAME and CANVAS_PASSWORD are required');
}

const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--window-size=1600,1000'],
});

try {
  const loginResponse = await fetch(`${api}/api/auth/login`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username, password}),
  });
  if (!loginResponse.ok) {
    throw new Error(`login failed: ${loginResponse.status}`);
  }
  const setCookies = typeof loginResponse.headers.getSetCookie === 'function'
    ? loginResponse.headers.getSetCookie()
    : [loginResponse.headers.get('set-cookie') || ''];
  const cookiePair = setCookies
    .map((value) => value.split(';', 1)[0])
    .find((value) => value.includes('='));
  if (!cookiePair) throw new Error('login did not return a session cookie');
  const [cookieName, ...cookieValueParts] = cookiePair.split('=');
  const cookieValue = cookieValueParts.join('=');

  const page = await browser.newPage();
  await page.setViewport({width: 1600, height: 1000});
  for (const domain of new Set([new URL(api).hostname, new URL(front).hostname])) {
    await page.setCookie({
      name: cookieName,
      value: cookieValue,
      domain,
      path: '/',
      secure: true,
      httpOnly: true,
      sameSite: 'Lax',
    });
  }

  const requests = [];
  const pageErrors = [];
  const consoleErrors = [];
  page.on('request', (request) => requests.push(request.url()));
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });

  await page.goto(
    `${front}/?ep=${encodeURIComponent(episodeId)}#${encodeURIComponent(projectId)}`,
    {waitUntil: 'domcontentloaded', timeout: 60_000},
  );
  const selector = `.react-flow__node-media[data-id="media-${nodeId}"] img.canvas-media-preview.is-photo`;
  await page.waitForSelector(selector, {timeout: 60_000});
  await page.waitForFunction(
    (imageSelector) => {
      const image = document.querySelector(imageSelector);
      return image instanceof HTMLImageElement
        && image.complete
        && image.naturalWidth > 0;
    },
    {timeout: 60_000},
    selector,
  );
  const result = await page.$eval(selector, (image) => ({
    src: image instanceof HTMLImageElement ? image.currentSrc || image.src : '',
    naturalWidth: image instanceof HTMLImageElement ? image.naturalWidth : 0,
    naturalHeight: image instanceof HTMLImageElement ? image.naturalHeight : 0,
  }));
  await page.screenshot({path: screenshot, fullPage: false});

  const thumbnailRequests = requests.filter((url) => (
    url.includes('generated-1793717181-50e3b5e872-thumb.jpg')
  ));
  const output = {
    projectId,
    episodeId,
    nodeId,
    result,
    thumbnailRequests,
    screenshot,
    pageErrors,
    consoleErrors: consoleErrors.filter((item) => (
      !item.includes('favicon')
      && !item.includes('Failed to load resource: the server responded with a status of 404')
    )),
  };
  console.log(JSON.stringify(output, null, 2));

  if (!result.src.includes('-thumb.jpg')) {
    throw new Error(`image card did not use thumbnail: ${result.src}`);
  }
  if (!thumbnailRequests.length) throw new Error('thumbnail request was not observed');
  if (result.naturalWidth > 700) throw new Error(`thumbnail is too large: ${result.naturalWidth}px`);
  if (output.pageErrors.length || output.consoleErrors.length) {
    throw new Error('browser errors detected');
  }
} finally {
  await browser.close();
}
