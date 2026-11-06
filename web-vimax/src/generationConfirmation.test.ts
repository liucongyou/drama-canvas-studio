import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';

const source = readFileSync(resolve(__dirname, 'CanvasView.tsx'), 'utf8');

describe('paid generation confirmation', () => {
  it('requires confirmation at the shared single-node submit path', () => {
    const block = source.slice(
      source.indexOf('const onGenerateMediaNode = useCallback'),
      source.indexOf('// 段级绑定资产', source.indexOf('const onGenerateMediaNode = useCallback')),
    );
    expect(block).toContain('if (!source?.confirmed)');
    expect(block).toContain('await dialog.confirm({');
    expect(block).toContain('提交后会调用供应商并可能产生费用');
  });

  it('does not show one dialog per item in batch generation', () => {
    const block = source.slice(
      source.indexOf('function BatchGenerateDialog'),
      source.indexOf('function CanvasViewInner'),
    );
    expect(block).toContain("title: '确认批量生成？'");
    expect(block).toContain('confirmed: true');
  });

  it('skips a second confirmation when submitting from the prompt modal', () => {
    expect(source).toContain("entry: 'prompt-modal'");
    expect(source).toContain('confirmed: true');
  });
});
