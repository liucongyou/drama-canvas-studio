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

const created = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title: '引用资产 @ 契约测试'}),
});
const projectId = created.id;
const pixel = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';

const nodes = [
  {
    id: 'at-source-image',
    type: 'image',
    title: '角色｜测试甲',
    position: {x: 0, y: 0},
    metadata: {
      prompt: '测试角色',
      status: 'done',
      content: pixel,
      params: {
        provider: 'ark',
        model: 'doubao-seedream-5-0-pro',
        ratio: '1:1',
        clarity: '1.5K',
        size: '1536x1536',
      },
    },
  },
  {
    id: 'at-target-video',
    type: 'video',
    title: '引用测试视频',
    position: {x: 420, y: 0},
    metadata: {
      prompt: '镜头从门口推进',
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
    id: 'at-source-voice',
    type: 'audio',
    title: '音频｜测试甲',
    position: {x: 0, y: 300},
    metadata: {
      prompt: '低沉东北口音男声',
      status: 'idle',
      params: {
        provider: 'aliyun_bailian',
        model: 'cosyvoice-v3-5-flash',
        ttsMode: 'design',
      },
    },
  },
];
await api(`/api/projects/${projectId}/nodes`, {
  method: 'PUT',
  body: JSON.stringify({nodes}),
});

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});
try {
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  const browserErrors = [];
  page.on('pageerror', (error) => browserErrors.push(`page: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() === 'error') browserErrors.push(`console: ${message.text()}`);
  });
  await page.goto('http://127.0.0.1:8891', {waitUntil: 'networkidle0', timeout: 60000});
  try {
    await page.waitForSelector('.canvas-project-btn', {timeout: 30000});
  } catch (error) {
    const body = await page.$eval('body', (node) => (node.textContent || '').slice(0, 500)).catch(() => '');
    throw new Error(`页面入口未加载：${browserErrors.join('\n') || (error instanceof Error ? error.message : String(error))}\nbody=${body}`);
  }
  await page.click('.canvas-project-btn');
  await page.waitForSelector('.canvas-project-menu', {timeout: 10000});
  const selectedProject = await page.evaluate(() => {
    const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
      .find((item) => (item.textContent || '').includes('引用资产 @ 契约测试'));
    if (!(button instanceof HTMLElement)) return false;
    button.click();
    return true;
  });
  if (!selectedProject) throw new Error('测试项目未出现在项目菜单');
  try {
    await page.waitForSelector('.react-flow', {timeout: 60000});
  } catch (error) {
    throw new Error(`画布未加载：${browserErrors.join('\n') || (error instanceof Error ? error.message : String(error))}`);
  }
  await page.waitForSelector('[data-id="media-at-target-video"] .canvas-media-card', {timeout: 60000});
  await sleep(800);

  const target = await page.$('[data-id="media-at-target-video"] .canvas-media-card');
  if (!target) throw new Error('目标视频节点未渲染');
  await target.click();
  await page.waitForSelector('.jim-bar[data-kind="video"]', {timeout: 15000});
  await page.click('.jim-bar .jim-tool[title="@ 引用素材"]');
  await page.waitForSelector('.jim-at-pop', {timeout: 10000});
  const clicked = await page.evaluate(() => {
    const item = [...document.querySelectorAll('.jim-at-pop .jim-at-item')]
      .find((node) => (node.textContent || '').includes('角色｜测试甲'));
    if (!(item instanceof HTMLElement)) return false;
    item.click();
    return true;
  });
  if (!clicked) throw new Error('@ 候选未显示测试角色');
  await sleep(1200);

  const prompt = await page.$eval('.jim-prompt', (node) => node.dataset.promptValue || node.textContent || '');
  if (!prompt.includes('@角色｜测试甲')) throw new Error(`@ 插入失败：${prompt}`);
  const inlineImage = await page.$('.jim-prompt-mention img');
  if (!inlineImage) throw new Error('提示词输入框内未显示 @ 资产图片');
  await page.click('.jim-bar .jim-tool[title="@ 引用素材"]');
  await page.waitForSelector('.jim-at-pop', {timeout: 10000});
  const clickedVoice = await page.evaluate(() => {
    const item = [...document.querySelectorAll('.jim-at-pop .jim-at-item')]
      .find((node) => (node.textContent || '').includes('音频｜测试甲'));
    if (!(item instanceof HTMLElement)) return false;
    if (!item.querySelector('img')) return false;
    item.click();
    return true;
  });
  if (!clickedVoice) throw new Error('@ 候选未显示测试音色');
  await sleep(1400);
  const voiceInlineImage = await page.$('.jim-prompt-mention.is-audio img');
  if (!voiceInlineImage) {
    const html = await page.$eval('.jim-prompt', (node) => node.innerHTML);
    throw new Error(`提示词输入框内未显示 @ 音色对应角色图：${html}`);
  }
  const topReferenceImages = await page.$$eval('.jim-bar .jim-ref-thumb img', (nodes) => nodes.length);
  if (topReferenceImages < 2) throw new Error(`已内联的引用未在上方继续显示缩略图：${topReferenceImages}`);
  await page.hover('.jim-bar .jim-ref-thumb');
  await page.waitForSelector('.jim-mention-preview img', {timeout: 5000});
  const topPreviewReady = await page.$eval('.jim-mention-preview img', (node) => node.complete && node.naturalWidth > 0);
  if (!topPreviewReady) throw new Error('上方引用缩略图悬停原图浮层失败');
  await page.focus('.jim-prompt');
  await page.keyboard.press('End');
  await page.keyboard.type(' stable', {delay: 140});
  await sleep(900);
  await page.focus('.jim-prompt');
  await page.keyboard.press('End');
  await page.keyboard.type(' @', {delay: 120});
  try {
    await page.waitForSelector('.jim-at-pop', {timeout: 8000});
  } catch {
    throw new Error('手动输入 @ 没有弹出资产候选');
  }
  const clickedManual = await page.evaluate(() => {
    const item = [...document.querySelectorAll('.jim-at-pop .jim-at-item')]
      .find((node) => (node.textContent || '').includes('角色｜测试甲'));
    if (!(item instanceof HTMLElement)) return false;
    item.click();
    return true;
  });
  if (!clickedManual) throw new Error('手动输入 @ 后无法选择资产');
  await sleep(1000);
  const visiblePrompt = await page.$eval('.jim-prompt', (node) => node.innerText || '');
  if (visiblePrompt.includes('@')) throw new Error(`输入框仍显示裸 @：${visiblePrompt}`);
  const internalPrompt = await page.$eval('.jim-prompt', (node) => node.dataset.promptValue || '');
  if (internalPrompt.trimEnd().endsWith('@')) throw new Error('触发用的裸 @ 未被消费');
  await fs.mkdir(shots, {recursive: true});
  await page.screenshot({path: path.join(shots, 'at-assets-contract-20261025.png'), fullPage: false});
  await page.hover('.jim-prompt-mention img');
  await sleep(250);
  const hoverTransform = await page.$eval('.jim-prompt-mention img', (node) => getComputedStyle(node).transform);
  await page.waitForSelector('.jim-mention-preview img', {timeout: 5000});
  const preview = await page.$eval('.jim-mention-preview img', (node) => ({
    width: node.getBoundingClientRect().width,
    height: node.getBoundingClientRect().height,
    ready: node.complete && node.naturalWidth > 0,
  }));
  if (!preview.ready || preview.width < 200 || preview.height < 200) {
    throw new Error(`资产原图悬停浮层未生效：${JSON.stringify(preview)}`);
  }
  await page.screenshot({path: path.join(shots, 'at-assets-hover-20261025.png'), fullPage: false});

  const project = await api(`/api/projects/${projectId}`);
  const targetNode = project.payload.nodes.find((node) => node.id === 'at-target-video');
  const refs = targetNode?.metadata?.params?.refs || [];
  const voiceRefs = targetNode?.metadata?.params?.voiceRefs || [];
  if (!refs.some((ref) => ref.nodeId === 'at-source-image')) {
    throw new Error(`节点引用未持久化：${JSON.stringify(refs)}`);
  }
  if (!voiceRefs.some((ref) => ref.nodeId === 'at-source-voice')) {
    throw new Error(`音色引用未持久化：${JSON.stringify(voiceRefs)}`);
  }
  if (!String(targetNode?.metadata?.prompt || '').includes('stable')) {
    throw new Error(`富文本提示词编辑未持久化：${targetNode?.metadata?.prompt}`);
  }
  const finalPrompt = await page.$eval('.jim-prompt', (node) => node.dataset.promptValue || node.textContent || '');
  await page.click('.jim-bar .jim-ref-thumb button');
  await sleep(1000);
  const afterRemoval = await api(`/api/projects/${projectId}`);
  const removedNode = afterRemoval.payload.nodes.find((node) => node.id === 'at-target-video');
  const remainingRefs = removedNode?.metadata?.params?.refs || [];
  const remainingPrompt = String(removedNode?.metadata?.prompt || '');
  if (remainingRefs.some((ref) => ref.nodeId === 'at-source-image')) {
    throw new Error('删除缩略图后引用参数仍残留');
  }
  if (remainingPrompt.includes('@角色｜测试甲')) {
    throw new Error('删除缩略图后提示词内联引用仍残留');
  }
  console.log(JSON.stringify({ok: true, prompt: finalPrompt, refs, voiceRefs, hoverTransform, preview, topReferenceImages, projectId}, null, 2));
} finally {
  await browser.close();
  await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
}
