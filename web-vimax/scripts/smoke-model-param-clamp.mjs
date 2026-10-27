import puppeteer from 'puppeteer-core';

const apiBase = 'http://127.0.0.1:8890';
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function api(pathname, options = {}) {
  const response = await fetch(apiBase + pathname, {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${response.status} ${payload.error || JSON.stringify(payload)}`);
  return payload;
}

const project = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title: '模型切换参数收敛测试'}),
});
const projectId = project.id;

try {
  await api(`/api/projects/${projectId}/nodes`, {
    method: 'PUT',
    body: JSON.stringify({
      nodes: [{
        id: 'clamp-video',
        type: 'video',
        title: '参数收敛视频',
        position: {x: 0, y: 0},
        metadata: {
          prompt: '测试模型切换参数',
          status: 'idle',
          refs: [],
          params: {
            provider: 'ark',
            model: 'doubao-seedance-2-5',
            ratio: '9:16',
            resolution: '1080P',
            duration: 30,
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
  try {
    const page = await browser.newPage();
    await page.setViewport({width: 1280, height: 820, deviceScaleFactor: 1});
    await page.goto('http://127.0.0.1:8891', {waitUntil: 'networkidle0', timeout: 60000});
    await page.waitForSelector('.canvas-project-btn', {timeout: 30000});
    await page.click('.canvas-project-btn');
    await page.waitForSelector('.canvas-project-menu', {timeout: 10000});
    const selected = await page.evaluate(() => {
      const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
        .find((item) => (item.textContent || '').includes('模型切换参数收敛测试'));
      if (!(button instanceof HTMLElement)) return false;
      button.click();
      return true;
    });
    if (!selected) throw new Error('参数收敛测试项目未出现在菜单');
    await page.waitForSelector('[data-id="media-clamp-video"] .canvas-media-card', {timeout: 60000});
    await page.click('[data-id="media-clamp-video"] .canvas-media-card');
    await page.waitForSelector('.jim-bar[data-kind="video"]', {timeout: 15000});
    await page.click('.jim-bar .jim-tool.is-model');
    await page.waitForSelector('.jim-pop-model', {timeout: 10000});
    const switched = await page.evaluate(() => {
      const button = [...document.querySelectorAll('.jim-pop-model .jim-model-item')]
        .find((item) => (item.textContent || '').includes('Seedance 2.0 mini'));
      if (!(button instanceof HTMLElement)) return false;
      button.click();
      return true;
    });
    if (!switched) throw new Error('模型菜单找不到 Seedance 2.0 mini');
    await sleep(1200);

    const current = await api(`/api/projects/${projectId}`);
    const params = current.payload.nodes.find((node) => node.id === 'clamp-video')?.metadata?.params || {};
    if (params.model !== 'doubao-seedance-2-0-mini') throw new Error(`模型未切换：${params.model}`);
    if (Number(params.duration) !== 15) throw new Error(`切到 mini 后时长未收敛到 15：${params.duration}`);
    if (!['480P', '720P'].includes(String(params.resolution))) {
      throw new Error(`切到 mini 后分辨率仍越界：${params.resolution}`);
    }
    console.log(JSON.stringify({ok: true, projectId, params}, null, 2));
  } finally {
    await browser.close();
  }
} finally {
  await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
}
