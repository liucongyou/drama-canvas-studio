import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';


const source = readFileSync(
  resolve(__dirname, 'CanvasView.tsx'),
  'utf8',
);


describe('generation error presentation', () => {
  it('does not mark timeouts or transport failures as a definitive failure', () => {
    expect(source).toContain(
      'const definitiveFailure = shouldReleaseOperationIdAfterError(error);',
    );
    expect(source).toMatch(
      /providerTaskStatus: definitiveFailure \? 'failed' : 'uncertain'/,
    );
    expect(source).toMatch(
      /providerTaskPhase: definitiveFailure \? '生成失败' : '结果待确认'/,
    );
  });

  it('keeps the warning text visible on the card for uncertain submissions', () => {
    expect(source).toContain('提交结果未确认，可能已被供应商受理');
  });
});
