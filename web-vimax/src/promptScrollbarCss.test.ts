import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';

const ui = readFileSync(
  resolve(__dirname, 'ui-refinements.css'),
  'utf8',
);
const tokens = readFileSync(
  resolve(__dirname, 'theme-tokens.css'),
  'utf8',
);
const source = readFileSync(
  resolve(__dirname, 'CanvasView.tsx'),
  'utf8',
);

describe('prompt editor scrollbar', () => {
  it('hides platform scrollbars so no native arrows can render', () => {
    expect(ui).toMatch(
      /\.jim-prompt\{[^}]*scrollbar-width:none/s,
    );
    expect(tokens).toMatch(
      /overflow-y:\s*auto;\s*padding-right:\s*0 !important;\s*scrollbar-width:\s*none !important;/s,
    );
    expect(tokens).toMatch(
      /\.jim-prompt::-webkit-scrollbar\s*\{[^}]*display\s*:\s*none\s*!important/s,
    );
  });

  it('renders a 2px overlay indicator outside the text flow', () => {
    expect(source).toMatch(/className="jim-prompt-scrollbar"/);
    expect(ui).toMatch(
      /\.jim-prompt-scrollbar\{[^}]*position:absolute;[^}]*width:4px/s,
    );
    expect(ui).toMatch(
      /\.jim-prompt-scrollbar>i\{[^}]*width:2px;[^}]*border-radius:99px/s,
    );
  });

  it('keeps the indicator as an assistant to scrolling, not another scroller', () => {
    expect(ui).toMatch(
      /\.jim-prompt-scrollbar\{[^}]*pointer-events:none/s,
    );
    expect(source).toMatch(/onScrollStateChange=\{setPromptScrollbar\}/);
  });

  it('reserves the narrow rail so it cannot cover prompt text', () => {
    expect(ui).toMatch(
      /\.jim-prompt\{[^}]*padding:2px 4px 2px 0;[^}]*overflow-y:auto/s,
    );
  });

  it('remeasures the rail when the browser or editor is resized', () => {
    expect(source).toContain('new ResizeObserver(scheduleSync)');
    expect(source).toContain("window.addEventListener('resize', scheduleSync)");
    expect(source).toContain('editor.scrollTop = 0');
  });

  it('registers resize hooks before the modal early return', () => {
    const earlyReturn = source.indexOf('if (!node) return null;');
    const effect = source.indexOf('new ResizeObserver(scheduleSync)');
    expect(earlyReturn).toBeGreaterThan(0);
    expect(effect).toBeGreaterThan(0);
    expect(effect).toBeLessThan(earlyReturn);
  });
});
