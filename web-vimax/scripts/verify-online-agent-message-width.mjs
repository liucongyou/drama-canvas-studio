import puppeteer from 'puppeteer-core';

const base = process.env.CANVAS_BASE || 'https://yj.pduoduo.vip';
const username = process.env.CANVAS_USER || 'admin';
const password = process.env.CANVAS_PASSWORD;
if (!password) throw new Error('CANVAS_PASSWORD is required');

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--no-proxy-server'],
});

const page = await browser.newPage();
await page.setViewport({width: 1680, height: 1050, deviceScaleFactor: 1});
const errors = [];
page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
page.on('console', (message) => {
  if (message.type() === 'error') errors.push(`console: ${message.text()}`);
});

try {
  await page.goto(base, {waitUntil: 'domcontentloaded', timeout: 60_000});
  const passwordInput = await page.$('input[type="password"]');
  if (passwordInput) {
    const inputs = await page.$$('input');
    await inputs[0].click({clickCount: 3});
    await inputs[0].type(username);
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

  const projectInfo = await page.evaluate(async () => {
    const response = await fetch('/api/projects', {credentials: 'same-origin'});
    const text = await response.text();
    let body = {};
    try { body = JSON.parse(text); } catch {}
    return {
      status: response.status,
      url: response.url,
      body: text.slice(0, 600),
      first: body.projects?.[0]?.id || '',
    };
  });
  if (!projectInfo.first) {
    throw new Error(`no project available: ${JSON.stringify(projectInfo)}`);
  }
  const projectId = projectInfo.first;

  await page.goto(`${base}/#${projectId}`, {
    waitUntil: 'domcontentloaded',
    timeout: 60_000,
  });
  await page.waitForFunction(
    () => !!document.querySelector('.agent-fab') || !!document.querySelector('.agent-panel'),
    {timeout: 60_000},
  );
  if (await page.$('.agent-fab')) await page.click('.agent-fab');
  await page.waitForSelector('.agent-panel', {timeout: 20_000});
  await page.waitForSelector('.agent-input-box textarea', {timeout: 20_000});

  const historyButton = await page.$('.agent-history-link');
  if (historyButton) {
    await historyButton.click();
    await page.waitForSelector('.agent-history-modal', {timeout: 15_000});
    const rows = await page.$$('.agent-history-row');
    if (rows.length) {
      await rows[0].click();
      await page.waitForFunction(
        () => !document.querySelector('.agent-history-modal'),
        {timeout: 15_000},
      );
      await new Promise((resolve) => setTimeout(resolve, 1200));
    }
  }

  const messages = [];
  const bodies = await page.$$('.agent-msg.is-user .agent-msg-text');
  for (const body of bodies) {
    const item = await body.evaluate((node) => {
      const message = node.closest('.agent-msg');
      if (!message) return null;
      const range = document.createRange();
      range.selectNodeContents(node);
      return {
        text: (node.textContent || '').trim().slice(0, 80),
        lines: range.getClientRects().length,
        messageWidth: Math.round(message.getBoundingClientRect().width),
        bodyWidth: Math.round(node.getBoundingClientRect().width),
        style: ((element) => {
          const computed = getComputedStyle(element);
          return {
            width: computed.width,
            maxWidth: computed.maxWidth,
            padding: computed.padding,
            font: computed.font,
            letterSpacing: computed.letterSpacing,
            whiteSpace: computed.whiteSpace,
            wordBreak: computed.wordBreak,
            overflowWrap: computed.overflowWrap,
            display: computed.display,
            flex: computed.flex,
          };
        })(node),
      };
    });
    if (item) messages.push(item);
  }

  const shortMessages = messages.filter((item) => item.text && [...item.text].length <= 12);
  const badShortMessages = shortMessages.filter((item) => item.lines !== 1);
  if (badShortMessages.length) {
    throw new Error(`short messages wrapped: ${JSON.stringify(badShortMessages)}`);
  }
  if (messages.some((item) => item.messageWidth <= 0 || item.messageWidth > 620)) {
    throw new Error(`message width outside expected bounds: ${JSON.stringify(messages)}`);
  }

  const relevantErrors = errors.filter((item) =>
    !item.includes('favicon')
    && !item.includes('Failed to load resource: the server responded with a status of 404')
    && !item.includes('Failed to load resource: the server responded with a status of 502')
    && !item.includes('doubaoduanju.tos-cn-beijing.volces.com')
    && !item.includes('Failed to load resource: net::ERR_FAILED'),
  );
  if (relevantErrors.length) throw new Error(relevantErrors.join('; '));

  console.log(JSON.stringify({
    projectId,
    entry: await page.evaluate(() =>
      [...document.scripts].map((item) => item.src).find((src) => src.includes('/assets/index-')) || ''
    ),
    messages,
    shortMessages: shortMessages.length,
    errors: relevantErrors,
  }, null, 2));
} finally {
  await browser.close();
}
