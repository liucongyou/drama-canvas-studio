import puppeteer from 'puppeteer-core';

const base = 'http://127.0.0.1:8890';
const title = `模型价格显示 ${Date.now()}`;

async function api(pathname, options = {}) {
  const response = await fetch(base + pathname, {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${response.status} ${JSON.stringify(payload)}`);
  return payload;
}

const cases = [
  {
    id: `price-seedance20-480-${Date.now()}`,
    title: 'Seedance 2.0 480P 15s',
    provider: 'ark',
    model: 'doubao-seedance-2-0',
    resolution: '480P',
    duration: 15,
    expected: '≈¥6.93',
  },
  {
    id: `price-seedance20-1080-${Date.now()}`,
    title: 'Seedance 2.0 1080P 15s',
    provider: 'ark',
    model: 'doubao-seedance-2-0',
    resolution: '1080P',
    duration: 15,
    expected: '≈¥37.17',
  },
  {
    id: `price-seedance25-${Date.now()}`,
    title: 'Seedance 2.5 15s',
    provider: 'ark',
    model: 'doubao-seedance-2-5',
    resolution: '1080P',
    duration: 15,
    expected: '按供应商账单',
  },
  {
    id: `price-agnes-${Date.now()}`,
    title: 'Agnes 15s',
    provider: 'agnes-ai',
    model: 'agnes-video-2.5-flash',
    resolution: '720P',
    duration: 12,
    expected: '按供应商账单',
  },
];

const project = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title}),
});
await api(`/api/projects/${project.id}/nodes`, {
  method: 'PUT',
  body: JSON.stringify({
    nodes: cases.map((item, index) => ({
      id: item.id,
      type: 'video',
      title: item.title,
      position: {x: 80 + index * 280, y: 120},
      metadata: {
        prompt: item.title,
        status: 'idle',
        params: {
          provider: item.provider,
          model: item.model,
          ratio: '9:16',
          resolution: item.resolution,
          duration: item.duration,
          count: 1,
        },
      },
    })),
  }),
});

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1600, height: 1000, deviceScaleFactor: 1});
  await page.goto(base, {waitUntil: 'domcontentloaded', timeout: 60_000});
  await page.waitForSelector('.canvas-project-btn', {timeout: 30_000});
  await page.click('.canvas-project-btn');
  await page.waitForSelector('.canvas-project-menu-list button', {timeout: 10_000});
  const selected = await page.evaluate((projectTitle) => {
    const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
      .find((item) => (item.textContent || '').includes(projectTitle));
    if (!(button instanceof HTMLElement)) return false;
    button.click();
    return true;
  }, title);
  if (!selected) throw new Error('temporary project not found');

  const results = [];
  for (const item of cases) {
    const card = `[data-id="media-${item.id}"] .canvas-media-card`;
    await page.waitForSelector(card, {timeout: 60_000});
    await page.$eval(card, (element) => element.click());
    await page.waitForSelector('.jim-bar .jim-cost', {timeout: 30_000});
    const actual = await page.$eval('.jim-bar .jim-cost', (element) => (element.textContent || '').trim());
    results.push({title: item.title, expected: item.expected, actual});
    if (actual !== item.expected) {
      throw new Error(`wrong price hint: ${JSON.stringify({title: item.title, expected: item.expected, actual})}`);
    }
    await page.keyboard.press('Escape');
    await page.waitForSelector('.jim-bar', {hidden: true, timeout: 10_000}).catch(() => {});
  }
  console.log(JSON.stringify({ok: true, results}, null, 2));
} finally {
  await browser.close();
  await api(`/api/projects/${project.id}`, {method: 'DELETE'}).catch(() => {});
}
