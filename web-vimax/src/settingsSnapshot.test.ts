import {describe, expect, it} from 'vitest';

import {
  denormalizedPreferences,
  mergeSavedPreferences,
  settingsSnapshot,
} from './settingsSnapshot';

describe('settings snapshot', () => {
  it('defaults missing or legacy theme values to graphite', () => {
    expect(denormalizedPreferences({}).theme).toBe('immersive');
    expect(denormalizedPreferences({theme: 'gold'}).theme).toBe('immersive');
    expect(denormalizedPreferences({theme: 'tech'}).theme).toBe('immersive');
  });

  it('keeps saved preferences that the server did not return', () => {
    expect(mergeSavedPreferences(
      {canvasBackgroundMode: 'dots', theme: 'forest', unrelated: 1},
      {theme: 'light'},
    )).toEqual({
      canvasBackgroundMode: 'dots',
      theme: 'light',
      unrelated: 1,
    });
  });

  it('changes one preference without invalidating the other', () => {
    const base = {canvasBackgroundMode: 'lines' as const, theme: 'copper' as const};
    expect(settingsSnapshot(base)).toBe('lines|copper');
    expect(settingsSnapshot({...base, theme: 'forest'})).toBe('lines|forest');
    expect(settingsSnapshot({...base, canvasBackgroundMode: 'dots'}))
      .toBe('dots|copper');
  });
});
