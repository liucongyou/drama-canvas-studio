import {describe, expect, it} from 'vitest';
import {
  mediaNodeDataEqual,
  mediaNodeDependencySignature,
  sameStringList,
} from './canvasNodeMemo';
import type {CanvasMediaNode} from './api';

/**
 * 这些用例守的是「memo 判等写错会不会把画布改坏」这条线：
 * 判定过于激进（该重渲染时返回 true）→ 卡片永远停在旧图/旧状态，用户以为没生成成功；
 * 判定过于保守（无关变化返回 false）→ 回到全树重渲染，白优化。
 */

function node(overrides: Partial<CanvasMediaNode> = {}): CanvasMediaNode {
  return {
    id: 'media-a',
    kind: 'video',
    title: 'A',
    prompt: 'p',
    status: 'idle',
    x: 0,
    y: 0,
    ...overrides,
  } as CanvasMediaNode;
}

function pool(...items: CanvasMediaNode[]): Map<string, CanvasMediaNode> {
  return new Map(items.map((item) => [item.id, item]));
}

describe('画布媒体卡片 memo 判等', () => {
  it('本卡数据未变时，即使 mediaNodes 重建导致 assetsById 换新 Map 也跳过重渲染', () => {
    const self = node({params: {refs: [{nodeId: 'media-b', label: 'B'}]}} as Partial<CanvasMediaNode>);
    const b = node({id: 'media-b', kind: 'image', status: 'done', mediaUrl: '/uploads/b.png'});
    const prev = {node: self, assetsById: pool(self, b), active: false};
    const next = {
      node: self,
      // 新的 Map、同样的内容
      assetsById: pool(self, {...b}),
      active: false,
    };
    expect(mediaNodeDataEqual(prev, next)).toBe(true);
  });

  it('本卡自身数据变化必须重渲染', () => {
    const prev = {node: node({status: 'generating'})};
    const next = {node: node({status: 'done', mediaUrl: '/uploads/v.mp4'})};
    expect(mediaNodeDataEqual(prev, next)).toBe(false);
  });

  it('被引用的节点从"未生成"变成"已生成"时必须重渲染（依赖徽章要变）', () => {
    const self = node({params: {refs: [{nodeId: 'media-b'}]}} as Partial<CanvasMediaNode>);
    const before = node({id: 'media-b', kind: 'image', status: 'idle'});
    const after = node({id: 'media-b', kind: 'image', status: 'done', mediaUrl: '/uploads/b.png'});
    expect(
      mediaNodeDataEqual(
        {node: self, assetsById: pool(self, before)},
        {node: self, assetsById: pool(self, after)},
      ),
    ).toBe(false);
  });

  it('被引用的节点消失时必须重渲染（徽章要变成"引用节点不存在"）', () => {
    const self = node({params: {refs: [{nodeId: 'media-b'}]}} as Partial<CanvasMediaNode>);
    const b = node({id: 'media-b', kind: 'image', status: 'done', mediaUrl: '/uploads/b.png'});
    expect(
      mediaNodeDataEqual(
        {node: self, assetsById: pool(self, b)},
        {node: self, assetsById: pool(self)},
      ),
    ).toBe(false);
  });

  it('音色引用（voiceRef 兼容单值）也算依赖', () => {
    const self = node({params: {voiceRef: {nodeId: 'media-v'}} as never});
    const before = node({id: 'media-v', kind: 'audio', status: 'idle'});
    const after = node({id: 'media-v', kind: 'audio', status: 'done', mediaUrl: '/uploads/v.wav'});
    expect(mediaNodeDependencySignature(self, pool(self, before)))
      .not.toBe(mediaNodeDependencySignature(self, pool(self, after)));
  });

  it('无关节点变化不影响本卡判等', () => {
    const self = node({params: {refs: [{nodeId: 'media-b'}]}} as Partial<CanvasMediaNode>);
    const b = node({id: 'media-b', kind: 'image', status: 'done', mediaUrl: '/uploads/b.png'});
    const unrelated = node({id: 'media-z', kind: 'video', status: 'done', mediaUrl: '/uploads/z.mp4'});
    expect(
      mediaNodeDataEqual(
        {node: self, assetsById: pool(self, b)},
        {node: self, assetsById: pool(self, b, unrelated)},
      ),
    ).toBe(true);
  });

  it('编组按内容比较：新数组但内容相同不重渲染，内容变了要重渲染', () => {
    const self = node();
    const prev = {node: self, assetsById: pool(self), groupMembers: ['media-a', 'media-b']};
    const sameArray = {node: self, assetsById: pool(self), groupMembers: [...['media-a', 'media-b']]};
    const changed = {node: self, assetsById: pool(self), groupMembers: ['media-a', 'media-c']};
    expect(mediaNodeDataEqual(prev, sameArray)).toBe(true);
    expect(mediaNodeDataEqual(prev, changed)).toBe(false);
  });

  it('生成态与激活态任何翻转都要重渲染', () => {
    const self = node();
    expect(mediaNodeDataEqual({node: self, generationBusy: false}, {node: self, generationBusy: true}))
      .toBe(false);
    expect(mediaNodeDataEqual({node: self, active: false}, {node: self, active: true}))
      .toBe(false);
    expect(
      mediaNodeDataEqual(
        {node: self, contentVisible: true},
        {node: self, contentVisible: false},
      ),
    ).toBe(false);
  });

  it('sameStringList 处理 null / undefined / 长度不同', () => {
    expect(sameStringList(null, null)).toBe(true);
    expect(sameStringList(null, ['a'])).toBe(false);
    expect(sameStringList(['a'], ['a', 'b'])).toBe(false);
    expect(sameStringList(['b', 'a'], ['a', 'b'])).toBe(false);
  });
});
