import {describe, expect, it, vi} from 'vitest';
import {newCanvasEntityId} from './canvasEntityId';

describe('canvas entity ids', () => {
  it('uses a cryptographically random UUID when available', () => {
    const randomUUID = vi.fn()
      .mockReturnValueOnce('00000000-0000-4000-8000-000000000001')
      .mockReturnValueOnce('00000000-0000-4000-8000-000000000002');

    expect(newCanvasEntityId('n', {randomUUID}))
      .toBe('n-00000000-0000-4000-8000-000000000001');
    expect(newCanvasEntityId('n', {randomUUID}))
      .not.toBe('n-00000000-0000-4000-8000-000000000001');
  });

  it('still returns unique ids when randomUUID is unavailable', () => {
    const ids = new Set(
      Array.from({length: 1000}, () => newCanvasEntityId('m-copy', undefined)),
    );
    expect(ids.size).toBe(1000);
  });
});
