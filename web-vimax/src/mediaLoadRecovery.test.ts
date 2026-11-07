import {describe, expect, it} from 'vitest';
import {
  cacheBustedMediaUrl,
  isTransientMediaError,
  shouldIgnorePlaybackInterruption,
  shouldMarkMediaMissing,
  shouldRetryMediaLoad,
} from './mediaLoadRecovery';

describe('media load recovery', () => {
  it('retries a transient playback failure once before declaring loss', () => {
    expect(shouldRetryMediaLoad(1, true)).toBe(true);
    expect(shouldMarkMediaMissing(1, false)).toBe(false);
    expect(shouldMarkMediaMissing(2, false)).toBe(false);
    expect(shouldMarkMediaMissing(3, false)).toBe(true);
  });

  it('ignores transient browser/media errors after refresh', () => {
    expect(isTransientMediaError({name: 'AbortError'})).toBe(true);
    expect(isTransientMediaError({name: 'NetworkError'})).toBe(true);
    expect(isTransientMediaError({name: 'NotSupportedError'})).toBe(true);
    expect(isTransientMediaError({name: 'DecodeError'})).toBe(false);
  });

  it('never retries without a source and marks backend-confirmed loss', () => {
    expect(shouldRetryMediaLoad(1, false)).toBe(false);
    expect(shouldMarkMediaMissing(1, true)).toBe(true);
  });

  it('adds a retry token without destroying signed query parameters', () => {
    expect(cacheBustedMediaUrl('/uploads/a.mp4?token=abc', 1))
      .toBe('/uploads/a.mp4?token=abc&__media_retry=1');
    expect(cacheBustedMediaUrl('data:image/png;base64,aaa', 1))
      .toBe('data:image/png;base64,aaa');
  });

  it('treats play promise AbortError as a normal pause interruption', () => {
    expect(shouldIgnorePlaybackInterruption({name: 'AbortError'})).toBe(true);
    expect(shouldIgnorePlaybackInterruption({name: 'NotSupportedError'})).toBe(false);
    expect(shouldIgnorePlaybackInterruption(null)).toBe(false);
  });
});
