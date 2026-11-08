type TimerHandle = ReturnType<typeof globalThis.setTimeout>;

type LifecycleOptions = {
  hasSnapshotCursor: () => boolean;
  snapshotCursor: () => number;
  connect: (since: number) => void;
  refreshFallback: () => void;
  clearTimer: (timer: TimerHandle) => void;
  setTimer: (callback: () => void, delay: number) => TimerHandle;
};

export type CanvasEventLifecycle = {
  startWhenReady: () => void;
  reconnect: () => void;
  connected: () => void;
  dispose: () => void;
};

/**
 * SSE is project-scoped. Switching episodes changes the projection consumer,
 * not the transport. `startWhenReady` also closes the startup race where the
 * snapshot lands after the effect has already run.
 */
export function createCanvasEventLifecycle({
  hasSnapshotCursor,
  snapshotCursor,
  connect,
  refreshFallback,
  clearTimer,
  setTimer,
}: LifecycleOptions): CanvasEventLifecycle {
  let disposed = false;
  let started = false;
  let attempts = 0;
  let timer: TimerHandle | null = null;

  const cancelTimer = () => {
    if (timer === null) return;
    clearTimer(timer);
    timer = null;
  };

  const startWhenReady = () => {
    if (disposed || started || !hasSnapshotCursor()) return;
    started = true;
    connect(Math.max(0, snapshotCursor()));
  };

  const reconnect = () => {
    if (disposed) return;
    cancelTimer();
    const delay = Math.min(30_000, 1_000 * (2 ** attempts));
    attempts += 1;
    timer = setTimer(() => {
      timer = null;
      if (disposed) return;
      // Read the cursor at reconnect time, not when onerror fired. Otherwise a
      // burst received during this connection is replayed again and again.
      const cursor = Math.max(0, snapshotCursor());
      if (!started) {
        startWhenReady();
      } else {
        connect(cursor);
      }
      if (cursor === 0) refreshFallback();
    }, delay);
  };

  const connected = () => {
    attempts = 0;
  };

  const dispose = () => {
    disposed = true;
    cancelTimer();
  };

  return {startWhenReady, reconnect, connected, dispose};
}
