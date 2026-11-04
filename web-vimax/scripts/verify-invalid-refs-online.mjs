import puppeteer from 'puppeteer-core';

const token = process.env.CANVAS_TOKEN;
if (!token) throw new Error('CANVAS_TOKEN required');

const cases = [
  {
    projectId: 'sj80hnySZfp26Jh1mY2wvC',
    episodeId: 'EP002',
  },
  {
    projectId: 'RyFNSHbwwgMX3ZtvwTxx0q',
    episodeId: 'EP001',
  },
  {
    projectId: 'JhKkYbCMnmTYPmZAGu2TEk',
    episodeId: 'EP002',
  },
];

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--window-size=1600,1000'],
});
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1600, height: 1000});
  await page.setCookie({
    name: 'canvas_session',
    value: token,
    domain: 'yj.pduoduo.vip',
    path: '/',
    httpOnly: true,
    secure: true,
    sameSite: 'Lax',
  });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const results = [];
  for (const testCase of cases) {
    await page.goto(
      `https://yj.pduoduo.vip/?ep=${encodeURIComponent(testCase.episodeId)}#${encodeURIComponent(testCase.projectId)}`,
      {waitUntil: 'domcontentloaded', timeout: 60_000},
    );
    await page.waitForSelector('.react-flow__node-media', {timeout: 60_000});
    await sleep(1200);
    const card = await page.$('.react-flow__node-media .canvas-media-card');
    if (!card) throw new Error(`no media card in ${testCase.projectId}`);
    await card.click();
    await page.waitForSelector('.jim-bar:not(.is-closing)', {timeout: 8_000});
    await sleep(800);
    const result = await page.evaluate(() => {
      const body = document.querySelector('.jim-bar')?.innerText || '';
      return {
        invalidRefText: body.includes('还有') && body.includes('引用节点不存在'),
        dependencyBlocked: Boolean(document.querySelector('.jim-bar .is-blocked')),
        refCount: document.querySelectorAll('.jim-bar .jim-ref-thumb').length,
      };
    });
    results.push({...testCase, ...result});
    await page.keyboard.press('Escape');
    await page.waitForFunction(
      () => !document.querySelector('.jim-bar'),
      {timeout: 5_000},
    );
  }
  console.log(JSON.stringify({cases: results, errors}, null, 2));
  if (results.some((item) => item.invalidRefText)) {
    throw new Error('invalid reference warning remains');
  }
  if (errors.length) throw new Error(`browser errors: ${errors.join(' | ')}`);
} finally {
  await browser.close();
}
