import puppeteer from 'puppeteer-core';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const base = 'http://127.0.0.1:8890';
const stamp = Date.now();
const originalTitle = `项目操作回归 ${stamp}`;
const renamedTitle = `${originalTitle} · 已重命名`;

async function api(pathname, options = {}) {
  const response = await fetch(base + pathname, {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(`${response.status} ${JSON.stringify(payload)}`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

async function findRowAction(page, title) {
  await page.waitForSelector('.canvas-project-menu-list', {timeout: 10_000});
  return page.evaluate((projectTitle) => {
    const row = [...document.querySelectorAll('.canvas-project-row')]
      .find((item) => (item.querySelector('.canvas-project-menu-label strong')?.textContent || '').includes(projectTitle));
    const trigger = row?.querySelector('.canvas-project-action-trigger');
    if (!(trigger instanceof HTMLElement)) throw new Error(`project action trigger missing: ${projectTitle}`);
    trigger.click();
  }, title);
}

async function waitForGone(pathname) {
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    try {
      await api(pathname);
    } catch (error) {
      if (error.status === 404) return;
      throw error;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`resource still exists: ${pathname}`);
}

const project = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title: originalTitle}),
});
await api(`/api/projects/${project.id}/nodes`, {
  method: 'PUT',
  body: JSON.stringify({
    nodes: [{
      id: `smoke-node-${stamp}`,
      type: 'image',
      title: '复制保真测试资产',
      position: {x: 88, y: 66},
      metadata: {status: 'done', content: '/uploads/smoke-project-action.png'},
    }],
  }),
});

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

