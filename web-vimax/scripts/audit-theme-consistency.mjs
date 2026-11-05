import puppeteer from 'puppeteer-core';

const base = 'http://127.0.0.1:8890';
const projectId = 'l0CcworH4Dv6vtGDTPoO9S';
const theme = process.env.THEME || 'light';
const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.evaluateOnNewDocument((value) => localStorage.setItem('vimax-web-theme', value), theme);
  await page.goto(`${base}/#${projectId}`, {waitUntil: 'domcontentloaded', timeout: 60_000});
  await page.waitForSelector('.canvas-minimap', {timeout: 60_000});
  const expectedTheme = theme === 'light'
    ? {theme: 'light', variant: 'light'}
    : {theme: 'dark', variant: theme === 'dark' ? 'aigc' : theme};
  await page.waitForFunction(
    ({theme: expected, variant}) => (
      document.documentElement.dataset.theme === expected
      && document.documentElement.dataset.variant === variant
    ),
    {timeout: 10_000},
    expectedTheme,
  );
  await sleep(900);

  const globalDarkBackgrounds = theme === 'light'
    ? await page.evaluate(() => {
      const rows = [];
      const ignored = [
        '.canvas-media-preview',
        '.canvas-video-shell',
        '.canvas-audio-shell',
        '.canvas-lightbox',
        '.canvas-lightbox-nav',
        '.react-flow__minimap',
        '.canvas-minimap',
      ];
      for (const element of document.querySelectorAll('body *')) {
        if (element.closest(ignored.map((selector) => selector).join(','))) continue;
        if (element.matches('img,video,canvas')) continue;
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        if (
          style.display === 'none'
          || style.visibility === 'hidden'
          || Number(style.opacity) === 0
          || rect.width < 16
          || rect.height < 12
        ) continue;
        const match = /rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/.exec(style.backgroundColor);
        if (!match) continue;
        const alpha = match[4] === undefined ? 1 : Number(match[4]);
        if (alpha < 0.55) continue;
        const luminance = (Number(match[1]) + Number(match[2]) + Number(match[3])) / 3;
        if (luminance >= 92) continue;
        const selector = [
          element.tagName.toLowerCase(),
          ...(typeof element.className === 'string' ? element.className.trim().split(/\s+/).slice(0, 2) : []),
        ].join('.');
        rows.push({
          selector,
          background: style.backgroundColor,
          color: style.color,
          text: (element.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 30),
        });
      }
      return rows.slice(0, 80);
    })
    : [];

  const panels = [];
  const capture = async (name, selector) => {
    await page.waitForSelector(selector, {timeout: 10_000});
    panels.push(await page.evaluate(({name, selector, theme}) => {
      const root = document.querySelector(selector);
      if (!(root instanceof HTMLElement)) throw new Error(`panel missing: ${selector}`);
      const rootStyle = getComputedStyle(root);
      const tokenStyle = getComputedStyle(document.documentElement);
      const visible = (element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== 'none'
          && style.visibility !== 'hidden'
          && Number(style.opacity) > 0
          && rect.width > 12
          && rect.height > 8;
      };
      const rgba = (value) => {
        const match = /rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/.exec(value || '');
        if (!match) return null;
        return {
          r: Number(match[1]),
          g: Number(match[2]),
          b: Number(match[3]),
          a: match[4] === undefined ? 1 : Number(match[4]),
        };
      };
      const darkChildren = [];
      for (const element of root.querySelectorAll('*')) {
        if (!visible(element)) continue;
        if (element.matches('img,video,canvas')) continue;
        const style = getComputedStyle(element);
        const color = rgba(style.backgroundColor);
        if (!color || color.a < 0.55) continue;
        const luminance = (color.r + color.g + color.b) / 3;
        if (theme === 'light' && luminance >= 92) continue;
        const selectorText = [
          element.tagName.toLowerCase(),
          ...(typeof element.className === 'string' ? element.className.trim().split(/\s+/).slice(0, 2) : []),
        ].join('.');
        darkChildren.push({
          selector: selectorText,
          background: style.backgroundColor,
          color: style.color,
          text: (element.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 24),
        });
      }
      const expectedSurface = tokenStyle.getPropertyValue('--v-surface').trim();
      const expectedHex = expectedSurface.replace('#', '');
      const parsedRoot = rgba(rootStyle.backgroundColor);
      const expectedRgb = expectedHex.length === 6
        ? {
          r: parseInt(expectedHex.slice(0, 2), 16),
          g: parseInt(expectedHex.slice(2, 4), 16),
          b: parseInt(expectedHex.slice(4, 6), 16),
        }
        : null;
      return {
        name,
        selector,
        background: rootStyle.backgroundColor,
        backgroundImage: rootStyle.backgroundImage,
        borderColor: rootStyle.borderColor,
        color: rootStyle.color,
        expectedSurface,
        expectedSunken: tokenStyle.getPropertyValue('--v-surface-sunken').trim(),
        matchesSurface: !!parsedRoot && !!expectedRgb
          && parsedRoot.r === expectedRgb.r
          && parsedRoot.g === expectedRgb.g
          && parsedRoot.b === expectedRgb.b,
        darkChildren: darkChildren.slice(0, 20),
      };
    }, {name, selector, theme}));
  };

  await page.click('.canvas-project-btn');
  await capture('项目菜单', '.canvas-project-menu');
  await page.click('.canvas-project-btn');

  await page.evaluate(() => {
    const button = document.querySelector('.shell-topbar-right .shell-topbar-btn');
    if (button instanceof HTMLElement) button.click();
  });
  await capture('设置菜单', '.shell-settings-menu');
  await page.evaluate(() => {
    const category = [...document.querySelectorAll('.shell-settings-nav button')]
      .find((item) => (item.textContent || '').startsWith('服务'));
    if (category instanceof HTMLElement) category.click();
  });
  await sleep(120);
  await page.evaluate(() => {
    const button = [...document.querySelectorAll('.shell-settings-menu button')]
      .find((item) => (item.textContent || '').includes('供应商、模型和参数范围'));
    if (button instanceof HTMLElement) button.click();
  });
  await capture('模型接口', '.model-settings-dialog');
  await page.click('.model-settings-dialog .canvas-inspector-close');
  await sleep(250);

  await page.click('.canvas-status-trigger');
  await capture('画布状态', '.canvas-status-menu');
  await page.click('.canvas-status-trigger');

  await page.evaluate(() => {
    const buttons = [...document.querySelectorAll('.canvas-header-tools .canvas-menu-wrap > button')];
    const button = buttons.at(-1);
    if (button instanceof HTMLElement) button.click();
  });
  await capture('工具菜单', '.canvas-tools-menu');
  await page.evaluate(() => {
    const button = [...document.querySelectorAll('.canvas-tools-menu button')]
      .find((item) => (item.textContent || '').includes('快捷键与操作说明'));
    if (button instanceof HTMLElement) button.click();
  });
  await capture('帮助弹窗', '.canvas-help');
  await page.evaluate(() => {
    const close = document.querySelector('.canvas-help .canvas-inspector-close');
    if (close instanceof HTMLElement) close.click();
  });
  await sleep(250);

  await page.click('.canvas-task-center-trigger');
  await capture('任务中心', '.canvas-task-center');
  await page.click('.canvas-task-center-trigger');

  await page.evaluate(() => {
    const button = [...document.querySelectorAll('.canvas-media-card-menu button')]
      .find((item) => item.getAttribute('title') === '展开全文');
    if (button instanceof HTMLElement) button.click();
  });
  await capture('文本全文', '.canvas-text-reader');
  await page.evaluate(() => {
    const button = document.querySelector('.canvas-text-reader > header button:last-child');
    if (button instanceof HTMLElement) button.click();
  });

  const pane = await page.$('.react-flow__pane');
  if (pane) {
    const box = await pane.boundingBox();
    if (box) {
      await page.mouse.click(box.x + box.width * 0.7, box.y + box.height * 0.72, {button: 'right'});
      const contextMenu = await page.waitForSelector('.canvas-context-menu', {timeout: 2_000}).catch(() => null);
      if (contextMenu) {
        await capture('画布右键', '.canvas-context-menu');
        await page.keyboard.press('Escape');
      }
    }
  }

  await page.evaluate(() => {
    const button = [...document.querySelectorAll('.canvas-toolbar button')]
      .find((item) => (item.textContent || '').includes('添加'));
    if (button instanceof HTMLElement) button.click();
  });
  if (await page.$('.canvas-add-dropdown')) {
    await capture('添加菜单', '.canvas-add-dropdown');
    await page.keyboard.press('Escape');
  }

  await page.evaluate(() => {
    const button = document.querySelector('.canvas-media-foot-more > button');
    if (button instanceof HTMLElement) button.click();
  });
  if (await page.$('.canvas-media-foot-pop')) {
    await capture('卡片更多菜单', '.canvas-media-foot-pop');
    await page.keyboard.press('Escape');
  }

  await page.evaluate(() => {
    const toggle = document.querySelector('.canvas-legend-toggle');
    if (toggle instanceof HTMLElement) toggle.click();
  });
  await capture('图例', '.canvas-legend');

  const card = await page.$('.canvas-media-card');
  if (card) {
    await page.evaluate(() => {
      const firstCard = document.querySelector('.canvas-media-card');
      if (firstCard instanceof HTMLElement) firstCard.click();
    });
    try {
      await capture('提示词弹窗', '.jim-bar');
      await page.evaluate(() => {
        const button = [...document.querySelectorAll('.jim-bar button')]
          .find((item) => (item.textContent || '').includes('从资产库添加'));
        if (button instanceof HTMLElement) button.click();
      });
      if (await page.$('.canvas-picker')) {
        await capture('资产选择器', '.canvas-picker');
      }
    } catch {
      // Older editor overlay may use a different shell.
      await capture('提示词弹窗', '.canvas-prompt-modal');
    }
  }

  const mismatchedPanels = panels.filter((panel) => !panel.matchesSurface);
  const darkLeaks = theme === 'light'
    ? panels.filter((panel) => panel.darkChildren.length > 0)
    : [];
  if (mismatchedPanels.length || darkLeaks.length) {
    throw new Error(`theme mismatch: ${JSON.stringify({
      mismatchedPanels: mismatchedPanels.map((panel) => ({
        name: panel.name,
        background: panel.background,
        expectedSurface: panel.expectedSurface,
      })),
      darkLeaks: darkLeaks.map((panel) => ({
        name: panel.name,
        children: panel.darkChildren,
      })),
    })}`);
  }

  console.log(JSON.stringify({
    ok: true,
    theme,
    checkedPanels: panels.map((panel) => panel.name),
    globalDarkBackgrounds,
  }, null, 2));
} finally {
  await browser.close();
}
