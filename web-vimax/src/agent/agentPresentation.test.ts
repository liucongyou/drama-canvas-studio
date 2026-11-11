import {describe, expect, it} from 'vitest';
import type {AgentRun} from './agentApi';
import {
  cleanRunText,
  executionDraftMessages,
  pendingImportRuns,
  transcriptMessages,
} from './agentPresentation';

function run(overrides: Partial<AgentRun> = {}): AgentRun {
  return {
    id: 'run-1',
    projectId: 'project-1',
    skillId: 'skill-1',
    skillVersion: '1',
    prompt: '生成第一集',
    status: 'waiting_user',
    error: '',
    messages: [],
    steps: [],
    stages: [],
    artifacts: [],
    ...overrides,
  };
}

describe('agent presentation', () => {
  it('keeps transcript messages visible and hides raw execution drafts', () => {
    const value = run({
      messages: [
        {id: 1, role: 'user', content: '生成第一集'},
        {id: 2, role: 'draft', content: '{"nodes": [1, 2, 3]}'},
        {id: 3, role: 'status', content: '正在校验'},
        {id: 4, role: 'assistant', content: '检查完成，可以继续。'},
      ],
    });

    expect(transcriptMessages(value).map((message) => message.content)).toEqual([
      '生成第一集',
      '检查完成，可以继续。',
    ]);
    expect(executionDraftMessages(value).map((message) => message.content)).toEqual([
      '{"nodes": [1, 2, 3]}',
    ]);
  });

  it('does not show transport status copy as an Agent reply', () => {
    expect(cleanRunText('投喂稿已通过画布预检，请在右侧确认导入。')).toBe('');
    expect(cleanRunText('生成完成，但画布预检未通过。')).toBe('');
  });

  it('finds a pending importable run across the current thread', () => {
    const failed = run({id: 'failed', status: 'failed'});
    const pending = run({
      id: 'pending',
      artifacts: [{id: 1, kind: 'canvas_feed', payload: {preview: {revision: 4}, errors: []}}],
    });
    const invalid = run({
      id: 'invalid',
      artifacts: [{id: 2, kind: 'canvas_feed', payload: {errors: [{message: 'bad'}]}}],
    });

    expect(pendingImportRuns([failed, pending, invalid]).map((item) => item.id)).toEqual(['pending']);
    expect(pendingImportRuns([pending, pending]).map((item) => item.id)).toEqual(['pending']);
  });
});
