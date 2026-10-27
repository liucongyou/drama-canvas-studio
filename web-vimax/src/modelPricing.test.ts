import {describe, expect, it} from 'vitest';
import type {CanvasMediaNode, ModelSpec} from './api';
import {generationCostAmount, generationCostHint, modelPricingNote} from './modelPricing';

function videoModel(pricing: ModelSpec['pricing']): ModelSpec {
  return {
    id: 'video-priced',
    providerId: 'provider',
    capability: 'video',
    label: 'Video',
    remoteModel: 'video',
    adapter: 'ark_video',
    description: '',
    badge: '',
    enabled: true,
    defaultParams: {},
    params: {},
    limits: {},
    pricing,
  };
}

function node(params: CanvasMediaNode['params']): CanvasMediaNode {
  return {
    id: 'node',
    kind: 'video',
    title: 'video',
    prompt: '',
    x: 0,
    y: 0,
    updatedAt: new Date().toISOString(),
    params,
  };
}

describe('model pricing', () => {
  it('uses model, resolution and duration instead of a provider-wide rate', () => {
    const model = videoModel({
      currency: 'CNY',
      video: {
        unit: 'second',
        rates: {'480P': 0.462, '720P': 0.994, '1080P': 2.478},
        ratesWithVideo: {'480P': 0.506, '720P': 1.088, '1080P': 2.712},
        noteWithVideo: '含视频输入价格',
      },
    });
    expect(generationCostHint(node({resolution: '480P', duration: 15}), model)).toBe('≈¥6.93');
    expect(generationCostAmount(node({resolution: '480P', duration: 15}), model)).toBeCloseTo(6.93);
    expect(generationCostHint(node({resolution: '1080P', duration: 15}), model)).toBe('≈¥37.17');
    expect(generationCostHint(node({resolution: '480P', duration: 15}), model, {hasVideoInput: true})).toBe('≈¥7.59');
    expect(modelPricingNote(model, true)).toBe('含视频输入价格');
  });

  it('does not invent a price when a model has no verified rule', () => {
    const model = {...videoModel({}), capability: 'video' as const};
    expect(generationCostHint(node({resolution: '1080P', duration: 15}), model)).toBe('按供应商账单');
    expect(modelPricingNote(model)).toContain('实际费用以供应商账单为准');
  });

  it('supports fixed per-video pricing', () => {
    const model = videoModel({
      currency: 'USD',
      video: {unit: 'video', fixed: 1.25},
    });
    expect(generationCostHint(node({resolution: '720P', duration: 15, count: 2}), model)).toBe('≈$2.50');
  });
});
