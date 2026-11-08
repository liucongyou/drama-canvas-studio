import {describe, expect, it, vi} from 'vitest';
import {createCanvasEventLifecycle} from './canvasEventLifecycle';

function harness(initialCursor: number | null) {
  let cursor = initialCursor;
  const timers: Array<() => void> = [];
  const connect = vi.fn();
  const refreshFallback = vi.fn();
  const lifecycle = createCanvasEventLifecycle({
    hasSnapshotCursor: () => cursor !== null,
    snapshotCursor: () => cursor || 0,
    connect,
    refreshFallback,
    clearTimer: vi.fn(),
    setTimer: (callback) => {
      timers.push(callback);
      return timers.length as unknown as ReturnType<typeof setTimeout>;
    },
  });
  return {
    connect,
    refreshFallback,
    lifecycle,
    setCursor: (next: number | null) => { cursor = next; },
    runTimer: () => timers.shift()?.(),
  };
}

describe('canvas event lifecycle', () => {
  it('waits for the snapshot boundary and then connects with its cursor', () => {
    const test = harness(null);
    test.lifecycle.startWhenReady();
    expect(test.connect).not.toHaveBeenCalled();
    test.setCursor(73);
    test.lifecycle.startWhenReady();
    expect(test.connect).toHaveBeenCalledOnce();
    expect(test.connect).toHaveBeenCalledWith(73);
  });

  it('does not reconnect just because the consumer restarts', () => {
    const test = harness(73);
    test.lifecycle.startWhenReady();
    test.lifecycle.startWhenReady();
    expect(test.connect).toHaveBeenCalledOnce();
  });

  it('reconnects from the supplied cursor and only falls back when it is zero', () => {
    const test = harness(73);
    test.lifecycle.startWhenReady();
    test.setCursor(91);
    test.lifecycle.reconnect();
    test.runTimer();
    expect(test.connect).toHaveBeenLastCalledWith(91);
    expect(test.refreshFallback).not.toHaveBeenCalled();

    test.setCursor(0);
    test.lifecycle.reconnect();
    test.runTimer();
    expect(test.refreshFallback).toHaveBeenCalledOnce();
  });

  it('cannot start or reconnect after disposal', () => {
    const test = harness(73);
    test.lifecycle.dispose();
    test.lifecycle.startWhenReady();
    test.lifecycle.reconnect();
    expect(test.connect).not.toHaveBeenCalled();
  });
});
