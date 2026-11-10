import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';

describe('media version controls', () => {
  const source = readFileSync(
    resolve(__dirname, 'CanvasView.tsx'),
    'utf8',
  );
  const css = readFileSync(
    resolve(__dirname, 'ui-refinements.css'),
    'utf8',
  );

  it('prevents card drag and card click from swallowing version changes', () => {
    expect(source).toMatch(
      /className="nodrag"\s+aria-label="上一个生成版本"/s,
    );
    expect(source).toMatch(
      /className="nodrag"\s+aria-label="下一个生成版本"/s,
    );
    expect(source).toContain('.canvas-media-version-nav button[aria-label="上一个生成版本"]');
    expect(source).toContain('.canvas-media-version-nav button[aria-label="下一个生成版本"]');
    expect(source).toMatch(/const onCanvasPointerDownCapture = useCallback/);
    expect(source).toMatch(/pendingMediaVersionPointerRef\.current = \{/);
    expect(source).toMatch(/selectMediaVersionFromCardRef\.current\(\s*node,/s);
    expect(source).toMatch(
      /target\?\.closest\('\.canvas-media-version-nav'\)\)\s*\{[\s\S]{0,350}event\.stopPropagation\(\);/,
    );
  });

  it('keeps the version control above the card surface', () => {
    const block = css.slice(
      css.indexOf('.canvas-media-version-nav{'),
      css.indexOf('.canvas-media-version-nav button{'),
    );
    expect(block).toContain('z-index:6');
    expect(block).toContain('pointer-events:auto');
  });

  it('opens a readable history list instead of exposing only blind arrows', () => {
    expect(source).toContain('aria-label="打开历史版本列表"');
    expect(source).toContain('className="canvas-media-version-pop"');
    expect(source).toContain('createPortal(');
    expect(source).toContain('position:fixed');
    expect(source).toContain('className="canvas-media-version-thumb"');
    expect(source).toContain('role="menuitemradio"');
    expect(source).toMatch(/versionIntentRef\.current = \{nodeId: node\.id, index\}/);
    expect(source).toMatch(/setVersionMenuOpen\(false\);\s+versionIntentRef\.current/);
    expect(source).toMatch(
      /aria-label="打开历史版本列表"[\s\S]{0,600}event\.preventDefault\(\);/,
    );
  });

  it('reserves a non-overlapping footer row for status and history controls', () => {
    const foot = css.slice(
      css.indexOf('.canvas-media-foot{'),
      css.indexOf('.canvas-media-foot-ico{'),
    );
    expect(foot).toContain('flex-wrap:wrap');
    const text = css.slice(
      css.indexOf('.canvas-media-foot-text{'),
      css.indexOf('.canvas-media-foot-main{'),
    );
    expect(text).toContain('flex:1 1 112px');
    expect(text).toContain('max-width:100%');
    const nav = css.slice(
      css.indexOf('.canvas-media-version-nav{'),
      css.indexOf('.canvas-media-version-nav button{'),
    );
    expect(nav).toContain('min-width:98px');
    expect(nav).toContain('flex-basis:98px');
  });

  it('closes history on blank space, Escape, wheel, and keeps it exclusive with More', () => {
    expect(source).toContain("document.addEventListener('pointerdown', closeOutside, true)");
    expect(source).toContain("document.addEventListener('keydown', closeEscape, true)");
    expect(source).toContain("window.addEventListener('wheel', closeForViewportChange");
    expect(source).toMatch(/setVersionMenuOpen\(false\);\s+setMenu\(\(v\) => !v\)/);
  });

  it('uses a touch-sized arrow target while keeping the footer compact', () => {
    const buttonBlock = css.slice(
      css.indexOf('.canvas-media-version-nav button{'),
      css.indexOf('.canvas-media-version-nav button:hover{'),
    );
    expect(buttonBlock).toMatch(/width:30px/);
    expect(buttonBlock).toMatch(/height:28px/);
  });

  it('advances from the last click intent before React commits a rerender', () => {
    expect(source).toMatch(/versionIntentRef/);
    expect(source).toMatch(/versionIntentRef\.current\.index - 1/);
    expect(source).toMatch(/versionIntentRef\.current\.index \+ 1/);
    expect(source).toMatch(/MediaVersionSelectionCoordinator/);
  });
});
