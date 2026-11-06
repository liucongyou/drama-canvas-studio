import puppeteer from 'puppeteer-core';

const base = process.env.CANVAS_BASE || 'http://127.0.0.1:8890';
const token = process.env.CANVAS_TOKEN || '';
const projectId = process.env.PROJECT_ID || '';
const runs = Math.max(1, Number(process.env.RUNS || 3));

if (!projectId) throw new Error('PROJECT_ID is required');

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

const results = [];
try {
  for (let run = 0; run < runs; run += 1) {
    const page = await browser.newPage();
    await page.setViewport({width: 1680, height: 1050, deviceScaleFactor: 1});
    if (token) {
      await page.setCookie({
        name: process.env.CANVAS_COOKIE || 'canvas_session',
        value: token,
        domain: '127.0.0.1',
        path: '/',
        httpOnly: true,
      });
    }
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text());
    });

    const startedAt = Date.now();
    await page.goto(`${base}/#${projectId}`, {
      waitUntil: 'domcontentloaded',
      timeout: 60_000,
    });
    await page.waitForSelector('.react-flow__node-media', {timeout: 60_000});
    const firstCardMs = Date.now() - startedAt;
    await page.waitForFunction(() => {
      return performance.getEntriesByName('canvas-layout-settled').length > 0;
    }, {timeout: 60_000});
    const settledMs = Date.now() - startedAt;
    await new Promise((resolve) => setTimeout(resolve, 300));

    const snapshot = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('.react-flow__node-media')]
        .map((element) => {
          const rect = element.getBoundingClientRect();
          return {
            id: element.getAttribute('data-id') || '',
            x: Number(rect.x.toFixed(1)),
            y: Number(rect.y.toFixed(1)),
          };
        });
      const marks = performance.getEntriesByType('mark')
        .filter((entry) => entry.name.startsWith('canvas-'))
        .map((entry) => ({
          name: entry.name,
          at: Number(entry.startTime.toFixed(1)),
          detail: 'detail' in entry ? entry.detail : undefined,
        }));
      return {
        mediaCards: rows.length,
        domNodes: document.querySelectorAll('*').length,
        rows,
        marks,
      };
    });

    results.push({
      run: run + 1,
      firstCardMs,
      settledMs,
      mediaCards: snapshot.mediaCards,
      domNodes: snapshot.domNodes,
      marks: snapshot.marks,
      errors,
      firstRows: snapshot.rows.slice(0, 20),
    });
    await page.close();
  }
} finally {
  await browser.close();
}

console.log(JSON.stringify({base, projectId, runs: results}, null, 2));
