import {describe, expect, it} from 'vitest';
import {
  SETTINGS_CATEGORIES,
  firstAvailableSettingsCategory,
} from './settingsMenu';

describe('settings navigation', () => {
  it('opens the stable default category without scanning a long menu', () => {
    expect(firstAvailableSettingsCategory()).toBe('appearance');
  });

  it('keeps the high-frequency settings in separate categories', () => {
    expect(SETTINGS_CATEGORIES.map((item) => item.key)).toEqual([
      'appearance',
      'integrations',
      'account',
      'project',
    ]);
  });

});
