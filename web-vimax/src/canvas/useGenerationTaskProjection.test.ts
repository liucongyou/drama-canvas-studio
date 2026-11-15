import {describe, expect, it} from 'vitest';

import type {GenerationTask} from '../api';
import {projectGenerationState} from '../generationPresentation';

function task(
  id: string,
  nodeId: string,
  status: GenerationTask['status'],
): GenerationTask {
  return {
    id,
    project_id: 'project',
    node_id: nodeId,
    title: nodeId,
    status,
    created_at: '2026-11-15T00:00:00.000Z',
    updated_at: '2026-11-15T00:00:01.000Z',
  };
}

describe('generation task projection controller', () => {
  it('releases a completed node while its sibling keeps running', () => {
    const projection = projectGenerationState(
      [
        task('done', 'node-done', 'succeeded'),
        task('running', 'node-running', 'running'),
      ],
      new Map(),
    );

    expect([...projection.activeNodeIds]).toEqual(['node-running']);
  });

  it('retains an optimistic submit only until its exact task appears', () => {
    const optimistic = new Map([[
      'node-a',
      {
        startedAt: '2026-11-15T00:00:00.000Z',
        operationId: 'operation-a',
      },
    ]]);
    const before = projectGenerationState(
      [],
      optimistic,
      Date.parse('2026-11-15T00:00:02.000Z'),
    );
    const after = projectGenerationState(
      [{
        ...task('running', 'node-a', 'running'),
        client_operation_id: 'operation-a',
      }],
      before.retainedOptimistic,
      Date.parse('2026-11-15T00:00:03.000Z'),
    );

    expect(before.activeNodeIds.has('node-a')).toBe(true);
    expect(after.retainedOptimistic.has('node-a')).toBe(false);
    expect(after.activeNodeIds.has('node-a')).toBe(true);
  });
});
