import fs from 'node:fs/promises';
import path from 'node:path';
import puppeteer from 'puppeteer-core';

const apiBase = 'http://127.0.0.1:8890';
const shots = path.resolve(import.meta.dirname, '..', '..', '.screens');
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
  body: JSON.stringify({title: '移动端参数与提示词上限测试'}),
});
const projectId = project.id;
const longPrompt = '长'.repeat(5000);

try {
  await api(`/api/projects/${projectId}/nodes`, {
    method: 'PUT',
    body: JSON.stringify({
      nodes: [
        {
          id: 'mobile-text',
          type: 'text',
          title: '移动端文本',
          position: {x: 0, y: 0},
          metadata: {
            prompt: longPrompt,
            status: 'idle',
            refs: [],
            params: {
              provider: 'teamorouter',
              model: 'deepseek-flash-1017',
              maxTokens: 3000,
              temperature: 0.7,
            },
          },
        },
        {
          id: 'mobile-video',
          type: 'video',
          title: '移动端视频',
          position: {x: 420, y: 0},
          metadata: {
            prompt: '移动端参数测试',
            status: 'idle',
            refs: [],
            params: {
              provider: 'ark',
              model: 'doubao-seedance-2-5',
              ratio: '9:16',
              resolution: '480P',
              duration: 5,
            },
          },
        },
        {
          id: 'mobile-failed',
          type: 'image',
          title: '失败原因展示',
          position: {x: 840, y: 0},
          metadata: {
            prompt: '失败展示测试',
            status: 'failed',
            lastError: '模拟生成失败：参数无效',
            refs: [],
            params: {
              provider: 'ark',
              model: 'doubao-seedream-5-0-pro',
              ratio: '1:1',
              clarity: '1.5K',
              size: '1536x1536',
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
    await page.setViewport({width: 390, height: 844, deviceScaleFactor: 1});
    await page.goto('http://127.0.0.1:8891', {waitUntil: 'networkidle0', timeout: 60000});
    await page.waitForSelector('.canvas-project-btn', {timeout: 30000});
    await page.click('.canvas-project-btn');
    await page.waitForSelector('.canvas-project-menu', {timeout: 10000});
    const selected = await page.evaluate(() => {
      const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
        .find((item) => (item.textContent || '').includes('移动端参数与提示词上限测试'));
      if (!(button instanceof HTMLElement)) return false;
      button.click();
      return true;
    });
    if (!selected) throw new Error('移动端测试项目未出现在菜单');
    await page.waitForSelector('[data-id="media-mobile-text"] .canvas-media-card', {timeout: 60000});
    await page.evaluate(() => {
      const card = document.querySelector('[data-id="media-mobile-text"] .canvas-media-card');
      if (card instanceof HTMLElement) card.click();
    });
    await page.waitForSelector('.jim-bar[data-kind="text"]', {timeout: 15000});
    await sleep(700);

    const textState = await page.evaluate(() => {
      const bar = document.querySelector('.jim-bar');
      const badge = document.querySelector('.jim-head-badge');
      const rect = bar?.getBoundingClientRect();
      return {
        badge: badge?.textContent || '',
        left: rect?.left || 0,
        right: rect?.right || 0,
        width: rect?.width || 0,
        viewport: window.innerWidth,
        overflow: bar ? bar.scrollWidth > bar.clientWidth + 1 : true,
        generateDisabled: document.querySelector('.jim-gen')?.hasAttribute('disabled') ?? false,
      };
    });
    if (!textState.badge.includes('5000 / 32000')) throw new Error(`文本提示词上限显示错误：${JSON.stringify(textState)}`);
    if (textState.left < -1 || textState.right > textState.viewport + 1 || textState.overflow) {
      throw new Error(`移动端文本参数栏溢出：${JSON.stringify(textState)}`);
    }
    if (textState.generateDisabled) throw new Error('5000 字的文本提示词被错误判为超出上限');
    await page.screenshot({path: path.join(shots, 'mobile-prompt-limit-20261025.png'), fullPage: false});

    await page.keyboard.press('Escape');
    await page.waitForSelector('.jim-bar', {hidden: true, timeout: 10000});
    await page.evaluate(() => {
      const card = document.querySelector('[data-id="media-mobile-video"] .canvas-media-card');
      if (card instanceof HTMLElement) card.click();
    });
    await page.waitForSelector('.jim-bar[data-kind="video"]', {timeout: 15000});
    await page.evaluate(() => {
      const button = document.querySelector('.jim-bar .jim-tool[title="时长"]');
      if (button instanceof HTMLElement) button.click();
    });
    await page.waitForSelector('.jim-pop-dur', {timeout: 10000});
    await sleep(250);
    const durationState = await page.evaluate(() => {
      const pop = document.querySelector('.jim-pop-dur');
      const bar = document.querySelector('.jim-bar');
      const popRect = pop?.getBoundingClientRect();
      const barRect = bar?.getBoundingClientRect();
      return {
        popLeft: popRect?.left || 0,
        popRight: popRect?.right || 0,
        barLeft: barRect?.left || 0,
        barRight: barRect?.right || 0,
        viewport: window.innerWidth,
      };
    });
    if (durationState.popLeft < -1 || durationState.popRight > durationState.viewport + 1
      || durationState.barLeft < -1 || durationState.barRight > durationState.viewport + 1) {
      throw new Error(`移动端时长弹层溢出：${JSON.stringify(durationState)}`);
    }
    await page.screenshot({path: path.join(shots, 'mobile-duration-popover-20261025.png'), fullPage: false});
    await page.keyboard.press('Escape');
    await sleep(150);
    await page.keyboard.press('Escape');
    await page.waitForSelector('.jim-bar', {hidden: true, timeout: 10000});
    await page.evaluate(() => {
      const card = document.querySelector('[data-id="media-mobile-failed"] .canvas-media-card');
      if (card instanceof HTMLElement) card.click();
    });
    await page.waitForSelector('.jim-bar[data-kind="image"]', {timeout: 15000});
    const failureState = await page.evaluate(() => ({
      note: document.querySelector('.jim-validation-notes')?.textContent || '',
      cardSub: document.querySelector('[data-id="media-mobile-failed"] .canvas-media-foot-sub')?.textContent || '',
    }));
    if (!failureState.note.includes('模拟生成失败：参数无效')
      || !failureState.cardSub.includes('模拟生成失败：参数无效')) {
      throw new Error(`生成失败原因未显示：${JSON.stringify(failureState)}`);
    }
    console.log(JSON.stringify({ok: true, projectId, textState, durationState, failureState}, null, 2));
  } finally {
    await browser.close();
  }
} finally {
  await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
}
