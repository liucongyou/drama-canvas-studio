import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';

const source = readFileSync(
  resolve(__dirname, 'CanvasView.tsx'),
  'utf8',
);
const css = readFileSync(
  resolve(__dirname, 'ui-refinements.css'),
  'utf8',
);

describe('batch generation submission UI', () => {
  it('tracks submitted nodes so retry only processes the remaining items', () => {
    expect(source).toContain('const pendingSelected = selected.filter');
    expect(source).toContain('重试剩余 ${pendingSelected.length} 项');
    expect(source).toContain('setSubmittedIds((previous) =>');
  });

  it('reports progress, supports stopping the remainder and guards duplicate clicks', () => {
    expect(source).toContain('disabled={pendingSelected.length === 0 || batchRunning}');
    expect(source).toContain('onClick={() => {');
    expect(source).toContain('stopBatchRef.current = true');
    expect(source).toContain('role="progressbar"');
    expect(css).toMatch(/\.canvas-batch-gen-progress\{[^}]*height:3px/s);
  });

  it('uses the shared entity ID fallback instead of unguarded randomUUID', () => {
    expect(source).toContain("const batchId = newCanvasEntityId('batch');");
    expect(source).not.toContain("`batch-${Date.now()}-${crypto.randomUUID().slice(0, 8)}`");
  });
});
