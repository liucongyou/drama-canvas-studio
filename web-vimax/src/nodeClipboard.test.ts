import {afterEach, describe, expect, it, vi} from 'vitest';
import {
  NODE_CLIPBOARD_STORAGE_KEY,
  readNodeClipboard,
  saveNodeClipboard,
} from './nodeClipboard';


afterEach(() => {
  vi.unstubAllGlobals();
});

describe('node clipboard persistence', () => {
  it('keeps copied nodes available after a canvas component remount', () => {
    const values = new Map<string, string>();
    vi.stubGlobal('window', {
      sessionStorage: {
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, value),
      },
    });
    const snapshot = {
      media: [{
        id: 'node-1',
        kind: 'image' as const,
        title: '角色',
        prompt: '角色设定图',
        status: 'done' as const,
        x: 40,
        y: 80,
        updatedAt: '2026-11-01T00:00:00Z',
      }],
      notes: [],
      edges: [],
    };

    saveNodeClipboard(snapshot);

    expect(values.has(NODE_CLIPBOARD_STORAGE_KEY)).toBe(true);
    expect(readNodeClipboard()).toEqual(snapshot);
  });

  it('ignores malformed clipboard data', () => {
    vi.stubGlobal('window', {
      sessionStorage: {
        getItem: () => '{"media":',
        setItem: () => undefined,
      },
    });
    expect(readNodeClipboard()).toBeNull();
  });
});
