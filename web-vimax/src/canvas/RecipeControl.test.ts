import {describe, expect, it} from 'vitest';

import {recipeBindingReady} from './RecipeControl';

describe('recipe control readiness', () => {
  it('requires all declared missing slots', () => {
    const binding = {
      id: 'handoff-ensemble-focus',
      version: '1.0.0',
      role: 'primary',
      segmentNo: 1,
      slots: {},
      missingSlots: ['lead_subject', 'setting'],
    };
    expect(recipeBindingReady(binding, {})).toBe(false);
    expect(recipeBindingReady(binding, {lead_subject: '@角色｜甲（图1）'})).toBe(false);
    expect(recipeBindingReady(binding, {
      lead_subject: '@角色｜甲（图1）',
      setting: '长桌南沿',
    })).toBe(true);
  });
});
