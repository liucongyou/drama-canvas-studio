/**
 * Serialize version selections per card and coalesce rapid clicks.
 *
 * Different cards use independent lanes, so a slow request for one node cannot
 * block every other card. Within one card only the newest queued intent runs
 * after the in-flight request, preserving server revision ordering.
 */
type Intent<T> = {
  value: T;
  sequence: number;
  send: (value: T, sequence: number) => Promise<void>;
};

type Lane<T> = {
  pending: Intent<T> | null;
  running: boolean;
  waiters: Array<() => void>;
};

export class MediaVersionSelectionCoordinator<T> {
  private sequence = 0;
  private lastRequested = new Map<string, T>();
  private lanes = new Map<string, Lane<T>>();

  /** Return the newest intent currently known for a card, including queued work. */
  peek(nodeId: string): T | null {
    const lane = this.lanes.get(String(nodeId || '').trim());
    return lane?.pending?.value ?? null;
  }

  /** True when no queued intent for this card supersedes the given value. */
  isNewest(nodeId: string, value: T): boolean {
    const queued = this.peek(nodeId);
    return queued === null || Object.is(queued, value);
  }

  request(
    nodeId: string,
    value: T,
    send: (value: T, sequence: number) => Promise<void>,
  ): Promise<void> {
    const key = String(nodeId || '').trim();
    if (!key) return Promise.reject(new Error('切换素材版本缺少节点 ID'));
    const sequence = ++this.sequence;
    this.lastRequested.set(key, value);
    let lane = this.lanes.get(key);
    if (!lane) {
      lane = {pending: null, running: false, waiters: []};
      this.lanes.set(key, lane);
    }
    // The sender travels with the intent. A render of another card must never
    // replace this card's closure while it waits for its own lane.
    lane.pending = {value, sequence, send};
    const completion = new Promise<void>((resolve) => {
      lane!.waiters.push(resolve);
    });
    if (!lane.running) {
      lane.running = true;
      void this.drainLane(key, lane);
    }
    return completion;
  }

  /** True when no later accepted intent for this card supersedes the value. */
  isLatestRequested(nodeId: string, value: T): boolean {
    const key = String(nodeId || '').trim();
    if (!this.lastRequested.has(key)) return true;
    return Object.is(this.lastRequested.get(key), value);
  }

  private async drainLane(key: string, lane: Lane<T>): Promise<void> {
    while (lane.pending) {
      const intent = lane.pending;
      lane.pending = null;
      try {
        await intent.send(intent.value, intent.sequence);
      } catch {
        // The caller owns error presentation. A later click must still run.
      }
    }
    lane.running = false;
    const waiters = lane.waiters.splice(0);
    for (const resolve of waiters) resolve();
    if (
      this.lanes.get(key) === lane
      && !lane.pending
      && lane.waiters.length === 0
    ) {
      this.lanes.delete(key);
    }
  }
}
