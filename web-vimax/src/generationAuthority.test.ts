import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';

describe('generation authority boundary', () => {
  const source = readFileSync(resolve(__dirname, 'CanvasView.tsx'), 'utf8');
  const api = readFileSync(resolve(__dirname, 'api.ts'), 'utf8');

  it('does not derive terminal card state from a task snapshot', () => {
    expect(source).toContain(
      'The task ledger is display history for the task center',
    );
    expect(source).toContain(
      'workspace-nodes is the card\'s sole state authority',
    );
  });

  it('does not manufacture an uncertain card from a submission exception', () => {
    const start = source.indexOf(
      'The task row is the only outcome authority.',
    );
    const block = source.slice(start, start + 700);
    expect(block).toContain('setToast(`生成请求发送失败');
    expect(block).not.toContain('providerTaskStatus');
    expect(block).not.toContain('updateMediaNode(');
  });

  it('keeps one submission path for media nodes', () => {
    expect(api).toContain('Media nodes are submitted exactly once');
    expect(api).toContain('return node.type === \'text\'');
    expect(api).not.toContain('waitingForBackend');
  });
});
