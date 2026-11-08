import {describe, expect, it} from 'vitest';
import {
  preserveSelectedMediaVersion,
  type CanvasMediaNode,
} from './api';


function node(overrides: Partial<CanvasMediaNode> = {}): CanvasMediaNode {
  return {
    id: 'image-1',
    kind: 'image',
    title: '场景',
    prompt: '',
    status: 'done',
    mediaUrl: '/uploads/v1.jpg',
    x: 0,
    y: 0,
    updatedAt: '2026-11-08T00:00:00Z',
    params: {
      activeVersionId: 'v1',
      mediaVersionIndex: 0,
      mediaVersions: [
        {versionId: 'v1', url: '/uploads/v1.jpg'},
        {versionId: 'v2', url: '/uploads/v2.jpg'},
      ],
    },
    ...overrides,
  };
}


describe('preserveSelectedMediaVersion', () => {
  it('keeps an in-flight local version selection during workspace refresh', () => {
    const local = node({
      mediaUrl: '/uploads/v2.jpg',
      _mediaVersionSelection: true,
      params: {
        activeVersionId: 'v2',
        mediaVersionIndex: 1,
        mediaVersions: [
          {versionId: 'v1', url: '/uploads/v1.jpg'},
          {versionId: 'v2', url: '/uploads/v2.jpg'},
        ],
      },
    });
    const remote = node({
      params: {
        activeVersionId: 'v1',
        mediaVersionIndex: 0,
        mediaVersions: [
          {versionId: 'v1', url: '/uploads/v1.jpg'},
          {versionId: 'v2', url: '/uploads/v2.jpg'},
        ],
      },
    });

    const merged = preserveSelectedMediaVersion(local, remote);
    expect(merged.params?.activeVersionId).toBe('v2');
    expect(merged.params?.mediaVersionIndex).toBe(1);
  });

  it('lets the backend pointer win when there is no local selection lock', () => {
    const local = node({
      _mediaVersionSelection: undefined,
      params: {
        activeVersionId: 'v2',
        mediaVersionIndex: 1,
        mediaVersions: [
          {versionId: 'v1', url: '/uploads/v1.jpg'},
          {versionId: 'v2', url: '/uploads/v2.jpg'},
        ],
      },
    });
    const remote = node({
      mediaUrl: '/uploads/v2.jpg',
      params: {
        activeVersionId: 'v2',
        mediaVersionIndex: 1,
        mediaVersions: [
          {versionId: 'v1', url: '/uploads/v1.jpg'},
          {versionId: 'v2', url: '/uploads/v2.jpg'},
        ],
      },
    });

    const merged = preserveSelectedMediaVersion(local, remote);
    expect(merged.params?.activeVersionId).toBe('v2');
    expect(merged.params?.mediaVersionIndex).toBe(1);
  });

  it('shows the backend-selected visible media when the local pointer lags', () => {
    const local = node({
      mediaUrl: '/uploads/v1.jpg',
      params: {
        activeVersionId: 'v2',
        mediaVersionIndex: 1,
        mediaVersions: [
          {versionId: 'v1', url: '/uploads/v1.jpg'},
          {versionId: 'v2', url: '/uploads/v2.jpg'},
        ],
      },
    });
    const remote = node({
      mediaUrl: '/uploads/v2.jpg',
      params: {
        activeVersionId: 'v2',
        mediaVersionIndex: 1,
        mediaVersions: [
          {versionId: 'v1', url: '/uploads/v1.jpg'},
          {versionId: 'v2', url: '/uploads/v2.jpg'},
        ],
      },
    });

    const merged = preserveSelectedMediaVersion(local, remote);
    expect(merged.mediaUrl).toBeUndefined();
    expect(merged.params?.activeVersionId).toBe('v2');
    expect(merged.params?.mediaVersionIndex).toBe(1);
  });

  it('uses the visible URL when the same media URL appears in many versions', () => {
    const local = node({
      mediaUrl: '/uploads/a.jpg',
      params: {
        activeVersionId: 'v3',
        mediaVersionIndex: 3,
        mediaVersions: [
          {versionId: 'v0', url: '/uploads/a.jpg'},
          {versionId: 'v1', url: '/uploads/a.jpg'},
          {versionId: 'v2', url: '/uploads/a.jpg'},
          {versionId: 'v3', url: '/uploads/a.jpg'},
        ],
      },
    });
    const remote = node({
      mediaUrl: '/uploads/a.jpg',
      params: {
        activeVersionId: 'v3',
        mediaVersionIndex: 3,
        mediaVersions: [
          {versionId: 'v0', url: '/uploads/a.jpg'},
          {versionId: 'v1', url: '/uploads/a.jpg'},
          {versionId: 'v2', url: '/uploads/a.jpg'},
          {versionId: 'v3', url: '/uploads/a.jpg'},
        ],
      },
    });

    const merged = preserveSelectedMediaVersion(local, remote);
    expect(merged.params?.mediaVersionIndex).toBe(3);
  });

  it('uses the same current version for the visible media and pointer', () => {
    const local = node({
      mediaUrl: '/uploads/v2.jpg',
      _mediaVersionSelection: true,
      params: {
        activeVersionId: 'v2',
        mediaVersionIndex: 1,
        mediaVersions: [
          {versionId: 'v1', url: '/uploads/v1.jpg'},
          {versionId: 'v2', url: '/uploads/v2.jpg'},
        ],
      },
    });
    const remote = node({
      params: {
        activeVersionId: 'v1',
        mediaVersionIndex: 0,
        mediaVersions: [
          {versionId: 'v1', url: '/uploads/v1.jpg'},
          {versionId: 'v2', url: '/uploads/v2.jpg'},
        ],
      },
    });

    const merged = preserveSelectedMediaVersion(local, remote);
    expect(merged.mediaUrl).toBe('/uploads/v2.jpg');
    expect(merged.params?.activeVersionId).toBe('v2');
  });

});
