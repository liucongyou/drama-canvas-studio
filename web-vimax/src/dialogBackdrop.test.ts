import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';

const source = readFileSync(resolve(__dirname, 'GDialog.tsx'), 'utf8');

describe('dialog backdrop dismissal', () => {
  it('closes only when the mask itself is pressed', () => {
    expect(source).toMatch(
      /className="gdialog-mask"[\s\S]*onMouseDown=\{\(e\) => \{[\s\S]*e\.target === e\.currentTarget[\s\S]*close\(isPrompt \? null : false\)/,
    );
  });

  it('does not attach backdrop dismissal to the dialog content', () => {
    expect(source).not.toMatch(
      /className=\{`gdialog has-icon[\s\S]*onMouseDown=\{/,
    );
  });
});
