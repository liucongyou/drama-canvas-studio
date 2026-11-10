import puppeteer from 'puppeteer-core';

/**
 * Project-aware Agent smoke test.
 *
 * It logs in, opens AgentDock, exercises the real composer/draft path, sends
 * one mock-model message through the same-origin gateway, and verifies the
 * structured response. It never confirms import and never triggers paid media
 * generation.
 */
const base = process.env.CANVAS_BASE || 'http://127.0.0.1:8891';
const username = process.env.CANVAS_USER || 'admin';
const password = process.env.CANVAS_PASSWORD || '';
const requestedProject = process.env.PROJECT_ID || '';
const shotPath = process.env.SHOT_PATH || 'scripts/_agent-smoke.png';

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--window-size=1680,1050'],
});

const errors = [];
let result;
let failed = false;
let step = 'init';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1680, height: 1050, deviceScaleFactor: 1});
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(`console: ${message.text()}`);
  });
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));

  await page.goto(base, {waitUntil: 'domcontentloaded', timeout: 60_000});
  step = 'login';
  const login = await page.evaluate(async ({username, password}) => {
    const response = await fetch('/api/auth/login', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username, password}),
    });
    return {status: response.status, body: await response.json().catch(() => ({}))};
  }, {username, password});
  if (login.status !== 200) {
    throw new Error(`登录失败 HTTP ${login.status}: ${JSON.stringify(login.body)}`);
  }

  const projects = await page.evaluate(async () => {
    const response = await fetch('/api/projects', {credentials: 'same-origin'});
    return {status: response.status, body: await response.json().catch(() => ({}))};
  });
  if (projects.status !== 200) {
    throw new Error(`读取项目失败 HTTP ${projects.status}`);
  }
  const projectId = requestedProject
    || String((projects.body.projects || projects.body || [])[0]?.id || '');
  if (!projectId) throw new Error('当前租户没有可用于冒烟的项目');

  await page.goto(`${base}/#${projectId}`, {waitUntil: 'domcontentloaded', timeout: 60_000});
  step = 'open-agent';
  await page.waitForFunction(
    () => !!document.querySelector('.react-flow')
      || !!document.querySelector('.agent-fab')
      || !!document.querySelector('.agent-panel'),
    {timeout: 60_000},
  );
  await sleep(1200);
  if (await page.$('.agent-fab')) {
    await page.evaluate(() => {
      window.__agentOpenStart = performance.now();
      document.querySelector('.agent-fab')?.click();
    });
  }
  await sleep(500);
  if (!(await page.$('.agent-panel')) && await page.$('.agent-fab')) {
    await page.evaluate(() => {
      window.__agentOpenStart = performance.now();
      document.querySelector('.agent-fab')?.click();
    });
  }
  await page.waitForSelector('.agent-panel', {timeout: 15_000});
  await page.click('.agent-icon[title="关闭"]');
  await page.waitForSelector('.agent-fab', {timeout: 10_000});
  const openMs = await page.evaluate(() => new Promise((resolve) => {
    const started = performance.now();
    const observer = new MutationObserver(() => {
      if (!document.querySelector('.agent-panel')) return;
      observer.disconnect();
      resolve(Number((performance.now() - started).toFixed(1)));
    });
    observer.observe(document.body, {childList: true, subtree: true});
    document.querySelector('.agent-fab')?.click();
  }));
  await page.waitForSelector('.agent-panel', {timeout: 10_000});

  step = 'select-skill';
  await page.waitForSelector('.agent-input-box textarea', {timeout: 15_000});

  await page.click('.agent-tool[title="选择技能"]');
  await page.waitForSelector('.agent-picker-item', {timeout: 15_000});
  await page.click('.agent-picker-item');

  const longInput = '长'.repeat(10_000);
  step = 'long-input';
  const longTask = await page.evaluate(async (text) => {
    const textarea = document.querySelector('.agent-input-box textarea');
    if (!textarea) throw new Error('composer textarea missing');
    let maxTask = 0;
    const observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        maxTask = Math.max(maxTask, entry.duration);
      }
    });
    try { observer.observe({entryTypes: ['longtask']}); } catch {}
    const setter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      'value',
    ).set;
    const started = performance.now();
    setter.call(textarea, text);
    textarea.dispatchEvent(new Event('input', {bubbles: true}));
    await new Promise((resolve) => requestAnimationFrame(() => resolve()));
    await new Promise((resolve) => setTimeout(resolve, 600));
    observer.disconnect();
    return {
      elapsedMs: performance.now() - started,
      maxLongTaskMs: maxTask,
      length: textarea.value.length,
    };
  }, longInput);
  await page.waitForFunction(
    () => document.querySelector('.agent-input-box textarea')?.value.length === 10000,
    {timeout: 10_000},
  );

  // Wait for the 500ms server draft sync and verify refresh recovery.
  step = 'draft-reload';
  await sleep(1200);
  await page.reload({waitUntil: 'domcontentloaded', timeout: 60_000});
  await page.waitForSelector('.agent-panel', {timeout: 20_000});
  await page.waitForFunction(
    () => document.querySelector('.agent-input-box textarea')?.value.length === 10000,
    {timeout: 20_000},
  );

  if (!(await page.$('.agent-skill-chip'))) {
    await page.click('.agent-tool[title="选择技能"]');
    await page.waitForSelector('.agent-picker-item', {timeout: 10_000});
    await page.click('.agent-picker-item');
  }
  await page.focus('.agent-input-box textarea');
  await page.keyboard.down('Control');
  await page.keyboard.press('KeyA');
  await page.keyboard.up('Control');
  await page.keyboard.sendCharacter('生成一份用于 Agent 冒烟的测试投喂稿，不要导入。');
  await sleep(500);
  step = 'send-message';
  await page.evaluate(() => document.querySelector('.agent-send')?.click());
  await page.waitForSelector('.agent-lock-card', {timeout: 10_000});
  await page.click('.agent-lock-card .agent-primary');
  step = 'wait-run';
  await page.waitForSelector('.agent-run', {timeout: 20_000});
  await page.waitForFunction(() => {
    const panel = document.querySelector('.agent-panel');
    return Boolean(panel && (
      panel.textContent?.includes('投喂稿已就绪')
      || panel.textContent?.includes('已中断')
      || panel.textContent?.includes('预检未通过')
    ));
  }, {timeout: 60_000});

  result = await page.evaluate(() => {
    const panel = document.querySelector('.agent-panel');
    return {
      projectId: location.hash.replace(/^#/, ''),
      hasComposer: !!panel?.querySelector('.agent-input-box textarea'),
      hasMessages: (panel?.querySelectorAll('.agent-msg').length || 0) > 0,
      hasStructuredContent: !!panel?.querySelector('.agent-message-content'),
      hasToolTrace: !!panel?.querySelector('.agent-tool-trace'),
      text: (panel?.textContent || '').slice(-600),
    };
  });
  await page.screenshot({path: shotPath, fullPage: false});

  const realErrors = errors.filter((item) =>
    !item.includes('favicon')
    && !item.includes('Failed to load resource: the server responded with a status of 502'),
  );
  const output = {
    at: new Date().toISOString(),
    base,
    projectId,
    openMs,
    longInput: longTask,
    result,
    errors: realErrors,
    shotPath,
  };
  console.log(JSON.stringify(output, null, 2));
  if (realErrors.length) failed = true;
  if (longTask.maxLongTaskMs > 50) failed = true;
  if (!result.hasComposer || !result.hasMessages || !result.hasStructuredContent) failed = true;
} catch (error) {
  console.log('CRASH', step, error?.message || error);
  console.log(JSON.stringify({errors}, null, 2));
  failed = true;
} finally {
  await browser.close();
}

console.log(failed ? 'RESULT FAIL' : 'RESULT PASS');
if (failed) process.exitCode = 2;
