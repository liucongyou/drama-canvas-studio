import puppeteer from 'puppeteer-core';

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.goto('http://127.0.0.1:8890', {waitUntil: 'domcontentloaded', timeout: 60_000});
  await page.waitForSelector('.canvas-project-btn', {timeout: 30_000});
  await page.click('.canvas-project-btn');
  await page.waitForSelector('.canvas-project-menu-list button', {timeout: 10_000});
  await page.evaluate(() => {
    const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
      .find((item) => (item.textContent || '').includes('铁西硬汉 · 画布工作流'));
    if (button instanceof HTMLElement) button.click();
  });
  await page.waitForSelector('.canvas-ep-list-rail', {timeout: 60_000});
  await page.click('.canvas-ep-list-rail');
  await page.waitForSelector('.canvas-ep-list.is-open', {timeout: 10_000});
  await new Promise((resolve) => setTimeout(resolve, 320));

  const openWidth = await page.$eval('.canvas-ep-list', (element) => element.getBoundingClientRect().width);
  await page.click('.canvas-ep-list-close');
  const closingWidth = await page.$eval('.canvas-ep-list', (element) => element.getBoundingClientRect().width);
  await new Promise((resolve) => setTimeout(resolve, 90));
  const middleWidth = await page.$eval('.canvas-ep-list', (element) => element.getBoundingClientRect().width);
  await new Promise((resolve) => setTimeout(resolve, 260));
  const closed = await page.evaluate(() => {
    const panel = document.querySelector('.canvas-ep-list');
    const rail = document.querySelector('.canvas-ep-list-rail');
    return {
      width: panel?.getBoundingClientRect().width || 0,
      hidden: panel?.getAttribute('aria-hidden') || '',
      railVisible: rail instanceof HTMLElement
        && getComputedStyle(rail).visibility !== 'hidden'
        && Number(getComputedStyle(rail).opacity) > 0,
    };
  });
  if (!(openWidth > 180 && middleWidth > 0 && middleWidth < openWidth && closed.width < 1 && closed.hidden === 'true' && closed.railVisible)) {
    throw new Error(`collapse animation failed: ${JSON.stringify({openWidth, closingWidth, middleWidth, closed})}`);
  }

  await page.click('.canvas-ep-list-rail');
  await new Promise((resolve) => setTimeout(resolve, 320));
  const reopenedWidth = await page.$eval('.canvas-ep-list', (element) => element.getBoundingClientRect().width);
  if (reopenedWidth < 180) throw new Error(`panel did not reopen: ${reopenedWidth}`);
  console.log(JSON.stringify({ok: true, openWidth, closingWidth, middleWidth, closed, reopenedWidth}, null, 2));
} finally {
  await browser.close();
}
