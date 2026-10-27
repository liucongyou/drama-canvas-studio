import fs from 'node:fs/promises';
import path from 'node:path';
import puppeteer from 'puppeteer-core';

const root = path.resolve(import.meta.dirname, '..');
const shots = path.resolve(root, '..', '.screens');
await fs.mkdir(shots, {recursive: true});
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

const errors = [];
try {
  const page = await browser.newPage();
  await page.setViewport({width: 1680, height: 1000, deviceScaleFactor: 1});
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(`console: ${msg.text()}`);
  });
  page.on('pageerror', (error) => errors.push(`page: ${error.message}`));
  page.on('response', async (response) => {
    if (response.status() < 400) return;
    const detail = await response.text().catch(() => '');
    errors.push(`http ${response.status()}: ${response.request().method()} ${response.url()} ${detail.slice(0, 500)}`);
  });
  await page.goto('http://127.0.0.1:8891', {waitUntil: 'networkidle0', timeout: 60000});
  await page.waitForSelector('.shell-topbar', {timeout: 30000});
  const projectList = await fetch('http://127.0.0.1:8890/api/projects').then((response) => response.json());
  let project = null;
  for (const item of projectList.projects || []) {
    const candidate = await fetch(`http://127.0.0.1:8890/api/projects/${encodeURIComponent(item.id)}`).then((response) => response.json());
    const types = new Set((candidate.payload?.nodes || []).map((node) => node.type));
    if (types.has('image') && types.has('audio') && types.has('video')) {
      project = candidate;
      break;
    }
  }
  const projectNodes = project?.payload?.nodes || [];
  if (!project) throw new Error('没有同时包含图片、音色、视频的测试项目');
  const firstImageId = projectNodes.find((node) => node.type === 'image')?.id;
  const firstAudioId = projectNodes.find((node) => node.type === 'audio')?.id;
  const firstVideoId = projectNodes.find((node) => node.type === 'video')?.id;

  const modelButton = await page.$('button.shell-topbar-btn');
  const buttons = await page.$$('button.shell-topbar-btn');
  let settingsButton = null;
  for (const button of buttons) {
    const text = await button.evaluate((node) => node.textContent || '');
    if (text.includes('模型接口')) {
      settingsButton = button;
      break;
    }
  }
  if (!settingsButton) throw new Error('模型接口按钮不存在');
  await settingsButton.click();
  await page.waitForSelector('.model-settings-dialog', {timeout: 15000});
  await sleep(800);

  const summary = await page.evaluate(() => {
    const dialog = document.querySelector('.model-settings-dialog');
    return {
      text: dialog?.textContent || '',
      providers: document.querySelectorAll('.model-settings-list-item').length,
      models: document.querySelectorAll('.model-settings-model-item').length,
      overflowX: dialog ? dialog.scrollWidth > dialog.clientWidth + 1 : false,
      overflowY: dialog ? dialog.scrollHeight > dialog.clientHeight + 1 : false,
    };
  });
  for (const expected of ['火山方舟', 'TeamRouter', '阿里云百炼', 'Seedream 5.0 Pro', 'Seedance 2.5', 'DeepSeek V4.1 Flash 1017', 'CosyVoice v3.5 Flash']) {
    if (!summary.text.includes(expected)) throw new Error(`模型设置缺少 ${expected}`);
  }
  if (summary.providers !== 3) throw new Error(`供应商数量异常：${summary.providers}`);
  if (summary.models < 7) throw new Error(`模型数量异常：${summary.models}`);
  if (summary.overflowX) throw new Error('模型设置弹窗横向溢出');

  await page.screenshot({path: path.join(shots, 'model-settings-20261025.png'), fullPage: false});
  await page.setViewport({width: 1024, height: 720, deviceScaleFactor: 1});
  await sleep(400);
  const compact = await page.evaluate(() => {
    const dialog = document.querySelector('.model-settings-dialog');
    return {
      overflowX: dialog ? dialog.scrollWidth > dialog.clientWidth + 1 : false,
      width: dialog?.getBoundingClientRect().width || 0,
      viewport: window.innerWidth,
    };
  });
  if (compact.overflowX || compact.width > compact.viewport) throw new Error(`紧凑视口弹窗溢出：${JSON.stringify(compact)}`);
  await page.screenshot({path: path.join(shots, 'model-settings-compact-20261025.png'), fullPage: false});
  await page.setViewport({width: 1680, height: 1000, deviceScaleFactor: 1});
  await page.click('.model-settings-header-actions .canvas-inspector-close');
  await page.waitForSelector('.react-flow', {timeout: 60000});
  await page.click('.canvas-project-btn');
  await page.waitForSelector('.canvas-project-menu', {timeout: 10000});
  const selectedProject = await page.evaluate((title) => {
    const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
      .find((item) => (item.textContent || '').includes(title));
    if (!(button instanceof HTMLElement)) return false;
    button.click();
    return true;
  }, project.title || '');
  if (!selectedProject) throw new Error(`项目菜单找不到：${project.title}`);
  await page.waitForSelector(`[data-id="media-${firstImageId}"] .canvas-media-card`, {timeout: 60000});
  await sleep(2500);

  const nodeSummary = await page.evaluate(() => {
    const nodes = [...document.querySelectorAll('.react-flow__node')];
    return nodes.map((node) => ({
      id: node.getAttribute('data-id') || '',
      className: node.className,
      text: (node.textContent || '').trim().slice(0, 120),
    })).slice(0, 30);
  });
  if (!nodeSummary.length) throw new Error('画布没有节点');

  const imageNode = firstImageId
    ? await page.$(`[data-id="media-${firstImageId}"] .canvas-media-card`)
    : await page.$('.react-flow__node-media .canvas-media-card');
  if (imageNode) {
    await imageNode.click();
    await page.waitForSelector('.jim-bar', {timeout: 15000});
    await sleep(500);
    const toolbarText = await page.$eval('.jim-bar', (node) => node.textContent || '');
    if (!toolbarText.includes('Seedream 5.0 Pro')) throw new Error('图片节点未显示模型');
    if (toolbarText.includes('火山方舟')) throw new Error('媒体节点参数栏不应显示供应商');
    await page.screenshot({path: path.join(shots, 'media-params-20261025.png'), fullPage: false});
    await page.keyboard.press('Escape');
    await page.waitForSelector('.jim-bar', {hidden: true, timeout: 10000});
  }

  const audioNode = firstAudioId
    ? await page.$(`[data-id="media-${firstAudioId}"] .canvas-media-card`)
    : await page.$('[data-id^="media-audio-"] .canvas-media-card');
  if (audioNode) {
    await audioNode.click();
    await page.waitForSelector('.jim-bar[data-kind="audio"]', {timeout: 15000});
    await sleep(350);
    const text = await page.$eval('.jim-bar[data-kind="audio"]', (node) => node.textContent || '');
    if (!text.includes('CosyVoice')) throw new Error('音色节点未显示百炼 TTS 模型');
    if (!text.includes('音色设计') && !text.includes('指定音色')) throw new Error('音色节点缺少模式参数');
    await page.screenshot({path: path.join(shots, 'audio-params-20261025.png'), fullPage: false});
    await page.keyboard.press('Escape');
    await page.waitForSelector('.jim-bar', {hidden: true, timeout: 10000});
  }

  if (!firstVideoId) throw new Error('没有找到可验证的视频节点');
  const videoNode = await page.$(`[data-id="media-${firstVideoId}"] .canvas-media-card`);
  if (!videoNode) throw new Error('视频节点未渲染');
  await videoNode.click();
  await page.waitForSelector('.jim-bar[data-kind="video"]', {timeout: 15000});
  await sleep(350);
  const videoText = await page.$eval('.jim-bar[data-kind="video"]', (node) => node.textContent || '');
  if (!videoText.includes('Seedance')) throw new Error('视频节点未显示 Seedance 模型');
  await page.click('.jim-bar .jim-tool.is-model');
  await page.waitForSelector('.jim-pop-model', {timeout: 10000});
  const modelMenuText = await page.$eval('.jim-pop-model', (node) => node.textContent || '');
  for (const label of ['Seedance 2.0', 'Seedance 2.0 mini', 'Seedance 2.0 fast', 'Seedance 2.5']) {
    if (!modelMenuText.includes(label)) throw new Error(`视频模型菜单缺少 ${label}`);
  }
  await page.screenshot({path: path.join(shots, 'video-model-menu-20261025.png'), fullPage: false});
  await page.keyboard.press('Escape');
  await page.screenshot({path: path.join(shots, 'video-params-20261025.png'), fullPage: false});

  await fs.writeFile(
    path.join(shots, 'model-settings-smoke.json'),
    JSON.stringify({summary, nodeSummary, errors}, null, 2),
    'utf8',
  );
  if (errors.length) throw new Error(errors.join('\n'));
  console.log(JSON.stringify({ok: true, summary, nodes: nodeSummary.length}, null, 2));
} finally {
  await browser.close();
}
