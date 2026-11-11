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

  it('lets short user messages stay on one line without overflowing long text', () => {
    expect(css).toMatch(/\.agent-msg\.is-user\{[^}]*width:max-content/);
    expect(css).toMatch(/\.agent-msg\.is-user\{[^}]*max-width:min\(94%,520px\)/);
    const noAvatarBody = css.match(/\.agent-msg\.is-user\.no-avatar \.agent-msg-body\{([^}]*)\}/)?.[1] || '';
    const declarations = noAvatarBody.split(';').map((item) => item.trim());
    expect(declarations).toContain('width:auto');
    expect(declarations).toContain('max-width:100%');
    expect(declarations).not.toContain('width:100%');
  });

  it('keeps the panel edge clean and error actions vivid', () => {
    expect(css).toContain('--agent-danger:#ff4f7b');
    expect(css).toMatch(/\.agent-panel\{[^}]*box-shadow:inset/);
    expect(css).not.toContain('box-shadow:-24px 0 70px');
    expect(css).toContain('color:var(--agent-danger,#ff4f7b)');
    expect(css).toMatch(/\.agent-note-actions button\+button\{[^}]*color:var\(--v-accent/);
  });

  it('keeps the floating AI entry on theme tokens', () => {
    expect(css).toMatch(/\.agent-fab\{[^}]*background:var\(--v-surface/);
    expect(css).toMatch(/\.agent-fab\{[^}]*border:1px solid var\(--v-border-strong/);
    expect(css).toMatch(/\.agent-fab svg\{[^}]*color:var\(--v-accent/);
    expect(css).not.toContain('rgba(78,161,255,.18)');
  });

  it('renders live model output with a theme-aware streaming block', () => {
    expect(css).toContain('.agent-live-draft');
    expect(css).toContain('animation:agentLiveCaret');
    expect(css).toMatch(/\.agent-live-draft\{[^}]*background:var\(--v-canvas/);
  });

  it('keeps the running status strip transparent and borderless', () => {
    expect(css).toMatch(/\.agent-progress\{[^}]*display:flex/);
    expect(css).toMatch(/\.agent-progress\{[^}]*border:0!important/);
    expect(css).toMatch(/\.agent-progress\{[^}]*background:transparent!important/);
  });
});
