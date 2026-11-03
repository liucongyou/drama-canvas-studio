import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';

const css = readFileSync(
  resolve(__dirname, 'ui-refinements.css'),
  'utf8',
);
const source = readFileSync(
  resolve(__dirname, 'CanvasView.tsx'),
  'utf8',
);

describe('video overlay controls', () => {
  it('hides the centre play button by default in every state', () => {
    expect(css).toMatch(
      /\.canvas-video-center-play\{[^}]*opacity:0;[^}]*pointer-events:none/s,
    );
  });

  it('reveals the centre play button only on hover or focus', () => {
    expect(css).toMatch(
      /\.canvas-video-shell:hover \.canvas-video-center-play,\s*\.canvas-video-shell:focus-within \.canvas-video-center-play\{[^}]*opacity:/s,
    );
  });

  it('hides the progress bar by default and shows it on hover/focus', () => {
    expect(css).toMatch(
      /\.canvas-video-controls\{[^}]*opacity:0;[^}]*pointer-events:none/s,
    );
    expect(css).toMatch(
      /\.canvas-video-shell:hover \.canvas-video-controls,\s*\.canvas-video-shell:focus-within \.canvas-video-controls\{[^}]*opacity:1/s,
    );
  });

  it('no longer parks the paused button above the control bar', () => {
    expect(css).not.toMatch(/is-paused \.canvas-video-center-play/);
  });

  it('activates the video source when the card is hovered or focused', () => {
    expect(source).toMatch(
      /onPointerEnter=\{\(\) => \{[\s\S]*setVideoSourceReady\(true\)/,
    );
    expect(source).toMatch(
      /onFocusCapture=\{\(\) => \{[\s\S]*setVideoSourceReady\(true\)/,
    );
  });
});
