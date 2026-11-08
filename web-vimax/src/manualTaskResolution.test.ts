import {describe, expect, it} from 'vitest';
import {manualTaskResolutionAvailable} from './manualTaskResolution';

const base = {
  id: 'task-1',
  project_id: 'project-1',
  node_id: 'node-1',
  title: '段1',
  kind: 'video' as const,
  created_at: '2026-11-08T00:00:00Z',
};

describe('manual task resolution', () => {
  it('requires the backend resolution capability', () => {
    expect(manualTaskResolutionAvailable({
      ...base,
      status: 'interrupted',
      phase: '结果待确认',
      outcome: {phase: 'unknown', regenerate: 'confirm'},
    })).toBe(false);
    expect(manualTaskResolutionAvailable({
      ...base,
      status: 'interrupted',
      phase: '结果待确认',
      outcome: {phase: 'unknown', regenerate: 'confirm', canResolve: true},
    })).toBe(true);
  });

  it('never exposes resolution for active or fetching tasks', () => {
    expect(manualTaskResolutionAvailable({
      ...base,
      status: 'running',
      outcome: {phase: 'running', regenerate: 'blocked', canResolve: true},
    })).toBe(false);
    expect(manualTaskResolutionAvailable({
      ...base,
      status: 'succeeded',
      outcome: {phase: 'fetching', regenerate: 'blocked', canResolve: true},
    })).toBe(false);
  });
});
