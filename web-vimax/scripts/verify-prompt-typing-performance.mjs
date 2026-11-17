import puppeteer from 'puppeteer-core';

const apiBase = 'http://127.0.0.1:8890';
const appUrl = 'http://127.0.0.1:8891';
const projectTitle = `输入性能回归-${Date.now()}`;
const password = process.env.CANVAS_PASSWORD || 'TmpCanvas2026!';
const onePixelPng =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';
let authCookie = '';

async function api(pathname, options = {}) {
  const response = await fetch(apiBase + pathname, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(authCookie ? {Cookie: authCookie} : {}),
      ...(options.headers || {}),
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(`${response.status} ${payload.error || JSON.stringify(payload)}`);
  }
  return payload;
}

const loginResponse = await fetch(`${apiBase}/api/auth/login`, {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({username: 'admin', password, tenant: 'owner'}),
});
if (!loginResponse.ok) {
  throw new Error(`login failed: ${loginResponse.status}`);
}
const setCookie = loginResponse.headers.get('set-cookie') || '';
authCookie = setCookie.split(';', 1)[0];
if (!authCookie) throw new Error('login did not return a session cookie');

const project = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title: projectTitle}),
});
const projectId = project.id;
const episodeId = 'EP001';

try {
  const nodes = Array.from({length: 120}, (_, index) => ({
    id: `typing-image-${index + 1}`,
    type: 'image',
    episodeId,
    title: `角色｜性能角色${index + 1}`,
    position: {x: (index % 8) * 240, y: Math.floor(index / 8) * 180},
    metadata: {
      prompt: `角色${index + 1}`,
      status: 'done',
      episodeId,
      content: onePixelPng,
      mediaUrl: onePixelPng,
      thumbUrl: onePixelPng,
    },
  }));
  const longPrompt = Array.from(
    {length: 42},
    (_, index) => `第${index + 1}段：角色从门口走入，停顿两秒后看向桌面并完成动作。`,
  ).join('\n');
  const targetRefs = nodes.slice(0, 9).map((node) => ({
    nodeId: node.id,
    label: node.title,
    url: onePixelPng,
  }));
  nodes.push({
    id: 'typing-target-video',
    type: 'video',
    episodeId,
    title: '输入性能目标',
    position: {x: 0, y: 1120},
    metadata: {
      prompt: longPrompt,
      status: 'idle',
      episodeId,
      params: {
        provider: 'ark',
        model: 'doubao-seedance-2-0',
        refs: targetRefs,
      },
    },
  });
  nodes.push({
    id: 'typing-target-text',
    type: 'text',
    episodeId,
    title: '文本输入性能目标',
    position: {x: 320, y: 1120},
    metadata: {
      prompt: longPrompt,
      status: 'idle',
      episodeId,
    },
  });
  await api(`/api/projects/${projectId}/nodes`, {
    method: 'PUT',
    body: JSON.stringify({episodeId, nodes}),
  });

  const browser = await puppeteer.launch({
    executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
    headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--no-proxy-server'],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport({width: 1680, height: 1050, deviceScaleFactor: 1});
    const [cookieName, ...cookieValueParts] = authCookie.split('=');
    await page.setCookie({
      name: cookieName,
      value: cookieValueParts.join('='),
      domain: '127.0.0.1',
      path: '/',
    });
    const errors = [];
    page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(`console: ${message.text()}`);
    });

    await page.goto(`${appUrl}/?ep=${episodeId}#${projectId}`, {
      waitUntil: 'domcontentloaded',
      timeout: 60_000,
    });
    await page.waitForSelector('.canvas-project-btn, .react-flow__node, input[type="password"]', {
      timeout: 60_000,
    });
    if (await page.$('input[type="password"]')) {
      const login = await page.evaluate(async (secret) => {
        const response = await fetch('/api/auth/login', {
          method: 'POST',
          credentials: 'include',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({username: 'admin', password: secret, tenant: 'owner'}),
        });
        return {ok: response.ok, status: response.status, body: await response.text()};
      }, password);
      if (!login.ok) throw new Error(`browser login failed: ${JSON.stringify(login)}`);
      await page.waitForFunction(
        () => !document.querySelector('input[type="password"]'),
        {timeout: 30_000},
      );
      await page.goto(`${appUrl}/?ep=${episodeId}#${projectId}`, {
        waitUntil: 'domcontentloaded',
        timeout: 60_000,
      });
    }
    if (!(await page.$('.react-flow__node'))) {
      await page.evaluate(() => {
        window.dispatchEvent(new Event('canvas-projects-changed'));
      });
      await new Promise((resolve) => setTimeout(resolve, 1000));
      await page.waitForSelector('.canvas-project-btn', {timeout: 30_000});
      await page.click('.canvas-project-btn');
      await page.waitForSelector('.canvas-project-menu', {timeout: 10_000});
      const selected = await page.evaluate((title) => {
        const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
          .find((item) => (item.textContent || '').includes(title));
        if (!(button instanceof HTMLElement)) return false;
        button.click();
        return true;
      }, projectTitle);
      if (!selected) throw new Error('typing test project was not found in the project menu');
    }
    await page.waitForSelector('[data-id="media-typing-target-video"]', {
      timeout: 60_000,
    });
    await page.click('[data-id="media-typing-target-video"]');
    await page.waitForSelector('.jim-prompt[contenteditable="true"]', {timeout: 20_000});
    // Let canvas thumbnail decodes and reconciliation settle before measuring
    // keystrokes. The typing path itself must not be benchmarked against a
    // still-loading 120-node board.
    await new Promise((resolve) => setTimeout(resolve, 4000));

    const firstOpen = await page.evaluate(() => {
      const editor = document.querySelector('.jim-prompt[contenteditable="true"]');
      if (!editor) return null;
      const style = getComputedStyle(editor);
      return {
        textLength: (editor.textContent || '').length,
        clientHeight: editor.clientHeight,
        scrollHeight: editor.scrollHeight,
        overflowY: style.overflowY,
      };
    });
    if (!firstOpen || firstOpen.textLength < 1000) {
      throw new Error('long prompt was not fully rendered on first open');
    }

    await page.evaluate(() => {
      const editor = document.querySelector('.jim-prompt[contenteditable="true"]');
      if (!editor) throw new Error('prompt editor missing');
      const range = document.createRange();
      range.selectNodeContents(editor);
      range.collapse(false);
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
      editor.focus();
      window.__promptInputFrames = [];
      editor.addEventListener('input', () => {
        const started = performance.now();
        requestAnimationFrame(() => {
          window.__promptInputFrames.push(performance.now() - started);
        });
      }, {capture: true});
    });

    const typingStarted = Date.now();
    await page.keyboard.type('abcdefghij', {delay: 0});
    await page.keyboard.sendCharacter('中');
    await page.keyboard.sendCharacter('文');
    const typingElapsedMs = Date.now() - typingStarted;
    await new Promise((resolve) => setTimeout(resolve, 200));

    const typing = await page.evaluate(() => {
      const editor = document.querySelector('.jim-prompt[contenteditable="true"]');
      if (!editor) return null;
      const selection = window.getSelection();
      const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
      const endRange = range ? document.createRange() : null;
      if (endRange && range) {
        endRange.selectNodeContents(editor);
        endRange.setEnd(range.endContainer, range.endOffset);
      }
      const textLength = (editor.textContent || '').length;
      const caretOffset = endRange?.toString().length ?? -1;
      return {
        textEndsWith: (editor.textContent || '').slice(-12),
        textLength,
        caretOffset,
        caretAtEnd: Boolean(range?.collapsed && caretOffset === textLength),
        inputFrameDelays: window.__promptInputFrames || [],
      };
    });
    if (!typing) throw new Error('typing result missing');
    if (!typing.textEndsWith.endsWith('abcdefghij中文')) {
      throw new Error(`typing order mismatch: ${typing.textEndsWith}`);
    }
    if (!typing.caretAtEnd) {
      throw new Error(`caret moved away from the end while typing: ${JSON.stringify(typing)}`);
    }
    if (errors.length) throw new Error(errors.join('; '));

    if (Math.max(0, ...typing.inputFrameDelays) > 60) {
      throw new Error(`input frame delay exceeded 60ms: ${JSON.stringify(typing.inputFrameDelays)}`);
    }

    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('.jim-bar'), {timeout: 10_000});
    await page.click('[data-id="media-typing-target-text"]');
    await page.waitForSelector('.jim-bar[data-kind="text"] .jim-prompt[contenteditable="true"]', {
      timeout: 20_000,
    });
    await new Promise((resolve) => setTimeout(resolve, 500));
    const textBefore = await page.evaluate(() => {
      const badge = document.querySelector('.jim-head-badge');
      const editor = document.querySelector('.jim-prompt[contenteditable="true"]');
      return {
        badge: badge?.textContent || '',
        length: (editor?.textContent || '').length,
      };
    });
    await page.evaluate(() => {
      const editor = document.querySelector('.jim-bar[data-kind="text"] .jim-prompt[contenteditable="true"]');
      if (!editor) throw new Error('text prompt editor missing');
      const range = document.createRange();
      range.selectNodeContents(editor);
      range.collapse(false);
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
      editor.focus();
    });
    await page.keyboard.type('abc', {delay: 0});
    await new Promise((resolve) => setTimeout(resolve, 150));
    const textAfter = await page.evaluate(() => {
      const badge = document.querySelector('.jim-head-badge');
      const editor = document.querySelector('.jim-bar[data-kind="text"] .jim-prompt[contenteditable="true"]');
      return {
        badge: badge?.textContent || '',
        length: (editor?.textContent || '').length,
        tail: (editor?.textContent || '').slice(-8),
      };
    });
    if (textAfter.length !== textBefore.length + 3 || !textAfter.tail.endsWith('abc')) {
      throw new Error(`text prompt typing lost characters: ${JSON.stringify({textBefore, textAfter})}`);
    }
    if (textAfter.badge === textBefore.badge) {
      throw new Error(`text prompt counter did not update: ${JSON.stringify({textBefore, textAfter})}`);
    }

    console.log(JSON.stringify({
      projectId,
      firstOpen,
      typingElapsedMs,
      inputFrameDelays: typing.inputFrameDelays,
      maxInputFrameDelayMs: Math.max(0, ...typing.inputFrameDelays),
      typing,
      textCounter: {textBefore, textAfter},
      errors,
    }, null, 2));
  } finally {
    await browser.close();
  }
} finally {
  await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
}
