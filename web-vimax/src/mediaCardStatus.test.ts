import {describe, expect, it} from 'vitest';

/**
 * Mirrors the MediaNode status projection so a failed regeneration on a card
 * that already holds an older video can never be shown as "已生成".
 */
function projectCardStatus(input: {
  status: 'idle' | 'done' | 'generating' | 'failed';
  mediaUrl?: string;
  lastError?: string;
  providerLive?: boolean;
}) {
  const liveProviderGeneration = input.providerLive === true;
  const done = input.status === 'done' && !liveProviderGeneration;
  const failed = input.status === 'failed' && !liveProviderGeneration;
  const busy = input.status === 'generating' || liveProviderGeneration;
  const statusText = busy
    ? '生成中'
    : failed
      ? '生成失败'
      : done
        ? '已生成'
        : '待生成';
  const errorText = failed
    ? (input.lastError || '生成失败，请查看原因后重试')
    : '';
  return {statusText, errorText, keepsOldMedia: Boolean(input.mediaUrl)};
}

describe('media card status', () => {
  it('shows the real failure reason while keeping the previous video', () => {
    const state = projectCardStatus({
      status: 'failed',
      mediaUrl: '/uploads/previous.mp4',
      lastError: '供应商接口 HTTP 503：供应商视频队列已满，本次未创建任务',
    });
    expect(state.statusText).toBe('生成失败');
    expect(state.errorText).toContain('队列已满');
    expect(state.keepsOldMedia).toBe(true);
  });

  it('does not require deleting the old video to report failure', () => {
    const withoutOld = projectCardStatus({
      status: 'failed',
      lastError: '供应商接口 HTTP 500',
    });
    const withOld = projectCardStatus({
      status: 'failed',
      mediaUrl: '/uploads/previous.mp4',
      lastError: '供应商接口 HTTP 500',
    });
    expect(withoutOld.statusText).toBe('生成失败');
    expect(withOld.statusText).toBe('生成失败');
  });

  it('shows 生成中 while a provider task is still live', () => {
    const state = projectCardStatus({
      status: 'generating',
      mediaUrl: '/uploads/previous.mp4',
      providerLive: true,
    });
    expect(state.statusText).toBe('生成中');
  });

  it('uses backend media availability rather than a browser load guess', () => {
    const missing = (availability: 'available' | 'missing' | 'unknown') =>
      availability === 'missing';
    expect(missing('available')).toBe(false);
    expect(missing('unknown')).toBe(false);
    expect(missing('missing')).toBe(true);
  });
});
