import puppeteer from 'puppeteer-core';

const apiBase = 'http://127.0.0.1:8890';
const password = process.env.CANVAS_PASSWORD || 'TmpCanvas2026!';
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const loginResponse = await fetch(`${apiBase}/api/auth/login`, {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({username: 'admin', password, tenant: 'owner'}),
});
if (!loginResponse.ok) throw new Error(`login failed: ${loginResponse.status}`);
const sessionCookie = (loginResponse.headers.get('set-cookie') || '').split(';', 1)[0];
if (!sessionCookie) throw new Error('missing canvas session cookie');

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
  if (!response.ok) throw new Error(`${response.status} ${payload.error || JSON.stringify(payload)}`);
  return payload;
}

const project = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title: '输入框内联引用删除测试'}),
});
const projectId = project.id;
const source = {nodeId: 'inline-source-image', label: '角色｜内联甲'};

try {
  await api(`/api/projects/${projectId}/nodes`, {
    method: 'PUT',
    body: JSON.stringify({
      episodeId: 'EP001',
      nodes: [
        {
          id: source.nodeId,
          type: 'image',
          title: source.label,
          position: {x: 0, y: 0},
          metadata: {
            prompt: '角色图',
            status: 'done',
            content: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
          },
        },
        {
          id: 'inline-target-video',
          type: 'video',
          title: '内联删除目标',
          position: {x: 420, y: 0},
          metadata: {
            prompt: `@${source.label} 镜头推进`,
            status: 'idle',
            refs: [source],
            params: {
              provider: 'ark',
              model: 'doubao-seedance-2-0',
              refs: [source],
            },
          },
        },
      ],
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
    const [cookieName, ...cookieValueParts] = sessionCookie.split('=');
    await page.setCookie({
      name: cookieName,
      value: cookieValueParts.join('='),
      domain: '127.0.0.1',
      path: '/',
      httpOnly: true,
      sameSite: 'Lax',
    });
    await page.goto('http://127.0.0.1:8891', {waitUntil: 'domcontentloaded', timeout: 60000});
    await page.waitForSelector('.canvas-project-btn', {timeout: 30000});
    await page.click('.canvas-project-btn');
    await page.waitForSelector('.canvas-project-menu', {timeout: 10000});
    const selected = await page.evaluate(() => {
      const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
        .find((item) => (item.textContent || '').includes('输入框内联引用删除测试'));
      if (!(button instanceof HTMLElement)) return false;
      button.click();
      return true;
    });
    if (!selected) throw new Error('内联删除测试项目未出现在菜单');
    await page.waitForSelector('[data-id="media-inline-target-video"] .canvas-media-card', {timeout: 60000});
    await page.click('[data-id="media-inline-target-video"] .canvas-media-card');
    await page.waitForSelector('.jim-bar[data-kind="video"]', {timeout: 15000});
    await page.waitForSelector('.jim-prompt-mention', {timeout: 10000});

    const selectedChip = await page.evaluate(() => {
      const chip = document.querySelector('.jim-prompt-mention');
      const editor = document.querySelector('.jim-prompt');
      if (!chip || !editor) return false;
      const range = document.createRange();
      range.selectNode(chip);
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
      editor.focus();
      return true;
    });
    if (!selectedChip) throw new Error('输入框内联引用块不存在');
    await page.keyboard.press('Backspace');
    await sleep(1200);

    const current = await api(`/api/projects/${projectId}`);
    const target = current.payload.nodes.find((node) => node.id === 'inline-target-video');
    const refs = target?.metadata?.params?.refs || [];
    if (refs.some((ref) => ref.nodeId === source.nodeId)) {
      throw new Error('输入框内删掉引用后 params.refs 仍残留');
    }
    if (String(target?.metadata?.prompt || '').includes(`@${source.label}`)) {
      throw new Error('输入框内删掉引用后提示词仍残留');
    }
    console.log(JSON.stringify({ok: true, projectId, prompt: target.metadata.prompt, refs}, null, 2));
  } finally {
    await browser.close();
  }
} finally {
  await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
}
