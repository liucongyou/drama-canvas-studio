import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';

const css = readFileSync(
  resolve(__dirname, 'ui-refinements.css'),
  'utf8',
);

describe('prompt editor scrollbar', () => {
  it('uses a 3px overlay scrollbar without a reserved gutter', () => {
    expect(css).toMatch(
      /\.jim-prompt::-webkit-scrollbar\{[^}]*width:3px;[^}]*background:transparent/s,
    );
  });

  it('keeps the scrollbar thumb visually 2px wide without inset borders', () => {
    expect(css).toMatch(
      /\.jim-prompt::-webkit-scrollbar-thumb\{[^}]*border:0;[^}]*border-radius:99px/s,
    );
  });

  it('does not retain the old 8px gutter rule', () => {
    expect(css).not.toMatch(
      /\.jim-prompt::-webkit-scrollbar\{[^}]*width:8px/s,
    );
  });
});
