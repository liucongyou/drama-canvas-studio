import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';


const source = readFileSync(
  resolve(__dirname, 'CanvasView.tsx'),
  'utf8',
);


describe('generation error presentation', () => {
  it('keeps operation-key policy without deriving card task state', () => {
    expect(source).toContain(
      'const definitiveFailure = shouldReleaseOperationIdAfterError(error);',
    );
    expect(source).not.toMatch(
      /providerTaskStatus: definitiveFailure \? 'failed' : 'uncertain'/,
    );
    expect(source).not.toMatch(
      /providerTaskPhase: definitiveFailure \? '生成失败' : '结果待确认'/,
    );
  });

  it('does not let a failed submit request override the backend card state', () => {
    expect(source).not.toContain(
      '提交结果未确认，可能已被供应商受理。系统不会自动重提，请先核对任务或账单。',
    );
  });

  it('does not reinterpret historical task errors into a new card state', () => {
    expect(source).not.toContain(
      'if (lower.includes(\'没有返回任务号\') && lower.includes(\'server_error\'))',
    );
  });

  it('does not turn accepted-submit refresh failures into uncertain results', () => {
    expect(source).toContain('const refreshTasks = async () => {');
    expect(source).toContain('const refreshNode = async () => {');
    expect(source).toMatch(
      /await Promise\.all\(\[refreshTasks\(\), refreshNode\(\)\]\);/,
    );
  });

  it('keeps an active task from being erased by a stale workspace row', () => {
    expect(source).toContain(
      'workspace-nodes is the card\'s state authority, except while its',
    );
    expect(source).toContain(
      'generationBusyIdsRef.current',
    );
  });

  it('does not write generating or cancelled states before the backend answers', () => {
    expect(source).not.toMatch(
      /updateMediaNode\(node\.id, \{\s*status: 'generating'/s,
    );
    expect(source).not.toMatch(
      /lastError: '已取消生成排队'[\s\S]{0,160}providerTaskPhase: '已取消'/,
    );
  });

  it('reconciles dependency readiness on modal open from the backend projection', () => {
    expect(source).toContain('getWorkspaceMediaNodes(session, currentEp)');
    expect(source).toContain(
      'modal uses the same current node state as the cards',
    );
    expect(source).toContain('preserveSelectedMediaVersion(local, remote)');
  });
});
