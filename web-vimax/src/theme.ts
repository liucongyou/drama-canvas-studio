export type Theme = 'light' | 'dark' | 'tech' | 'immersive' | 'forest' | 'copper';

export const THEME_STORAGE_KEY = 'vimax-web-theme';
export const THEME_COOKIE_KEY = 'vimax-web-theme';

export const THEMES: Array<{key: Theme; label: string; desc: string}> = [
  {key: 'immersive', label: '石墨蓝', desc: '当前默认，深灰底与沉稳蓝灰'},
  {key: 'forest', label: '松石绿', desc: '黑绿灰底与柔和青绿强调'},
  {key: 'copper', label: '暖铜灰', desc: '深暖灰底与低饱和铜色'},
  {key: 'dark', label: 'AIGC 暗色', desc: '靛灰科技感与克制冷色强调'},
  {key: 'light', label: '白色主题', desc: '高对比浅色界面，适合明亮环境'},
];

export function isDarkTheme(theme: Theme): boolean {
  return theme !== 'light';
}

export function resolveTheme(stored: string | null | undefined, prefersDark: boolean): Theme {
  if (stored === 'gold' || stored === 'tech') return 'immersive';
  if (stored === 'light' || stored === 'dark' || stored === 'immersive' || stored === 'forest' || stored === 'copper') {
    return stored;
  }
  return prefersDark ? 'immersive' : 'light';
}

export function readStoredTheme(): string | null {
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (value) return value;
  } catch {
    // Fall through to the cookie fallback.
  }
  try {
    const prefix = `${THEME_COOKIE_KEY}=`;
    const match = document.cookie
      .split(';')
      .map((item) => item.trim())
      .find((item) => item.startsWith(prefix));
    return match ? decodeURIComponent(match.slice(prefix.length)) : null;
  } catch {
    return null;
  }
}

export function storeTheme(theme: Theme) {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Cookie persistence below still keeps the setting across reloads.
  }
  try {
    const secure = window.location.protocol === 'https:' ? '; Secure' : '';
    document.cookie = `${THEME_COOKIE_KEY}=${encodeURIComponent(theme)}; Path=/; Max-Age=31536000; SameSite=Lax${secure}`;
  } catch {
    // Storage is optional in hardened browser contexts.
  }
}

export function applyTheme(theme: Theme) {
  const normalized = resolveTheme(theme, true);
  document.documentElement.dataset.theme = normalized === 'light' ? 'light' : 'dark';
  document.documentElement.dataset.variant = normalized === 'dark' ? 'aigc' : normalized === 'tech' ? 'immersive' : normalized;
  const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (meta) {
    meta.content = normalized === 'light'
      ? '#eef1f5'
      : normalized === 'forest'
        ? '#111714'
        : normalized === 'copper'
          ? '#171311'
          : normalized === 'dark'
            ? '#0f1219'
            : '#121212';
  }
}
