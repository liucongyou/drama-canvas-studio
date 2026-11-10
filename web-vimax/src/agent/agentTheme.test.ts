import {readFileSync} from 'node:fs';
import {describe, expect, it} from 'vitest';

const css = readFileSync(new URL('../agent-dock.css', import.meta.url), 'utf8');

describe('AgentDock theme polish', () => {
  it('uses theme variables for panel surfaces and accents', () => {
    expect(css).toContain('background:var(--v-surface');
    expect(css).toContain('border-color:var(--v-accent');
    expect(css).toContain('color:var(--v-text');
  });

  it('keeps resize and close animations', () => {
    expect(css).toContain('.agent-resize::after');
    expect(css).toContain('.agent-panel.is-closing');
    expect(css).toContain('agentPanelOut');
  });
});
