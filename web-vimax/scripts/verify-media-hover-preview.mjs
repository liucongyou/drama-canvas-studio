import puppeteer from 'puppeteer-core';

const base = (process.env.CANVAS_BASE || 'http://127.0.0.1:8901').replace(/\/$/, '');
const projectId = process.env.PROJECT_ID || 'gk6woLnGjh998UeNwvhqYG';
const episodeId = process.env.EPISODE_ID || 'EP010';
const rounds = Math.max(10, Number(process.env.RAPID_ROUNDS || 20));

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--window-size=1920,1080'],
});
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1920, height: 1080});
  const pageErrors = [];
  const consoleErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  await page.goto(
    `${base}/?ep=${encodeURIComponent(episodeId)}#${encodeURIComponent(projectId)}`,
    {waitUntil: 'domcontentloaded', timeout: 60_000},
  );
  await page.waitForSelector(
    '.react-flow__node-media .canvas-media-card img',
    {timeout: 60_000},
  );
  await sleep(800);

  const card = await page.$eval(
    '.react-flow__node-media .canvas-media-card img',
    (image) => {
      const card = image.closest('.canvas-media-card');
      const flow = image.closest('.react-flow__node');
      if (!card || !flow) return null;
      const rect = card.getBoundingClientRect();
      return {
        flowId: flow.getAttribute('data-id') || '',
        x: rect.x + Math.min(30, rect.width / 3),
        y: rect.y + Math.min(30, rect.height / 3),
      };
    },
  );
  if (!card?.flowId) throw new Error('image media card not found');

  await page.$eval('.react-flow__node-media .canvas-media-card img', (image) => {
    image.dispatchEvent(new MouseEvent('click', {
      bubbles: true,
      cancelable: true,
      view: window,
      detail: 1,
    }));
  });
  await page.waitForSelector('.jim-bar:not(.is-closing)', {timeout: 5_000});
  const singleClickOpened = await page.evaluate(
    () => Boolean(document.querySelector('.jim-bar:not(.is-closing)')),
  );
  await page.keyboard.press('Escape');
  await page.waitForFunction(
    () => !document.querySelector('.jim-bar'),
    {timeout: 5_000},
  );

  const cardHoverPoint = await page.$eval(
    '.react-flow__node-media .canvas-media-card',
    (element) => {
      const rect = element.getBoundingClientRect();
      return {
        x: rect.x + Math.min(30, rect.width / 3),
        y: rect.y + Math.min(30, rect.height / 3),
      };
    },
  );
  await page.mouse.move(cardHoverPoint.x, cardHoverPoint.y);
  try {
    await page.waitForFunction(
      () => {
        const button = document.querySelector('.canvas-media-card-menu button[title="查看原图"]');
        if (!button) return false;
        const style = getComputedStyle(button);
        const menu = button.closest('.canvas-media-card-menu');
        const menuStyle = menu ? getComputedStyle(menu) : null;
        const rect = button.getBoundingClientRect();
        return style.display !== 'none'
          && style.visibility !== 'hidden'
          && Number(style.opacity) > 0
          && style.pointerEvents !== 'none'
          && (!menuStyle || (
            menuStyle.visibility !== 'hidden'
            && Number(menuStyle.opacity) > 0
            && menuStyle.pointerEvents !== 'none'
          ))
          && rect.width > 0
          && rect.height > 0;
      },
      {timeout: 5_000},
    );
  } catch (error) {
    const diagnostic = await page.evaluate(() => {
      const button = document.querySelector('.canvas-media-card-menu button[title="查看原图"]');
      const card = document.querySelector('.react-flow__node-media .canvas-media-card');
      const describe = (node) => {
        if (!node) return null;
        const style = getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        return {
          tag: node.tagName,
          className: node.className,
          opacity: style.opacity,
          visibility: style.visibility,
          display: style.display,
          pointerEvents: style.pointerEvents,
          rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
        };
      };
      return {
        button: describe(button),
        menu: describe(button?.closest('.canvas-media-card-menu')),
        card: describe(card),
        hovered: [...document.querySelectorAll(':hover')]
          .map((node) => `${node.tagName}.${node.className}`)
          .slice(-8),
      };
    });
    console.log('HOVER_DIAGNOSTIC', JSON.stringify(diagnostic, null, 2));
    throw error;
  }
  const previewButton = await page.$eval(
    '.canvas-media-card-menu button[title="查看原图"]',
    (button) => {
      const rect = button.getBoundingClientRect();
      return {
        x: rect.x + rect.width / 2,
        y: rect.y + rect.height / 2,
        width: rect.width,
        height: rect.height,
      };
    },
  );
  await page.mouse.click(previewButton.x, previewButton.y);
  try {
    await page.waitForSelector('.canvas-lightbox', {timeout: 5_000});
  } catch (error) {
    const diagnostic = await page.evaluate(({x, y}) => {
      const point = document.elementFromPoint(x, y);
      return {
        point: point?.outerHTML?.slice(0, 800) || '',
        lightbox: Boolean(document.querySelector('.canvas-lightbox')),
        modal: Boolean(document.querySelector('.jim-bar')),
        lightboxes: [...document.querySelectorAll('.canvas-lightbox')].map((node) => node.outerHTML.slice(0, 500)),
      };
    }, {x: previewButton.x, y: previewButton.y});
    console.log('PREVIEW_DIAGNOSTIC', JSON.stringify(diagnostic, null, 2));
    throw error;
  }
  const previewState = await page.evaluate(() => ({
    lightbox: Boolean(document.querySelector('.canvas-lightbox')),
    modal: Boolean(document.querySelector('.jim-bar:not(.is-closing)')),
  }));
  await page.$eval('.canvas-lightbox', (lightbox) => {
    lightbox.dispatchEvent(new MouseEvent('click', {
      bubbles: true,
      cancelable: true,
      view: window,
    }));
  });
  await page.waitForFunction(
    () => !document.querySelector('.canvas-lightbox'),
    {timeout: 5_000},
  );
  const failures = [];
  for (let round = 0; round < rounds; round += 1) {
    await page.$eval('.react-flow__node-media .canvas-media-card img', (image) => {
      image.dispatchEvent(new MouseEvent('click', {
        bubbles: true,
        cancelable: true,
        view: window,
        detail: 1,
      }));
    });
    await page.waitForSelector('.jim-bar:not(.is-closing)', {timeout: 4_000});
    await page.mouse.click(70, 950);
    await page.waitForFunction(
      () => (
        !document.querySelector('.jim-bar')
        || document.querySelector('.jim-bar.is-closing')
      ),
      {timeout: 2_000},
    );
    await sleep([0, 16, 45, 90][round % 4]);
    await page.$eval('.react-flow__node-media .canvas-media-card img', (image) => {
      image.dispatchEvent(new MouseEvent('click', {
        bubbles: true,
        cancelable: true,
        view: window,
        detail: 1,
      }));
    });
    await page.waitForSelector('.jim-bar:not(.is-closing)', {timeout: 4_000});
    await sleep(230);
    const state = await page.evaluate(() => {
      const panel = document.querySelector('.jim-bar');
      return {
        present: Boolean(panel),
        closing: panel?.classList.contains('is-closing') ?? false,
      };
    });
    if (!state.present || state.closing) failures.push({round, ...state});
    await page.keyboard.press('Escape');
    await page.waitForFunction(
      () => !document.querySelector('.jim-bar'),
      {timeout: 4_000},
    );
  }

  const result = {
    projectId,
    episodeId,
    singleClickOpened,
    previewState,
    previewButton,
    rapidRounds: rounds,
    rapidFailures: failures,
    pageErrors,
    consoleErrors: consoleErrors.filter((item) => !item.includes('favicon')),
  };
  console.log(JSON.stringify(result, null, 2));
  if (!singleClickOpened) throw new Error('single click did not open modal');
  if (!previewState.lightbox || previewState.modal) {
    throw new Error('hover preview did not exclusively open lightbox');
  }
  if (failures.length) throw new Error('rapid modal toggle regression');
  if (pageErrors.length || result.consoleErrors.length) {
    throw new Error('browser errors detected');
  }
} finally {
  await browser.close();
}
