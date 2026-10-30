import {describe, expect, it} from 'vitest';
import {
  generationDurationLabel,
  generationPhaseLabel,
  generationProgressLabel,
  hasTrustedProviderProgress,
} from './generationPresentation';
import type {GenerationTask} from './api';

describe('generation presentation', () => {
  it('treats stage-only values as non-progress', () => {
    expect(hasTrustedProviderProgress('submitting', 10)).toBe(false);
    expect(hasTrustedProviderProgress('queued', 58)).toBe(false);
    expect(hasTrustedProviderProgress('running', 0)).toBe(false);
    expect(hasTrustedProviderProgress('running', 100)).toBe(false);
    expect(hasTrustedProviderProgress('running', 58)).toBe(true);
  });

  it('removes stale percent suffixes from stage text', () => {
    expect(generationPhaseLabel('生成中 10%')).toBe('生成中');
    expect(generationPhaseLabel('生成中 58%')).toBe('生成中');
    expect(generationPhaseLabel('正在连接供应商')).toBe('正在连接供应商');
  });

  it('renders exactly one percentage in progress labels', () => {
    expect(generationProgressLabel('生成中 10%', 'running', 10)).toBe('生成中 10%');
    expect(generationProgressLabel('生成中', 'running', 58)).toBe('生成中 58%');
    expect(generationProgressLabel('排队中 10%', 'queued', 10)).toBe('生成中');
    expect(generationPhaseLabel('排队中')).toBe('生成中');
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
});
