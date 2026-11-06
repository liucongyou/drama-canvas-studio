import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';

const source = readFileSync(
  resolve(__dirname, 'CanvasView.tsx'),
  'utf8',
);

describe('read-only share controls', () => {
  it('uses hand mode and disables graph editing', () => {
    expect(source).toContain(
      "const effectiveMouseMode = readOnly ? 'hand' : mouseMode;",
    );
    expect(source).toContain(
      "nodesDraggable={!readOnly && effectiveMouseMode === 'select'}",
    );
    expect(source).toContain(
      "nodesConnectable={!readOnly && effectiveMouseMode !== 'hand'}",
    );
    expect(source).toContain(
      "elementsSelectable={!readOnly && effectiveMouseMode !== 'hand'}",
    );
  });

  it('keeps only viewing actions in the prompt panel', () => {
    expect(source).toContain('editable={!readOnly}');
    expect(source).toContain('{!readOnly && <div className="jim-ref-row">');
    expect(source).toContain('{!readOnly && <div className="jim-toolbar">');
    expect(source).toContain('{!readOnly && dependenciesBlocked && (');
  });

  it('hides project administration and write-only controls', () => {
    expect(source).toContain("{!readOnly && railTab === 'eps' && (");
    expect(source).toContain('{!readOnly && selectedCount < 2 && (');
    expect(source).toContain('{!readOnly && <button type="button" className={snapGrid');
    expect(source).toContain("{!readOnly && <button type=\"button\" className={effectiveMouseMode === 'connect'");
  });
});
