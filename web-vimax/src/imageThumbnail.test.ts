import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';

const canvasSource = readFileSync(
  resolve(__dirname, 'CanvasView.tsx'),
  'utf8',
);
const apiSource = readFileSync(
  resolve(__dirname, 'api.ts'),
  'utf8',
);

describe('image thumbnail projection', () => {
  it('uses the persisted thumbnail for image cards with original retry fallback', () => {
    expect(canvasSource).toContain(
      'src={mediaRetryUrl || node.thumbUrl || node.mediaUrl}',
    );
  });

  it('maps thumbUrl for both image and video media nodes', () => {
    expect(apiSource).toContain(
      "thumbUrl: (kind === 'video' || kind === 'image') && normalizedMediaUrl",
    );
  });
});
