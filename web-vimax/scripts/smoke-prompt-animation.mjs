import puppeteer from 'puppeteer-core';

const base = 'http://127.0.0.1:8890';
const title = `提示词动画 ${Date.now()}`;
const nodeId = `prompt-animation-${Date.now()}`;
const secondNodeId = `prompt-animation-next-${Date.now()}`;

async function api(pathname, options = {}) {
  const response = await fetch(base + pathname, {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${response.status} ${JSON.stringify(payload)}`);
  return payload;
}

async function clickCard(page, nodeId) {
  const selector = `[data-id="media-${nodeId}"] .canvas-media-card`;
  await page.waitForSelector(selector, {timeout: 60_000});
  const box = await page.$eval(selector, (element) => {
    const rect = element.getBoundingClientRect();
    return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
  });
  if (!box.width || !box.height) throw new Error(`card is not visible: ${nodeId}`);
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
}

async function readBarState(page, nodeId, anchorVar) {
  const cardSelector = `[data-id="media-${nodeId}"] .canvas-media-card`;
  return page.evaluate((selector, variable) => {
    const bar = document.querySelector('.jim-bar');
    const card = document.querySelector(selector);
    if (!(bar instanceof HTMLElement) || !(card instanceof HTMLElement)) {
      throw new Error('prompt bar or target card missing');
    }
    const barRect = bar.getBoundingClientRect();
    const cardRect = card.getBoundingClientRect();
    return {
      exists: true,
      closing: bar.classList.contains('is-closing'),
      title: bar.querySelector('.jim-head-title')?.textContent?.trim() || '',
      barLeft: barRect.left,
      anchorX: Number.parseFloat(bar.style.getPropertyValue(variable)),
      cardLeft: cardRect.left,
    };
  }, cardSelector, anchorVar);
}

const project = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title}),
});
await api(`/api/projects/${project.id}/nodes`, {
  method: 'PUT',
  body: JSON.stringify({
    nodes: [
      {
        id: nodeId,
        type: 'image',
        title: '提示词动画测试',
        position: {x: 160, y: 120},
        metadata: {
          prompt: '测试提示词弹窗动效',
          status: 'idle',
          params: {ratio: '1:1', clarity: '1.5K', size: '1536x1536'},
        },
      },
      {
        id: secondNodeId,
        type: 'image',
        title: '提示词动画切换测试',
        position: {x: 520, y: 120},
        metadata: {
          prompt: '切换节点时保持弹窗打开',
          status: 'idle',
          params: {ratio: '1:1', clarity: '1.5K', size: '1536x1536'},
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
  await page.goto(base, {waitUntil: 'domcontentloaded', timeout: 60_000});
  await page.waitForSelector('.canvas-project-btn', {timeout: 30_000});
  await page.click('.canvas-project-btn');
  await page.waitForSelector('.canvas-project-menu-list button', {timeout: 10_000});
  await page.evaluate((projectTitle) => {
    const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
      .find((item) => (item.textContent || '').includes(projectTitle));
    if (button instanceof HTMLElement) button.click();
  }, title);
  await clickCard(page, nodeId);
  await page.waitForSelector('.jim-bar', {timeout: 30_000});

  const opening = await page.evaluate(() => {
    const bar = document.querySelector('.jim-bar');
    if (!(bar instanceof HTMLElement)) throw new Error('prompt bar missing');
    const style = getComputedStyle(bar);
    const surface = getComputedStyle(bar, '::before');
    return {
      animation: style.animationName,
      surfaceAnimation: surface.animationName,
      surfaceBackground: surface.backgroundImage,
      closing: bar.classList.contains('is-closing'),
    };
  });
  if (opening.animation === 'none' || opening.surfaceAnimation === 'none' || !opening.surfaceBackground.includes('gradient') || opening.closing) {
    throw new Error(`opening animation missing: ${JSON.stringify(opening)}`);
  }

  await new Promise((resolve) => setTimeout(resolve, 280));
  const firstBar = await readBarState(page, nodeId, '--jim-anchor-x');
  await clickCard(page, secondNodeId);
  await new Promise((resolve) => setTimeout(resolve, 80));
  const followed = await readBarState(page, secondNodeId, '--jim-anchor-x');
  if (
    !followed.exists
    || followed.closing
    || followed.title !== '提示词动画切换测试'
    || Math.abs(followed.anchorX - followed.cardLeft) > 1
    || Math.abs(followed.barLeft - firstBar.barLeft) < 20
  ) {
    throw new Error(`prompt bar did not follow the clicked card: ${JSON.stringify({firstBar, followed})}`);
  }

  await page.evaluate(() => {
    window.__promptCloseSamples = [];
    window.__promptCloseTimer = window.setInterval(() => {
      const bar = document.querySelector('.jim-bar');
      const backdrop = document.querySelector('.jim-backdrop');
      if (!(bar instanceof HTMLElement) || !(backdrop instanceof HTMLElement)) return;
      if (!bar.classList.contains('is-closing')) return;
      window.__promptCloseSamples.push({
        barAnimation: getComputedStyle(bar).animationName,
        backdropAnimation: getComputedStyle(backdrop).animationName,
        backdropClosing: backdrop.classList.contains('is-closing'),
      });
    }, 10);
  });
  await page.keyboard.press('Escape');
  await new Promise((resolve) => setTimeout(resolve, 40));
  await clickCard(page, nodeId);
  await new Promise((resolve) => setTimeout(resolve, 250));
  const closingSamples = await page.evaluate(() => {
    window.clearInterval(window.__promptCloseTimer);
    return window.__promptCloseSamples;
  });
  const closing = closingSamples[0];
  if (!closing) throw new Error('closing animation was not observed');
  if (closing.barAnimation !== 'jim-pop-out' || closing.backdropAnimation !== 'jim-fade-out' || !closing.backdropClosing) {
    throw new Error(`closing animation missing: ${JSON.stringify(closing)}`);
  }
  const switched = await readBarState(page, nodeId, '--jim-anchor-x');
  if (
    !switched.exists
    || switched.closing
    || switched.title !== '提示词动画测试'
    || Math.abs(switched.anchorX - switched.cardLeft) > 1
  ) {
    throw new Error(`prompt did not recover when switching nodes during close: ${JSON.stringify(switched)}`);
  }
  await page.keyboard.press('Escape');
  await page.waitForSelector('.jim-bar', {hidden: true, timeout: 3_000});
  console.log(JSON.stringify({ok: true, opening, followed, closing, switched}, null, 2));
} finally {
  await browser.close();
  await api(`/api/projects/${project.id}`, {method: 'DELETE'}).catch(() => {});
}
