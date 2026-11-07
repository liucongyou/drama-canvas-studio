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
      /!videoFrameReady && !videoCoverUrl && !videoCoverUnavailable[\s\S]*canvas-video-cover-pending/,
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

  it('keeps the persisted cover until the video paints a real frame', () => {
    expect(canvas).toMatch(
      /const videoCoverVisible = node\.kind === 'video'[\s\S]*&& !videoFrameReady/,
    );
    expect(canvas).toMatch(/\{videoCoverVisible && \(/);
    expect(canvas).toMatch(
      /!videoFrameReady && !videoCoverUrl && !videoCoverUnavailable[\s\S]*canvas-video-cover-pending/,
    );
    expect(canvas).toMatch(
      /!videoFrameReady && !videoCoverUrl && videoCoverUnavailable[\s\S]*canvas-video-cover-fallback/,
    );
    expect(canvas).toMatch(/onPlaying=\{\(\) => setVideoFrameReady\(true\)\}/);
  });

  it('keeps video controls above every cover layer', () => {
    expect(refinements).toMatch(
      /\.canvas-video-center-play,\s*\.canvas-video-controls\s*\{[^}]*z-index:\s*4/s,
    );
    expect(refinements).toMatch(
      /\.canvas-video-cover-frame,\s*\.canvas-video-cover-pending,\s*\.canvas-video-cover-fallback\s*\{[^}]*pointer-events:\s*none/s,
    );
  });

  it('does not turn a card click into video playback', () => {
    expect(canvas).not.toMatch(
      /onClick=\{\(event\) => \{[\s\S]{0,240}closest\('button, input, \.canvas-video-controls'\)[\s\S]{0,160}toggleVideoPlayback\(\)/,
    );
    expect(canvas).toMatch(
      /className="canvas-video-center-play nodrag"[\s\S]{0,500}toggleVideoPlayback\(\)/,
    );
  });
});
