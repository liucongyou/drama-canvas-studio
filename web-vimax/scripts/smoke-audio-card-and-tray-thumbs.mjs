import puppeteer from 'puppeteer-core';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const base = 'http://127.0.0.1:8890';
const stamp = Date.now();

async function api(pathname, options = {}) {
  const response = await fetch(base + pathname, {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${response.status} ${JSON.stringify(payload)}`);
  return payload;
}

const source = await api('/api/projects/gk6woLnGjh998UeNwvhqYG');
const sourceNodes = source.payload.nodes || [];
const sourceImage = sourceNodes.find((node) => node.type === 'image' && node.metadata?.content);
const sourceAudio = sourceNodes.find((node) => node.type === 'audio' && node.metadata?.content);
if (!sourceImage || !sourceAudio) throw new Error('source media nodes missing');

const project = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title: `音频卡片与缩略图回归 ${stamp}`}),
});
await api(`/api/projects/${project.id}/nodes`, {
  method: 'PUT',
  body: JSON.stringify({
    nodes: [
      {
        id: `theme-image-${stamp}`,
        type: 'image',
        title: '角色｜回归角色（图999）',
        position: {x: 0, y: 0},
        metadata: {
          status: 'done',
          content: sourceImage.metadata.content,
          params: {ratio: '1:1', clarity: '1.5K', refs: []},
        },
      },
      {
        id: `theme-audio-${stamp}`,
        type: 'audio',
        title: '音频｜回归角色（图999）',
        position: {x: 360, y: 0},
        metadata: {
          status: 'done',
          content: sourceAudio.metadata.content,
          params: {ttsMode: 'design', outputFormat: 'mp3', refs: []},
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
  await page.evaluate(() => localStorage.setItem('vimax-web-theme', 'light'));
  await page.goto(`${base}/#${encodeURIComponent(project.id)}`, {waitUntil: 'domcontentloaded', timeout: 60_000});
  await page.waitForSelector('.canvas-audio-shell', {timeout: 60_000});
  await page.waitForFunction(() => (
    document.documentElement.dataset.theme === 'light'
    && document.documentElement.dataset.variant === 'light'
  ));

  const audioTheme = await page.evaluate(() => {
    const shell = document.querySelector('.canvas-audio-shell');
    const card = shell?.closest('.canvas-media-card');
    if (!(shell instanceof HTMLElement) || !(card instanceof HTMLElement)) {
      throw new Error('audio card structure missing');
    }
    const shellStyle = getComputedStyle(shell);
    const cardStyle = getComputedStyle(card);
    const rgb = (value) => value.match(/rgb\([^)]+\)/g) || [];
    return {
      cardBackground: cardStyle.backgroundImage,
      shellBackground: shellStyle.backgroundImage,
      cardStops: rgb(cardStyle.backgroundImage).slice(-3),
      shellStops: rgb(shellStyle.backgroundImage).slice(-3),
    };
  });
  if (
    audioTheme.cardStops.length !== 3
    || audioTheme.shellStops.length !== 3
    || audioTheme.cardStops.join('|') !== audioTheme.shellStops.join('|')
    || audioTheme.shellBackground.includes('rgb(25, 25, 27)')
  ) {
    throw new Error(`audio shell does not inherit card theme: ${JSON.stringify(audioTheme)}`);
  }

  const railTrigger = await page.$('.canvas-ep-list-rail');
  if (railTrigger) {
    await railTrigger.click();
    await page.waitForSelector('.canvas-ep-list.is-open', {timeout: 5_000});
  }
  await page.evaluate(() => {
    const tab = [...document.querySelectorAll('.canvas-rail-tab')]
      .find((item) => (item.textContent || '').includes('资产'));
    if (!(tab instanceof HTMLElement)) throw new Error('asset tab missing');
    tab.click();
  });
  await page.waitForSelector(`.canvas-tray-item[data-node-id="theme-image-${stamp}"] img.canvas-tray-thumb`, {timeout: 10_000});
  await page.waitForFunction((nodeId) => {
    const image = document.querySelector(`.canvas-tray-item[data-node-id="${nodeId}"] img.canvas-tray-thumb`);
    return image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0;
  }, {timeout: 20_000}, `theme-image-${stamp}`);
  const thumb = await page.evaluate((nodeId) => {
    const image = document.querySelector(`.canvas-tray-item[data-node-id="${nodeId}"] img.canvas-tray-thumb`);
    const wrap = image?.closest('.canvas-tray-thumb-wrap');
    if (!(image instanceof HTMLImageElement) || !(wrap instanceof HTMLElement)) {
      throw new Error('asset thumbnail structure missing');
    }
    const imageRect = image.getBoundingClientRect();
    const wrapRect = wrap.getBoundingClientRect();
    return {
      objectFit: getComputedStyle(image).objectFit,
      objectPosition: getComputedStyle(image).objectPosition,
      naturalWidth: image.naturalWidth,
      naturalHeight: image.naturalHeight,
      imageRect: {width: imageRect.width, height: imageRect.height},
      wrapRect: {width: wrapRect.width, height: wrapRect.height},
    };
  }, `theme-image-${stamp}`);
  if (
    thumb.objectFit !== 'cover'
    || thumb.objectPosition !== '50% 50%'
    || thumb.naturalWidth <= 0
    || Math.abs(thumb.wrapRect.width - thumb.wrapRect.height) > 0.5
    || thumb.imageRect.width < thumb.wrapRect.width - 2
    || thumb.imageRect.height < thumb.wrapRect.height - 2
  ) {
    throw new Error(`asset thumbnail is not filling its slot: ${JSON.stringify(thumb)}`);
  }

  const screenshotDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'data');
  await fs.mkdir(screenshotDir, {recursive: true});
  const screenshotPath = path.join(screenshotDir, `audio-card-thumbnail-${stamp}.png`);
  await page.screenshot({path: screenshotPath, fullPage: false});

  console.log(JSON.stringify({ok: true, audioTheme, thumb, screenshotPath}, null, 2));
} finally {
  await browser.close();
  await api(`/api/projects/${project.id}`, {method: 'DELETE'}).catch(() => {});
}
