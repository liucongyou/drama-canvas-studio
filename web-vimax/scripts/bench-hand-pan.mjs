import puppeteer from 'puppeteer-core';

const password = process.env.CANVAS_PASSWORD;
if (!password) throw new Error('CANVAS_PASSWORD is required');
const base = (process.env.CANVAS_BASE || 'https://yj.pduoduo.vip').replace(/\/$/, '');

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

async function login() {
  await page.goto(base, {waitUntil: 'domcontentloaded', timeout: 60_000});
  const passwordInput = await page.$('input[type="password"]');
  if (!passwordInput) return;
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

async function firstProjectId() {
  return page.evaluate(async () => {
    const response = await fetch('/api/projects', {credentials: 'same-origin'});
    const body = await response.json();
    return body.projects?.[0]?.id || '';
  });
}

function percentile(values, p) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * p))];
}

async function dragBackAndForth(startX, startY, dx, dy, steps) {
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  for (let index = 1; index <= steps; index++) {
    await page.mouse.move(
      startX + (dx * index) / steps,
      startY + (dy * index) / steps,
    );
  }
  await page.mouse.up();
}

try {
  await login();
  const projectId = process.env.PROJECT_ID || await firstProjectId();
  if (!projectId) throw new Error('no project');
  await page.goto(`${base}/#${projectId}`, {
    waitUntil: 'domcontentloaded',
    timeout: 60_000,
  });
  await page.waitForSelector('.react-flow__node', {timeout: 60_000});

  const handButton = await page.$('[title^="抓手模式"]');
  if (handButton) await handButton.click();
  else await page.keyboard.press('KeyH');
  await page.waitForFunction(
    () => document.querySelector('.canvas-flow')?.getAttribute('data-mouse-mode') === 'hand',
    {timeout: 10_000},
  );

  const stage = await page.$('.react-flow__pane');
  if (!stage) throw new Error('React Flow pane missing');
  const box = await stage.boundingBox();
  if (!box) throw new Error('React Flow pane has no bounds');
  const startX = box.x + Math.min(480, box.width * 0.35);
  const startY = box.y + Math.min(360, box.height * 0.4);
  const dx = 420;
  const dy = 180;
  const steps = 120;

  await page.evaluate(() => {
    window.__handPanFrames = [];
    window.__handPanLongTasks = [];
    window.__handPanLast = performance.now();
    window.__handPanActive = true;
    const sample = (time) => {
      if (!window.__handPanActive) return;
      window.__handPanFrames.push(time - window.__handPanLast);
      window.__handPanLast = time;
      requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
    try {
      window.__handPanObserver = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          window.__handPanLongTasks.push(entry.duration);
        }
      });
      window.__handPanObserver.observe({entryTypes: ['longtask']});
    } catch {}
  });

  await dragBackAndForth(startX, startY, dx, dy, steps);
  await new Promise((resolve) => setTimeout(resolve, 250));
  const sample = await page.evaluate(() => {
    window.__handPanActive = false;
    window.__handPanObserver?.disconnect();
    return {
      frames: window.__handPanFrames || [],
      longTasks: window.__handPanLongTasks || [],
    };
  });

  // Restore approximately the same viewport without changing the project data.
  await dragBackAndForth(startX + dx, startY + dy, -dx, -dy, steps);
  await new Promise((resolve) => setTimeout(resolve, 250));

  const frames = sample.frames.filter((value) => value > 0 && value < 1000);
  const longTasks = sample.longTasks.filter((value) => value > 50);
  const result = {
    projectId,
    frames: frames.length,
    p50: Number(percentile(frames, 0.5).toFixed(1)),
    p95: Number(percentile(frames, 0.95).toFixed(1)),
    p99: Number(percentile(frames, 0.99).toFixed(1)),
    max: Number(Math.max(0, ...frames).toFixed(1)),
    over32ms: frames.filter((value) => value > 32).length,
    longTasks: longTasks.length,
    maxLongTask: Number(Math.max(0, ...longTasks).toFixed(1)),
    errors,
  };
  console.log(JSON.stringify(result, null, 2));
  if (errors.length) throw new Error(errors.join('; '));
} finally {
  await browser.close();
}
