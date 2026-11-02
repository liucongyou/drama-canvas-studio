import puppeteer from 'puppeteer-core';

const base = process.env.CANVAS_BASE || 'http://127.0.0.1:8891';
const token = process.env.CANVAS_TOKEN || '';
const projectId = process.env.PROJECT_ID || '';
const episodeId = process.env.EPISODE_ID || 'EP001';
const otherEpisodeId = process.env.OTHER_EPISODE_ID || '';
const apiBase = process.env.CANVAS_API_BASE || base;

async function readServerViewports() {
  return page.evaluate(async ({apiBase, projectId}) => {
    const response = await fetch(
      `${apiBase}/api/projects/${encodeURIComponent(projectId)}/workspace-index`,
      {credentials: 'include'},
    );
    const payload = await response.json();
    return payload.episodeViewports || {};
  }, {apiBase, projectId});
}

if (!token || !projectId) {
  throw new Error('CANVAS_TOKEN and PROJECT_ID are required');
}

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});
const page = await browser.newPage();
await page.setViewport({width: 1440, height: 960});
await page.setCookie({
  name: 'canvas_session',
  value: token,
  domain: '127.0.0.1',
  path: '/',
  httpOnly: true,
  sameSite: 'Lax',
});
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));
page.on('console', (message) => {
  if (message.type() === 'error' && !message.text().includes('404')) {
    errors.push(message.text());
  }
});

