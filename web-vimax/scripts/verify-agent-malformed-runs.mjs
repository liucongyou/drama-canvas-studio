import puppeteer from 'puppeteer-core';

const base = (process.env.CANVAS_BASE_URL || 'http://127.0.0.1:8890').replace(/\/$/, '');
const token = process.env.CANVAS_SESSION_TOKEN || '';
const projectId = process.env.CANVAS_PROJECT_ID || '';
const episode = process.env.CANVAS_EPISODE || 'EP015';
if (!token || !projectId) throw new Error('session token and project id are required');

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});
try {
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 960});
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error' && !message.text().includes('Failed to load resource')) {
      errors.push(message.text());
    }
  });

  const host = new URL(base).hostname;
  const domains = host.endsWith('pduoduo.vip')
    ? ['yj.pduoduo.vip', 'yjapi.pduoduo.vip']
    : [host];
  for (const domain of domains) {
    await page.setCookie({
      name: 'canvas_session',
      value: token,
      domain,
      path: '/',
      httpOnly: true,
      secure: base.startsWith('https://'),
    });
  }
  await page.evaluateOnNewDocument(() => {
    localStorage.setItem('canvas.agentDock.open', '1');
  });
  await page.setRequestInterception(true);
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (request.method() === 'GET' && url.pathname === '/api/agent/runs') {
      return request.respond({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          runs: [
            null,
            {status: 'done'},
            {
              id: 'run-malformed-safe',
              projectId,
              skillId: 'test',
              skillVersion: '1',
              prompt: 'test',
              status: 'done',
              error: '',
              messages: [],
              steps: [],
              stages: [],
              artifacts: [],
            },
          ],
        }),
      });
    }
    return request.continue();
  });

  await page.goto(`${base}/?ep=${encodeURIComponent(episode)}#${projectId}`, {
    waitUntil: 'domcontentloaded',
    timeout: 60_000,
  });
  await page.waitForSelector('.agent-panel', {timeout: 60_000});
  await new Promise((resolve) => setTimeout(resolve, 3_000));
  const state = await page.evaluate(() => ({
    boundary: Boolean(document.querySelector('[role="alert"]')),
    body: (document.body.innerText || '').slice(0, 500),
    flow: Boolean(document.querySelector('.react-flow')),
  }));
  if (state.boundary || !state.flow || state.body.includes('界面出了点问题')) {
    throw new Error(`malformed run response crashed the UI: ${JSON.stringify(state)}`);
  }
  if (errors.some((value) => /reading 'id'/.test(value))) {
    throw new Error(`reading-id regression: ${errors.join(' | ')}`);
  }
  console.log(JSON.stringify({state, errors}, null, 2));
} finally {
  await browser.close();
}
