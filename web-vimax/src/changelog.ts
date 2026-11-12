export type ChangelogEntry = {
  version: string;
  date: string;
  releasedAt: string;
  title: string;
  changes: string[];
};

// 每次发布前端改动时：先升级 APP_VERSION，再把同版本记录放到数组第一项。
// changelog.test.ts 会阻止版本升级但漏写更新记录。
export const APP_VERSION = '2026.11.12.05';
export const RELEASE_SEEN_KEY = 'yingjie-release-seen';
