import type {StoredThemePreference} from './theme';

export type ShellPreferences = {
  canvasBackgroundMode?: 'dots' | 'lines' | 'blank';
  theme?: StoredThemePreference | string;
  [key: string]: unknown;
};

export type DenormalizedPreferences = {
  background: 'dots' | 'lines' | 'blank';
  theme: StoredThemePreference;
};

export function settingsSnapshot(preferences: ShellPreferences): string {
  const background = preferences.canvasBackgroundMode === 'dots'
    || preferences.canvasBackgroundMode === 'lines'
    || preferences.canvasBackgroundMode === 'blank'
    ? preferences.canvasBackgroundMode
    : 'blank';
  const theme = isThemePreference(preferences.theme) ? preferences.theme : '';
  return `${background}|${theme}`;
}

export function denormalizedPreferences(preferences: ShellPreferences): DenormalizedPreferences {
  const background = preferences.canvasBackgroundMode === 'dots'
    || preferences.canvasBackgroundMode === 'lines'
    || preferences.canvasBackgroundMode === 'blank'
    ? preferences.canvasBackgroundMode
    : 'blank';
  return {
    background,
    theme: isThemePreference(preferences.theme) ? preferences.theme : 'immersive',
  };
}

export function isThemePreference(value: unknown): value is StoredThemePreference {
  return value === 'light'
    || value === 'dark'
    || value === 'immersive'
    || value === 'forest'
    || value === 'copper';
}

export function mergeSavedPreferences(
  current: ShellPreferences,
  saved: ShellPreferences,
): ShellPreferences {
  return {...current, ...saved};
}
