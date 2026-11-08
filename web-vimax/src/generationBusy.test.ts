import {describe, expect, it} from 'vitest';
import {mediaGenerationVisualState} from './generationPresentation';

describe('generation busy projection', () => {
  it('leaves a completed media node unlocked', () => {
    expect(mediaGenerationVisualState({
      status: 'done',
      mediaUrl: '/uploads/final.png',
    })).toMatchObject({
      busy: false,
      resumableInBackground: false,
      submitLocked: false,
    });
  });

  it('keeps a task busy while it has no final media yet', () => {
    expect(mediaGenerationVisualState({
      status: 'generating',
      generationBusy: true,
    })).toMatchObject({
      busy: true,
      resumableInBackground: false,
      submitLocked: true,
    });
  });

  it('uses the same full busy mask for regeneration with previous media', () => {
    expect(mediaGenerationVisualState({
      status: 'generating',
      mediaUrl: '/uploads/old.mp4',
      generationBusy: true,
    })).toMatchObject({
      busy: true,
      resumableInBackground: false,
      submitLocked: true,
    });
  });

  it('never lets a stale task snapshot override a persisted failure', () => {
    expect(mediaGenerationVisualState({
      status: 'failed',
      mediaUrl: '/uploads/old.mp4',
      providerLive: true,
      generationBusy: true,
    })).toMatchObject({
      busy: false,
      failed: true,
      submitLocked: true,
    });
  });
});
