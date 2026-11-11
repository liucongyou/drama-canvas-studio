import {readFileSync} from 'node:fs';
import puppeteer from 'puppeteer-core';

const css = readFileSync(new URL('../src/agent-dock.css', import.meta.url), 'utf8');
const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

try {
  const page = await browser.newPage();
  await page.setViewport({width: 1200, height: 800, deviceScaleFactor: 1});
  await page.setContent(`<!doctype html>
    <html><head><style>
      *{box-sizing:border-box}body{margin:0;font-family:"Microsoft YaHei",sans-serif}
      ${css}
    </style></head><body>
      <main class="agent-chat" id="chat"><div class="agent-run">
        <div class="agent-msg is-user no-avatar">
          <div class="agent-msg-body">
            <div class="agent-msg-head"><span>你</span><time>14:22</time></div>
            <div class="agent-msg-text" id="short">生成第一集</div>
          </div>
        </div>
        <div class="agent-msg is-user no-avatar">
          <div class="agent-msg-body">
            <div class="agent-msg-head"><span>你</span><time>14:23</time></div>
            <div class="agent-msg-text" id="long">生成第一集：按照项目现有角色资产完成分镜，保持贾富贵与陈大强的身份、服装和空间关系一致，不引用未挂接素材。</div>
          </div>
        </div>
      </div></main>
    </body></html>`);

  for (const width of [480, 320]) {
    await page.evaluate((value) => {
      document.querySelector('#chat').style.width = `${value}px`;
      document.querySelector('#chat').style.height = '600px';
    }, width);
    const result = await page.evaluate(() => {
      const measure = (id) => {
        const text = document.getElementById(id);
        const body = text.closest('.agent-msg-body');
        const message = text.closest('.agent-msg');
        const range = document.createRange();
        range.selectNodeContents(text);
        return {
          lines: range.getClientRects().length,
          textWidth: Math.round(text.getBoundingClientRect().width),
          bodyWidth: Math.round(body.getBoundingClientRect().width),
          messageWidth: Math.round(message.getBoundingClientRect().width),
          overflow: message.scrollWidth > message.clientWidth + 1,
        };
      };
      const time = document.querySelector('.agent-msg time');
      return {short: measure('short'), long: measure('long'), timeLines: time.getClientRects().length};
    });

    if (result.short.lines !== 1 || result.timeLines !== 1) {
      throw new Error(`用户短消息仍被压缩断行 @${width}px: ${JSON.stringify(result)}`);
    }
    if (result.short.bodyWidth < 80 || result.long.lines < 2 || result.long.overflow) {
      throw new Error(`用户消息宽度约束不正确 @${width}px: ${JSON.stringify(result)}`);
    }
    console.log(`${width}px ${JSON.stringify(result)}`);
  }
} finally {
  await browser.close();
}
