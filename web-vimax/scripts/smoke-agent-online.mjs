import puppeteer from 'puppeteer-core';

const base = process.env.CANVAS_BASE || 'https://yj.pduoduo.vip';
const username = process.env.CANVAS_USER || 'admin';
const password = process.env.CANVAS_PASSWORD || '';
const shotPath = process.env.SHOT_PATH || 'scripts/_agent-online-smoke.png';

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--no-proxy-server', '--window-size=1680,1050'],
});
const page = await browser.newPage();
const errors = [];
let failed = false;
let step = 'init';

page.on('console', (message) => {
  if (message.type() === 'error') errors.push(`console: ${message.text()}`);
});
page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

try {
  await page.setViewport({width: 1680, height: 1050, deviceScaleFactor: 1});
  await page.goto(base, {waitUntil: 'domcontentloaded', timeout: 60_000});
  step = 'login';
  const passwordInput = await page.$('input[type="password"]');
  if (passwordInput) {
    const textInputs = await page.$$('input');
    await textInputs[0].click({clickCount: 3});
    await textInputs[0].type(username);
    await passwordInput.click({clickCount: 3});
    await passwordInput.type(password);
    const buttons = await page.$$('button');
    for (const button of buttons) {
      const text = await button.evaluate((node) => node.textContent || '');
      if (text.includes('进入工作台')) {
        await button.click();
        break;
      }
    }
    await page.waitForFunction(
      () => !document.querySelector('input[type="password"]'),
      {timeout: 30_000},
    );
  } else {
    const login = await page.evaluate(async ({username, password}) => {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username, password}),
      });
      return {status: response.status};
    }, {username, password});
    if (login.status !== 200) throw new Error(`login HTTP ${login.status}`);
  }

  const project = await page.evaluate(async () => {
    const response = await fetch('/api/projects', {credentials: 'same-origin'});
    const body = await response.json();
    return {status: response.status, first: body.projects?.[0] || null};
  });
  if (project.status !== 200 || !project.first) throw new Error('no project');

  step = 'open-agent';
  await page.goto(`${base}/#${project.first.id}`, {
    waitUntil: 'domcontentloaded',
    timeout: 60_000,
  });
  await page.waitForFunction(
    () => !!document.querySelector('.agent-fab')
      || !!document.querySelector('.agent-panel')
      || !!document.querySelector('.react-flow'),
    {timeout: 60_000},
  );
  await sleep(1500);
  if (await page.$('.agent-fab')) await page.click('.agent-fab');
  await page.waitForSelector('.agent-panel', {timeout: 20_000});
  await page.waitForSelector('.agent-input-box textarea', {timeout: 20_000});

  step = 'skill';
  if (!(await page.$('.agent-skill-chip'))) {
    await page.click('.agent-tool[title="选择技能"]');
    await page.waitForSelector('.agent-picker-item', {timeout: 15_000});
    await page.click('.agent-picker-item');
  }

  step = 'draft';
  await page.focus('.agent-input-box textarea');
  await page.keyboard.down('Control');
  await page.keyboard.press('KeyA');
  await page.keyboard.up('Control');
  await page.keyboard.sendCharacter('线上只读冒烟：验证 Agent 输入和草稿恢复，不执行生成');
  await sleep(1200);

  step = 'reload';
  await page.reload({waitUntil: 'domcontentloaded', timeout: 60_000});
  await page.waitForSelector('.agent-panel', {timeout: 20_000});
  await page.waitForFunction(
    () => (document.querySelector('.agent-input-box textarea')?.value || '').includes('线上只读冒烟'),
    {timeout: 20_000},
  );
  await page.waitForSelector('.agent-skill-chip', {timeout: 20_000});

  const snapshot = await page.evaluate(() => {
    const panel = document.querySelector('.agent-panel');
    return {
      panel: !!panel,
      composer: !!panel?.querySelector('.agent-input-box textarea'),
      skill: !!panel?.querySelector('.agent-skill-chip'),
      draft: document.querySelector('.agent-input-box textarea')?.value || '',
      apiHost: window.__CANVAS_API_BASE__ || location.origin,
    };
  });
  await page.click('.agent-icon[title="关闭"]');
  await page.waitForSelector('.agent-panel.is-closing', {timeout: 2_000});
  const closeAnimated = true;
  await page.waitForSelector('.agent-fab', {timeout: 5_000});
  await page.click('.agent-fab');
  await page.waitForSelector('.agent-panel', {timeout: 5_000});
  await page.screenshot({path: shotPath, fullPage: false});
  const relevantErrors = errors.filter((item) =>
    !item.includes('favicon')
    && !item.includes('Failed to load resource: the server responded with a status of 404')
    && !item.includes('Failed to load resource: the server responded with a status of 502')
    && !item.includes('doubaoduanju.tos-cn-beijing.volces.com')
    && !item.includes('Failed to load resource: net::ERR_FAILED'),
  );
  console.log(JSON.stringify({
    at: new Date().toISOString(),
    base,
    projectId: project.first.id,
    snapshot,
    closeAnimated,
    errors: relevantErrors,
    shotPath,
  }, null, 2));
  if (!snapshot.panel || !snapshot.composer || !snapshot.skill) failed = true;
  if (relevantErrors.length) failed = true;
} catch (error) {
  console.log('CRASH', step, error?.message || error);
  try {
    console.log('PAGE', await page.evaluate(() => ({
      url: location.href,
      text: document.body.innerText.slice(0, 500),
      root: !!document.querySelector('#root'),
      flow: !!document.querySelector('.react-flow'),
      fab: !!document.querySelector('.agent-fab'),
      panel: !!document.querySelector('.agent-panel'),
    })));
    await page.screenshot({path: shotPath, fullPage: false});
  } catch {}
  console.log(JSON.stringify({errors}, null, 2));
  failed = true;
} finally {
  await browser.close();
}

console.log(failed ? 'RESULT FAIL' : 'RESULT PASS');
if (failed) process.exitCode = 2;
