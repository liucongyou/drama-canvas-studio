import {describe, expect, it} from 'vitest';
import type {Canvas, CanvasEp} from './api';
import {mergeEpisodeGraph} from './canvasEpisodeCatalog';

function episode(id: string): CanvasEp {
  return {
    ep_id: id,
    title: id,
    progress: null,
    storyboard_ready: true,
    shot_count: 1,
    described_count: 1,
    keyframes: 0,
    clips: 0,
    final_ready: false,
    status: 'ready',
    updated_at: null,
    segments_total: 1,
    segments_rendered: 0,
  };
}

function canvas(current: string, episodes: CanvasEp[]): Canvas {
  return {
    meta: {
      session_id: 'project-1',
      current_ep: current,
      episodes,
      project_title: 'project',
      overview: false,
    },
    characters: [],
    scenes: [],
    props: [],
    voices: [],
    shots: [],
    segments: [],
  };
}

describe('mergeEpisodeGraph', () => {
  it('keeps every sibling from the project catalog', () => {
    const loaded = canvas('EP002', [episode('EP002')]);
    const merged = mergeEpisodeGraph(loaded, [
      episode('EP001'),
      episode('EP002'),
      episode('EP003'),
    ]);
    expect(merged.meta.episodes.map((item) => item.ep_id)).toEqual([
      'EP001',
      'EP002',
      'EP003',
    ]);
    expect(merged.meta.current_ep).toBe('EP002');
  });

  it('prefers indexed metadata over a stale fallback', () => {
    const loaded = canvas('EP002', [episode('EP002')]);
    const merged = mergeEpisodeGraph(
      loaded,
      [episode('EP001'), episode('EP002')],
      [episode('EP002'), episode('EP003')],
    );
    expect(merged.meta.episodes.map((item) => item.ep_id)).toEqual([
      'EP001',
      'EP002',
    ]);
  });

  it('uses the graph episode only for legacy projects without an index', () => {
    const loaded = canvas('EP002', [episode('EP002')]);
    const merged = mergeEpisodeGraph(loaded, []);
    expect(merged.meta.episodes.map((item) => item.ep_id)).toEqual(['EP002']);
  });
});
