import puppeteer from 'puppeteer-core';

const password = process.env.CANVAS_PASSWORD;
if (!password) throw new Error('CANVAS_PASSWORD is required');

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--no-proxy-server'],
});
const page = await browser.newPage();
await page.setViewport({width: 1680, height: 1050});
const errors = [];
page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
page.on('console', (message) => {
  if (message.type() === 'error') errors.push(`console: ${message.text()}`);
});

try {
  await page.goto('https://yj.pduoduo.vip', {waitUntil: 'domcontentloaded', timeout: 60_000});
  const passwordInput = await page.$('input[type="password"]');
  if (passwordInput) {
    const inputs = await page.$$('input');
    await inputs[0].click({clickCount: 3});
    await inputs[0].type('admin');
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
  }

  const projectId = await page.evaluate(async () => {
    const response = await fetch('/api/projects', {credentials: 'same-origin'});
    const body = await response.json();
    return body.projects?.[0]?.id || '';
  });
  if (!projectId) throw new Error('no project');
  await page.goto(`https://yj.pduoduo.vip/#${projectId}`, {
    waitUntil: 'domcontentloaded',
    timeout: 60_000,
  });
  await page.waitForSelector('.react-flow', {timeout: 60_000});
  const target = await page.$('.react-flow__node');
  if (!target) throw new Error('no canvas node');
  const focusStart = performance.now();
  await target.click();
  await page.waitForSelector('.jim-prompt[contenteditable="true"]', {timeout: 20_000});
  const focusMs = performance.now() - focusStart;
  const editor = await page.$('.jim-prompt[contenteditable="true"]');
  if (!editor) throw new Error('prompt editor missing');
  await editor.click();

  const inputResult = await page.evaluate(async (element) => {
    const values = ['性能测试', '性能测试输入响应', '性能测试输入响应稳定'];
    const timings = [];
    for (const value of values) {
      const start = performance.now();
      element.focus();
      document.execCommand('insertText', false, value);
      await new Promise((resolve) => requestAnimationFrame(() => resolve()));
      timings.push(performance.now() - start);
    }
    return {
      timings,
      text: element.textContent || '',
      active: document.activeElement === element,
    };
  }, editor);
  await page.waitForTimeout(350);
  const synced = await page.evaluate(() => ({
    sync: document.querySelector('.canvas-status-trigger')?.textContent || '',
    overflow: document.querySelector('.agent-panel')?.scrollWidth || 0,
  }));
  console.log(JSON.stringify({projectId, focusMs, inputResult, synced, errors}, null, 2));
  if (inputResult.timings.some((value) => value > 120)) {
    throw new Error(`input frame latency too high: ${inputResult.timings.join(',')}`);
  }
} finally {
  await browser.close();
}
