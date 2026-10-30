import {describe, expect, it} from 'vitest';
import {
  generationDurationLabel,
  generationPhaseLabel,
  generationProgressLabel,
  hasTrustedProviderProgress,
  latestTaskByNode,
} from './generationPresentation';
import type {GenerationTask} from './api';

describe('generation presentation', () => {
  it('treats stage-only values as non-progress', () => {
    const now = Date.parse('2026-10-30T00:00:20Z');
    const changedAt = '2026-10-30T00:00:10Z';
    expect(hasTrustedProviderProgress('submitting', 10, changedAt, now)).toBe(false);
    expect(hasTrustedProviderProgress('queued', 58, changedAt, now)).toBe(false);
    expect(hasTrustedProviderProgress('running', 0, changedAt, now)).toBe(false);
    expect(hasTrustedProviderProgress('running', 100, changedAt, now)).toBe(false);
    expect(hasTrustedProviderProgress('running', 58, changedAt, now)).toBe(true);
  });

  it('removes stale percent suffixes from stage text', () => {
    expect(generationPhaseLabel('生成中 10%')).toBe('生成中');
    expect(generationPhaseLabel('生成中 58%')).toBe('生成中');
    expect(generationPhaseLabel('正在连接供应商')).toBe('正在连接供应商');
  });

  it('renders exactly one percentage in progress labels', () => {
    const now = Date.parse('2026-10-30T00:00:20Z');
    const changedAt = '2026-10-30T00:00:10Z';
    expect(generationProgressLabel('生成中 10%', 'running', 10, changedAt, now)).toBe('生成中 10%');
    expect(generationProgressLabel('生成中', 'running', 58, changedAt, now)).toBe('生成中 58%');
    expect(generationProgressLabel('排队中 10%', 'queued', 10, changedAt, now)).toBe('生成中');
    expect(generationPhaseLabel('排队中')).toBe('生成中');
  });

  it('hides a provider percentage that has stopped moving', () => {
    const now = Date.parse('2026-10-30T01:00:00Z');
    const stale = '2026-10-30T00:00:00Z';
    expect(generationProgressLabel('生成中 10%', 'running', 10, stale, now)).toBe('生成中');
    expect(generationProgressLabel('生成中 10%', 'running', 10, undefined, now)).toBe('生成中');
  });

  it('uses a terminal label for completed task elapsed time', () => {
    const task: GenerationTask = {
      id: 'task',
      project_id: 'project',
      node_id: 'node',
      title: '段1',
      kind: 'video',
      status: 'succeeded',
      created_at: '2026-10-30T00:00:00.000Z',
      started_at: '2026-10-30T00:00:10.000Z',
      finished_at: '2026-10-30T00:01:10.000Z',
    };
    expect(generationDurationLabel(task)).toBe('完成于 1:00');
  });

  it('selects the newest task for a node regardless of API array order', () => {
    const tasks: GenerationTask[] = [
      {
        id: 'new',
        project_id: 'project',
        node_id: 'node',
        title: '段1',
        kind: 'video',
        status: 'succeeded',
        created_at: '2026-10-30T15:24:05.714Z',
        updated_at: '2026-10-30T16:01:26.574Z',
      },
      {
        id: 'old',
        project_id: 'project',
        node_id: 'node',
        title: '段1',
        kind: 'video',
        status: 'failed',
        created_at: '2026-10-30T15:00:00.000Z',
        updated_at: '2026-10-30T15:01:00.000Z',
      },
    ];

    expect(latestTaskByNode(tasks).get('node')?.id).toBe('new');
    expect(latestTaskByNode([...tasks].reverse()).get('node')?.id).toBe('new');
  });
});