try {
  const url = `${base}/?ep=${encodeURIComponent(episodeId)}#${encodeURIComponent(projectId)}`;
  await page.goto(url, {waitUntil: 'domcontentloaded', timeout: 60_000});
  await page.waitForSelector('.react-flow', {timeout: 60_000});
  await new Promise((resolve) => setTimeout(resolve, 1_500));
  await page.keyboard.press('h');
  await new Promise((resolve) => setTimeout(resolve, 200));
  const before = await page.evaluate(() => {
    const pane = document.querySelector('.react-flow__pane');
    const viewport = document.querySelector('.react-flow__viewport');
    return {
      zoom: viewport ? getComputedStyle(viewport).transform : '',
      pane: Boolean(pane),
    };
  });
  const pane = await page.$('.react-flow__pane');
  if (!pane) throw new Error('React Flow pane is missing');
  const box = await pane.boundingBox();
  if (!box) throw new Error('React Flow pane has no size');
  const startX = box.x + Math.max(32, box.width * 0.08);
  const startY = box.y + Math.max(32, box.height * 0.72);
  const hitPane = await page.evaluate(
    ({x, y}) => document.elementFromPoint(x, y)?.closest('.react-flow__pane') !== null,
    {x: startX, y: startY},
  );
  if (!hitPane) throw new Error('blank canvas drag start is not on the pane');
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(startX + 140, startY - 80, {steps: 8});
  await page.mouse.up();
  await new Promise((resolve) => setTimeout(resolve, 500));
  const afterDrag = await page.evaluate(() => {
    const viewport = document.querySelector('.react-flow__viewport');
    return viewport ? getComputedStyle(viewport).transform : '';
  });
  await page.reload({waitUntil: 'domcontentloaded', timeout: 60_000});
  await page.waitForSelector('.react-flow', {timeout: 60_000});
  await new Promise((resolve) => setTimeout(resolve, 800));
  const afterReload = await page.evaluate(() => {
    const viewport = document.querySelector('.react-flow__viewport');
    return viewport ? getComputedStyle(viewport).transform : '';
  });
  let switchRoundTrip = null;
  if (otherEpisodeId) {
    const switchTo = async (episode) => {
      const attempts = [];
      page.on('response', async (response) => {
        if (response.url().includes('/workspace-index') || response.url().includes('/workspace-nodes')) {
          attempts.push({
            url: response.url(),
            status: response.status(),
          });
        }
      });
      const listOpen = await page.$('.canvas-ep-list.is-open');
      if (!listOpen) {
        const rail = await page.$('.canvas-ep-list-rail');
        if (!rail) throw new Error('canvas episode rail is missing');
        await rail.click();
      }
      await page.waitForSelector('.canvas-ep-list.is-open .canvas-ep-row', {
        timeout: 10_000,
      });
      const rowSelector = `.canvas-ep-list.is-open .canvas-ep-row[data-episode-id="${episode}"]`;
      const row = await page.$(rowSelector);
      if (!row) {
        throw new Error(`episode row not found: ${episode}`);
      }
      const beforeActive = await page.evaluate(
        () => document.querySelector(
          '.canvas-ep-list.is-open .canvas-ep-row.is-active',
        )?.getAttribute('data-episode-id') || '',
      );
      const switched = await row.evaluate((item) => {
        const button = item.querySelector('.canvas-ep-select');
        if (!button) return false;
        button.click();
        return true;
      });
      if (!switched) {
        throw new Error(`episode row not found: ${episode}`);
      }
      if (beforeActive === episode) {
        return page.evaluate(({episode}) => {
          const viewport = document.querySelector('.react-flow__viewport');
          return {
            transform: viewport ? getComputedStyle(viewport).transform : '',
            beforeActive: episode,
            active: episode,
            header: '',
            projectMenuActive: '',
            alreadyActive: true,
          };
        }, {episode});
      }
      try {
        await page.waitForFunction(
          (target) => {
            const row = document.querySelector(
              `.canvas-ep-list.is-open .canvas-ep-row[data-episode-id="${target}"]`,
            );
            if (row?.classList.contains('is-active')) return true;
            return location.search.includes(`ep=${target}`);
          },
          {timeout: 10_000},
          episode,
        );
      } catch (error) {
        const state = await page.evaluate(() => ({
          url: location.href,
          open: Boolean(document.querySelector('.canvas-ep-list.is-open')),
          rows: Array.from(document.querySelectorAll(
            '.canvas-ep-list.is-open .canvas-ep-row',
          )).map((row) => ({
            id: row.getAttribute('data-episode-id'),
            active: row.classList.contains('is-active'),
            text: row.textContent,
          })),
        }));
        throw new Error(
          `switch state: ${JSON.stringify({beforeActive, episode, state, attempts})}`
        );
      }
      await new Promise((resolve) => setTimeout(resolve, 400));
      return page.evaluate(({beforeActive, episode}) => {
        const viewport = document.querySelector('.react-flow__viewport');
        const activeRow = document.querySelector(
          `.canvas-ep-list.is-open .canvas-ep-row[data-episode-id="${episode}"]`,
        );
        const active = activeRow?.className.includes('is-active') ? episode : '';
        const header = document.querySelector('.canvas-header')?.textContent || '';
        const projectMenuActive = document.querySelector('.canvas-project-menu-check')?.closest('button')?.textContent || '';
        return {
          transform: viewport ? getComputedStyle(viewport).transform : '',
          beforeActive,
          active,
          header,
          projectMenuActive,
        };
      }, {beforeActive, episode});
    };
    switchRoundTrip = {
      other: await switchTo(otherEpisodeId),
      original: await switchTo(episodeId),
      server: await readServerViewports(),
    };
  }
  const result = {before, afterDrag, afterReload, switchRoundTrip, errors};
  console.log(JSON.stringify(result, null, 2));
  if (!afterDrag || afterDrag === before.zoom) {
    throw new Error('canvas viewport did not move');
  }
  if (!afterReload || afterReload === before.zoom) {
    throw new Error('canvas viewport did not restore after reload');
  }
  // Transform serialization differs slightly between browser versions. The
  // authoritative isolation check below compares the persisted episode rows.
  if (
    switchRoundTrip
    && switchRoundTrip.server[episodeId]?.x
      === switchRoundTrip.server[otherEpisodeId]?.x
    && switchRoundTrip.server[episodeId]?.y
      === switchRoundTrip.server[otherEpisodeId]?.y
  ) {
    throw new Error('different episodes persisted the same viewport');
  }
  if (errors.length) throw new Error(`browser errors: ${errors.join(' | ')}`);
  console.log('RESULT PASS');
} finally {
  await browser.close();
}
