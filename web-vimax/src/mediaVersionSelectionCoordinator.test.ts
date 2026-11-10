import {describe, expect, it} from 'vitest';
import {MediaVersionSelectionCoordinator} from './mediaVersionSelectionCoordinator';

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return {promise, resolve};
}

describe('media version selection coordinator', () => {
  it('serializes requests and coalesces rapid clicks to the newest intent', async () => {
    const coordinator = new MediaVersionSelectionCoordinator<string>();
    const gate = deferred();
    const sent: Array<{value: string; sequence: number}> = [];

    const result = coordinator.request('image-1', 'v1', async (value, sequence) => {
      sent.push({value, sequence});
      if (sequence === 1) await gate.promise;
    });
    void coordinator.request('image-1', 'v2', async () => {});
    void coordinator.request('image-1', 'v3', async (value, sequence) => {
      sent.push({value, sequence});
    });

    expect(sent).toEqual([{value: 'v1', sequence: 1}]);
    gate.resolve();
    await result;

    expect(sent).toEqual([
      {value: 'v1', sequence: 1},
      {value: 'v3', sequence: 3},
    ]);
  });

  it('queues different cards independently', async () => {
    const coordinator = new MediaVersionSelectionCoordinator<string>();
    const gate = deferred();
    const sent: string[] = [];
    const imageSender = async (value: string) => {
      sent.push(`image-action:${value}`);
      await gate.promise;
    };
    const videoSender = async (value: string) => {
      sent.push(`video-action:${value}`);
    };

    const result = coordinator.request('image-1', 'v1', imageSender);
    void coordinator.request('video-1', 'v2', videoSender);
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    expect(sent).toEqual(['image-action:v1', 'video-action:v2']);
    gate.resolve();
    await result;

    expect(sent).toEqual(['image-action:v1', 'video-action:v2']);
  });

  it('does not route a stale card closure into another queued card', async () => {
    const coordinator = new MediaVersionSelectionCoordinator<string>();
    const firstGate = deferred();
    const invoked: string[] = [];
    let imageCalls = 0;
    let videoCalls = 0;

    const result = coordinator.request('image-1', 'image-v1', async () => {
      imageCalls += 1;
      invoked.push('image-1');
      await firstGate.promise;
    });
    void coordinator.request('video-1', 'video-v2', async () => {
      videoCalls += 1;
      invoked.push('video-1');
    });

    // Simulate a later render of the image card supplying a new closure while
    // the first request is running. It must not replace the queued video card.
    void coordinator.request('image-1', 'image-v2', async () => {
      imageCalls += 1;
      invoked.push('image-1');
    });
    firstGate.resolve();
    await result;

    // Each card's intent runs through its own closure exactly once.
    expect(invoked.filter((card) => card === 'image-1')).toHaveLength(2);
    expect(invoked.filter((card) => card === 'video-1')).toHaveLength(1);
    expect(imageCalls).toBe(2);
    expect(videoCalls).toBe(1);
  });

  it('continues accepting work after a failed request', async () => {
    const coordinator = new MediaVersionSelectionCoordinator<string>();
    const sent: string[] = [];

    const result = coordinator.request('image-1', 'bad', async () => {
      throw new Error('missing');
    });
    void coordinator.request('image-1', 'good', async (value) => {
      sent.push(value);
    });
    await result;

    expect(sent).toEqual(['good']);
  });

  it('drains an intent queued during the final sender microtask', async () => {
    const coordinator = new MediaVersionSelectionCoordinator<string>();
    const sent: string[] = [];

    const first = coordinator.request('image-1', 'v1', async (value) => {
      sent.push(value);
      queueMicrotask(() => {
        void coordinator.request('image-1', 'v2', async (next) => {
          sent.push(next);
        });
      });
    });

    await first;
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    expect(sent).toEqual(['v1', 'v2']);
  });

  it('can start a new drain after the previous drain completed', async () => {
    const coordinator = new MediaVersionSelectionCoordinator<string>();
    const sent: string[] = [];

    await coordinator.request('image-1', 'v1', async (value) => {
      sent.push(value);
    });
    await coordinator.request('image-1', 'v2', async (value) => {
      sent.push(value);
    });

    expect(sent).toEqual(['v1', 'v2']);
  });

  it('does not let an older intent overwrite a newer queued sender', async () => {
    const coordinator = new MediaVersionSelectionCoordinator<string>();
    const gate = deferred();
    const sent: string[] = [];

    const first = coordinator.request('image-1', 'v1', async () => {
      await gate.promise;
    });
    void coordinator.request('image-1', 'v2-old', async (value) => {
      sent.push(`old:${value}`);
    });
    void coordinator.request('image-1', 'v3-new', async (value) => {
      sent.push(`new:${value}`);
    });

    gate.resolve();
    await first;

    expect(sent).toEqual(['new:v3-new']);
  });

  it('distinguishes an obsolescent response from the newest intent', async () => {
    const coordinator = new MediaVersionSelectionCoordinator<string>();
    const gate = deferred();

    const first = coordinator.request('image-1', 'v1', async () => {
      await gate.promise;
    });
    expect(coordinator.isNewest('image-1', 'v1')).toBe(true);
    void coordinator.request('image-1', 'v2', async () => {});
    expect(coordinator.isNewest('image-1', 'v1')).toBe(false);
    expect(coordinator.isNewest('image-1', 'v2')).toBe(true);

    gate.resolve();
    await first;
  });

  it('keeps the final accepted pointer authoritative after older work drains', async () => {
    const coordinator = new MediaVersionSelectionCoordinator<string>();
    const first = coordinator.request('image-1', 'v1', async () => {});
    void coordinator.request('image-1', 'v2', async () => {});
    await first;

    // `isNewest` answers the queue question; it cannot drive UI authority,
    // because the final accepted pointer is no longer pending once drained.
    expect(coordinator.isNewest('image-1', 'v2')).toBe(true);
    expect(coordinator.isLatestRequested('image-1', 'v1')).toBe(false);
    expect(coordinator.isLatestRequested('image-1', 'v2')).toBe(true);
  });
});
