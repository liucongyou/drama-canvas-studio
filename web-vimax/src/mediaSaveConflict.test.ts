import {describe, expect, it} from 'vitest';
import type {CanvasMediaNode} from './api';
import {rebaseLocalMediaNode} from './mediaSaveCoordinator';

function node(overrides: Partial<CanvasMediaNode> = {}): CanvasMediaNode {
  return {
    id: 'image-1',
    kind: 'image',
    title: '场景',
    prompt: '服务器旧词',
    status: 'done',
    mediaUrl: '/uploads/v1.jpg',
    x: 10,
    y: 20,
    updatedAt: '2026-11-08T01:00:00Z',
    params: {ratio: '16:9', activeVersionId: 'v1'},
    ...overrides,
  };
}

describe('media save conflict recovery', () => {
  it('keeps local text and params while advancing to the server revision', () => {
    const local = node({
      prompt: '用户刚写的本地提示词',
      updatedAt: '2026-11-08T00:59:00Z',
      params: {ratio: '9:16', activeVersionId: 'v2'},
    });
    const remote = node({
      prompt: '另一个页面写入的提示词',
      updatedAt: '2026-11-08T01:02:00Z',
      params: {ratio: '16:9', activeVersionId: 'v1', refs: [{nodeId: 'a', label: 'A'}]},
    });

    const rebased = rebaseLocalMediaNode(local, remote);
    expect(rebased.prompt).toBe('用户刚写的本地提示词');
    expect(rebased.updatedAt).toBe(remote.updatedAt);
    expect(rebased.params).toMatchObject({
      ratio: '9:16',
      activeVersionId: 'v2',
      refs: [{nodeId: 'a', label: 'A'}],
    });
  });
});
