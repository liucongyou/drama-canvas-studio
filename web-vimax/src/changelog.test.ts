import {describe, expect, it} from 'vitest';
import {APP_VERSION, CHANGELOG} from './changelog';

describe('frontend changelog', () => {
  it('keeps the current version as the first release note', () => {
    expect(CHANGELOG[0]?.version).toBe(APP_VERSION);
  });

  it('keeps versions unique and release notes non-empty', () => {
    const versions = CHANGELOG.map((entry) => entry.version);
    expect(new Set(versions).size).toBe(versions.length);
    for (const entry of CHANGELOG) {
      expect(entry.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
      expect(entry.title.trim()).not.toBe('');
      expect(entry.changes.length).toBeGreaterThan(0);
    }
  });
});
