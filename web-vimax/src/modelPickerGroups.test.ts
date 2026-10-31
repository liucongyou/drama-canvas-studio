import {describe, expect, it} from 'vitest';
import {
  activeProviderGroup,
  favoriteModelsForProvider,
  groupModelsByProvider,
  pickerProviderGroups,
} from './modelPickerGroups';
import fs from 'node:fs';
import path from 'node:path';

describe('provider-grouped model picker', () => {
  const providers = [
    {id: '4stoken', name: '4SToken', icon: 'https://cdn.example/4stoken.png'},
    {id: 'ark', name: '火山方舟'},
    {id: 'agnes-ai', name: 'Agnes AI'},
  ];
  const models = [
    {id: 'video-a', providerId: '4stoken', label: 'Seedance 2.0', remoteModel: '4sdance431'},
    {id: 'image-a', providerId: 'ark', label: 'Seedream 5.0', remoteModel: 'seedream-5'},
    {id: 'audio-a', providerId: 'agnes-ai', label: 'Agnes Voice', remoteModel: 'voice'},
    {id: 'text-a', providerId: '4stoken', label: 'Text Pro', remoteModel: 'text-pro'},
  ];

  it('groups every capability under stable provider categories', () => {
    const groups = groupModelsByProvider(models, providers);
    expect(groups.map((group) => group.id)).toEqual(['4stoken', 'ark', 'agnes-ai']);
    expect(groups.map((group) => group.models.length)).toEqual([2, 1, 1]);
    expect(groups[0].icon).toBe('https://cdn.example/4stoken.png');
  });

  it('searches model and provider names without losing the provider grouping', () => {
    expect(groupModelsByProvider(models, providers, 'seedream').map((group) => ({
      id: group.id,
      models: group.models.map((model) => model.id),
    }))).toEqual([{id: 'ark', models: ['image-a']}]);
    expect(groupModelsByProvider(models, providers, '4stoken').map((group) => group.id))
      .toEqual(['4stoken']);
  });

  it('keeps unregistered providers visible instead of dropping their models', () => {
    expect(groupModelsByProvider(
      [{id: 'custom', providerId: 'future-provider', label: 'Future'}],
      providers,
    )).toEqual([{
      id: 'future-provider',
      name: 'future-provider',
      icon: '',
      models: [{id: 'custom', providerId: 'future-provider', label: 'Future'}],
    }]);
  });

  it('starts at the selected model provider or the first configured provider', () => {
    const groups = groupModelsByProvider(models, providers);
    expect(activeProviderGroup(groups, '', 'image-a')?.id).toBe('ark');
    expect(activeProviderGroup(groups, 'agnes-ai', 'image-a')?.id).toBe('agnes-ai');
    expect(activeProviderGroup(groups, '', 'missing')?.id).toBe('4stoken');
  });

  it('collects only this capability provider favorites', () => {
    expect(favoriteModelsForProvider(models.slice(0, 1), ['video-a', 'image-a', 'stale'])
      .map((model) => model.id)).toEqual(['video-a']);
  });

  it('puts favorites before provider categories only when a capability favorite exists', () => {
    const groups = groupModelsByProvider(models, providers);
    expect(pickerProviderGroups(groups, []).map((group) => group.id))
      .toEqual(['4stoken', 'ark', 'agnes-ai']);
    expect(pickerProviderGroups(groups, [{id: 'video-a'}]).map((group) => group.id))
      .toEqual(['__favorites__', '4stoken', 'ark', 'agnes-ai']);
  });

  it('keeps model picker dimensions independent from the model count', () => {
    const css = fs.readFileSync(
      path.resolve(process.cwd(), 'src/ui-refinements.css'),
      'utf8',
    );
    const single = css.match(/\.jim-pop-model\{([^}]*)\}/)?.[1] || '';
    expect(single).toMatch(/width:400px/);
    expect(single).toMatch(/height:320px/);
    expect(single).toMatch(/min-width:400px/);
    const batch = css.match(/\.canvas-batch-dropdown\.is-fixed\{([^}]*)\}/)?.[1] || '';
    expect(batch).toMatch(/height:min\(320px, calc\(100dvh - 24px\)\)/);
    const browser = css.match(/\.jim-model-browser\{([^}]*)\}/)?.[1] || '';
    expect(browser).toMatch(/height:0/);
    expect(browser).toMatch(/overflow:hidden/);
  });
});
