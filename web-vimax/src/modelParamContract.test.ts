import {describe, expect, it} from 'vitest';
import type {ModelSpec} from './api';
import {validateModelParams} from './modelParamContract';

const model: ModelSpec = {
  id: '65-seedream-5-pro',
  providerId: 'new-apl',
  capability: 'image',
  label: '即梦-65-seedream-5-pro',
  remoteModel: '即梦-65-seedream-5-pro',
  adapter: 'newapi_image',
  description: '',
  badge: 'img',
  enabled: true,
  defaultParams: {ratio: '9:16', clarity: '2K'},
  params: {
    ratio: {type: 'select', options: ['1:1', '16:9', '9:16']},
    clarity: {type: 'select', options: ['1K', '2K']},
  },
  limits: {},
  pricing: {},
};

describe('model parameter contract', () => {
  it('accepts the values declared by model settings', () => {
    expect(validateModelParams({ratio: '9:16', clarity: '2K'}, model)).toEqual([]);
  });

  it('rejects a video-style value on an image model', () => {
    const issues = validateModelParams({ratio: '9:16', clarity: '480P'}, model);
    expect(issues).toHaveLength(1);
    expect(issues[0].key).toBe('clarity');
    expect(issues[0].message).toContain('1K/2K');
  });
});
