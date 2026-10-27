import path from 'node:path';
import puppeteer from 'puppeteer-core';

const base = 'http://127.0.0.1:8890';
const screenshot = path.resolve('../data/model-reference-limits-20261026.png');

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.goto(base, {waitUntil: 'domcontentloaded', timeout: 60000});
  await page.waitForSelector('button[title^="配置供应商、模型和参数范围"]', {timeout: 30000});
  await page.click('button[title^="配置供应商、模型和参数范围"]');
  await page.waitForSelector('.model-settings-dialog', {timeout: 30000});
  const selected = await page.evaluate(() => {
    const item = [...document.querySelectorAll('.model-settings-model-item')]
      .find((node) => (node.textContent || '').includes('Seedance 2.5'));
    if (!(item instanceof HTMLElement)) return false;
    item.click();
    return true;
  });
  if (!selected) throw new Error('Seedance 2.5 model row not found');
  await page.waitForSelector('.model-settings-limit-panel', {timeout: 10000});
  const limits = await page.$$eval('.model-settings-limit-grid label', (nodes) => nodes.map((node) => ({
    label: node.querySelector('span')?.childNodes[0]?.textContent?.trim() || '',
    value: node.querySelector('input')?.value || '',
  })));
  const expected = {
    '总参考素材（自动）': '50',
    '参考图片': '30',
    '参考视频': '10',
    '参考音频 / 音色': '10',
    '文本引用': '',
    '提示词字符': '12000',
  };
  if (limits.length !== Object.keys(expected).length) {
    throw new Error(`limit field count mismatch: ${JSON.stringify(limits)}`);
  }
  for (const item of limits) {
    if (expected[item.label] !== item.value) {
      throw new Error(`limit mismatch: ${JSON.stringify(item)}`);
    }
  }
  await page.screenshot({path: screenshot, fullPage: false});
  const newModelClicked = await page.evaluate(() => {
    const button = [...document.querySelectorAll('.model-settings-pane-head button')]
      .find((node) => (node.textContent || '').includes('新增模型'));
    if (!(button instanceof HTMLElement)) return false;
    button.click();
    return true;
  });
  if (!newModelClicked) throw new Error('new model button not found');
  await page.waitForFunction(() => {
    const input = document.querySelector('.model-settings-limit-grid input');
    return input instanceof HTMLInputElement && input.value === '';
  }, {timeout: 10000});
  console.log(JSON.stringify({ok: true, limits, screenshot}, null, 2));
} finally {
  await browser.close();
}
