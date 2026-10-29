import {describe, expect, it} from 'vitest';
import {modelDraft, parameterRowsFromDraft, pricingRowsFromDraft, rowsToParamsJson, pricingJsonFromRows} from './ModelSettings';

describe('model settings visual editors', () => {
  it('keeps hidden custom schema fields while editing visual parameter rows', () => {
    const draft = modelDraft({
      id: 'm1',
      providerId: 'p1',
      capability: 'image',
      label: '测试模型',
      remoteModel: 'remote',
      adapter: 'ark_image',
      description: '',
      badge: 'M',
      enabled: true,
      defaultParams: {ratio: '1:1'},
      params: {ratio: {type: 'select', options: ['1:1', '16:9'], api: 'ratio', custom: 'keep'}},
      limits: {},
    });
    const rows = parameterRowsFromDraft(draft);
    rows[0] = {...rows[0], optionsText: '1:1, 16:9, 9:16'};
    const output = rowsToParamsJson(rows);
    expect(JSON.parse(output.defaultParams).ratio).toBe('1:1');
    expect(JSON.parse(output.params).ratio).toMatchObject({
      type: 'select',
      options: ['1:1', '16:9', '9:16'],
      api: 'ratio',
      custom: 'keep',
    });
  });

  it('round-trips pricing tiers and video-input rates', () => {
    const rows = pricingRowsFromDraft({
      currency: 'CNY',
      video: {
        unit: 'second',
        rates: {'480P': 0.462, '720P': 0.994},
        ratesWithVideo: {'480P': 0.506},
        note: '官方价',
      },
    });
    expect(rows.tiers.map((tier) => tier.label)).toEqual(['480P', '720P']);
    const output = JSON.parse(pricingJsonFromRows('video', rows));
    expect(output.video.rates['720P']).toBe(0.994);
    expect(output.video.ratesWithVideo['480P']).toBe(0.506);
  });
});
