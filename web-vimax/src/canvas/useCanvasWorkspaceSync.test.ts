import {describe, expect, it} from 'vitest';

import {shouldReadTaskLedger} from './useCanvasWorkspaceSync';

describe('canvas workspace task ledger decision', () => {
  it('reads the ledger for an active card even when task center is closed', () => {
    expect(shouldReadTaskLedger({
      taskCenterOpen: false,
      busyNodeIds: new Set(['node-1']),
      optimisticCount: 0,
    })).toBe(true);
  });

  it('skips the ledger when no visible task surface needs it', () => {
    expect(shouldReadTaskLedger({
      taskCenterOpen: false,
      busyNodeIds: new Set(),
      optimisticCount: 0,
    })).toBe(false);
  });

  it('reads the ledger while a local submit has not persisted yet', () => {
    expect(shouldReadTaskLedger({
      taskCenterOpen: false,
      busyNodeIds: new Set(),
      optimisticCount: 1,
    })).toBe(true);
  });
});
