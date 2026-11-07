import puppeteer from 'puppeteer-core';

const base = 'https://yj.pduoduo.vip';
const token = process.env.CANVAS_SESSION_TOKEN || '';
const feedPath = process.env.FEED_PATH || '';
const projectId = 'sj80hnySZfp26Jh1mY2wvC';
const episodeId = 'EP006';

if (!feedPath) throw new Error('FEED_PATH is required');

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--window-size=1680,1050'],
});

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1680, height: 1050});
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  await page.setCookie({
    name: 'canvas_session',
    value: token,
    domain: 'yj.pduoduo.vip',
    path: '/',
    secure: true,
    httpOnly: true,
  });
  await page.goto(
    `${base}/?ep=${episodeId}#${projectId}`,
    {waitUntil: 'domcontentloaded', timeout: 60_000},
  );
  await page.waitForSelector('.canvas-node', {timeout: 60_000});

  const assetCounts = () => page.evaluate(async ({projectId, episodeId}) => {
    const response = await fetch(
      `/api/projects/${projectId}/workspace-nodes?episodeId=${episodeId}`,
      {credentials: 'include'},
    );
    const data = await response.json();
    return {
      status: response.status,
      images: (data.nodes || []).filter((node) => node.type === 'image').length,
      audio: (data.nodes || []).filter((node) => node.type === 'audio').length,
      videos: (data.nodes || []).filter((node) => node.type === 'video').length,
    };
  }, {projectId, episodeId});
  const before = await assetCounts();

  await page.evaluate(() => {
    const trigger = [...document.querySelectorAll('.shell-topbar-btn')]
      .find((button) => button.textContent?.includes('设置'));
    if (!trigger) throw new Error('settings trigger not found');
    trigger.click();
  });
  await page.waitForSelector('.shell-settings-menu', {timeout: 5_000});
  await page.evaluate(() => {
    const category = [...document.querySelectorAll('.shell-settings-nav button')]
      .find((button) => button.textContent?.includes('项目'));
    if (!category) throw new Error('project settings category not found');
    category.click();
  });
  const importInput = await page.$('.shell-settings-menu input[type="file"]');
  if (!importInput) throw new Error('import file input not found');
  await importInput.uploadFile(feedPath);
  await page.waitForSelector('.gdialog-mask .gdialog', {timeout: 20_000});
  const beforeClose = await page.$eval('.gdialog-mask .gdialog-title', (node) => node.textContent);
  await page.mouse.click(20, 20);
  await page.waitForFunction(
    () => !document.querySelector('.gdialog-mask'),
    {timeout: 5_000},
  );

  const after = await assetCounts();
  const result = {beforeClose, before, after, errors};
  console.log(JSON.stringify(result, null, 2));
  if (beforeClose !== '导入预检通过') {
    throw new Error(`unexpected dialog title: ${beforeClose}`);
  }
  if (
    before.status !== 200
    || after.status !== 200
    || before.images !== 31
    || before.audio !== 10
    || before.videos !== 12
    || after.images !== 31
    || after.audio !== 10
    || after.videos !== 12
  ) {
    throw new Error('asset pool changed during preview dialog flow');
  }
  if (errors.some((item) => !item.includes('favicon') && !item.includes('401'))) {
    throw new Error(errors.join(' | '));
  }
} finally {
  await browser.close();
}
