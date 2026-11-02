import {describe, expect, it} from 'vitest';
import {readFileSync} from 'node:fs';

const source = readFileSync(
  new URL('./CanvasView.tsx', import.meta.url),
  'utf8',
);

describe('CanvasView hook order', () => {
  it('keeps all media modal hooks before its nullable early return', () => {
    const modalStart = source.indexOf('function MediaNodeModal');
    const modalEnd = source.indexOf('\nfunction ', modalStart + 1);
    const modal = source.slice(
      modalStart,
      modalEnd === -1 ? undefined : modalEnd,
    );
    const earlyReturn = modal.indexOf('if (!node) return null;');

    expect(modalStart).toBeGreaterThanOrEqual(0);
    expect(earlyReturn).toBeGreaterThanOrEqual(0);

    const afterEarlyReturn = modal.slice(earlyReturn);
    expect(afterEarlyReturn).not.toMatch(
      /\buse(?:State|Ref|Effect|Memo|Callback|LayoutEffect)\s*\(/,
    );
  });

  it('clamps the duration control from its live input value on blur', () => {
    expect(source).toMatch(
      /onBlur=\{\(event\) => \{\s*commitDuration\(event\.currentTarget\.value\);/,
    );
    expect(source).toMatch(
      /clampDurationInput\(/,
    );
  });

  it('commits a pending duration before closing the popover from outside', () => {
    expect(source).toMatch(
      /commitDurationRef\.current\(durationInput\.value\)/,
    );
  });
});
