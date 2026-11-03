import puppeteer from 'puppeteer-core';

/**
 * Runtime sharpness gate for the Canvas edge overlay.
 *
 * ViewportPortal is CSS-scaled by React Flow. We verify that the backing store
 * grows with zoom and that effective device pixels stay at least 1x rather
 * than degrading to 1/zoom.
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
  await page.setViewport({width: 1920, height: 1080, deviceScaleFactor: 1});
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
  await page.waitForSelector('.canvas-edge-flow-canvas', {timeout: 60_000});
  // Activate at least one comet so the overlay has painted after the zoom.
  const edgeBox = await page.$eval('.react-flow__edge .react-flow__edge-interaction', (path) => {
    const rect = path.getBoundingClientRect();
    return {x: rect.x + rect.width * 0.15, y: rect.y + rect.height / 2};
  });
  await page.mouse.move(edgeBox.x, edgeBox.y);
  await new Promise((resolve) => setTimeout(resolve, 100));

  const readMetrics = () => page.evaluate(() => {
    const canvas = document.querySelector('.canvas-edge-flow-canvas');
    const viewport = document.querySelector('.react-flow__viewport');
    const transform = getComputedStyle(viewport).transform;
    const matrix = transform === 'none' ? new DOMMatrixReadOnly() : new DOMMatrixReadOnly(transform);
    const zoom = matrix.a || 1;
    const cssWidth = Number.parseFloat(canvas.style.width) || canvas.getBoundingClientRect().width / zoom;
    const tokens = getComputedStyle(document.documentElement);
    const bufferScale = Math.min(
      4096 / Math.max(1, Number.parseFloat(canvas.style.width) || cssWidth),
      4096 / Math.max(1, Number.parseFloat(canvas.style.height) || 0),
      (window.devicePixelRatio || 1) * zoom,
    );
    const requiredBufferPx = Math.ceil(cssWidth * (window.devicePixelRatio || 1) * zoom);
    const bufferLimited = requiredBufferPx > 4096;
    return {
      zoom,
      backingWidth: canvas.width,
      backingHeight: canvas.height,
      cssWidth,
      expectedBufferPx: Math.ceil(cssWidth * bufferScale),
      effectivePixelsPerCssPixel: bufferScale,
      bufferLimited,
      edgeHover: tokens.getPropertyValue('--v-edge-hover').trim(),
      edgeSelected: tokens.getPropertyValue('--v-edge-selected').trim(),
      edgeRunning: tokens.getPropertyValue('--v-edge-running').trim(),
    };
  });

  const initial = await readMetrics();
  // Use the same toolbar zoom button a user clicks so this gate is not tied
  // to keyboard focus state.
  await page.click('button[aria-label="放大"]');
  await page.click('button[aria-label="放大"]');
  await new Promise((resolve) => setTimeout(resolve, 500));
  const zoomed = await readMetrics();

  console.log(JSON.stringify({initial, zoomed, pageErrors}, null, 2));
  if (!(zoomed.zoom > initial.zoom)) {
    throw new Error(`zoom did not increase: ${initial.zoom} -> ${zoomed.zoom}`);
  }
  if (!zoomed.bufferLimited && zoomed.backingWidth < zoomed.expectedBufferPx) {
    throw new Error(
      `comet backing store is under-sampled at ${zoomed.zoom}x: `
      + `${zoomed.backingWidth} < ${zoomed.expectedBufferPx}`,
    );
  }
  if (zoomed.backingWidth > 4096 || zoomed.backingHeight > 4096) {
    throw new Error(`backing store exceeded cap: ${zoomed.backingWidth}x${zoomed.backingHeight}`);
  }
  if (
    zoomed.edgeHover !== '#009efa'
    || zoomed.edgeSelected !== '#0099f2'
    || zoomed.edgeRunning !== '#0091e5'
  ) {
    throw new Error(`unexpected edge tokens: ${JSON.stringify(zoomed)}`);
  }
  if (pageErrors.length) {
    throw new Error(`browser errors: ${pageErrors.join(' | ')}`);
  }
} finally {
  await browser.close();
}
