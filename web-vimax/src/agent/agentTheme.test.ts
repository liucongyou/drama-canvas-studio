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

  it('keeps user message actions out of the content height', () => {
    expect(css).toMatch(/\.agent-msg-actions\{[^}]*position:absolute/);
    expect(css).toMatch(/\.agent-msg\.is-user \.agent-msg-body\{[^}]*background:var\(--v-surface-hover/);
    expect(css).not.toMatch(/\.agent-msg\.is-user \.agent-msg-body\{[^}]*background:var\(--v-accent/);
  });
});
