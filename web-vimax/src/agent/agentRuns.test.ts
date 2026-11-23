import {describe, expect, it} from 'vitest';

import {normalizeAgentRun, normalizeAgentRuns, upsertAgentRun} from './agentRuns';

describe('agent run response normalization', () => {
  it('drops malformed list entries instead of exposing undefined ids', () => {
    const runs = normalizeAgentRuns([
      {id: 'run-1', status: 'done'},
      null,
      undefined,
      {status: 'failed'},
      {id: 'run-1', status: 'running'},
      {id: ' run-2 ', status: 'waiting_user'},
    ]);

    expect(runs.map((run) => run.id)).toEqual(['run-1', 'run-2']);
    expect(runs[0].messages).toEqual([]);
    expect(runs[0].artifacts).toEqual([]);
  });

  it('rejects a malformed single run', () => {
    expect(normalizeAgentRun(undefined)).toBeNull();
    expect(normalizeAgentRun({status: 'done'})).toBeNull();
  });

  it('upserts safely even when existing state already contains undefined', () => {
    const runs = upsertAgentRun(
      [undefined, {id: 'run-old'}, null],
      {id: 'run-new', status: 'queued'},
    );

    expect(runs.map((run) => run.id)).toEqual(['run-new', 'run-old']);
  });
});
