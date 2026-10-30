import {describe, expect, it} from 'vitest';
import {filterDiscoveredModels} from './modelDiscoveryFilter';

const models = [
  {remoteModel: 'text-a', capability: 'text'},
  {remoteModel: 'image-a', capability: 'image'},
  {remoteModel: 'video-a', capability: 'video'},
  {remoteModel: 'audio-a', capability: 'audio'},
  {remoteModel: 'unknown-a', capability: 'unknown'},
];

describe('model discovery type filter', () => {
  it('filters one capability at a time', () => {
    expect(filterDiscoveredModels(models, 'video', {}, '').map((item) => item.remoteModel))
      .toEqual(['video-a']);
    expect(filterDiscoveredModels(models, 'text', {}, '').map((item) => item.remoteModel))
      .toEqual(['text-a']);
  });

  it('includes manual capability choices for unknown models', () => {
    expect(filterDiscoveredModels(
      models,
      'image',
      {'unknown-a': 'image'},
      '',
    ).map((item) => item.remoteModel)).toEqual(['image-a', 'unknown-a']);
  });
});
