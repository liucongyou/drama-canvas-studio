import fs from 'node:fs/promises';
import path from 'node:path';
import puppeteer from 'puppeteer-core';
import {fileURLToPath} from 'node:url';

const base = 'http://127.0.0.1:8890';
const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

async function openModelSettings(page) {
  await page.waitForSelector('.shell-topbar-right .shell-topbar-btn', {timeout: 30_000});
  await page.click('.shell-topbar-right .shell-topbar-btn');
  await page.waitForSelector('.shell-settings-menu', {timeout: 10_000});
  await page.evaluate(() => {
    const button = [...document.querySelectorAll('.shell-settings-menu button')]
      .find((item) => (item.textContent || '').includes('供应商、模型和参数范围'));
    if (!(button instanceof HTMLElement)) throw new Error('model settings entry missing');
    button.click();
  });
  await page.waitForSelector('.model-settings-dialog', {timeout: 20_000});
  await page.waitForFunction(() => document.querySelectorAll('.model-settings-list-item').length > 1);
}

try {
  const catalog = await fetch(`${base}/api/model-catalog`).then((response) => response.json());
  const shots = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'data');
  await fs.mkdir(shots, {recursive: true});
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.evaluateOnNewDocument(() => localStorage.setItem('vimax-web-theme', 'immersive'));
  await page.goto(base, {waitUntil: 'domcontentloaded', timeout: 60_000});
  await openModelSettings(page);

  const initial = await page.evaluate(() => {
    const body = document.querySelector('.model-settings-body');
    const providerActive = document.querySelector('.model-settings-list-item.is-active');
    return {
      columns: body ? getComputedStyle(body).gridTemplateColumns : '',
      providerActive: providerActive?.textContent?.replace(/\s+/g, ' ').trim() || '',
      detailHead: document.querySelector('.model-settings-detail-head h3')?.textContent?.trim() || '',
      providerForm: !!document.querySelector('.model-settings-provider-nav + .model-settings-model-nav + .model-settings-detail .model-settings-form'),
      providerCount: document.querySelectorAll('.model-settings-list-item').length,
      modelCount: document.querySelectorAll('.model-settings-model-item').length,
      dialogOverflowX: (() => {
        const dialog = document.querySelector('.model-settings-dialog');
        return dialog ? dialog.scrollWidth > dialog.clientWidth + 1 : true;
      })(),
    };
  });
  if (initial.providerCount !== catalog.providers.length) {
    throw new Error(`provider count mismatch: ${JSON.stringify(initial)}`);
  }
  if (Number(initial.columns.split(' ').length) < 3) {
    throw new Error(`desktop workspace is not three columns: ${initial.columns}`);
  }
  if (!initial.providerForm || initial.detailHead === '新增供应商') {
    throw new Error(`provider detail is not selected initially: ${JSON.stringify(initial)}`);
  }
  if (initial.dialogOverflowX) throw new Error('desktop model settings overflow horizontally');

  const providerRows = await page.$$('.model-settings-list-item');
  await providerRows[1].click();
  await new Promise((resolve) => setTimeout(resolve, 250));
  const filtered = await page.evaluate(() => ({
    provider: document.querySelector('.model-settings-list-item.is-active strong')?.textContent?.trim() || '',
    context: document.querySelector('.model-settings-model-context')?.textContent?.trim() || '',
    models: [...document.querySelectorAll('.model-settings-model-item strong')]
      .map((element) => element.textContent?.trim() || ''),
    detailHead: document.querySelector('.model-settings-detail-head h3')?.textContent?.trim() || '',
  }));
  const selectedProvider = catalog.providers.find((provider) => provider.name === filtered.provider);
  if (!selectedProvider) throw new Error(`selected provider missing from catalog: ${filtered.provider}`);
  const expectedModels = catalog.models.filter((model) => model.providerId === selectedProvider.id);
  if (filtered.models.length !== expectedModels.length || filtered.context !== selectedProvider.name) {
    throw new Error(`model filter mismatch: ${JSON.stringify({filtered, expected: expectedModels.map((model) => model.label)})}`);
  }

  const firstModel = await page.$('.model-settings-model-item');
  if (!firstModel) throw new Error('selected provider has no model to test');
  await firstModel.click();
  await new Promise((resolve) => setTimeout(resolve, 250));
  const modelDetail = await page.evaluate(() => ({
    kicker: document.querySelector('.model-settings-detail-head .model-settings-nav-kicker')?.textContent?.trim() || '',
    head: document.querySelector('.model-settings-detail-head h3')?.textContent?.trim() || '',
    activeModel: document.querySelector('.model-settings-model-item.is-active strong')?.textContent?.trim() || '',
    saveVisible: [...document.querySelectorAll('.model-settings-actions button')]
      .some((button) => (button.textContent || '').includes('保存模型')),
    scrollable: (() => {
      const detail = document.querySelector('.model-settings-detail-scroll');
      return detail ? detail.scrollHeight > detail.clientHeight : false;
    })(),
  }));
  if (modelDetail.kicker !== '模型设置' || !modelDetail.head || modelDetail.head !== modelDetail.activeModel || !modelDetail.saveVisible) {
    throw new Error(`model detail did not switch correctly: ${JSON.stringify(modelDetail)}`);
  }
  const screenshotPath = path.join(shots, 'model-settings-workspace.png');
  await page.screenshot({path: screenshotPath, fullPage: false});

  await page.click('.model-settings-model-nav .model-settings-nav-head button');
  await page.waitForFunction(() => (
    document.querySelector('.model-settings-detail-head h3')?.textContent?.includes('新增模型')
  ));
  const newModel = await page.evaluate(() => ({
    providerSelected: document.querySelector('.model-settings-detail .model-select span')?.textContent?.trim() || '',
    activeModelCount: document.querySelectorAll('.model-settings-model-item.is-active').length,
  }));
  if (newModel.providerSelected !== filtered.provider || newModel.activeModelCount !== 0) {
    throw new Error(`new model state mismatch: ${JSON.stringify(newModel)}`);
  }

  const compact = [];
  for (const viewport of [{width: 1024, height: 760}, {width: 800, height: 720}]) {
    await page.setViewport({...viewport, deviceScaleFactor: 1});
    await new Promise((resolve) => setTimeout(resolve, 250));
    compact.push(await page.evaluate((viewport) => {
      const dialog = document.querySelector('.model-settings-dialog');
      const body = document.querySelector('.model-settings-body');
      const rect = dialog?.getBoundingClientRect();
      return {
        viewport,
        width: rect?.width || 0,
        height: rect?.height || 0,
        columns: body ? getComputedStyle(body).gridTemplateColumns : '',
        overflowX: dialog ? dialog.scrollWidth > dialog.clientWidth + 1 : true,
        withinViewport: !!rect && rect.left >= 0 && rect.right <= viewport.width + 1 && rect.top >= 0 && rect.bottom <= viewport.height + 1,
      };
    }, viewport));
  }
  if (compact.some((item) => item.overflowX || !item.withinViewport)) {
    throw new Error(`compact layout overflow: ${JSON.stringify(compact)}`);
  }

  console.log(JSON.stringify({
    ok: true,
    initial,
    filtered: {provider: filtered.provider, modelCount: filtered.models.length},
    modelDetail,
    newModel,
    compact,
    screenshotPath,
  }, null, 2));
} finally {
  await browser.close();
}
