import puppeteer from 'puppeteer-core';

const base = (process.env.CANVAS_BASE_URL || 'https://yj.pduoduo.vip').replace(/\/$/, '');
const token = process.env.CANVAS_SESSION_TOKEN || '';
const projectId = process.env.CANVAS_PROJECT_ID || '';
const episode = process.env.CANVAS_EPISODE || 'EP015';
if (!token || !projectId) throw new Error('session token and project id are required');

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});
try {
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 960});
  const host = new URL(base).hostname;
  const domains = host.endsWith('pduoduo.vip')
    ? ['yj.pduoduo.vip', 'yjapi.pduoduo.vip']
    : [host];
  for (const domain of domains) {
    await page.setCookie({
      name: 'canvas_session',
      value: token,
      domain,
      path: '/',
      httpOnly: true,
      secure: base.startsWith('https://'),
    });
  }
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error' && !message.text().includes('Failed to load resource')) {
      errors.push(message.text());
    }
  });
  await page.goto(`${base}/?ep=${encodeURIComponent(episode)}#${projectId}`, {
    waitUntil: 'domcontentloaded',
    timeout: 60_000,
  });
  await page.waitForSelector('.canvas-media-card', {timeout: 60_000});
  const cards = await page.$$('.canvas-media-card');
  for (const card of cards) {
    if (await card.evaluate((node) => Boolean(node.querySelector('.canvas-media-image-frame')))) {
      await card.click();
      break;
    }
  }
  await page.waitForSelector('.jim-toolbar', {timeout: 20_000});
  await page.click('[data-pop-owner="clarity"]');
  await page.waitForSelector('.jim-pop-clarity .jim-chip.is-active', {timeout: 20_000});
  const result = await page.evaluate(() => {
    const active = Array.from(document.querySelectorAll('.jim-pop-clarity .jim-chip.is-active'));
    if (!active.length) return null;
    const activeStyles = active.map((node) => {
      const style = getComputedStyle(node);
      return {
        text: node.textContent || '',
        background: style.backgroundColor,
        border: style.borderColor,
        color: style.color,
      };
    });
    const root = getComputedStyle(document.documentElement);
    return {
      html: active.map((node) => node.outerHTML),
      activeStyles,
      tokens: {
        selected: root.getPropertyValue('--v-selected').trim(),
        accentSoft: root.getPropertyValue('--v-accent-soft').trim(),
        accentMid: root.getPropertyValue('--v-accent-mid').trim(),
        accentLine: root.getPropertyValue('--v-accent-line').trim(),
      },
      theme: {
        theme: document.documentElement.dataset.theme || '',
        brand: document.documentElement.dataset.brand || '',
        className: document.documentElement.className,
      },
    };
  });
  if (!result || result.activeStyles.length < 2) {
    throw new Error(`expected clarity and watermark selections: ${JSON.stringify(result)}`);
  }
  const uniqueBackgrounds = new Set(result.activeStyles.map((item) => item.background));
  const uniqueBorders = new Set(result.activeStyles.map((item) => item.border));
  if (uniqueBackgrounds.size !== 1 || uniqueBorders.size !== 1) {
    throw new Error(`popup selections are not unified: ${JSON.stringify(result.activeStyles)}`);
  }
  const background = result.activeStyles[0].background;
  const rgbMatch = background.match(/rgba?\(([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/);
  const srgbMatch = background.match(/color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)/);
  const rgb = rgbMatch
    ? rgbMatch.slice(1, 4).map(Number)
    : srgbMatch
      ? srgbMatch.slice(1, 4).map((value) => Number(value) * 255)
      : [];
  if (rgb.length >= 3 && rgb[2] > Math.max(rgb[0], rgb[1]) + 10) {
    throw new Error(`popup selection is still blue: ${background}`);
  }
  if (errors.some((value) => /reading 'id'/.test(value))) {
    throw new Error(`runtime error: ${errors.join(' | ')}`);
  }
  await page.screenshot({path: 'artifacts/chip-color-current.png', fullPage: false});
  console.log(JSON.stringify({result, errors}, null, 2));
} finally {
  await browser.close();
}
