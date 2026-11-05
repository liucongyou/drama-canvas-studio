import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';

const canvas = readFileSync(resolve(__dirname, 'CanvasView.tsx'), 'utf8');
const refinements = readFileSync(resolve(__dirname, 'ui-refinements.css'), 'utf8');

describe('video cover loading state', () => {
  it('shows the shared image loading placeholder before the video cover paints', () => {
    expect(canvas).toMatch(/function VideoCoverImage\(/);
    expect(canvas).toMatch(
      /!loaded && <span className="canvas-image-loading" aria-hidden="true" \/>/,
    );
    expect(canvas).toMatch(
      /<VideoCoverImage\s+src=\{videoCoverUrl\}\s+onFailure=\{\(\) => setVideoCoverUrl\(''\)\}/s,
    );
    expect(canvas).toMatch(
      /!videoSourceReady && !videoCoverUrl && !videoCoverUnavailable[\s\S]*canvas-video-cover-pending/,
    );
    expect(canvas).not.toMatch(/fallbackTimer/);
    expect(canvas).toMatch(/setVideoCoverUnavailable\(true\)/);
    expect(canvas).toMatch(/setVideoCoverUnavailable\(false\)/);
    expect(canvas).toMatch(/canvas-video-cover-fallback/);
  });

  it('keeps the placeholder visible until the poster image has loaded', () => {
    expect(refinements).toMatch(/\.canvas-video-cover-frame\s*\{/);
    expect(refinements).toMatch(
      /\.canvas-video-cover-frame > \.canvas-image-loading\s*\{/,
    );
    expect(refinements).toMatch(
      /\.canvas-video-cover-frame > \.canvas-video-lazy-poster\s*\{[^}]*opacity:\s*0/s,
    );
    expect(refinements).toMatch(
      /\.canvas-video-cover-frame\.is-loaded > \.canvas-video-lazy-poster\s*\{[^}]*opacity:\s*1/s,
    );
    expect(refinements).toMatch(/\.canvas-video-cover-pending\s*\{/);
    expect(refinements).toMatch(/\.canvas-video-cover-fallback\s*\{/);
  });
});
