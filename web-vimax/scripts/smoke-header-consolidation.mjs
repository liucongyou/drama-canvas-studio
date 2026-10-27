import puppeteer from 'puppeteer-core';

const base = 'http://127.0.0.1:8890';
const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

try {
  const page = await browser.newPage();
  const results = [];
  for (const viewport of [
    {width: 1920, height: 1080},
    {width: 1440, height: 900},
    {width: 1024, height: 768},
  ]) {
    await page.setViewport({...viewport, deviceScaleFactor: 1});
    await page.goto(base, {waitUntil: 'domcontentloaded', timeout: 60_000});
    await page.waitForSelector('.canvas-project-btn', {timeout: 30_000});
    await page.click('.canvas-project-btn');
    await page.waitForSelector('.canvas-project-menu-list button', {timeout: 10_000});
    await page.evaluate(() => {
      const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
        .find((item) => (item.textContent || '').includes('铁西硬汉 · 画布工作流'));
      if (!(button instanceof HTMLElement)) throw new Error('project entry missing');
      button.click();
    });
    await page.waitForSelector('.shell-canvas-slot .canvas-inline-topbar', {timeout: 60_000});
    await new Promise((resolve) => setTimeout(resolve, 450));

    const metrics = await page.evaluate(() => {
      const header = document.querySelector('.shell-topbar');
      const title = document.querySelector('.canvas-inline-topbar .workspace-title-copy');
      const status = document.querySelector('.canvas-status-trigger');
      const toolTriggers = document.querySelectorAll('.canvas-header-tools > .canvas-menu-wrap > button');
      const tools = [...toolTriggers].at(-1);
      if (
        !(header instanceof HTMLElement)
        || !(title instanceof HTMLElement)
        || !(status instanceof HTMLElement)
        || !(tools instanceof HTMLElement)
      ) {
        throw new Error('consolidated header elements missing');
      }
      const titleRect = title.getBoundingClientRect();
      return {
        headerHeight: header.getBoundingClientRect().height,
        titleHeight: titleRect.height,
        overflow: header.scrollWidth > header.clientWidth,
        legacySecondRow: !!document.querySelector('.canvas-view > .workspace-header'),
        statusText: status.textContent?.replace(/\s+/g, ' ').trim() || '',
        toolsText: tools.textContent?.replace(/\s+/g, ' ').trim() || '',
      };
    });
    if (
      metrics.headerHeight !== 46
      || metrics.titleHeight > 24
      || metrics.overflow
      || metrics.legacySecondRow
      || !metrics.statusText.includes('已同步')
      || !metrics.toolsText.includes('工具')
    ) {
      throw new Error(`header consolidation mismatch: ${JSON.stringify({viewport, metrics})}`);
    }

    await page.click('.canvas-status-trigger');
    await page.waitForSelector('.canvas-status-menu', {timeout: 5_000});
    const statusRows = await page.$$eval('.canvas-status-menu .canvas-status-row', (rows) => rows.length);
    await page.click('.canvas-header-tools .canvas-menu-wrap:last-of-type > button');
    await page.waitForSelector('.canvas-tools-menu', {timeout: 5_000});
    const menuItems = await page.evaluate(() => ({
      tools: [...document.querySelectorAll('.canvas-tools-menu button')].map((item) => item.textContent?.trim()),
    }));
    if (statusRows < 4 || (menuItems.tools || []).length < 8) {
      throw new Error(`consolidated menus incomplete: ${JSON.stringify(menuItems)}`);
    }
    results.push({viewport, metrics, statusRows, menuItems});
  }
  console.log(JSON.stringify({ok: true, results}, null, 2));
} finally {
  await browser.close();
}
