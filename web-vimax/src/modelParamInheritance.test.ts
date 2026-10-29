import {describe, expect, it} from 'vitest';
import type {ModelSpec} from './api';
import {
  inheritedGenerationParams,
  inheritModelParams,
  migrateUnavailableModelParams,
} from './modelParamInheritance';

const agnes: ModelSpec = {
  id: 'agnes-video-2.5-flash',
  providerId: 'agnes-ai',
  capability: 'video',
  label: 'Agnes Video 2.5 Flash',
  remoteModel: 'agnes-video-2.5-flash',
  adapter: 'agnes_video',
  description: '',
  badge: '',
  enabled: true,
  defaultParams: {ratio: '16:9', resolution: '720P', duration: 5},
  params: {
    ratio: {type: 'select', options: ['16:9', '9:16']},
    resolution: {type: 'select', options: ['720P']},
    duration: {type: 'range', min: 4, max: 12},
  },
  limits: {},
  pricing: {},
};

describe('inheritModelParams', () => {
  it('keeps compatible user parameters when switching models', () => {
    const result = inheritModelParams(
      {ratio: '9:16', resolution: '720P', duration: 5},
      agnes,
    );
    expect(result.ratio).toBe('9:16');
    expect(result.resolution).toBe('720P');
    expect(result.duration).toBe(5);
  });

  it('corrects only the unsupported value to the model limit', () => {
    const result = inheritModelParams(
      {ratio: '9:16', resolution: '480P', duration: 15},
      agnes,
    );
    expect(result.ratio).toBe('9:16');
    expect(result.resolution).toBe('720P');
    expect(result.duration).toBe(12);
  });

  it('fills defaults only when a value was never set', () => {
    const result = inheritModelParams({}, agnes);
    expect(result.ratio).toBe('16:9');
    expect(result.resolution).toBe('720P');
    expect(result.duration).toBe(5);
  });

  it('does not persist defaults while a model remains available', () => {
    const current = {
      provider: 'agnes-ai',
      model: 'agnes-video-2.5-flash',
      ratio: '9:16',
      resolution: '720P',
      duration: 5,
    };
    expect(migrateUnavailableModelParams(current, agnes, 'video')).toEqual(current);
  });

  it('replaces only unsupported values when migrating an unavailable model', () => {
    const result = migrateUnavailableModelParams(
      {model: 'removed-video', ratio: '9:16', resolution: '480P', duration: 15},
      agnes,
      'video',
    );
    expect(result.model).toBe('agnes-video-2.5-flash');
    expect(result.ratio).toBe('9:16');
    expect(result.resolution).toBe('720P');
    expect(result.duration).toBe(12);
    expect(result.guidance).toBeUndefined();
  });

  it('keeps the explicit submit snapshot for 9 by 16', () => {
    const result = inheritedGenerationParams(
      {ratio: '9:16', resolution: '720P', duration: 5},
      agnes,
    );
    expect(result.ratio).toBe('9:16');
    expect(result.resolution).toBe('720P');
    expect(result.duration).toBe(5);
  });
});
