import path from 'node:path';
import puppeteer from 'puppeteer-core';

const base = 'http://127.0.0.1:8890';
const projectId = process.env.ANIMATION_FIXTURE_PROJECT || '';
if (!projectId) throw new Error('ANIMATION_FIXTURE_PROJECT is required');
const projectTitle = '连线动画临时验证';
const screenshot = path.resolve('../data/edge-generation-animation-20261026.png');

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

try {
  const page = await browser.newPage();
  const errors = [];
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  page.on('pageerror', (error) => errors.push(error.message));
  await page.setViewport({width: 1440, height: 900, deviceScaleFactor: 1});
  await page.goto(base, {waitUntil: 'domcontentloaded', timeout: 60000});
  await page.waitForSelector('.canvas-project-btn', {timeout: 30000});
  await page.click('.canvas-project-btn');
  await page.waitForSelector('.canvas-project-menu', {timeout: 10000});
  const selected = await page.evaluate((title) => {
    const button = [...document.querySelectorAll('.canvas-project-menu-list button')]
      .find((item) => (item.textContent || '').includes(title));
    if (!(button instanceof HTMLElement)) return false;
    button.click();
    return true;
  }, projectTitle);
  if (!selected) throw new Error('animation fixture project not found');

  await page.waitForSelector('[data-id="media-anim-idle-src"]', {timeout: 60000});
  await page.waitForSelector('[data-id="media-anim-busy-target"]', {timeout: 60000});
  await new Promise((resolve) => setTimeout(resolve, 800));

  const edgeSelectors = await page.evaluate(() => [...document.querySelectorAll('.react-flow__edge')]
    .map((node) => ({
      id: node.getAttribute('data-id'),
      testid: node.getAttribute('data-testid'),
    })));
  const edgeSelector = (sourceNodeId, targetNodeId) => {
    const found = edgeSelectors.find((edge) => {
      const value = `${edge.id || ''} ${edge.testid || ''}`;
      return value.includes(sourceNodeId) && value.includes(targetNodeId);
    });
    if (!found) {
      throw new Error(
        `edge not found in DOM: ${sourceNodeId} -> ${targetNodeId} ${JSON.stringify(edgeSelectors)}`,
      );
    }
    return found.id ? `.react-flow__edge[data-id="${found.id}"]` : `.react-flow__edge[data-testid="${found.testid}"]`;
  };

  const styleOf = async (selector) => page.$eval(selector, (node) => {
    const style = getComputedStyle(node);
    return {
      animationName: style.animationName,
      animationDuration: style.animationDuration,
      opacity: style.opacity,
      stroke: style.stroke,
      strokeWidth: style.strokeWidth,
    };
  });

  const idleEdge = edgeSelector('anim-idle-src', 'anim-idle-target');
  const busyEdge = edgeSelector('anim-busy-src', 'anim-busy-target');
  const debugState = await page.evaluate((busySelector) => ({
    busyNodeClass: document.querySelector('[data-id="media-anim-busy-target"]')?.className || '',
    busyInnerClass: document.querySelector('[data-id="media-anim-busy-target"] .canvas-media-node')?.className || '',
    busyText: document.querySelector('[data-id="media-anim-busy-target"]')?.textContent || '',
    busyFlowClasses: [...document.querySelectorAll(`${busySelector} .canvas-edge-flow`)]
      .map((node) => node.getAttribute('class') || ''),
  }), busyEdge);
  const idleStyle = await styleOf(`${idleEdge} .canvas-edge-flow`);
  const busyStyle = await styleOf(`${busyEdge} .canvas-edge-flow`);
  const busyTailStyle = await styleOf(`${busyEdge} .canvas-edge-flow-tail`);
  const busyHeadStyle = await styleOf(`${busyEdge} .canvas-edge-flow-head`);
  const busyBaseStyle = await styleOf(`${busyEdge} .react-flow__edge-path`);
  if (idleStyle.animationName !== 'none' || idleStyle.opacity !== '0') {
    throw new Error(`idle edge should be static: ${JSON.stringify(idleStyle)}`);
  }
  if (
    busyStyle.animationName !== 'canvas-edge-flow-run-body'
    || busyTailStyle.animationName !== 'canvas-edge-flow-run'
    || busyHeadStyle.animationName !== 'canvas-edge-flow-run-head'
    || busyStyle.animationDuration !== '1.2s'
    || busyStyle.strokeWidth !== '2px'
    || busyBaseStyle.stroke !== 'rgb(77, 159, 255)'
    || busyStyle.opacity === '0'
  ) {
    throw new Error(
      `busy edge should use the default blue flow: ${JSON.stringify({busyStyle, busyTailStyle, busyHeadStyle, debugState})}`,
    );
  }

  await page.click('[data-id="media-anim-idle-src"] .canvas-media-card');
  await page.waitForFunction(
    (selector) => !!document.querySelector(`${selector} .canvas-edge-flow.is-linked`),
    {timeout: 10000},
    idleEdge,
  );
  await new Promise((resolve) => setTimeout(resolve, 220));
  const linkedStyle = await styleOf(`${idleEdge} .canvas-edge-flow.is-linked`);
  const linkedBaseStyle = await styleOf(`${idleEdge} .react-flow__edge-path`);
  if (linkedStyle.animationName === 'none' || linkedStyle.opacity === '0') {
    throw new Error(`selected-card linked animation regressed: ${JSON.stringify(linkedStyle)}`);
  }
  if (linkedBaseStyle.stroke !== 'rgb(77, 159, 255)') {
    throw new Error(`selected-card baseline is not blue: ${JSON.stringify(linkedBaseStyle)}`);
  }
  await page.evaluate(() => document.querySelector('.canvas-flow')?.classList.add('has-multi-selection'));
  await new Promise((resolve) => setTimeout(resolve, 80));
  const multiLinkedBaseStyle = await styleOf(`${idleEdge} .react-flow__edge-path`);
  if (multiLinkedBaseStyle.stroke !== 'rgb(77, 159, 255)') {
    throw new Error(`multi-selection baseline is not blue: ${JSON.stringify(multiLinkedBaseStyle)}`);
  }
  await page.screenshot({path: screenshot, fullPage: false});
  if (errors.length) throw new Error(errors.join('\n'));
  console.log(JSON.stringify({
    ok: true,
    projectId,
    idleStyle,
    busyStyle,
    busyTailStyle,
    busyHeadStyle,
    busyBaseStyle,
    linkedStyle,
    linkedBaseStyle,
    multiLinkedBaseStyle,
    screenshot,
  }, null, 2));
} finally {
  await browser.close();
}
