import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';

describe('media card pointer contract', () => {
  const source = readFileSync(resolve(__dirname, 'CanvasView.tsx'), 'utf8');

  it('opens the media modal on a single click, not the second click of a double click', () => {
    expect(source).toContain('if (e.detail >= 2) return;');
    expect(source).toContain("if (!editingText) onEdit(node);");
  });

  it('exposes explicit hover preview controls without delaying single-click editing', () => {
    expect(source).toContain('className="canvas-media-card-menu"');
    expect(source).toContain('onZoom(node.mediaUrl!);');
    expect(source).toContain('toggleVideoFullscreen();');
  });

  it('prevents the browser native context menu on the canvas', () => {
    expect(source).toContain(
      "document.addEventListener('contextmenu', suppressNativeMenu, true)",
    );
  });
});
