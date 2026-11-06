import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';

describe('batch position persistence', () => {
  it('queues every moved media node for the positions endpoint', () => {
    const source = readFileSync(resolve(__dirname, 'CanvasView.tsx'), 'utf8');
    const block = source.slice(
      source.indexOf('const applyPositionPatch = useCallback'),
      source.indexOf('// 对齐 / 分布：基于 RF 实测宽高'),
    );
    expect(block).toContain('const moved: CanvasMediaNode[] = [];');
    expect(block).toContain('moved.push(movedNode);');
    expect(block).toContain('persistMediaPositions(next, moved);');
    expect(block).not.toContain('persistMediaPositions(next);');
  });
});
