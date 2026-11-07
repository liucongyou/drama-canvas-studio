import puppeteer from 'puppeteer-core';

const base = (process.env.CANVAS_BASE || 'http://127.0.0.1:8890').replace(/\/$/, '');
const projectId = process.env.PROJECT_ID || 'gk6woLnGjh998UeNwvhqYG';
const episodeId = process.env.EPISODE_ID || 'EP010';
const sessionToken = process.env.CANVAS_SESSION_TOKEN || '';

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--window-size=1600,1000'],
});

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1600, height: 1000});
  if (sessionToken) {
    const host = new URL(base).hostname;
    await page.setCookie({
      name: 'canvas_session',
      value: sessionToken,
      domain: host,
      path: '/',
      secure: base.startsWith('https://'),
      httpOnly: true,
    });
  }
  const pageErrors = [];
  const consoleErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });

  const url = `${base}/?ep=${encodeURIComponent(episodeId)}#${encodeURIComponent(projectId)}`;
  await page.goto(url, {waitUntil: 'domcontentloaded', timeout: 60_000});
  await page.waitForSelector('.react-flow__node-media video.canvas-media-preview.is-video', {
    timeout: 60_000,
  });
  await sleep(400);

  const card = await page.$eval(
    '.react-flow__node-media:has(video.canvas-media-preview.is-video) .canvas-media-card',
    (element) => {
      const rect = element.getBoundingClientRect();
      return {
        x: rect.x + Math.min(36, rect.width / 3),
        y: rect.y + Math.min(44, rect.height / 3),
      };
    },
  );

  await page.mouse.click(card.x, card.y);
  await page.waitForSelector('.jim-bar:not(.is-closing)', {timeout: 8_000});
  await sleep(350);
  const clickState = await page.evaluate(() => {
    const video = document.querySelector('.react-flow__node-media video.canvas-media-preview.is-video');
    const card = video?.closest('.canvas-media-card');
    return {
      modalOpen: Boolean(document.querySelector('.jim-bar:not(.is-closing)')),
      paused: video instanceof HTMLVideoElement ? video.paused : null,
      missing: Boolean(card?.querySelector('.canvas-media-placeholder')),
    };
  });
  await page.keyboard.press('Escape');
  await page.waitForFunction(
    () => !document.querySelector('.jim-bar:not(.is-closing)'),
    {timeout: 5_000},
  );

  await page.reload({waitUntil: 'domcontentloaded', timeout: 60_000});
  await page.waitForSelector('.react-flow__node-media video.canvas-media-preview.is-video', {
    timeout: 60_000,
  });

  const playButton = await page.$eval(
    '.react-flow__node-media:has(video.canvas-media-preview.is-video) .canvas-video-center-play',
    (element) => {
      const rect = element.getBoundingClientRect();
      return {
        x: rect.x + rect.width / 2,
        y: rect.y + rect.height / 2,
      };
    },
  );
  await page.mouse.move(playButton.x, playButton.y);
  await page.mouse.click(playButton.x, playButton.y);
  await sleep(500);
  const playbackState = await page.evaluate(() => {
    const video = document.querySelector('.react-flow__node-media video.canvas-media-preview.is-video');
    const card = video?.closest('.canvas-media-card');
    const missing = card?.querySelector('.canvas-media-placeholder');
    return {
      paused: video instanceof HTMLVideoElement ? video.paused : null,
      readyState: video instanceof HTMLVideoElement ? video.readyState : null,
      currentTime: video instanceof HTMLVideoElement ? video.currentTime : null,
      missing: Boolean(missing),
      placeholderText: missing?.textContent || '',
      modalOpen: Boolean(document.querySelector('.jim-bar:not(.is-closing)')),
    };
  });

  const rapidRounds = [];
  for (let round = 0; round < 8; round += 1) {
    const toggle = await page.$eval(
      '.react-flow__node-media:has(video.canvas-media-preview.is-video) .canvas-video-center-play',
      (element) => {
        const rect = element.getBoundingClientRect();
        return {
          x: rect.x + rect.width / 2,
          y: rect.y + rect.height / 2,
        };
      },
    );
    await page.mouse.move(toggle.x, toggle.y);
    await page.mouse.click(toggle.x, toggle.y);
    await sleep([20, 80, 180][round % 3]);
    const state = await page.evaluate(() => {
      const video = document.querySelector('.react-flow__node-media video.canvas-media-preview.is-video');
      const card = video?.closest('.canvas-media-card');
      const missing = card?.querySelector('.canvas-media-placeholder');
      return {
        paused: video instanceof HTMLVideoElement ? video.paused : null,
        readyState: video instanceof HTMLVideoElement ? video.readyState : null,
        missing: Boolean(missing),
        placeholderText: missing?.textContent || '',
      };
    });
    rapidRounds.push(state);
    if (state.missing || state.placeholderText.includes('媒体文件丢失')) {
      throw new Error(`rapid playback round ${round} was misclassified as missing media`);
    }
  }

  const result = {
    projectId,
    episodeId,
    clickState,
    playbackState,
    rapidRounds,
    pageErrors,
    consoleErrors: consoleErrors.filter((item) => (
      !item.includes('favicon')
      && !item.includes('Failed to load resource: the server responded with a status of 404')
    )),
  };
  console.log(JSON.stringify(result, null, 2));

  if (!clickState.modalOpen || clickState.paused !== true) {
    throw new Error('single-click should open modal without starting video playback');
  }
  if (playbackState.missing || playbackState.placeholderText.includes('媒体文件丢失')) {
    throw new Error('immediate playback after refresh was misclassified as missing media');
  }
  if (result.pageErrors.length || result.consoleErrors.length) {
    throw new Error('browser errors detected');
  }
} finally {
  await browser.close();
}
