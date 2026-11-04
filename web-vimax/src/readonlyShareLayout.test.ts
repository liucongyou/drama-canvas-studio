import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';

describe('read-only share layout', () => {
  it('gives the share-mounted canvas an explicit viewport block size', () => {
    const css = readFileSync(
      resolve(__dirname, 'ui-refinements.css'),
      'utf8',
    );
    const block = css.slice(
      css.indexOf('.share-canvas-host{'),
      css.indexOf('.canvas-share-banner{'),
    );
    expect(block).toContain('width:100%');
    expect(block).toContain('height:100vh');
    expect(block).toContain('height:100dvh');
    expect(block).toContain('min-height:100vh');
    expect(block).toContain('min-height:100dvh');
    expect(block).toContain('display:flex');
    expect(block).toContain(
      '.share-canvas-host > .canvas-view.is-readonly-share',
    );
  });

  it('keeps the critical height contract in the document shell', () => {
    const html = readFileSync(resolve(__dirname, '../index.html'), 'utf8');
    const block = html.slice(
      html.indexOf('.share-canvas-host {'),
      html.indexOf('</style>'),
    );
    expect(block).toContain('height: 100vh');
    expect(block).toContain('height: 100dvh');
    expect(block).toContain('display: flex');
    expect(block).toContain('.share-canvas-host > .canvas-view.is-readonly-share');
  });

  it('mounts the shared canvas inside a full-viewport host', () => {
    const source = readFileSync(resolve(__dirname, 'main.tsx'), 'utf8');
    expect(source).toContain('<div className="share-canvas-host">');
    expect(source).toContain('<CanvasView');
  });
});
