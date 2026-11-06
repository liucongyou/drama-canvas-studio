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
  it('does not double count a generated media card and its asset projection', () => {
    const stats = canvasHeaderStats(canvas(), [
      mediaNode({id: 'asset', title: '角色｜赵大强'}),
      mediaNode({id: 'generated', title: '角色｜赵大强', params: {}}),
    ]);
    expect(stats.assetCount).toBe(1);
  });

  it('sums completed video durations only', () => {
    const stats = canvasHeaderStats(canvas(), [
      mediaNode({kind: 'video', status: 'done', actualDuration: 5.2}),
      mediaNode({kind: 'video', status: 'done', actualDuration: 10}),
      mediaNode({kind: 'video', status: 'failed', actualDuration: 99}),
    ]);
    expect(stats.videoDurationSeconds).toBeCloseTo(15.2);
    expect(formatCanvasDuration(stats.videoDurationSeconds)).toBe('0:15');
  });
});
