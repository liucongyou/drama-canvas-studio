// 画布总览 ep-box 排版修复冒烟：截图整剧总览（默认集）画布，双主题各一张
import {chromium} from 'file:///C:/Users/liuch/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
import path from 'node:path';
import url from 'node:url';

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const chrome = 'C:/Users/liuch/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe';
const base = 'http://localhost:4173';

const browser = await chromium.launch({executablePath: chrome, headless: true});
const errors = [];
for (const theme of ['dark', 'light']) {
  const ctx = await browser.newContext({viewport: {width: 1680, height: 1000}, deviceScaleFactor: 1});
  const page = await ctx.newPage();
  page.on('console', (m) => { if (m.type() === 'error') errors.push(`[${theme}] ${m.text()}`); });
  page.on('pageerror', (e) => errors.push(`[${theme}] pageerror: ${e.message}`));
  await page.goto(base, {waitUntil: 'networkidle', timeout: 60000});
  await page.evaluate((t) => { document.documentElement.dataset.theme = t; document.documentElement.setAttribute('data-theme', t); }, theme);
  // 进入工作区 → 画布（侧栏按钮可能被遮挡，统一用 dispatchEvent 直接触发 onClick）
  const canvasBtn = page.locator('button:has-text("画布")').first();
  await canvasBtn.dispatchEvent('click');
  await page.waitForSelector('.react-flow', {timeout: 30000});
  await page.waitForTimeout(2000);
  // 默认进单集（pickDefaultEpisode），点侧栏"整剧总览"切到 ep-box 总览
  const overviewBtn = page.locator('button:has(span.ep-name:has-text("整剧总览"))').first();
  if (await overviewBtn.count() > 0) { await overviewBtn.dispatchEvent('click'); } else { console.log('WARN: overview btn not found'); }
  await page.waitForSelector('.canvas-ep-box', {timeout: 60000, state: 'attached'}).catch(async () => {
    console.log('WARN: .canvas-ep-box not attached; debug screenshot');
    await page.screenshot({path: path.join(__dirname, 'debug-overview.png')});
    console.log('ep sidebar text:', (await page.locator('aside.sidebar').innerText().catch(() => 'n/a')).slice(0, 300));
  });
  const shot = path.join(__dirname, `canvas-epbox-${theme}.png`);
  await page.screenshot({path: shot});
  // 适应视图 + 放大两档，截 ep-box 特写
  const fitBtn = page.locator('button:has-text("适应")').first();
  if (await fitBtn.count() > 0) await fitBtn.dispatchEvent('click');
  await page.waitForTimeout(800);
  // 以 ep-box 中心为缩放焦点滚轮放大（React Flow 以光标为中心缩放）
  const box0 = page.locator('.canvas-ep-box').first();
  if (await box0.count() > 0) {
    const bb0 = await box0.boundingBox();
    if (bb0) {
      await page.mouse.move(bb0.x + bb0.width / 2, bb0.y + Math.min(bb0.height / 2, 400));
      for (let i = 0; i < 6; i++) { await page.mouse.wheel(0, -240); await page.waitForTimeout(150); }
    }
  }
  await page.waitForTimeout(1500);
  const box = page.locator('.canvas-ep-box').first();
  if (await box.count() > 0) {
    const bb = await box.boundingBox();
    const vp = page.viewportSize();
    if (bb) {
      const x0 = Math.max(0, bb.x - 30), y0 = Math.max(0, bb.y - 30);
      const x1 = Math.min(bb.x + bb.width + 30, vp.width), y1 = Math.min(bb.y + bb.height + 30, vp.height);
      if (x1 - x0 > 60 && y1 - y0 > 60) {
        await page.screenshot({path: path.join(__dirname, `canvas-epbox-closeup-${theme}.png`), clip: {x: x0, y: y0, width: x1 - x0, height: y1 - y0}});
      } else {
        console.log('WARN: ep-box outside viewport after zoom, skip closeup');
      }
    }
  }
  console.log(`theme=${theme} done, ep-box count =`, await box.count());
  await ctx.close();
}
await browser.close();
console.log('console errors:', errors.length ? errors.slice(0, 10) : 'none');
