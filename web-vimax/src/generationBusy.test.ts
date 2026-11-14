import {describe, expect, it} from 'vitest';
import {
  activeGenerationNodeIds,
  mediaGenerationVisualState,
  preserveActiveGenerationStatus,
} from './generationPresentation';
import type {GenerationTask} from './api';

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

  it('does not keep a completed node busy while a sibling is still generating', () => {
    const tasks: GenerationTask[] = [
      {
        id: 'done-task',
        project_id: 'project',
        node_id: 'node-done',
        title: '段2',
        kind: 'video',
        status: 'succeeded',
        created_at: '2026-11-14T03:38:28.056Z',
        updated_at: '2026-11-14T03:40:57.345Z',
      },
      {
        id: 'running-task',
        project_id: 'project',
        node_id: 'node-running',
        title: '段3',
        kind: 'video',
        status: 'running',
        created_at: '2026-11-14T03:48:20.538Z',
        updated_at: '2026-11-14T03:49:00.000Z',
      },
    ];

    const busyNodeIds = activeGenerationNodeIds(tasks);
    expect([...busyNodeIds]).toEqual(['node-running']);
    expect(preserveActiveGenerationStatus({
      id: 'node-done',
      status: 'done',
    }, busyNodeIds)).toMatchObject({
      id: 'node-done',
      status: 'done',
    });
  });
});
