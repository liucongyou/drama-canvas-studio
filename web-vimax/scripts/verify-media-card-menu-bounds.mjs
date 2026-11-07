import puppeteer from 'puppeteer-core';

const base = (process.env.CANVAS_BASE || 'http://127.0.0.1:8901').replace(/\/$/, '');
const projectId = process.env.PROJECT_ID || 'gk6woLnGjh998UeNwvhqYG';
const episodeId = process.env.EPISODE_ID || 'EP001';

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--window-size=1680,1050'],
});

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1680, height: 1050});
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  await page.goto(
    `${base}/?ep=${encodeURIComponent(episodeId)}#${encodeURIComponent(projectId)}`,
    {waitUntil: 'networkidle2', timeout: 60_000},
  );
  await page.waitForSelector(
    '.react-flow__node-media .canvas-media-card',
    {timeout: 60_000},
  );

  await page.evaluate(() => {
    const flow = document.querySelector('.react-flow__viewport');
    if (!flow) throw new Error('React Flow viewport missing');
    flow.style.transform = 'translate(260px, 120px) scale(0.2)';
  });
  await sleep(500);

  const candidates = await page.$$eval(
    '.react-flow__node-media',
    (nodes) => nodes.flatMap((node) => {
      const card = node.querySelector('.canvas-media-card');
      const menu = node.querySelector('.canvas-media-card-menu');
      if (!(card instanceof HTMLElement) || !(menu instanceof HTMLElement)) return [];
      const hasImage = Boolean(node.querySelector('.canvas-media-preview.is-photo'));
      const hasVideo = Boolean(node.querySelector('.canvas-media-preview.is-video'));
      if (!hasImage && !hasVideo) return [];
      return [{
        id: node.getAttribute('data-id'),
        kind: hasVideo ? 'video' : 'image',
      }];
    }),
  );

  const samples = [];
  for (const candidate of candidates.slice(0, 12)) {
    const selector = `[data-id="${candidate.id}"]`;
    const node = await page.$(selector);
    if (!node) continue;
    await node.hover();
    await sleep(180);
    const geometry = await page.$eval(selector, (nodeElement) => {
      const card = nodeElement.querySelector('.canvas-media-card');
      const menu = nodeElement.querySelector('.canvas-media-card-menu');
      if (!(card instanceof HTMLElement) || !(menu instanceof HTMLElement)) {
        throw new Error('card or menu missing');
      }
      const cardRect = card.getBoundingClientRect();
      const menuRect = menu.getBoundingClientRect();
      const style = getComputedStyle(menu);
      return {
        card: {
          left: Math.round(cardRect.left),
          right: Math.round(cardRect.right),
          top: Math.round(cardRect.top),
          bottom: Math.round(cardRect.bottom),
          width: Math.round(cardRect.width),
        },
        menu: {
          left: Math.round(menuRect.left),
          right: Math.round(menuRect.right),
          top: Math.round(menuRect.top),
          bottom: Math.round(menuRect.bottom),
          width: Math.round(menuRect.width),
        },
        opacity: style.opacity,
        transform: style.transform,
        maxScale: style.getPropertyValue('--card-menu-max-scale').trim(),
      };
    });
    samples.push({kind: candidate.kind, ...geometry});
  }

  for (const sample of samples) {
    if (
      sample.menu.left < sample.card.left - 1
      || sample.menu.right > sample.card.right + 1
      || sample.menu.top < sample.card.top - 1
      || sample.menu.bottom > sample.card.bottom + 1
    ) {
      throw new Error(
        `media menu escaped its card: ${JSON.stringify(sample)}`,
      );
    }
  }
  for (const kind of ['image', 'video']) {
    if (!samples.some((sample) => sample.kind === kind)) {
      throw new Error(`no ${kind} card sampled`);
    }
  }

  console.log(JSON.stringify({
    projectId,
    episodeId,
    sampled: samples.length,
    kinds: [...new Set(samples.map((sample) => sample.kind))],
    samples,
    errors: errors.filter((item) => !item.includes('favicon')),
  }, null, 2));
  if (errors.some((item) => !item.includes('favicon'))) {
    throw new Error('browser errors detected');
  }
} finally {
  await browser.close();
}