let duplicatedId = '';
try {
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.goto(`${base}/#${encodeURIComponent(project.id)}`, {waitUntil: 'domcontentloaded', timeout: 60_000});
  await page.waitForSelector('.canvas-project-name', {timeout: 30_000});
  await page.waitForFunction((title) => document.querySelector('.canvas-project-name')?.textContent?.includes(title), {}, originalTitle);

  await page.click('.canvas-project-btn');
  await page.waitForSelector('.canvas-project-row.is-current', {timeout: 5_000});
  const currentRowRect = await page.evaluate((title) => {
    const row = [...document.querySelectorAll('.canvas-project-row')]
      .find((item) => (item.querySelector('.canvas-project-menu-label strong')?.textContent || '').includes(title));
    if (!(row instanceof HTMLElement)) throw new Error('current project row missing');
    const rect = row.getBoundingClientRect();
    return {left: rect.left, top: rect.top, width: rect.width, height: rect.height};
  }, originalTitle);
  await page.mouse.move(900, 720);
  await new Promise((resolve) => setTimeout(resolve, 180));
  const restState = await page.evaluate((title) => {
    const row = [...document.querySelectorAll('.canvas-project-row')]
      .find((item) => (item.querySelector('.canvas-project-menu-label strong')?.textContent || '').includes(title));
    const check = row?.querySelector('.canvas-project-menu-check');
    const action = row?.querySelector('.canvas-project-action-trigger');
    return {
      checkOpacity: check ? getComputedStyle(check).opacity : '',
      actionOpacity: action ? getComputedStyle(action).opacity : '',
      actionPointerEvents: action ? getComputedStyle(action).pointerEvents : '',
      checkCenter: check ? {
        x: check.getBoundingClientRect().left + check.getBoundingClientRect().width / 2,
        y: check.getBoundingClientRect().top + check.getBoundingClientRect().height / 2,
      } : null,
      actionCenter: action ? {
        x: action.getBoundingClientRect().left + action.getBoundingClientRect().width / 2,
        y: action.getBoundingClientRect().top + action.getBoundingClientRect().height / 2,
      } : null,
    };
  }, originalTitle);
  if (
    Number(restState.checkOpacity) < 0.98
    || Number(restState.actionOpacity) > 0.02
    || restState.actionPointerEvents !== 'none'
    || !restState.checkCenter
    || !restState.actionCenter
    || Math.abs(restState.checkCenter.x - restState.actionCenter.x) > 0.5
    || Math.abs(restState.checkCenter.y - restState.actionCenter.y) > 0.5
  ) {
    throw new Error(`current project rest state mismatch: ${JSON.stringify(restState)}`);
  }
  await page.mouse.move(
    currentRowRect.left + currentRowRect.width / 2,
    currentRowRect.top + currentRowRect.height / 2,
  );
  await new Promise((resolve) => setTimeout(resolve, 180));
  const hoverState = await page.evaluate((title) => {
    const row = [...document.querySelectorAll('.canvas-project-row')]
      .find((item) => (item.querySelector('.canvas-project-menu-label strong')?.textContent || '').includes(title));
    const check = row?.querySelector('.canvas-project-menu-check');
    const action = row?.querySelector('.canvas-project-action-trigger');
    return {
      checkOpacity: check ? getComputedStyle(check).opacity : '',
      actionOpacity: action ? getComputedStyle(action).opacity : '',
      actionPointerEvents: action ? getComputedStyle(action).pointerEvents : '',
      checkCenter: check ? {
        x: check.getBoundingClientRect().left + check.getBoundingClientRect().width / 2,
        y: check.getBoundingClientRect().top + check.getBoundingClientRect().height / 2,
      } : null,
      actionCenter: action ? {
        x: action.getBoundingClientRect().left + action.getBoundingClientRect().width / 2,
        y: action.getBoundingClientRect().top + action.getBoundingClientRect().height / 2,
      } : null,
    };
  }, originalTitle);
  if (
    Number(hoverState.checkOpacity) > 0.02
    || Number(hoverState.actionOpacity) < 0.98
    || hoverState.actionPointerEvents !== 'auto'
    || !hoverState.checkCenter
    || !hoverState.actionCenter
    || Math.abs(hoverState.checkCenter.x - hoverState.actionCenter.x) > 0.5
    || Math.abs(hoverState.checkCenter.y - hoverState.actionCenter.y) > 0.5
  ) {
    throw new Error(`current project hover state mismatch: ${JSON.stringify(hoverState)}`);
  }
  await findRowAction(page, originalTitle);
  await page.waitForSelector('.canvas-project-action-menu', {timeout: 5_000});
  const actionLabels = await page.$$eval('.canvas-project-action-menu button', (buttons) => (
    buttons.map((button) => (button.textContent || '').replace(/\s+/g, ' ').trim())
  ));
  for (const expected of ['重命名项目', '在新窗口打开', '复制项目', '删除项目']) {
    if (!actionLabels.includes(expected)) {
      throw new Error(`project action missing ${expected}: ${JSON.stringify(actionLabels)}`);
    }
  }

  const screenshotDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'data');
  await fs.mkdir(screenshotDir, {recursive: true});
  const screenshotPath = path.join(screenshotDir, `project-actions-${stamp}.png`);
  await page.screenshot({path: screenshotPath, fullPage: false});

  await page.evaluate(() => {
    const button = [...document.querySelectorAll('.canvas-project-action-menu button')]
      .find((item) => (item.textContent || '').includes('重命名项目'));
    if (!(button instanceof HTMLElement)) throw new Error('rename action missing');
    button.click();
  });
  await page.waitForSelector('.canvas-project-rename input', {timeout: 5_000});
  await page.click('.canvas-project-rename input');
  await page.keyboard.down('Control');
  await page.keyboard.press('A');
  await page.keyboard.up('Control');
  await page.type('.canvas-project-rename input', renamedTitle);
  await page.click('.canvas-project-rename button[type="submit"]');
  await page.waitForFunction((title) => document.querySelector('.canvas-project-name')?.textContent?.includes(title), {timeout: 20_000}, renamedTitle);
  const renamedProject = await api(`/api/projects/${project.id}`);
  if (renamedProject.title !== renamedTitle || renamedProject.payload.title !== renamedTitle) {
    throw new Error(`rename did not persist both columns: ${JSON.stringify({title: renamedProject.title, payloadTitle: renamedProject.payload.title})}`);
  }

  await page.click('.canvas-project-btn');
  await findRowAction(page, renamedTitle);
  await page.waitForSelector('.canvas-project-action-menu', {timeout: 5_000});
  await page.evaluate(() => {
    const button = [...document.querySelectorAll('.canvas-project-action-menu button')]
      .find((item) => (item.textContent || '').includes('复制项目'));
    if (!(button instanceof HTMLElement)) throw new Error('duplicate action missing');
    button.click();
  });
  await page.waitForFunction(
    (title) => document.querySelector('.canvas-project-name')?.textContent?.includes(`${title} · 副本`),
    {timeout: 30_000},
    renamedTitle,
  );
  const projects = (await api('/api/projects')).projects || [];
  const duplicated = projects.find((item) => item.title === `${renamedTitle} · 副本`);
  if (!duplicated) throw new Error('duplicated project missing from project list');
  duplicatedId = duplicated.id;
  const duplicatedProject = await api(`/api/projects/${duplicatedId}`);
  const copiedNode = (duplicatedProject.payload.nodes || []).find((node) => node.id === `smoke-node-${stamp}`);
  if (!copiedNode || copiedNode.position?.x !== 88 || copiedNode.metadata?.content !== '/uploads/smoke-project-action.png') {
    throw new Error(`duplicated project did not preserve node state: ${JSON.stringify(copiedNode)}`);
  }

  await page.click('.canvas-project-btn');
  await findRowAction(page, `${renamedTitle} · 副本`);
  await page.waitForSelector('.canvas-project-action-menu', {timeout: 5_000});
  await page.evaluate(() => {
    const button = [...document.querySelectorAll('.canvas-project-action-menu button')]
      .find((item) => (item.textContent || '').includes('删除项目'));
    if (!(button instanceof HTMLElement)) throw new Error('delete action missing');
    button.click();
  });
  await page.waitForSelector('.gdialog-btn.is-danger', {timeout: 5_000});
  await page.click('.gdialog-btn.is-danger');
  await waitForGone(`/api/projects/${duplicatedId}`);
  duplicatedId = '';

  console.log(JSON.stringify({
    ok: true,
    actionLabels,
    restState,
    hoverState,
    screenshotPath,
    renamedProject: renamedProject.title,
    copiedNode: copiedNode.title,
  }, null, 2));
} finally {
  await browser.close();
  if (duplicatedId) await api(`/api/projects/${duplicatedId}`, {method: 'DELETE'}).catch(() => {});
  await api(`/api/projects/${project.id}`, {method: 'DELETE'}).catch(() => {});
}
