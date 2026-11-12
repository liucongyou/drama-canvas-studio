import puppeteer from 'puppeteer-core';

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu', '--no-proxy-server'],
});
const page = await browser.newPage();
await page.setViewport({width: 1680, height: 1050});
const errors = [];
page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
page.on('console', (message) => {
  if (message.type() === 'error') errors.push(`console: ${message.text()}`);
});

try {
  await page.setContent('<!doctype html><textarea class="jim-prompt" rows="1"></textarea>');
  const editor = await page.$('textarea.jim-prompt');
  if (!editor) throw new Error('textarea missing');
  let committed = '';
  await page.exposeFunction('commitPrompt', (value) => { committed = value; });
  await page.evaluate(() => {
    const element = document.querySelector('textarea.jim-prompt');
    if (!element) return;
    let composing = false;
    element.addEventListener('compositionstart', () => { composing = true; });
    element.addEventListener('compositionend', () => {
      composing = false;
      window.setTimeout(() => window.commitPrompt(element.value), 0);
    });
    element.addEventListener('input', () => {
      if (composing) return;
      window.commitPrompt(element.value);
    });
  });
  await editor.click();
  await page.keyboard.down('Control');
  await page.keyboard.press('KeyA');
  await page.keyboard.up('Control');
  await page.keyboard.type('1');
  await page.keyboard.press('Enter');
  await page.keyboard.type('2');
  const afterOneNewline = await editor.evaluate((element) => ({
    value: element.value,
    selectionStart: element.selectionStart,
    selectionEnd: element.selectionEnd,
  }));
  await page.keyboard.press('Enter');
  await page.keyboard.press('Enter');
  await page.keyboard.type('3');
  await editor.evaluate((element) => {
    element.dispatchEvent(new CompositionEvent('compositionstart', {bubbles: true}));
    element.value = '1\n2\n\n3zhong文';
    element.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertCompositionText', data: 'zhong'}));
  });
  const duringComposition = await page.evaluate(() => ({
    value: document.querySelector('textarea.jim-prompt')?.value || '',
  }));
  await editor.evaluate((element) => {
    element.value = '1\n2\n\n3中文';
    element.dispatchEvent(new CompositionEvent('compositionend', {bubbles: true, data: '中文'}));
  });
  await new Promise((resolve) => setTimeout(resolve, 30));
  const afterThreeNewlines = await editor.evaluate((element) => ({
    value: element.value,
    selectionStart: element.selectionStart,
  }));
  console.log(JSON.stringify({
    projectId: 'isolated-dom-test',
    afterOneNewline,
    duringComposition,
    afterThreeNewlines,
    committed,
    errors,
  }, null, 2));
  if (afterOneNewline.value !== '1\n2') throw new Error('Enter inserted more than one newline');
  if (duringComposition.value !== '1\n2\n\n3zhong文') throw new Error('composition DOM changed unexpectedly');
  if (afterThreeNewlines.value !== '1\n2\n\n3中文') throw new Error('composition did not settle to final text');
  if (afterOneNewline.selectionStart !== 3) throw new Error('caret moved after normal typing');
  if (!committed.includes('中文')) throw new Error('final composed text was not committed');
  if (errors.length) throw new Error(errors.join('; '));
} finally {
  await browser.close();
  process.exit(process.exitCode || 0);
}
