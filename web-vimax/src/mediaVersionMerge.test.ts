import {describe, expect, it} from 'vitest';
import {
  GENERATION_OWNED_MEDIA_PARAMS,
  mediaVersionList,
  mediaPatchMetadataForStorage,
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
  it('binds the current image thumbnail to the URL-matched duplicate id', () => {
    const versions = mediaVersionList(node({
      kind: 'image',
      mediaUrl: '/uploads/local.png',
      thumbUrl: '/uploads/local-thumb.jpg',
      params: {
        activeVersionId: 'dup',
        mediaVersionIndex: 1,
        mediaVersions: [
          {versionId: 'dup', url: 'https://provider.example/expired.png'},
          {versionId: 'dup', url: '/uploads/local.png'},
        ],
      },
    }));

    expect(versions[0].thumbUrl).toBeFalsy();
    expect(versions[1].thumbUrl).toBe('/uploads/local-thumb.jpg');
  });

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

  it('keeps the poster bound when a video version selection is pending save', () => {
    const local = node({
      kind: 'video',
      mediaUrl: '/uploads/v1.mp4',
      thumbUrl: '/uploads/v1.jpg',
      _mediaVersionSelection: true,
      params: {
        activeVersionId: 'v1',
        mediaVersionIndex: 0,
        mediaVersions: [
          {versionId: 'v1', url: '/uploads/v1.mp4', thumbUrl: '/uploads/v1.jpg'},
          {versionId: 'v2', url: '/uploads/v2.mp4', thumbUrl: '/uploads/v2.jpg'},
        ],
      },
    });
    const remote = node({
      kind: 'video',
      mediaUrl: '/uploads/v2.mp4',
      thumbUrl: '/uploads/v2.jpg',
      params: {
        activeVersionId: 'v2',
        mediaVersionIndex: 1,
        mediaVersions: [
          {versionId: 'v1', url: '/uploads/v1.mp4', thumbUrl: '/uploads/v1.jpg'},
          {versionId: 'v2', url: '/uploads/v2.mp4', thumbUrl: '/uploads/v2.jpg'},
        ],
      },
    });

    const merged = preserveSelectedMediaVersion(local, remote);
    expect(merged.mediaUrl).toBe('/uploads/v1.mp4');
    expect(merged.thumbUrl).toBe('/uploads/v1.jpg');
    expect(merged.params?.activeVersionId).toBe('v1');
  });

});


describe('mediaPatchMetadataForStorage', () => {
  it('sends the active version pointer while excluding generation-owned version lists', () => {
    const selected = node({
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

    const metadata = mediaPatchMetadataForStorage(selected);
    expect(metadata._mediaVersionSelection).toBe(true);
    expect(metadata.content).toBe('/uploads/v2.jpg');
    expect(metadata.params).toMatchObject({
      activeVersionId: 'v2',
      mediaVersionIndex: 1,
    });
    expect(GENERATION_OWNED_MEDIA_PARAMS.has('mediaVersions')).toBe(true);
  });

  it('does not let an ordinary edit submit a stale version pointer', () => {
    const selected = node({
      mediaUrl: '/uploads/v2.jpg',
      params: {
        activeVersionId: 'v2',
        mediaVersionIndex: 1,
        mediaVersions: [
          {versionId: 'v1', url: '/uploads/v1.jpg'},
          {versionId: 'v2', url: '/uploads/v2.jpg'},
        ],
        ratio: '16:9',
      },
    });

    const metadata = mediaPatchMetadataForStorage(selected);
    expect(metadata._mediaVersionSelection).toBeUndefined();
    expect(metadata.content).toBeUndefined();
    expect(metadata.params).toMatchObject({ratio: '16:9'});
  });
});
