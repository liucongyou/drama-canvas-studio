import {describe, expect, it} from 'vitest';
import {
  canvasEventId,
  eventBelongsToEpisode,
  shouldProcessCanvasEvent,
} from './canvasEventCursor';

describe('canvas event cursor', () => {
  it('uses the SSE MessageEvent id instead of requiring eventId in JSON', () => {
    const event = {
      lastEventId: '2716',
      data: '{"eventId":2576,"nodeId":"node-1"}',
    };
    expect(canvasEventId(event, {eventId: 2576}))
      .toBe(2716);
  });

  it('falls back to a numeric payload id for compatible servers', () => {
    expect(canvasEventId({data: '{}'}, {id: 18})).toBe(18);
    expect(canvasEventId({data: '{}'}, {eventId: 17})).toBe(17);
  });

  it('accepts a bare numeric id from the current event bridge', () => {
    expect(canvasEventId('42')).toBe(42);
  });

  it('ignores invalid ids but still processes unknown events defensively', () => {
    expect(canvasEventId({lastEventId: 'bad', data: '{}'}))
      .toBe(0);
    expect(shouldProcessCanvasEvent(0, 1000)).toBe(true);
  });

  it('drops replay events already represented by the snapshot', () => {
    expect(shouldProcessCanvasEvent(2576, 2716)).toBe(false);
    expect(shouldProcessCanvasEvent(2716, 2716)).toBe(false);
  });

  it('processes only events after the snapshot boundary', () => {
    expect(shouldProcessCanvasEvent(2717, 2716)).toBe(true);
  });

  it('acknowledges known events from another episode without refreshing this one', () => {
    expect(eventBelongsToEpisode({episodeId: 'EP002'}, 'EP001')).toBe(false);
    expect(eventBelongsToEpisode({episodeId: 'ep001'}, 'EP001')).toBe(true);
    expect(eventBelongsToEpisode({}, 'EP001')).toBe(true);
  });
});
