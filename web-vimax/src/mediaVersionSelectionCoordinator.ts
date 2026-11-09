/**
 * Serialize version selections per card and coalesce rapid clicks.
 *
 * A successful selection advances the server revision. Parallel clicks would
 * otherwise reuse a stale updatedAt, while an older response could repaint over
 * a newer choice. Only the newest queued intent matters.
 */
export class MediaVersionSelectionCoordinator<T> {
  private sequence = 0;
  private running: Promise<void> | null = null;
  private draining = false;
  private lastRequested = new Map<string, T>();
  private pending = new Map<string, {
    value: T;
    sequence: number;
    send: (value: T, sequence: number) => Promise<void>;
  }>();

  /** Return the newest intent currently known for a card, including queued work. */
  peek(nodeId: string): T | null {
    const intent = this.pending.get(String(nodeId || '').trim());
    return intent ? intent.value : null;
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
    // Keep the sender next to the intent. A single global closure can be
    // overwritten by another card while this node is queued, which would make
    // one card's selection execute against a different node.
    this.pending.set(key, {value, sequence, send});
    if (this.draining) {
      return this.running ?? Promise.resolve();
    }
    this.startDrain();
    return this.running ?? Promise.resolve();
  }

  /** True when no later accepted intent for this card supersedes the value. */
  isLatestRequested(nodeId: string, value: T): boolean {
    const key = String(nodeId || '').trim();
    if (!this.lastRequested.has(key)) return true;
    return Object.is(this.lastRequested.get(key), value);
  }

  private startDrain(): void {
    if (this.draining) return;
    this.draining = true;
    this.running = this.drain().finally(() => {
      this.draining = false;
      this.running = null;
      // A request can arrive after drain() observes an empty queue but before
      // this cleanup microtask runs. It sees `running` and waits instead of
      // starting another drain, so wake a fresh drain if that window captured
      // an intent. Without this, the last click is acknowledged but never sent.
      if (this.pending.size > 0) {
        this.startDrain();
      }
    });
  }

  private async drain(): Promise<void> {
    while (this.pending.size > 0) {
      const [nodeId, intent] = this.pending.entries().next().value as [
        string,
        {
          value: T;
          sequence: number;
          send: (value: T, sequence: number) => Promise<void>;
        },
      ];
      this.pending.delete(nodeId);
      try {
        await intent.send(intent.value, intent.sequence);
      } catch {
        // The caller owns error presentation. A later click must still run.
      }
    }
  }
}
