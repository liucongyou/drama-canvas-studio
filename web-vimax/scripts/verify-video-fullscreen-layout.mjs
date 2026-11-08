import puppeteer from 'puppeteer-core';

const base = process.env.CANVAS_BASE || 'http://127.0.0.1:8892';
const sessionToken = process.env.CANVAS_SESSION_TOKEN || '';
const cases = [
  {
    name: 'portrait',
    projectId: process.env.PORTRAIT_PROJECT_ID || 'gk6woLnGjh998UeNwvhqYG',
    episodeId: process.env.PORTRAIT_EPISODE_ID || 'EP001',
    nodeId: process.env.PORTRAIT_NODE_ID || 'node-55ghplggyepy-b4c52fd3',
  },
  {
    name: 'landscape',
    projectId: process.env.LANDSCAPE_PROJECT_ID || 'gk6woLnGjh998UeNwvhqYG',
    episodeId: process.env.LANDSCAPE_EPISODE_ID || 'EP004',
    nodeId: process.env.LANDSCAPE_NODE_ID || 'node-t4jcds3v1b9s-af8a871e',
  },
];

const verifyCase = async (browser, item) => {
  const {name, projectId, episodeId, nodeId} = item;

  const page = await browser.newPage();
  const errors = [];
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('pageerror', (error) => errors.push(error.message));
  await page.setViewport({width: 1680, height: 1050});
  if (sessionToken) {
    const url = new URL(base);
    await page.setCookie({
      name: 'canvas_session',
      value: sessionToken,
      domain: url.hostname,
      path: '/',
      secure: url.protocol === 'https:',
      httpOnly: true,
    });
  }
  try {
    await page.goto(
    `${base}/?ep=${encodeURIComponent(episodeId)}#${encodeURIComponent(projectId)}`,
    {waitUntil: 'domcontentloaded', timeout: 60_000},
  );
  await page.waitForSelector(`[data-id="media-${nodeId}"]`, {timeout: 60_000});
  await new Promise((resolve) => setTimeout(resolve, 1200));

  const shellSelector = `[data-id="media-${nodeId}"] .canvas-video-shell`;
  const shell = await page.$(shellSelector);
  if (!shell) throw new Error(`video shell not found for ${nodeId}`);
  const measure = () => page.$eval(shellSelector, (node) => {
    const video = node.querySelector('video');
    const controls = node.querySelector('.canvas-video-controls');
    if (!(video instanceof HTMLVideoElement) || !(controls instanceof HTMLElement)) {
      throw new Error('video or controls missing');
    }
    const rectOf = (element) => {
      const rect = element.getBoundingClientRect();
      return {
        left: Math.round(rect.left),
        right: Math.round(rect.right),
        top: Math.round(rect.top),
        bottom: Math.round(rect.bottom),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      };
    };
    const ratioVar = Number.parseFloat(
      getComputedStyle(node).getPropertyValue('--canvas-video-ratio'),
    );
    const intrinsicRatio = video.videoWidth > 0 && video.videoHeight > 0
      ? video.videoWidth / video.videoHeight
      : 0;
    const videoRect = rectOf(video);
    const contentWidth = Math.min(window.innerWidth, window.innerHeight * intrinsicRatio);
    const contentHeight = contentWidth / intrinsicRatio;
    const contentLeft = (window.innerWidth - contentWidth) / 2;
    const contentTop = (window.innerHeight - contentHeight) / 2;
    const stage = node.querySelector('.canvas-video-stage');
    if (!(stage instanceof HTMLElement)) throw new Error('video stage missing');
    const shellStyle = getComputedStyle(node);
    return {
      fullscreen: document.fullscreenElement === node,
      viewport: {width: window.innerWidth, height: window.innerHeight},
      shell: rectOf(node),
      stage: rectOf(stage),
      video: videoRect,
      content: {
        left: Math.round(contentLeft),
        right: Math.round(contentLeft + contentWidth),
        top: Math.round(contentTop),
        bottom: Math.round(contentTop + contentHeight),
        width: Math.round(contentWidth),
        height: Math.round(contentHeight),
      },
      controls: rectOf(controls),
      ratioVar: Number(ratioVar.toFixed(4)),
      intrinsicRatio: Number(intrinsicRatio.toFixed(4)),
      videoRatio: Number((videoRect.width / videoRect.height).toFixed(4)),
      styles: {
        width: shellStyle.width,
        height: shellStyle.height,
        aspectRatio: shellStyle.aspectRatio,
        position: shellStyle.position,
        inset: shellStyle.inset,
        display: shellStyle.display,
        widthLimit: shellStyle.getPropertyValue('--canvas-video-width-limit').trim(),
      },
      paused: node.classList.contains('is-paused'),
    };
  });

  const before = await measure();
  await shell.hover();
  await page.click(`${shellSelector} button[aria-label="全屏播放"]`);
  await page.waitForFunction(
    (selector) => document.fullscreenElement === document.querySelector(selector),
    {timeout: 5_000},
    shellSelector,
  );
  await page.evaluate((selector) => {
    const video = document.querySelector(`${selector} video`);
    if (video instanceof HTMLVideoElement) video.pause();
  }, shellSelector);
  await new Promise((resolve) => setTimeout(resolve, 250));
  await shell.hover();
  const paused = await measure();

  await page.evaluate(async (selector) => {
    const video = document.querySelector(`${selector} video`);
    if (!video) return;
    try {
      video.muted = true;
      await video.play();
    } catch {}
  }, shellSelector);
  await new Promise((resolve) => setTimeout(resolve, 500));
  const playing = await measure();

  await page.evaluate(() => {
    if (document.fullscreenElement) void document.exitFullscreen();
  });
  await page.waitForFunction(
    () => document.fullscreenElement === null,
    {timeout: 5_000},
  );

  console.log(JSON.stringify({name, before, paused, playing, errors}, null, 2));
  if (!paused.fullscreen || !playing.fullscreen) {
    throw new Error('could not enter the real browser fullscreen mode');
  }
  if (!paused.paused || playing.paused) {
    throw new Error('video did not reach both paused and playing states');
  }
  if (Math.abs(paused.ratioVar - paused.intrinsicRatio) > 0.02) {
    throw new Error(
      `fullscreen ratio variable does not match intrinsic video: `
      + `var=${paused.ratioVar}, video=${paused.intrinsicRatio}`,
    );
  }
  if (Math.abs(paused.videoRatio - playing.videoRatio) > 0.01) {
    throw new Error(
      `fullscreen video ratio changes: paused=${paused.videoRatio}, playing=${playing.videoRatio}`,
    );
  }
  for (const state of [paused, playing]) {
    if (
      Math.abs(state.stage.width - state.content.width) > 2
      || Math.abs(state.stage.left - state.content.left) > 2
    ) {
      throw new Error(
        `fullscreen stage does not match the video aspect box: `
        + `stage=${state.stage.left}-${state.stage.right}, `
        + `content=${state.content.left}-${state.content.right}`,
      );
    }
    if (
      state.controls.left < state.content.left - 1
      || state.controls.right > state.content.right + 1
      || state.controls.width > state.content.width + 2
    ) {
      throw new Error(
        `video controls overflow the video bounds: `
        + `controls=${state.controls.left}-${state.controls.right}, `
        + `content=${state.content.left}-${state.content.right}`,
      );
    }
    if (state.content.height > state.viewport.height || state.content.width > state.viewport.width) {
      throw new Error('fullscreen video exceeds the viewport');
    }
  }
    if (errors.length) throw new Error(errors.join(' | '));
    return {name, paused, playing};
  } finally {
    await page.close();
  }
};

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

try {
  const results = [];
  for (const item of cases) results.push(await verifyCase(browser, item));
  console.log(JSON.stringify({
    passing: results.map((item) => item.name),
    geometry: results.map((item) => ({
      name: item.name,
      intrinsicRatio: item.paused.intrinsicRatio,
      videoWidth: item.paused.video.width,
      videoHeight: item.paused.video.height,
      controlWidth: item.paused.controls.width,
    })),
  }, null, 2));
} finally {
  await browser.close();
}
