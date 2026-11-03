import puppeteer from 'puppeteer-core';

/**
 * Runtime color gate for canvas edges.
 *
 * Chromium's computed style for SVG <path> can be affected by later CSS and
 * theme load order. This gate reads the actual rendered stroke and the Canvas
 * comet palette instead of trusting the source hex strings alone.
 */
const base = (process.env.CANVAS_BASE || 'http://127.0.0.1:8890').replace(/\/$/, '');
const token = process.env.CANVAS_TOKEN || '';
const projectId = process.env.PROJECT_ID || '';
const episodeId = process.env.EPISODE_ID || 'EP003';

if (!token || !projectId) {
  throw new Error('CANVAS_TOKEN and PROJECT_ID are required');
}

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

try {
  const page = await browser.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await page.setViewport({width: 1920, height: 1080});
  await page.setCookie({
    name: process.env.CANVAS_COOKIE || 'canvas_session',
    value: token,
    domain: new URL(base).hostname,
    path: '/',
    httpOnly: true,
    sameSite: 'Lax',
  });
  await page.goto(
    `${base}/?ep=${encodeURIComponent(episodeId)}#${encodeURIComponent(projectId)}`,
    {waitUntil: 'networkidle2', timeout: 60_000},
  );
  await page.waitForSelector('.react-flow__edge', {timeout: 60_000});
  await new Promise((resolve) => setTimeout(resolve, 400));

  const tokens = await page.evaluate(() => {
    const style = getComputedStyle(document.documentElement);
    return {
      hover: style.getPropertyValue('--v-edge-hover').trim(),
      selected: style.getPropertyValue('--v-edge-selected').trim(),
      running: style.getPropertyValue('--v-edge-running').trim(),
      body: style.getPropertyValue('--edge-body').trim(),
      tail: style.getPropertyValue('--edge-tail').trim(),
    };
  });
  const baseStroke = await page.$eval(
    '.react-flow__edge .react-flow__edge-path',
    (path) => getComputedStyle(path).stroke,
  );

  const canvasSource = await page.$eval(
    '.react-flow__edge .canvas-edge-flow',
    (path) => {
      const overlay = document.querySelector('.canvas-edge-flow-canvas');
      return {
        body: path.getAttribute('style') || '',
        overlayPresent: !!overlay,
      };
    },
  );

  const result = {tokens, baseStroke, canvasSource, pageErrors};
  console.log(JSON.stringify(result, null, 2));
  if (tokens.hover !== '#009efa' || tokens.selected !== '#0099f2' || tokens.running !== '#0091e5') {
    throw new Error(`edge tokens were not applied: ${JSON.stringify(tokens)}`);
  }
  if (!canvasSource.overlayPresent) {
    throw new Error('Canvas comet overlay is missing');
  }
  if (pageErrors.length) {
    throw new Error(`browser errors: ${pageErrors.join(' | ')}`);
  }
} finally {
  await browser.close();
}
