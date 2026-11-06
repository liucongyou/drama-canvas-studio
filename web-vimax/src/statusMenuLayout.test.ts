import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';

describe('status menu layout', () => {
  const css = readFileSync(resolve(__dirname, 'ui-refinements.css'), 'utf8');

  it('allows long failure messages and controls to grow naturally', () => {
    const menu = css.slice(
      css.indexOf('.canvas-status-menu{'),
      css.indexOf('.canvas-status-menu-head{'),
    );
    const check = css.slice(
      css.indexOf('.canvas-header-menu .canvas-status-check{'),
      css.indexOf('.canvas-status-failures{'),
    );
    const failure = css.slice(
      css.indexOf('.canvas-header-menu .canvas-status-failure{'),
      css.indexOf('.canvas-header-menu .canvas-status-failure:hover{'),
    );
    expect(menu).toContain('max-height:min(70vh, 620px)');
    expect(menu).toContain('overflow-y:auto');
    expect(check).toContain('height:auto');
    expect(check).toContain('min-height:32px');
    expect(check).toContain('line-height:1.35');
    expect(failure).toContain('height:auto');
    expect(failure).toContain('white-space:normal');
    expect(failure).toContain('overflow-wrap:anywhere');
    expect(css).not.toContain('\n.canvas-status-failure{');
  });
});
