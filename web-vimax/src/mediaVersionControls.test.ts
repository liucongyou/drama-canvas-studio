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
    expect(source.match(/onPointerDown=\{\(event\) => event\.stopPropagation\(\)\}/g)?.length ?? 0)
      .toBeGreaterThanOrEqual(2);
  });

  it('keeps the version control above the card surface', () => {
    const block = css.slice(
      css.indexOf('.canvas-media-version-nav{'),
      css.indexOf('.canvas-media-version-nav button{'),
    );
    expect(block).toContain('z-index:6');
    expect(block).toContain('pointer-events:auto');
  });
});
