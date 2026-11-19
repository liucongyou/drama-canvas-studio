import {describe, expect, it} from 'vitest';

import {recipeFromDetail, recipeToBindings} from './agentRecipes';

describe('agent recipe bindings', () => {
  it('creates one primary unassigned binding from a selected recipe', () => {
    const bindings = recipeToBindings({
      id: 'handoff-ensemble-focus',
      version: '1.0.0',
      kind: 'creative',
      category: '12_综合镜头配方',
      title: '群演聚焦接力',
      description: 'test',
      models: ['seedance-2.0'],
      requiredSlots: ['lead_subject'],
      contentSha256: 'sha-1',
    });
    expect(bindings).toEqual([{
      id: 'handoff-ensemble-focus',
      version: '1.0.0',
      sha256: 'sha-1',
      role: 'primary',
      segmentNo: 0,
      slots: {},
    }]);
  });

  it('restores compact recipe data from a detail response', () => {
    const recipe = recipeFromDetail({
      id: 'handoff-ensemble-focus',
      version: '1.0.0',
      kind: 'creative',
      category: '12_综合镜头配方',
      title: '群演聚焦接力',
      description: 'test',
      models: ['seedance-2.0'],
      slots: [{key: 'lead_subject'}, {key: 'setting'}],
      contentSha256: 'sha-1',
    });
    expect(recipe).toMatchObject({
      id: 'handoff-ensemble-focus',
      version: '1.0.0',
      requiredSlots: ['lead_subject', 'setting'],
      contentSha256: 'sha-1',
    });
  });
});
