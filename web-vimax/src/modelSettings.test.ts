import {describe, expect, it} from 'vitest';
import {
  adaptersForCapability,
  defaultAdapterForCapability,
  defaultAdapterForProvider,
  modelDraft,
  parameterRowsFromDraft,
  pricingRowsFromDraft,
  rowsToParamsJson,
  pricingJsonFromRows,
  providerIconFileError,
  slugifyProviderId,
  slugifyModelId,
  providerIdFromUrl,
  suggestProviderId,
  suggestModelId,
  uniqueIdSuffix,
} from './ModelSettings';

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

  it('keeps adapter choices compatible with the selected capability', () => {
    expect(adaptersForCapability('image').map((item) => item.value)).toEqual([
      'ark_image',
      'newapi_image',
      'agnes_image',
      'openai_image',
      'fourstoken_image',
    ]);
    expect(adaptersForCapability('video').map((item) => item.value)).toEqual([
      'ark_video',
      'canvaspro_video',
      'openai_video',
      'agnes_video',
      'fourstoken_video',
      'fourstoken_newapi_video',
    ]);
    expect(defaultAdapterForCapability('image')).toBe('ark_image');
    expect(adaptersForCapability('image').some((item) => item.value === 'canvaspro_video')).toBe(false);
  });

  it('picks the adapter for the provider rather than the first global adapter', () => {
    expect(defaultAdapterForProvider('image', 'agnes-ai')).toBe('agnes_image');
    expect(defaultAdapterForProvider('video', 'agnes-ai')).toBe('agnes_video');
    expect(defaultAdapterForProvider('image', 'new-apl')).toBe('newapi_image');
    expect(defaultAdapterForProvider('video', 'new-apl')).toBe('canvaspro_video');
    expect(defaultAdapterForProvider('image', '4stoken')).toBe('fourstoken_image');
    expect(defaultAdapterForProvider('video', '4stoken')).toBe('fourstoken_video');
    expect(defaultAdapterForProvider('video', 'other-relay')).toBe('openai_video');
    expect(defaultAdapterForProvider('video', 'ark')).toBe('ark_video');
  });

  it('accepts only supported provider icon files up to 5MB', () => {
    expect(providerIconFileError({name: 'logo.png', type: 'image/png', size: 1024})).toBe('');
    expect(providerIconFileError({name: 'logo.webp', type: 'image/webp', size: 1024})).toBe('');
    expect(providerIconFileError({name: 'logo.svg', type: 'image/svg+xml', size: 1024}))
      .toContain('PNG');
    expect(providerIconFileError({name: 'logo.png', type: 'image/png', size: 5 * 1024 * 1024 + 1}))
      .toContain('5MB');
    expect(providerIconFileError({name: 'logo.png', type: 'image/png', size: 0}))
      .toContain('为空');
  });

  it('derives clean provider IDs from English names', () => {
    expect(slugifyProviderId('Sand AI')).toBe('sand-ai');
    expect(slugifyProviderId('  TeamRouter  ')).toBe('teamrouter');
    expect(slugifyProviderId('Agnes_Ai@v2')).toBe('agnes_ai-v2');
    expect(slugifyProviderId('OpenAI Compatible')).toBe('openai-compatible');
    expect(slugifyProviderId('')).toBe('');
  });

  it('falls back to the API host when the provider name is Chinese', () => {
    expect(suggestProviderId('火山方舟', 'https://ark.cn-beijing.volces.com/api/v3'))
      .toBe('ark-cn-beijing-volces-com');
    expect(providerIdFromUrl('platform.sand.ai')).toBe('platform-sand-ai');
    expect(providerIdFromUrl('https://www.example.com/v1')).toBe('example-com');
    expect(providerIdFromUrl('not a url at all')).toBe('not-a-url-at-all');
  });

  it('derives model IDs from remote model first, then capability and label', () => {
    expect(slugifyModelId('doubao-seedance-2-5-260628')).toBe('doubao-seedance-2-5-260628');
    expect(suggestModelId('doubao-seedance-2-5-260628', '', 'video'))
      .toBe('doubao-seedance-2-5-260628');
    expect(suggestModelId('', 'Seedance 视频', 'video')).toBe('video-seedance');
    expect(suggestModelId('', '', 'image')).toBe('image');
    expect(suggestModelId('Video.Model_1', 'x', 'video')).toBe('video.model_1');
  });

  it('appends an incrementing suffix so auto IDs never collide', () => {
    expect(uniqueIdSuffix('sand-ai', [])).toBe('sand-ai');
    expect(uniqueIdSuffix('sand-ai', ['sand-ai'])).toBe('sand-ai-2');
    expect(uniqueIdSuffix('sand-ai', ['sand-ai', 'sand-ai-2'])).toBe('sand-ai-3');
    expect(uniqueIdSuffix('', ['sand-ai'])).toBe('');
    const long = 'a'.repeat(60);
    const suffixed = uniqueIdSuffix(long, [long], 48);
    expect(suffixed.endsWith('-2')).toBe(true);
    expect(suffixed.length).toBeLessThanOrEqual(48);
  });
});
