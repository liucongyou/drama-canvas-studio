import path from 'node:path';
import puppeteer from 'puppeteer-core';

const apiBase = 'http://127.0.0.1:8890';
const projectId = 'gk6woLnGjh998UeNwvhqYG';
const projectTitle = '铁西硬汉 · 画布工作流';
const shotPath = path.resolve('../data/ep09-browser-check-20261026.png');

async function api(pathname) {
  const response = await fetch(apiBase + pathname);
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  return response.json();
}

const project = await api(`/api/projects/${projectId}`);
const ep9 = project.payload.episodeImports.find((group) => group.id === 'EP009');
if (!ep9) throw new Error('EP009 not found');
const nodeById = new Map(project.payload.nodes.map((node) => [node.id, node]));
const segment8 = ep9.videoNodeIds
  .map((nodeId) => nodeById.get(nodeId))
  .find((node) => String(node?.title || '').includes('段8'));
if (!segment8) throw new Error('EP009 segment 8 not found');
const params = segment8.metadata?.params || {};
const expectedRefs = params.refs || [];
const expectedVoices = params.voiceRefs || [];
const expectedPrompt = String(segment8.metadata?.prompt || '');
if (!expectedPrompt.includes('@道具｜匿名纸条（图32）')) {
  throw new Error('segment 8 prompt does not contain anonymous note mention');
}

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

const errors = [];
try {
  const page = await browser.newPage();
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(`console: ${message.text()}`);
  });
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
  await page.setViewport({width: 1680, height: 1000, deviceScaleFactor: 1});
  await page.goto(apiBase, {waitUntil: 'networkidle0', timeout: 60000});

  await page.waitForSelector('.canvas-project-btn', {timeout: 30000});
  await page.click('.canvas-project-btn');
  await page.waitForSelector('.canvas-project-menu', {timeout: 10000});
  const selected = await page.evaluate((title) => {
    const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
      .find((item) => (item.textContent || '').includes(title));
    if (!(button instanceof HTMLElement)) return false;
    button.click();
    return true;
  }, projectTitle);
  if (!selected) throw new Error('project not found in project menu');

  await page.waitForSelector('.react-flow', {timeout: 60000});
  await page.waitForSelector('.canvas-ep-list-body', {timeout: 30000});
  const episodeSelected = await page.evaluate(() => {
    const button = [...document.querySelectorAll('.canvas-ep-list-body button')]
      .find((item) => (item.textContent || '').includes('第09集'));
    if (!(button instanceof HTMLElement)) return false;
    button.click();
    return true;
  });
  if (!episodeSelected) throw new Error('EP009 button not found');

  const mediaSelector = `[data-id="media-${segment8.id}"] .canvas-media-card`;
  await page.waitForSelector(mediaSelector, {timeout: 60000});
  await page.click(mediaSelector);
  await page.waitForSelector('.jim-bar[data-kind="video"]', {timeout: 30000});
  await page.waitForFunction(
    () => document.querySelectorAll('.jim-bar .jim-ref-thumb').length > 0,
    {timeout: 30000},
  );

  const result = await page.evaluate(() => {
    const bar = document.querySelector('.jim-bar');
    return {
      barText: bar?.textContent || '',
      mentionTexts: [...document.querySelectorAll('.jim-bar .jim-prompt-mention')]
        .map((node) => (node.textContent || '').trim()),
      thumbCount: document.querySelectorAll('.jim-bar .jim-ref-thumb').length,
      audioThumbCount: document.querySelectorAll('.jim-bar .jim-ref-thumb.is-audio').length,
    };
  });
  const previewChip = await page.$('.jim-bar .jim-prompt-mention.has-preview');
  if (!previewChip) throw new Error('no generated asset mention chip has an image preview');
  await previewChip.hover();
  await page.waitForSelector('.jim-mention-preview img', {timeout: 10000});
  const preview = await page.$eval('.jim-mention-preview img', (image) => ({
    src: image.getAttribute('src') || '',
    loaded: image.naturalWidth > 0 && image.naturalHeight > 0,
  }));
  if (!preview.loaded) throw new Error(`mention preview image did not load: ${preview.src}`);
  if (!result.mentionTexts.some((text) => text.includes('匿名纸条'))) {
    throw new Error(`anonymous note mention chip missing: ${JSON.stringify(result)}`);
  }
  if (result.thumbCount !== expectedRefs.length + expectedVoices.length) {
    throw new Error(
      `thumbnail count mismatch: ${result.thumbCount} != ${expectedRefs.length + expectedVoices.length}`,
    );
  }
  await page.screenshot({path: shotPath, fullPage: false});
  if (errors.length) throw new Error(errors.join('\n'));
  console.log(JSON.stringify({
    ok: true,
    projectId,
    episode: 'EP009',
    segment: segment8.title,
    refs: expectedRefs.length,
    voices: expectedVoices.length,
    mentionChips: result.mentionTexts,
    mentionPreview: preview.src,
    thumbnails: result.thumbCount,
    screenshot: shotPath,
  }, null, 2));
} finally {
  await browser.close();
}
