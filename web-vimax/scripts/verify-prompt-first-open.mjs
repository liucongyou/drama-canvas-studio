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
  await page.waitForSelector('.react-flow__node', {timeout: 60_000});
  const target = await page.$('.react-flow__node');
  if (!target) throw new Error('no node');
  await target.click();
  await page.waitForSelector('.jim-prompt[contenteditable="true"]', {timeout: 20_000});
  await new Promise((resolve) => setTimeout(resolve, 400));
  const snapshot = await page.evaluate(() => {
    const editor = document.querySelector('.jim-prompt[contenteditable="true"]');
    if (!editor) return null;
    const style = getComputedStyle(editor);
    return {
      text: (editor.textContent || '').slice(0, 500),
      scrollHeight: editor.scrollHeight,
      clientHeight: editor.clientHeight,
      overflowY: style.overflowY,
      contentVisibility: style.contentVisibility,
      expanded: document.querySelector('.jim-prompt-area')?.classList.contains('is-expanded') || false,
      focused: document.activeElement === editor,
    };
  });
  console.log(JSON.stringify({projectId, snapshot, errors}, null, 2));
  if (!snapshot || !snapshot.text || snapshot.contentVisibility === 'auto') {
    throw new Error('prompt was not fully rendered on first open');
  }
  if (snapshot.scrollHeight > snapshot.clientHeight && snapshot.overflowY === 'hidden') {
    throw new Error('clipped prompt has no scroll affordance');
  }
} finally {
  await browser.close();
}
