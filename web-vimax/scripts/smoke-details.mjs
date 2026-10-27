// 细节优化素材采集：展开段卡 + 镜头提示词/资产引用弹窗 + 工具栏，双主题截图
import {chromium} from 'file:///C:/Users/liuch/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.mjs';
import path from 'node:path';
import url from 'node:url';

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const chrome = 'C:/Users/liuch/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe';
const base = 'http://localhost:4173';

const browser = await chromium.launch({executablePath: chrome, headless: true});
for (const theme of ['dark', 'light']) {
  const ctx = await browser.newContext({viewport: {width: 1680, height: 1000}, deviceScaleFactor: 1});
  const page = await ctx.newPage();
  await page.goto(base, {waitUntil: 'networkidle', timeout: 60000});
  await page.evaluate((t) => { document.documentElement.dataset.theme = t; }, theme);
  // 进画布
  await page.locator('button:has-text("画布")').first().dispatchEvent('click');
  await page.waitForSelector('.react-flow', {timeout: 30000});
  await page.waitForTimeout(2000);
  // 整剧总览
  const overviewBtn = page.locator('button:has(span.ep-name:has-text("整剧总览"))').first();
  if (await overviewBtn.count() > 0) await overviewBtn.dispatchEvent('click');
  await page.waitForSelector('.canvas-ep-box', {timeout: 60000, state: 'attached'}).catch(() => {});
  await page.waitForTimeout(1500);

  // 以某个 segment 卡为中心放大，截展开段卡主体
  const seg = page.locator('.canvas-seg-scene').first();
  if (await seg.count() > 0) {
    const bb = await seg.boundingBox();
    if (bb) {
      await page.mouse.move(bb.x + bb.width / 2, bb.y + Math.min(bb.height / 2, 300));
      for (let i = 0; i < 5; i++) { await page.mouse.wheel(0, -240); await page.waitForTimeout(120); }
    }
  }
  await page.waitForTimeout(1200);
  // 段卡主体特写
  const segClip = await segClipper(page, theme, 'segcard');
  // 点镜头媒体打开提示词弹窗
  const shot = page.locator('.canvas-seg-shot').first();
  if (await shot.count() > 0) { await shot.dispatchEvent('click'); }
  await page.waitForSelector('.canvas-prompt-modal', {timeout: 15000}).catch(() => {});
  await page.waitForTimeout(1200);
  const modal = page.locator('.canvas-prompt-modal').first();
  if (await modal.count() > 0) {
    const mbb = await modal.boundingBox();
    if (mbb) {
      await page.screenshot({path: path.join(__dirname, `detail-prompt-${theme}.png`), clip: {x: Math.max(0, mbb.x - 10), y: Math.max(0, mbb.y - 10), width: Math.min(mbb.width + 20, 1680), height: Math.min(mbb.height + 20, 1000)}});
    }
  } else {
    console.log(`[${theme}] prompt modal not found`);
  }

  // 工具栏/图例也截一张
  await page.screenshot({path: path.join(__dirname, `detail-overview-${theme}.png`)});
  console.log(`theme=${theme} done; segClip=${segClip}`);
  await ctx.close();
}
await browser.close();

async function segClipper(page, theme, name) {
  const el = page.locator('.canvas-seg-scene').first();
  const bb = await el.boundingBox();
  const vp = page.viewportSize();
  if (!bb) return 'no-bb';
  const x0 = Math.max(0, bb.x - 10), y0 = Math.max(0, bb.y - 10);
  const x1 = Math.min(bb.x + bb.width + 10, vp.width), y1 = Math.min(bb.y + bb.height + 10, vp.height);
  if (x1 - x0 > 60 && y1 - y0 > 60) {
    await page.screenshot({path: path.join(__dirname, `detail-${name}-${theme}.png`), clip: {x: x0, y: y0, width: x1 - x0, height: y1 - y0}});
    return 'ok';
  }
  return 'outside';
}
