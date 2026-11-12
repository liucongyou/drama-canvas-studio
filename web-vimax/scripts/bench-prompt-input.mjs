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

  const projectId = process.env.PROJECT_ID || await page.evaluate(async () => {
    const response = await fetch('/api/projects', {credentials: 'same-origin'});
    const body = await response.json();
    return body.projects?.[0]?.id || '';
  });
  if (!projectId) throw new Error('no project');
  await page.goto(`https://yj.pduoduo.vip/#${projectId}`, {
    waitUntil: 'domcontentloaded',
    timeout: 60_000,
  });
  await page.waitForSelector('.react-flow__node', {timeout: 60_000});
  const target = await page.$('.react-flow__node');
  if (!target) throw new Error('no canvas node');
  const focusStart = performance.now();
  await target.click();
  await page.waitForSelector('.jim-prompt[contenteditable="true"]', {timeout: 20_000});
  const focusMs = performance.now() - focusStart;
  const editor = await page.$('.jim-prompt[contenteditable="true"]');
  if (!editor) throw new Error('prompt editor missing');
  const originalText = await editor.evaluate((element) => element.textContent || '');
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
  await new Promise((resolve) => setTimeout(resolve, 500));
  const restored = await page.evaluate((expected) => {
    const editor = document.querySelector('.jim-prompt[contenteditable="true"]');
    return Boolean(editor && (editor.textContent || '') === expected);
  }, originalText);
  await new Promise((resolve) => setTimeout(resolve, 500));
  if (!restored) {
    throw new Error('prompt benchmark did not restore the original text');
  }
  const synced = await page.evaluate((expectedText) => {
    const editor = document.querySelector('.jim-prompt[contenteditable="true"]');
    return {
      sync: document.querySelector('.canvas-status-trigger')?.textContent || '',
      text: editor?.textContent || '',
      containsExpected: Boolean(editor?.textContent?.includes(expectedText)),
    };
  }, inputResult.text);
  console.log(JSON.stringify({projectId, focusMs, inputResult, synced, errors}, null, 2));
  if (inputResult.timings.some((value) => value > 120)) {
    throw new Error(`input frame latency too high: ${inputResult.timings.join(',')}`);
  }
  if (!synced.containsExpected) {
    throw new Error('prompt draft was not committed after debounce');
  }
  await page.evaluate((element, original) => {
    element.focus();
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(element);
    selection?.removeAllRanges();
    selection?.addRange(range);
    document.execCommand('insertText', false, original);
    element.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText'}));
  }, editor, originalText);
  await new Promise((resolve) => setTimeout(resolve, 500));
} finally {
  await browser.close();
}
