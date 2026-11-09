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
  private pending = new Map<string, {
    value: T;
    sequence: number;
    send: (value: T, sequence: number) => Promise<void>;
  }>();

  request(
    nodeId: string,
    value: T,
    send: (value: T, sequence: number) => Promise<void>,
  ): Promise<void> {
    const key = String(nodeId || '').trim();
    if (!key) return Promise.reject(new Error('切换素材版本缺少节点 ID'));
    const sequence = ++this.sequence;
    // Keep the sender next to the intent. A single global closure can be
    // overwritten by another card while this node is queued, which would make
    // one card's selection execute against a different node.
    this.pending.set(key, {value, sequence, send});
    if (this.running) return this.running;
    this.running = this.drain().finally(() => {
      this.running = null;
    });
    return this.running;
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
