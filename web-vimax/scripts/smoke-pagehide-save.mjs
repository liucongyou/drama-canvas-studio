import puppeteer from 'puppeteer-core';

const base = 'http://127.0.0.1:8890';
const title = `关页保存 ${Date.now()}`;
const prompt = `关页前最后修改 ${Date.now()}`;

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
const nodeId = `pagehide-save-${Date.now()}`;
await api(`/api/projects/${project.id}/nodes`, {
  method: 'PUT',
  body: JSON.stringify({
    nodes: [{
      id: nodeId,
      type: 'image',
      title: '关页保存测试',
      position: {x: 160, y: 120},
      metadata: {
        prompt: '',
        status: 'idle',
        params: {ratio: '1:1', clarity: '1.5K', size: '1536x1536'},
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
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
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

  const card = `[data-id="media-${nodeId}"] .canvas-media-card`;
  await page.waitForSelector(card, {timeout: 60_000});
  await page.$eval(card, (node) => node.click());
  await page.waitForSelector('.jim-bar .jim-prompt[contenteditable="true"]', {timeout: 30_000});
  await page.focus('.jim-bar .jim-prompt');
  await page.keyboard.down('Control');
  await page.keyboard.press('A');
  await page.keyboard.up('Control');
  await page.keyboard.type(prompt);
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide')));

  let stored = '';
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const projectData = await api(`/api/projects/${project.id}`);
    const node = (projectData.payload.nodes || []).find((item) => item.id === nodeId);
    stored = String(node?.metadata?.prompt || '');
    if (stored === prompt) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (stored !== prompt) {
    throw new Error(`pagehide did not flush prompt: ${JSON.stringify({expected: prompt, stored})}`);
  }

  console.log(JSON.stringify({ok: true, projectId: project.id, nodeId, prompt}, null, 2));
} finally {
  await browser.close();
  await api(`/api/projects/${project.id}`, {method: 'DELETE'}).catch(() => {});
}
