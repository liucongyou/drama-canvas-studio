import {describe, expect, it} from 'vitest';
import {resolveTheme, THEMES} from './theme';

describe('theme selection', () => {
  it('uses an explicit saved theme', () => {
    expect(resolveTheme('dark', false)).toBe('dark');
    expect(resolveTheme('immersive', false)).toBe('immersive');
    expect(resolveTheme('forest', false)).toBe('forest');
    expect(resolveTheme('copper', true)).toBe('copper');
    expect(resolveTheme('light', true)).toBe('light');
  });

  it('always falls back to the graphite default', () => {
    expect(resolveTheme(null, true)).toBe('immersive');
    expect(resolveTheme(undefined, false)).toBe('immersive');
    expect(resolveTheme('light', false)).toBe('light');
  });

  it('exposes exactly the five product themes', () => {
    expect(THEMES.map((item) => item.key)).toEqual(['immersive', 'forest', 'copper', 'dark', 'light']);
  });

  it('maps the retired tech theme to the graph-blue default', () => {
    expect(resolveTheme('tech', false)).toBe('immersive');
    expect(resolveTheme('gold', false)).toBe('immersive');
  });
});
