import fs from 'node:fs/promises';
import path from 'node:path';
import puppeteer from 'puppeteer-core';

const shots = path.resolve(import.meta.dirname, '..', '..', '.screens');
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const projects = await fetch('http://127.0.0.1:8890/api/projects').then((response) => response.json());
let project = null;
let video = null;
for (const item of projects.projects || []) {
  const candidate = await fetch(`http://127.0.0.1:8890/api/projects/${encodeURIComponent(item.id)}`).then((response) => response.json());
  const candidateVideo = candidate.payload?.nodes?.find((node) => node.type === 'video');
  if (candidateVideo) {
    project = candidate;
    video = candidateVideo;
    break;
  }
}
if (!project || !video) throw new Error('所有项目里都没有视频节点');

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});
try {
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.goto('http://127.0.0.1:8891', {waitUntil: 'networkidle0', timeout: 60000});
  await page.waitForSelector('.canvas-project-btn', {timeout: 30000});
  await page.click('.canvas-project-btn');
  await page.waitForSelector('.canvas-project-menu', {timeout: 10000});
  const selected = await page.evaluate((title) => {
    const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
      .find((item) => (item.textContent || '').includes(title));
    if (!(button instanceof HTMLElement)) return false;
    button.click();
    return true;
  }, project.title || '');
  if (!selected) throw new Error(`项目菜单中找不到：${project.title}`);
  await page.waitForSelector(`[data-id="media-${video.id}"] .canvas-media-card`, {timeout: 60000});
  await page.click(`[data-id="media-${video.id}"] .canvas-media-card`);
  await page.waitForSelector('.jim-bar[data-kind="video"]', {timeout: 15000});
  await fs.mkdir(shots, {recursive: true});

  for (const [name, title] of [
    ['ratio', '画幅比例'],
    ['resolution', '分辨率'],
    ['duration', '时长'],
  ]) {
    await page.click(`.jim-bar .jim-tool[title="${title}"]`);
    await page.waitForSelector('.jim-pop', {timeout: 10000});
    await sleep(220);
    const popup = await page.$('.jim-pop:last-of-type');
    const box = await popup.boundingBox();
    if (!box) throw new Error(`${title} 弹层无尺寸`);
    await page.screenshot({
      path: path.join(shots, `param-${name}-polished-20261025.png`),
      clip: {
        x: Math.max(0, box.x - 8),
        y: Math.max(0, box.y - 8),
        width: Math.min(box.width + 16, 1440),
        height: Math.min(box.height + 16, 900),
      },
    });
    await page.keyboard.press('Escape');
    await sleep(180);
  }
  console.log(JSON.stringify({ok: true, videoId: video.id}, null, 2));
} finally {
  await browser.close();
}
