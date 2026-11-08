import {describe, expect, it} from 'vitest';
import {resolveMediaVersionIndex} from './mediaVersions';

function version(versionId: string, url: string) {
  return {versionId, url};
}

describe('resolveMediaVersionIndex', () => {
  it('follows the current file when every version has a distinct URL', () => {
    const versions = [
      version('v0', '/uploads/a.jpg'),
      version('v1', '/uploads/b.jpg'),
    ];
    // 旧指针指向 v0，但卡片实际显示 b.jpg：以文件为准。
    expect(resolveMediaVersionIndex('/uploads/b.jpg', versions, {
      activeVersionId: 'v0',
      index: 0,
    })).toBe(1);
  });

  it('uses the stored version id to disambiguate duplicate URLs', () => {
    const versions = [
      version('v0', '/uploads/a.jpg'),
      version('v1', '/uploads/a.jpg'),
      version('v2', '/uploads/b.jpg'),
    ];
    expect(resolveMediaVersionIndex('/uploads/a.jpg', versions, {
      activeVersionId: 'v1',
      index: 1,
    })).toBe(1);
  });

  it('falls back to the stored index between duplicate URLs', () => {
    const versions = [
      version('v0', '/uploads/a.jpg'),
      version('v1', '/uploads/a.jpg'),
    ];
    expect(resolveMediaVersionIndex('/uploads/a.jpg', versions, {
      index: 0,
    })).toBe(0);
  });

  it('does not get stuck at the first duplicate when the pointer is ahead', () => {
    // 线上实例：第 0/1 版共用同一张图，第 2 版是后写的 TOS 原始图，
    // 指针已到第 2 版但卡片文件仍是第 0/1 版的地址。
    const versions = [
      version('v0', '/uploads/a.jpg'),
      version('v1', '/uploads/a.jpg'),
      version('v2', 'https://tos.example/a.jpg?sign=1'),
    ];
    const current = resolveMediaVersionIndex('/uploads/a.jpg', versions, {
      activeVersionId: 'v2',
      index: 2,
    });
    expect(current).toBe(1);
    // 点「下一版」必须真的走到第 2 版，而不是又回到第 0 版。
    expect((current + 1) % versions.length).toBe(2);
  });

  it('keeps all identical-URL history entries addressable', () => {
    const versions = [
      version('v0', '/uploads/a.jpg'),
      version('v1', '/uploads/a.jpg'),
      version('v2', '/uploads/a.jpg'),
      version('v3', '/uploads/a.jpg'),
    ];
    expect(resolveMediaVersionIndex('/uploads/a.jpg', versions, {
      activeVersionId: 'v3',
      index: 3,
    })).toBe(3);
  });

  it('prefers a valid saved index over an unrelated active id on duplicate URLs', () => {
    const versions = [
      version('v0', '/uploads/a.jpg'),
      version('v1', '/uploads/a.jpg'),
      version('v2', 'https://tos.example/a.jpg?sign=1'),
    ];
    expect(resolveMediaVersionIndex('/uploads/a.jpg', versions, {
      activeVersionId: 'v2',
      index: 0,
    })).toBe(0);
  });

  it('trusts the stored index when the file URL is a freshly signed copy', () => {
    const versions = [
      version('v0', '/uploads/a.jpg'),
      version('v1', '/uploads/b.jpg'),
    ];
    expect(resolveMediaVersionIndex('https://tos.example/b.jpg?sign=fresh', versions, {
      activeVersionId: 'v1',
      index: 1,
    })).toBe(1);
  });

  it('falls back to the newest version when no pointer is stored', () => {
    const versions = [
      version('v0', '/uploads/a.jpg'),
      version('v1', '/uploads/b.jpg'),
    ];
    expect(resolveMediaVersionIndex('', versions)).toBe(1);
    expect(resolveMediaVersionIndex('/uploads/unknown.jpg', versions)).toBe(1);
  });

  it('handles an empty history safely', () => {
    expect(resolveMediaVersionIndex('/uploads/a.jpg', [])).toBe(0);
  });
});
