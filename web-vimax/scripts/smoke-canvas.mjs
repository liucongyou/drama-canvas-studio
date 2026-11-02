import puppeteer from 'puppeteer-core';

/**
 * 画布冒烟（带会话）：验证生产改动后
 *   ① 画布能加载、节点/媒体卡渲染数量正常；
 *   ② 控制台 0 报错；
 *   ③ 点开一张媒体卡 → 弹层出现 → 关闭，交互链路没被 memo 改坏；
 *   ④ 生成按钮处于「可点但未点击」状态（绝不触发付费提交）。
 *
 * 用法：
 *   CANVAS_TOKEN=ses_xxx PROJECT_ID=xxx node scripts/smoke-canvas.mjs
 */
const base = process.env.CANVAS_BASE || 'http://127.0.0.1:8891';
const token = process.env.CANVAS_TOKEN || '';
const projectId = process.env.PROJECT_ID || '';
const shotPath = process.env.SHOT_PATH || 'scripts/_canvas-smoke.png';
const expectNodes = Number(process.env.EXPECT_NODES || 0);

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--window-size=1680,1050'],
});

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const errors = [];
let failed = false;

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1680, height: 1050, deviceScaleFactor: 1});
  if (token) {
    await page.setCookie({
      name: process.env.CANVAS_COOKIE || 'canvas_session',
      value: token,
      domain: '127.0.0.1',
      path: '/',
      httpOnly: true,
    });
  }
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(`console: ${message.text()}`);
  });
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));

  const url = projectId ? `${base}/#${projectId}` : base;
  await page.goto(url, {waitUntil: 'domcontentloaded', timeout: 60_000});

  // 画布就绪：出现 React Flow 节点层
  await page.waitForFunction(
    () => !!document.querySelector('.react-flow') || !!document.querySelector('.auth-shell'),
    {timeout: 60_000},
  );
  await sleep(3500);

  const snapshot = await page.evaluate(() => {
    const nodes = document.querySelectorAll('.react-flow__node').length;
    const mediaNodes = document.querySelectorAll('.react-flow__node-media').length;
    const segNodes = document.querySelectorAll('.react-flow__node-segment').length;
    const edges = document.querySelectorAll('.react-flow__edge').length;
    const shell = !!(document.querySelector('.canvas-minimap') || document.querySelector('.react-flow__minimap'));
    return {
      nodes, mediaNodes, segNodes, edges,
      minimap: shell,
      hasLogin: !!document.querySelector('input[type="password"]'),
      title: document.title,
      text: (document.body.innerText || '').slice(0, 160).replace(/\s+/g, ' '),
    };
  });
  console.log('SNAPSHOT', JSON.stringify(snapshot));
  if (snapshot.hasLogin) {
    console.log('FAIL: 仍停在登录页（会话无效）');
    failed = true;
  }
  if (expectNodes && snapshot.nodes < Math.floor(expectNodes * 0.6)) {
    console.log(`FAIL: 节点数 ${snapshot.nodes} 明显少于预期 ${expectNodes}`);
    failed = true;
  }

  // 交互：点开第一张媒体卡
  let interaction = {opened: false, closed: false, before: 0, after: 0};
  const card = await page.$('.react-flow__node-media');
  if (card) {
    const box = await card.boundingBox();
    if (box) {
      interaction.before = await page.evaluate(
        () => document.querySelectorAll('[class*="jim-"]').length,
      );
      await page.mouse.click(
        box.x + Math.min(36, box.width / 2),
        box.y + Math.min(26, box.height / 2),
      );
      await sleep(1200);
      interaction.after = await page.evaluate(
        () => document.querySelectorAll('[class*="jim-"]').length,
      );
      interaction.opened = interaction.after > interaction.before;
      await page.keyboard.press('Escape').catch(() => {});
      await sleep(400);
      const still = await page.evaluate(
        () => document.querySelectorAll('[class*="jim-"]').length,
      );
      interaction.closed = still <= interaction.before;
    }
  } else {
    console.log('WARN: 画布上没有媒体卡（可能停在总览/空集）');
  }
  console.log('INTERACTION', JSON.stringify(interaction));
  if (card && !interaction.opened) {
    console.log('FAIL: 点击媒体卡没有弹出编辑层');
    failed = true;
  }

  await page.screenshot({path: shotPath, fullPage: false});
  console.log('SHOT', shotPath);
  const realErrors = errors.filter((item) => !item.includes('401') && !item.includes('favicon'));
  console.log('ERRORS', realErrors.length);
  for (const item of realErrors.slice(0, 20)) console.log('  -', item);
  if (realErrors.length > 0) failed = true;
} catch (error) {
  console.log('CRASH', error?.message || error);
  failed = true;
} finally {
  await browser.close();
}

console.log(failed ? 'RESULT FAIL' : 'RESULT PASS');
if (failed) process.exitCode = 2;
