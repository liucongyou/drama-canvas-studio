import puppeteer from 'puppeteer-core';

const apiBase = 'http://127.0.0.1:8890';
const feedPath = 'C:\\Users\\liuch\\Doubao\\chats\\2026-10-15\\new-chat\\铁西硬汉_第01集_投喂载荷.json';
const title = 'V7429 导入兼容浏览器测试';
const reusePixel = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';
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

const seedProject = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title: '角色资产复用种子项目'}),
});
await api(`/api/projects/${seedProject.id}/nodes`, {
  method: 'PUT',
  body: JSON.stringify({
    nodes: [{
      id: 'seed-zhaotiezhu',
      type: 'image',
      title: '角色｜赵铁柱（图9）',
      position: {x: 0, y: 0},
      metadata: {
        prompt: '已有赵铁柱资产',
        status: 'done',
        content: reusePixel,
        params: {provider: 'ark', model: 'doubao-seedream-5-0-pro', ratio: '16:9', size: '1536x864'},
      },
    }],
  }),
});
const created = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title}),
});
const projectId = created.id;

async function selectProject(page, projectTitle = title) {
  await page.waitForSelector('.canvas-project-btn', {timeout: 30000});
  await page.click('.canvas-project-btn');
  await page.waitForSelector('.canvas-project-menu', {timeout: 10000});
  const selected = await page.evaluate((projectTitle) => {
    const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
      .find((item) => (item.textContent || '').includes(projectTitle));
    if (!(button instanceof HTMLElement)) return false;
    button.click();
    return true;
  }, projectTitle);
  if (!selected) throw new Error('导入测试项目未出现在菜单');
}

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.goto('http://127.0.0.1:8891', {waitUntil: 'networkidle0', timeout: 60000});
  await selectProject(page);

  await page.click('button[title^="项目维护"]');
  await page.waitForSelector('.shell-tools-menu', {timeout: 10000});
  const fileInput = await page.$('.shell-tools-menu input[type="file"][accept="application/json,.json"]');
  if (!fileInput) throw new Error('项目设置中没有本地 JSON 文件选择入口');
  await fileInput.uploadFile(feedPath);
  await page.waitForSelector('.gdialog', {timeout: 30000});
  const dialog = await page.$eval('.gdialog', (node) => node.textContent || '');
  if (!dialog.includes('导入完成') || !dialog.includes('22 个节点') || !dialog.includes('57 条连线')
    || !dialog.includes('复用已有资产 1 个')) {
    throw new Error(`导入结果异常：${dialog}`);
  }
  await Promise.all([
    page.waitForNavigation({waitUntil: 'networkidle0', timeout: 60000}),
    page.click('.gdialog-btn.is-primary'),
  ]);

  try {
    await page.waitForSelector('.react-flow', {timeout: 10000});
  } catch {
    await selectProject(page, '铁西硬汉 · 第01集 · 投喂看板源');
  }
  await page.waitForSelector('.react-flow', {timeout: 60000});
  await page.waitForFunction(() => document.querySelectorAll('.react-flow__node-media').length >= 22, {timeout: 60000});
  const domCount = await page.$$eval('.react-flow__node-media', (nodes) => nodes.length);
  if (domCount !== 22) throw new Error(`画布节点数量错误：${domCount}`);

  const project = await api(`/api/projects/${projectId}`);
  const reusedCharacter = project.payload.nodes.find(
    (node) => node.type === 'image' && node.title.includes('赵铁柱'),
  );
  if (reusedCharacter?.metadata?.status !== 'done'
    || reusedCharacter?.metadata?.content !== reusePixel
    || reusedCharacter?.metadata?.reusedAsset?.sourceProjectId !== seedProject.id) {
    throw new Error(`角色资产未复用：${JSON.stringify(reusedCharacter?.metadata)}`);
  }
  const reuseBadge = await page.$eval(
    `[data-id="media-${reusedCharacter.id}"] .canvas-media-reuse-badge`,
    (node) => node.textContent || '',
  ).catch(() => '');
  if (reuseBadge.trim() !== '复用') throw new Error(`复用资产没有标记：${reuseBadge}`);
  const audio = project.payload.nodes.find((node) => node.type === 'audio' && node.title.includes('钱万金'));
  if (!audio || audio.metadata.content !== '') {
    throw new Error(`音色节点 content 不应写入描述文本：${JSON.stringify(audio?.metadata?.content)}`);
  }
  const audioPlayerCount = await page.$$eval(
    `[data-id="media-${audio.id}"] audio`,
    (nodes) => nodes.length,
  );
  const audioPlaceholderCount = await page.$$eval(
    `[data-id="media-${audio.id}"] .canvas-media-placeholder`,
    (nodes) => nodes.length,
  );
  if (audioPlayerCount !== 0 || audioPlaceholderCount !== 1) {
    throw new Error(`音色卡片结构与原始卡片不一致：player=${audioPlayerCount} placeholder=${audioPlaceholderCount}`);
  }
  const voiceEdgeCount = await page.$$eval('.canvas-edge-voice', (nodes) => nodes.length);
  if (voiceEdgeCount < 1) throw new Error('导入后音色节点没有可见连线');
  const video = project.payload.nodes.find((node) => node.type === 'video' && node.title.includes('段1'));
  if (!video) throw new Error('导入后找不到段1视频节点');
  const params = video.metadata.params || {};
  if (params.model !== 'doubao-seedance-2-0' || params.duration !== 15 || params.ratio !== '9:16') {
    throw new Error(`视频参数不兼容：${JSON.stringify(params)}`);
  }
  const refs = params.refs || [];
  const voices = params.voiceRefs || [];
  if (refs.length + voices.length > 9) throw new Error(`段1引用超限：${refs.length}+${voices.length}`);
  if (refs.some((ref) => String(ref.label || '').includes('剧本头卡'))) {
    throw new Error(`剧本头卡不应自动成为视频引用：${JSON.stringify(refs)}`);
  }

  await page.click(`[data-id="media-${video.id}"] .canvas-media-card`);
  await page.waitForSelector('.jim-bar[data-kind="video"]', {timeout: 15000});
  const barText = await page.$eval('.jim-bar', (node) => node.textContent || '');
  if (!barText.includes('Seedance 2.0') || !barText.includes('9:16') || !barText.includes('15s')) {
    throw new Error(`画布参数回显错误：${barText.slice(0, 500)}`);
  }
  const thumbs = await page.$$eval('.jim-bar .jim-ref-thumb', (nodes) => nodes.length);
  if (thumbs !== refs.length + voices.length) {
    throw new Error(`引用缩略图数量不一致：${thumbs} != ${refs.length + voices.length}`);
  }

  console.log(JSON.stringify({
    ok: true,
    projectId,
    nodes: domCount,
    connections: project.payload.connections.length,
    reusedCharacter: reusedCharacter.id,
    voiceEdgeCount,
    segment1: {refs: refs.length, voices: voices.length, params},
  }, null, 2));
} finally {
  await browser.close();
  await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
  await api(`/api/projects/${seedProject.id}`, {method: 'DELETE'}).catch(() => {});
}
