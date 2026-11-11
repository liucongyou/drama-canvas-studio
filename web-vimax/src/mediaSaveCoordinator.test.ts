import {describe, expect, it} from 'vitest';
import {
  generationOwnsAllNodes,
  isMediaSaveConflict,
  mediaSaveRetryDecision,
  rebaseLocalMediaNode,
} from './mediaSaveCoordinator';
import {withRequestStatus} from './requestRetry';

describe('media save coordinator', () => {
  it('identifies only explicit node edit conflicts', () => {
    const conflict = withRequestStatus(
      new Error('另一个标签页已更新此节点，已拒绝覆盖'),
      409,
      'NODE_EDIT_CONFLICT',
    );
    expect(isMediaSaveConflict(conflict)).toBe(true);
    expect(isMediaSaveConflict(withRequestStatus(
      new Error('项目正在被其他操作更新，请重新保存'),
      409,
      'NODE_REVISION_CONFLICT',
    ))).toBe(false);
  });

  it('recognizes saves entirely owned by an active generation', () => {
    const active = new Set(['image-1']);
    expect(generationOwnsAllNodes([{id: 'image-1'}], active)).toBe(true);
    expect(generationOwnsAllNodes([], active)).toBe(false);
    expect(generationOwnsAllNodes(
      [{id: 'image-1'}, {id: 'image-2'}],
      active,
    )).toBe(false);
  });

  it('backs off transient save failures up to one minute and keeps retrying', () => {
    const error = withRequestStatus(new Error('bad gateway'), 502);
    expect(mediaSaveRetryDecision({error, attempt: 1}))
      .toMatchObject({retry: true, delayMs: 1_000});
    expect(mediaSaveRetryDecision({error, attempt: 4}))
      .toMatchObject({retry: true, delayMs: 15_000});
    expect(mediaSaveRetryDecision({error, attempt: 9}))
      .toMatchObject({retry: true, delayMs: 60_000});
  });

  it('rebases local prompt edits onto remote generation state', () => {
    const local = {
      id: 'node-1',
      kind: 'video' as const,
      title: '段9',
      prompt: '本地提示词',
      status: 'idle' as const,
      x: 0,
      y: 0,
      updatedAt: '2026-11-12T00:00:00Z',
      params: {
        model: 'video-model',
        refs: [{nodeId: 'image-1'}],
        providerResultUrl: 'blob:local-only',
        mediaVersions: [{versionId: 'stale'}],
      },
    };
    const remote = {
      ...local,
      status: 'done' as const,
      mediaUrl: '/uploads/current.mp4',
      updatedAt: '2026-11-12T00:00:03Z',
      params: {
        ...local.params,
        providerResultUrl: 'https://provider/current.mp4',
        mediaVersions: [{versionId: 'current'}],
      },
    };
    const rebased = rebaseLocalMediaNode(local, remote);
    expect(rebased.prompt).toBe('本地提示词');
    expect(rebased.status).toBe('done');
    expect(rebased.mediaUrl).toBe('/uploads/current.mp4');
    expect(rebased.params?.providerResultUrl).toBe('https://provider/current.mp4');
    expect(rebased.params?.mediaVersions).toEqual([{versionId: 'current'}]);
    expect(rebased.updatedAt).toBe(remote.updatedAt);
  });

  it('waits for the online event and never retries client or auth errors', () => {
    expect(mediaSaveRetryDecision({
      error: new TypeError('Failed to fetch'),
      attempt: 1,
      online: false,
    })).toMatchObject({retry: false, kind: 'offline'});
    expect(mediaSaveRetryDecision({
      error: withRequestStatus(new Error('bad request'), 400),
      attempt: 1,
    })).toMatchObject({retry: false, kind: 'client'});
    expect(mediaSaveRetryDecision({
      error: withRequestStatus(new Error('登录过期'), 401),
      attempt: 1,
    })).toMatchObject({retry: false, kind: 'auth'});
  });
});
