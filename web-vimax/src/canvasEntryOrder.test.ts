import {describe, expect, it} from 'vitest';
import {mediaEntryOrder, sortMediaNodesForEntry} from './canvasEntryOrder';
import type {CanvasMediaNode} from './api';

function node(
  id: string,
  kind: CanvasMediaNode['kind'],
  x: number,
  y: number,
): CanvasMediaNode {
  return {id, kind, x, y, title: id} as CanvasMediaNode;
}

describe('canvas entry order', () => {
  it('renders images before videos and keeps spatial order', () => {
    const nodes = [
      node('video-2', 'video', 0, 0),
      node('image-2', 'image', 300, 10),
      node('image-1', 'image', 0, 20),
      node('audio-1', 'audio', 0, 0),
    ];
    const ordered = sortMediaNodesForEntry(nodes);
    expect(ordered.map((item) => item.id)).toEqual([
      'image-1',
      'image-2',
      'audio-1',
      'video-2',
    ]);
    expect([...nodes].sort(mediaEntryOrder)).toEqual(ordered);
  });

  it('is deterministic for equal positions', () => {
    const first = sortMediaNodesForEntry([
      node('image-b', 'image', 0, 0),
      node('image-a', 'image', 0, 0),
    ]);
    const second = sortMediaNodesForEntry([
      node('image-a', 'image', 0, 0),
      node('image-b', 'image', 0, 0),
    ]);
    expect(first.map((item) => item.id)).toEqual(['image-a', 'image-b']);
    expect(second.map((item) => item.id)).toEqual(['image-a', 'image-b']);
  });
});
