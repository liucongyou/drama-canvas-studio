import {describe, expect, it} from 'vitest';

import {sanitizeTelemetry} from './telemetry';

describe('frontend telemetry sanitizer', () => {
  it('removes cookies, tokens, prompts and signed URL queries', () => {
    const clean = sanitizeTelemetry({
      message: 'https://bucket.example/video.mp4?X-Tos-Signature=secret',
      cookie: 'session=secret',
      token: 'secret',
      prompt: '完整提示词',
      projectId: 'p1',
      nodeId: 'n1',
    } as never);

    expect(clean).toMatchObject({
      message: 'https://bucket.example/video.mp4',
      projectId: 'p1',
      nodeId: 'n1',
    });
    expect(clean.cookie).toBeUndefined();
    expect(clean.token).toBeUndefined();
    expect(clean.prompt).toBeUndefined();
  });

  it('does not submit an empty message', () => {
    expect(sanitizeTelemetry({projectId: 'p1'})).toEqual({});
  });
});
