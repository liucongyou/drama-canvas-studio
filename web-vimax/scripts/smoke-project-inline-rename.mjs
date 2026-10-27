import puppeteer from 'puppeteer-core';

const base = 'http://127.0.0.1:8890';
const originalTitle = `内联重命名测试 ${Date.now()}`;
const renamedTitle = `${originalTitle} · 已改名`;

async function api(pathname, options = {}) {
  const response = await fetch(base + pathname, {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${response.status} ${JSON.stringify(payload)}`);
  return payload;
}

const project = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title: originalTitle}),
});

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.goto(base, {waitUntil: 'domcontentloaded', timeout: 60_000});
  await page.waitForSelector('.canvas-project-btn', {timeout: 30_000});
  await page.click('.canvas-project-btn');
  await page.waitForSelector('.canvas-project-menu-list button', {timeout: 10_000});
  await page.evaluate((title) => {
    const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
      .find((item) => (item.textContent || '').includes(title));
    if (!(button instanceof HTMLElement)) throw new Error('temporary project missing');
    button.click();
  }, originalTitle);
  await page.waitForFunction((title) => document.querySelector('.canvas-project-name')?.textContent?.includes(title), {}, originalTitle);

  await page.click('.canvas-project-rename-trigger');
  await page.waitForSelector('.canvas-project-rename input', {timeout: 5_000});
  await page.click('.canvas-project-rename input');
  await page.keyboard.down('Control');
  await page.keyboard.press('A');
  await page.keyboard.up('Control');
  await page.type('.canvas-project-rename input', renamedTitle);
  const typedValue = await page.$eval('.canvas-project-rename input', (input) => input instanceof HTMLInputElement ? input.value : '');
  if (typedValue !== renamedTitle) throw new Error(`rename input mismatch: ${JSON.stringify({typedValue, renamedTitle})}`);
  await page.click('.canvas-project-rename button[type="submit"]');
  await page.waitForFunction(
    (title) => document.querySelector('.canvas-project-name')?.textContent?.includes(title),
    {timeout: 20_000},
    renamedTitle,
  );
  const headingAfterRename = await page.$eval('.canvas-project-name', (element) => element.textContent?.trim() || '');
  if (!headingAfterRename.includes(renamedTitle)) {
    const debug = await page.evaluate(() => ({
      body: document.body.innerText.slice(0, 500),
      inputExists: !!document.querySelector('.canvas-project-rename input'),
    }));
    throw new Error(`rename heading did not update: ${JSON.stringify({typedValue, headingAfterRename, debug})}`);
  }

  const persisted = await api(`/api/projects/${project.id}`);
  if (!persisted.title.includes(renamedTitle)) {
    throw new Error(`rename not persisted: ${persisted.title}`);
  }

  await page.click('.shell-topbar-right .shell-topbar-btn');
  await page.waitForSelector('.shell-settings-menu', {timeout: 5_000});
  const settings = await page.evaluate(() => ({
    hasRename: [...document.querySelectorAll('.shell-settings-menu button')]
      .some((item) => (item.textContent || '').includes('重命名项目')),
    themeCount: document.querySelectorAll('.shell-settings-themes .shell-theme-option').length,
    hasModelSettings: [...document.querySelectorAll('.shell-settings-menu button')]
      .some((item) => (item.textContent || '').includes('供应商、模型和参数范围')),
    hasImport: document.querySelector('.shell-settings-menu .shell-tools-field input') instanceof HTMLInputElement,
  }));
  if (settings.hasRename || settings.themeCount < 5 || !settings.hasModelSettings || !settings.hasImport) {
    throw new Error(`settings menu consolidation mismatch: ${JSON.stringify(settings)}`);
  }

  console.log(JSON.stringify({ok: true, renamedTitle: persisted.title, settings}, null, 2));
} finally {
  await browser.close();
  await api(`/api/projects/${project.id}`, {method: 'DELETE'}).catch(() => {});
}
