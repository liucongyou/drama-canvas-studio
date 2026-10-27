import path from 'node:path';
import puppeteer from 'puppeteer-core';

const base = 'http://127.0.0.1:8890';
const title = `生成提示稳定性 ${Date.now()}`;
const mediaUrl = '/uploads/generated-1792954217-8db1488463.mp4';
const screenshot = path.resolve('../data/generation-chip-stability-20261026.png');

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
const nodeId = `generation-chip-${Date.now()}`;
const refNodeId = `generation-chip-ref-${Date.now()}`;
const refUrl = '/uploads/generated-1792925149-c4533adafd.png';

await api(`/api/projects/${project.id}/nodes`, {
  method: 'PUT',
  body: JSON.stringify({
    nodes: [
      {
        id: refNodeId,
        type: 'image',
        title: '引用资产',
        position: {x: 40, y: 120},
        metadata: {
          prompt: '引用资产',
          status: 'done',
          content: refUrl,
        },
      },
      {
        id: nodeId,
        type: 'video',
        title: '段1 · 生成提示稳定性',
        position: {x: 420, y: 120},
        metadata: {
          prompt: '测试生成提示条稳定性',
          status: 'done',
          content: mediaUrl,
          model: 'doubao-seedance-2-0',
          refs: [{nodeId: refNodeId, label: '引用资产', url: refUrl}],
          params: {
            provider: 'ark',
            model: 'doubao-seedance-2-0',
            ratio: '9:16',
            resolution: '480P',
            duration: 5,
            count: 1,
            refs: [{nodeId: refNodeId, label: '引用资产', url: refUrl}],
            voiceRefs: [],
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

let releaseGenerate;
let generateSeen = 0;
try {
  const page = await browser.newPage();
  const holdGenerate = new Promise((resolve) => { releaseGenerate = resolve; });
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.setRequestInterception(true);
  page.on('request', (request) => {
    if (request.method() === 'POST' && request.url().includes(`/nodes/${nodeId}/generate`)) {
      generateSeen += 1;
      void holdGenerate.then(() => request.respond({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({url: mediaUrl}),
      }));
      return;
    }
    void request.continue();
  });

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
  await page.waitForSelector('.jim-bar[data-kind="video"] .jim-gen', {timeout: 30_000});
  await page.evaluate((targetNodeId) => {
    window.__generationChipSamples = [];
    window.__generationChipTimer = window.setInterval(() => {
      const chip = document.querySelector('.canvas-task-chip');
      const marker = document.querySelector('.canvas-task-spin');
      const busyEdge = document.querySelector('.canvas-edge-flow.is-busy');
      const card = document.querySelector(`[data-id="media-${targetNodeId}"] .canvas-media-node`);
      const chipStyle = chip instanceof HTMLElement ? getComputedStyle(chip) : null;
      const markerStyle = marker instanceof HTMLElement ? getComputedStyle(marker) : null;
      window.__generationChipSamples.push({
        at: performance.now(),
        chipPresent: chip instanceof HTMLElement,
        chipOpacity: chipStyle?.opacity || '',
        chipVisibility: chipStyle?.visibility || '',
        markerAnimation: markerStyle?.animationName || '',
        busyEdgePresent: busyEdge instanceof SVGElement,
        cardBusy: card?.classList.contains('is-busy') || false,
        text: chip?.textContent?.trim() || '',
      });
    }, 50);
  }, nodeId);
  await page.click('.jim-bar[data-kind="video"] .jim-gen');
  await new Promise((resolve) => setTimeout(resolve, 2_500));
  const samples = await page.evaluate(() => {
    window.clearInterval(window.__generationChipTimer);
    return window.__generationChipSamples;
  });

  const firstPresentIndex = samples.findIndex((sample) =>
    sample.chipPresent && sample.busyEdgePresent && sample.cardBusy);
  if (firstPresentIndex < 0) {
    throw new Error(`generation busy state never became ready: ${JSON.stringify({generateSeen, samples})}`);
  }
  const activeSamples = samples.slice(firstPresentIndex);
  const unstable = activeSamples.filter((sample) =>
    !sample.chipPresent
    || sample.chipOpacity !== '1'
    || sample.chipVisibility !== 'visible'
    || sample.markerAnimation !== 'none'
    || !sample.busyEdgePresent
    || !sample.cardBusy);
  if (unstable.length > 0) {
    throw new Error(`generation chip is unstable: ${JSON.stringify({
      generateSeen,
      firstSample: activeSamples[0],
      lastSample: samples.at(-1),
      unstable: unstable.slice(0, 3),
    })}`);
  }

  await page.screenshot({path: screenshot, fullPage: false});
  console.log(JSON.stringify({
    ok: true,
    nodeId,
    samples: activeSamples.length,
    markerAnimation: activeSamples[0]?.markerAnimation,
    busyEdgePresent: activeSamples[0]?.busyEdgePresent,
    cardBusy: activeSamples[0]?.cardBusy,
    presentSamples: samples.filter((sample) => sample.chipPresent).length,
    screenshot,
  }, null, 2));
} finally {
  releaseGenerate?.();
  await browser.close();
  await api(`/api/projects/${project.id}`, {method: 'DELETE'}).catch(() => {});
}
