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
  body: JSON.stringify({title: '手动 @ 唤起测试'}),
});
const projectId = project.id;

try {
  await api(`/api/projects/${projectId}/nodes`, {
    method: 'PUT',
    body: JSON.stringify({
      nodes: [
        {
          id: 'manual-at-image',
          type: 'image',
          title: '角色｜手动甲',
          position: {x: 0, y: 0},
          metadata: {
            prompt: '角色图',
            status: 'done',
            content: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
            params: {provider: 'ark', model: 'doubao-seedream-5-0-pro', ratio: '1:1'},
          },
        },
        {
          id: 'manual-at-video',
          type: 'video',
          title: '手动 @ 目标',
          position: {x: 420, y: 0},
          metadata: {
            prompt: '',
            status: 'idle',
            refs: [],
            params: {provider: 'ark', model: 'doubao-seedance-2-0', ratio: '9:16', duration: 5},
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
    await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
    await page.goto('http://127.0.0.1:8891', {waitUntil: 'networkidle0', timeout: 60000});
    await page.waitForSelector('.canvas-project-btn', {timeout: 30000});
    await page.click('.canvas-project-btn');
    await page.waitForSelector('.canvas-project-menu', {timeout: 10000});
    const selected = await page.evaluate(() => {
      const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
        .find((item) => (item.textContent || '').includes('手动 @ 唤起测试'));
      if (!(button instanceof HTMLElement)) return false;
      button.click();
      return true;
    });
    if (!selected) throw new Error('手动 @ 测试项目未出现在菜单');
    await page.waitForSelector('[data-id="media-manual-at-video"] .canvas-media-card', {timeout: 60000});
    await page.click('[data-id="media-manual-at-video"] .canvas-media-card');
    await page.waitForSelector('.jim-bar[data-kind="video"]', {timeout: 15000});
    await page.waitForSelector('.jim-prompt[contenteditable="true"]', {timeout: 10000});
    const focusOnOpen = await page.evaluate(() => document.activeElement?.classList.contains('jim-prompt') || false);
    if (focusOnOpen) throw new Error('点击卡片进入弹窗时提示词输入框不应自动聚焦');
    await page.click('.jim-prompt');
    const focusedAfterClick = await page.evaluate(() => document.activeElement?.classList.contains('jim-prompt') || false);
    if (!focusedAfterClick) throw new Error('主动点击提示词输入框后未获得焦点');
    await page.keyboard.type('@', {delay: 120});
    try {
      await page.waitForSelector('.jim-at-pop', {timeout: 5000});
    } catch {
      const state = await page.evaluate(() => {
        const editor = document.querySelector('.jim-prompt');
        return {
          value: editor?.dataset.promptValue || '',
          text: editor?.textContent || '',
          active: document.activeElement?.className || '',
          atVisible: !!document.querySelector('.jim-at-pop'),
        };
      });
      throw new Error(`裸 @ 未唤起候选：${JSON.stringify(state)}`);
    }
    const names = await page.$$eval('.jim-at-pop .jim-at-name', (nodes) => nodes.map((node) => node.textContent || ''));
    if (!names.some((name) => name.includes('角色｜手动甲'))) throw new Error(`@ 候选缺少资产：${names.join(',')}`);

    await page.keyboard.press('Enter');
    await page.waitForSelector('.jim-at-pop', {hidden: true, timeout: 5000});
    await page.focus('.jim-prompt');
    await page.keyboard.press('End');
    await page.keyboard.type(' 中文 @', {delay: 120});
    try {
      await page.waitForSelector('.jim-at-pop', {timeout: 5000});
    } catch {
      const state = await page.evaluate(() => {
        const editor = document.querySelector('.jim-prompt');
        const value = editor?.dataset.promptValue || '';
        return {
          value,
          text: editor?.textContent || '',
          regexMatches: /(?:^|\s|@)@\s*$/.test(value),
          active: document.activeElement?.className || '',
          html: editor?.innerHTML || '',
        };
      });
      throw new Error(`已有引用后输入 @ 未唤起候选：${JSON.stringify(state)}`);
    }
    await page.keyboard.press('Enter');
    await sleep(1000);
    const after = await page.evaluate(() => ({
      value: document.querySelector('.jim-prompt')?.dataset.promptValue || '',
      hasMention: !!document.querySelector('.jim-prompt-mention'),
      refs: document.querySelectorAll('.jim-ref-thumb').length,
    }));
    if (!after.value.includes('@角色｜手动甲') || !after.hasMention || after.refs < 1) {
      throw new Error(`手动 @ 选择后未插入：${JSON.stringify(after)}`);
    }

    await page.evaluate(() => {
      const editor = document.querySelector('.jim-prompt');
      if (!(editor instanceof HTMLElement)) throw new Error('提示词编辑器不存在');
      editor.focus();
      const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
      const textNodes = [];
      while (walker.nextNode()) textNodes.push(walker.currentNode);
      const text = textNodes.at(-1);
      if (!text) throw new Error('提示词文本节点不存在');
      const range = document.createRange();
      range.setStart(text, Math.min(4, text.textContent?.length || 0));
      range.collapse(true);
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
    });
    await page.keyboard.type('@', {delay: 100});
    try {
      await page.waitForSelector('.jim-at-pop', {timeout: 5000});
    } catch {
      const state = await page.evaluate(() => {
        const editor = document.querySelector('.jim-prompt');
        const selection = window.getSelection();
        return {
          value: editor?.dataset.promptValue || '',
          text: editor?.textContent || '',
          html: editor?.innerHTML || '',
          anchor: selection?.anchorNode?.textContent || '',
          offset: selection?.anchorOffset || 0,
        };
      });
      throw new Error(`光标中间输入 @ 未唤起：${JSON.stringify(state)}`);
    }
    await page.keyboard.press('Escape');
    await page.waitForSelector('.jim-at-pop', {hidden: true, timeout: 5000});
    const middleState = await page.evaluate(() => ({
      value: document.querySelector('.jim-prompt')?.dataset.promptValue || '',
      active: document.activeElement?.className || '',
    }));

    console.log(JSON.stringify({ok: true, projectId, names, after, middleState}, null, 2));
  } finally {
    await browser.close();
  }
} finally {
  await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
}
