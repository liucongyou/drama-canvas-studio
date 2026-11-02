import {describe, expect, it} from 'vitest';
import {APP_VERSION} from './changelog';
import {CHANGELOG} from './changelogEntries';

describe('frontend changelog', () => {
  it('keeps the current version as the first release note', () => {
    expect(CHANGELOG[0]?.version).toBe(APP_VERSION);
  });

  it('keeps versions unique and release notes non-empty', () => {
    const versions = CHANGELOG.map((entry) => entry.version);
    expect(new Set(versions).size).toBe(versions.length);
    for (const entry of CHANGELOG) {
      expect(entry.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(entry.releasedAt).toMatch(
        /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \+08:00$/,
      );
      expect(entry.title.trim()).not.toBe('');
      expect(entry.changes.length).toBeGreaterThan(0);
    }
  });

  it('requires the currently deployed changelog entries to be present', () => {
    const versions = new Set(CHANGELOG.map((entry) => entry.version));
    for (const version of [
      '2026.10.31.12',
      '2026.10.31.13',
      '2026.10.31.14',
      '2026.10.31.15',
    ]) {
      expect(versions.has(version)).toBe(true);
    }
  });

  it('keeps the 2026.10.29.21 account presence work out of the visible release notes', () => {
    expect(CHANGELOG.some((entry) => entry.version === '2026.10.29.21')).toBe(false);
    expect(CHANGELOG.some((entry) => entry.version === '2026.10.29.22')).toBe(false);
    const visibleText = CHANGELOG
      .flatMap((entry) => [entry.title, ...entry.changes])
      .join(' ');
    expect(visibleText).not.toMatch(/在线状态|最后活动|账号状态|会话心跳/);
  });
});
