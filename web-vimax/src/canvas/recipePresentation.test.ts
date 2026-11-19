import {describe, expect, it} from 'vitest';

import {
  autoFillRecipeSlots,
  recipeDisplayDescription,
  recipeSlotHint,
  recipeSlotLabel,
} from './recipePresentation';

describe('recipe presentation', () => {
  it('localizes common and compound slot names without exposing English keys', () => {
    expect(recipeSlotLabel('lead_subject')).toBe('主体角色');
    expect(recipeSlotLabel('camera_grammar')).toBe('镜头语法');
    expect(recipeSlotLabel('unmapped_reference_field')).not.toMatch(/[a-z]/i);
  });

  it('uses the Chinese purpose before the English catalog description', () => {
    expect(recipeDisplayDescription({
      category: '01_动作与战斗',
      title: '刻意遮断省略',
      summary: '## 用途\n\n先建立动作升级，再以遮断卡跳到可观察余波。\n\n## 推荐输入格式\n\n省略。',
      requiredSlots: ['lead_subject', 'setting'],
    })).toBe('先建立动作升级，再以遮断卡跳到可观察余波。');
  });

  it('auto-fills roles and scenes from the current prompt', () => {
    expect(autoFillRecipeSlots(
      ['lead_subject', 'setting', 'camera_grammar'],
      '@角色｜陈大强（图4）站在 @场景｜长桌南沿（图13），中景跟随。',
    )).toMatchObject({
      lead_subject: '@角色｜陈大强（图4）',
      setting: '@场景｜长桌南沿（图13）',
      camera_grammar: '先交代空间，再跟随主要动作，最后落到明确结果',
    });
  });

  it('gives users a concrete fill-in example', () => {
    expect(recipeSlotHint('lead_subject')).toContain('@角色｜陈大强（图4）');
  });
});
