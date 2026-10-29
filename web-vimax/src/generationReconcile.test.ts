import {describe, expect, it} from 'vitest';
import {reconcileSucceededNode} from './generationReconcile';

describe('generation reconcile', () => {
  it('lets a succeeded task overwrite a stale failed node and restore the image url', () => {
    const node = {
      id: 'node-1',
      status: 'failed',
      mediaUrl: undefined,
      lastError: 'The read operation timed out',
    };
    const task = {
      id: 'task-1',
      node_id: 'node-1',
      status: 'succeeded',
      finished_at: '2026-10-29T14:18:05.897Z',
    };
    const remote = {
      id: 'node-1',
      status: 'done',
      mediaUrl: '/uploads/generated-1793283485-4b9c9b88cd.png',
    };
    expect(reconcileSucceededNode(node, task, remote)).toMatchObject({
      status: 'done',
      mediaUrl: '/uploads/generated-1793283485-4b9c9b88cd.png',
      lastError: undefined,
      providerTaskStatus: 'completed',
      providerTaskProgress: 100,
    });
  });

  it('does nothing when the remote node is not actually done', () => {
    expect(reconcileSucceededNode(
      {id: 'node-1', status: 'failed'},
      {id: 'task-1', node_id: 'node-1', status: 'succeeded'},
      {id: 'node-1', status: 'generating'},
    )).toBeNull();
  });
});
