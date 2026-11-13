import {describe, expect, it} from 'vitest';
import {
  mediaReadinessClass,
  mediaReadinessLabel,
  mediaReadinessPhase,
} from './mediaReadiness';

describe('media readiness', () => {
  it('keeps a completed image in loading state until its bytes paint', () => {
    const loading = mediaReadinessPhase({
      kind: 'image',
      status: 'done',
      hasMedia: true,
      mediaReady: false,
    });
    expect(loading).toBe('loading');
    expect(mediaReadinessLabel(loading, 'image')).toBe('素材加载中');
    expect(mediaReadinessClass(loading)).toBe('is-loading');

    expect(mediaReadinessPhase({
      kind: 'image',
      status: 'done',
      hasMedia: true,
      mediaReady: true,
    })).toBe('ready');
  });

  it('uses actual cover or video-frame readiness for videos', () => {
    expect(mediaReadinessPhase({
      kind: 'video',
      status: 'done',
      hasMedia: true,
      mediaReady: false,
    })).toBe('loading');
    expect(mediaReadinessPhase({
      kind: 'video',
      status: 'done',
      hasMedia: true,
      mediaReady: true,
    })).toBe('ready');
  });

  it('surfaces exhausted browser retries instead of claiming completion', () => {
    const failed = mediaReadinessPhase({
      kind: 'video',
      status: 'done',
      hasMedia: true,
      mediaReady: false,
      loadFailed: true,
    });
    expect(failed).toBe('load_failed');
    expect(mediaReadinessLabel(failed, 'video')).toContain('重试');
    expect(mediaReadinessClass(failed)).toBe('is-failed');
  });

  it('keeps backend-confirmed missing state authoritative', () => {
    const missing = mediaReadinessPhase({
      kind: 'image',
      status: 'done',
      hasMedia: true,
      mediaMissing: true,
      mediaReady: true,
    });
    expect(missing).toBe('missing');
    expect(mediaReadinessLabel(missing, 'image')).toBe('媒体文件丢失');
  });

  it('does not gate text nodes on media elements', () => {
    expect(mediaReadinessPhase({
      kind: 'text',
      status: 'done',
    })).toBe('ready');
  });
});
