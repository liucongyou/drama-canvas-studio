import puppeteer from 'puppeteer-core';

/**
 * Read-only visual contract for the media modal animation.
 *
 * A below-card panel must grow upward from the card edge (transform-origin at
 * its own bottom center). A flipped panel must grow downward instead. The test
 * also captures the open frame and a settled frame for visual inspection.
 */
const base = (process.env.CANVAS_BASE || 'http://127.0.0.1:8890').replace(/\/$/, '');
const token = process.env.CANVAS_TOKEN || '';
const projectId = process.env.PROJECT_ID || '';
const episodeId = process.env.EPISODE_ID || 'EP003';

if (!token || !projectId) {
  throw new Error('CANVAS_TOKEN and PROJECT_ID are required');
}

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

try {
  const page = await browser.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await page.setViewport({width: 1920, height: 1080});
  await page.setCookie({
    name: process.env.CANVAS_COOKIE || 'canvas_session',
    value: token,
    domain: new URL(base).hostname,
    path: '/',
    httpOnly: true,
    sameSite: 'Lax',
  });
  await page.goto(
    `${base}/?ep=${encodeURIComponent(episodeId)}#${encodeURIComponent(projectId)}`,
    {waitUntil: 'networkidle2', timeout: 60_000},
  );
  await page.waitForSelector('.react-flow__node-media .canvas-media-card', {
    timeout: 60_000,
  });
  await new Promise((resolve) => setTimeout(resolve, 400));

  const card = await page.$('.react-flow__node-media .canvas-media-card');
  const box = await card?.boundingBox();
  if (!box) throw new Error('media card has no bounding box');
  await page.mouse.click(
    box.x + Math.min(24, box.width / 3),
    box.y + Math.min(24, box.height / 3),
  );
  await page.waitForSelector('.jim-bar', {timeout: 5_000});

  const opening = await page.$eval('.jim-bar', (element) => {
    const style = getComputedStyle(element);
    const animation = element.getAnimations().find((item) => (
      item.animationName === 'jim-pop-anchored'
      || item.animationName === 'jim-pop-anchored-up'
      || item.animationName === 'jim-pop-center'
    ));
    return {
      animationName: animation?.animationName || '',
      duration: animation?.effect?.getTiming().duration ?? null,
      flip: element.getAttribute('data-flip'),
      center: element.getAttribute('data-center'),
      transformOrigin: style.transformOrigin,
      card: element.getBoundingClientRect().toJSON(),
    };
  });
  await page.screenshot({path: 'scripts/_modal-animation-opening.png'});
  await new Promise((resolve) => setTimeout(resolve, 260));
  const settled = await page.$eval('.jim-bar', (element) => {
    const style = getComputedStyle(element);
    return {
      opacity: style.opacity,
      animationCount: element.getAnimations().filter((item) => (
        item.animationName.startsWith('jim-pop')
      )).length,
      card: element.getBoundingClientRect().toJSON(),
    };
  });
  await page.screenshot({path: 'scripts/_modal-animation-settled.png'});
  console.log(JSON.stringify({opening, settled, pageErrors}, null, 2));

  if (opening.center === '1') {
    if (!opening.animationName || opening.duration !== 180) {
      throw new Error('centered modal is missing its 180ms anchor animation');
    }
  } else if (opening.flip === '0') {
    if (opening.animationName !== 'jim-pop-anchored') {
      throw new Error(`below-card modal used ${opening.animationName || 'no animation'}`);
    }
    // Dimensions vary by viewport; the invariant is that the Y origin is the
    // panel's lower edge, not its upper edge.
    const originY = Number.parseFloat(opening.transformOrigin.split(' ')[1] || '');
    if (!Number.isFinite(originY) || originY < opening.card.height * 0.75) {
      throw new Error(`below-card modal did not grow from its lower edge: ${opening.transformOrigin}`);
    }
  } else if (opening.animationName !== 'jim-pop-anchored-up') {
    throw new Error(`above-card modal used ${opening.animationName || 'no animation'}`);
  }
  if (settled.opacity !== '1' || settled.animationCount !== 0) {
    throw new Error('modal did not settle after its opening animation');
  }
  if (pageErrors.length) {
    throw new Error(`browser errors: ${pageErrors.join(' | ')}`);
  }
} finally {
  await browser.close();
}
