import {describe, expect, it} from 'vitest';
import {hasModelPresentation, resolveModelPresentation} from '../src/modelPresentation';

describe('model presentation', () => {
  it('maps Ark deployment endpoints to readable model names', () => {
    expect(resolveModelPresentation('ep-m-20260917021019-cfgc7')).toEqual({
      name: 'Doubao-Seedance-2.0-mini',
      ref: 'ep-m-20260917021019-cfgc7',
    });
    expect(resolveModelPresentation('ep-m-20260911200253-v5snx').name).toBe('Doubao-Seedream-5.0-pro');
  });

  it('leaves other provider model IDs unchanged', () => {
    expect(resolveModelPresentation('deepseek-flash')).toEqual({name: 'deepseek-flash', ref: ''});
    expect(hasModelPresentation('deepseek-flash')).toBe(false);
  });
});
