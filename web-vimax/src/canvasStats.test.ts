import {describe, expect, it} from 'vitest';
import {canvasHeaderStats, formatCanvasDuration} from './canvasStats';
import type {Canvas, CanvasMediaNode} from './api';

function mediaNode(patch: Partial<CanvasMediaNode>): CanvasMediaNode {
  return {
    id: 'node',
    kind: 'image',
    title: '',
    prompt: '',
    status: 'done',
    action: '',
    mediaUrl: '/uploads/item.jpg',
    meta: '',
    x: 0,
    y: 0,
    updatedAt: '',
    ...patch,
  } as CanvasMediaNode;
}

function canvas(patch: Partial<Canvas> = {}): Canvas {
  return {
    meta: {session_id: 'p', current_ep: 'EP001', episodes: [], project_title: '', overview: false},
    characters: [{idx: 1, name: '赵大强'}],
    scenes: [],
    props: [],
    voices: [],
    shots: [],
    segments: [],
    ...patch,
  } as Canvas;
}

describe('canvas header stats', () => {
  it('counts distinct media cards even when their titles match', () => {
    const stats = canvasHeaderStats(canvas(), [
      mediaNode({id: 'asset', title: '角色｜赵大强'}),
      mediaNode({id: 'generated', title: '角色｜赵大强', params: {}}),
    ]);
    expect(stats.assetCount).toBe(2);
  });

  it('counts every media card and each playable video node', () => {
    const stats = canvasHeaderStats(canvas(), [
      mediaNode({id: 'image-1', title: '角色｜赵大强'}),
      mediaNode({id: 'image-2', title: '角色｜赵大强'}),
      mediaNode({id: 'audio-1', kind: 'audio', title: '音色｜赵大强'}),
      mediaNode({id: 'video-1', kind: 'video', status: 'done', actualDuration: 5.2}),
      mediaNode({id: 'video-2', kind: 'video', status: 'done', actualDuration: 10}),
      mediaNode({id: 'video-3', kind: 'video', status: 'failed', actualDuration: 99}),
      mediaNode({id: 'video-idle', kind: 'video', status: 'idle', mediaUrl: undefined}),
    ]);
    expect(stats.assetCount).toBe(6);
    expect(stats.videoDurationSeconds).toBeCloseTo(15.2);
    expect(formatCanvasDuration(stats.videoDurationSeconds)).toBe('0:15');
  });

  it('sums only the media nodes supplied for the currently opened episode', () => {
    const stats = canvasHeaderStats(canvas({
      meta: {
        session_id: 'p',
        current_ep: 'EP002',
        episodes: [
          {ep_id: 'EP001', title: '第1集'},
          {ep_id: 'EP002', title: '第2集'},
        ],
        project_title: '',
        overview: false,
      } as Canvas['meta'],
    }), [
      mediaNode({id: 'ep2-video-1', kind: 'video', status: 'done', actualDuration: 6}),
      mediaNode({id: 'ep2-video-2', kind: 'video', status: 'done', actualDuration: 9}),
    ]);
    expect(stats.videoDurationSeconds).toBe(15);
    expect(formatCanvasDuration(stats.videoDurationSeconds)).toBe('0:15');
  });
});
