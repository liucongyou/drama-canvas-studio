import {describe, expect, it} from 'vitest';
import {promptMentionMatches} from './promptMentions';

const mentions = [
  {label: '角色｜钱万金（图2）', kind: 'image' as const},
  {label: '角色｜老会计（图19）', kind: 'image' as const},
  {label: '场景｜厂区后街·母版（图10）', kind: 'image' as const},
];

describe('prompt mention matching', () => {
  it('only matches explicit @ references', () => {
    const prompt = [
      '【场景】<厂区后街> 对应场景｜厂区后街·母版（图10），仅是说明文字。',
      '<厂区后街> 对应@场景｜厂区后街·母版（图10），左侧标签不是引用。',
      '<@场景｜厂区后街·母版（图10）> 是历史残留标签，也不是引用。',
      '【镜头2】画面主体：@角色｜老会计（图19）按住账本。',
      '台词：【老会计（拉住·压得极低·急）："那半本真账，要是在钱万金手里，你可得当心。"】',
      '台词：钱万金（急）："站住。"',
    ].join('\n');
    const matched = promptMentionMatches(prompt, mentions);
    expect(matched.map((item) => item.label)).toEqual([
      '场景｜厂区后街·母版（图10）',
      '角色｜老会计（图19）',
    ]);
  });

  it('keeps explicit @ references exact', () => {
    const prompt = '@角色｜钱万金（图2）站在门口。';
    const matched = promptMentionMatches(prompt, mentions);
    expect(matched).toHaveLength(1);
    expect(matched[0].exact).toBe(true);
  });

  it('does not duplicate a mapping-row reference that only contains the canonical title', () => {
    const prompt = [
      '<厂区后街> 对应@场景｜厂区后街·母版（图10），仅采用空间布局。',
      '【场景资产】@场景｜厂区后街·母版（图10）；',
    ].join('\n');
    const matched = promptMentionMatches(prompt, mentions);
    const sceneMatches = matched.filter(
      (item) => item.label === '场景｜厂区后街·母版（图10）',
    );

    expect(sceneMatches).toHaveLength(2);
    expect(sceneMatches.every((item) => item.exact)).toBe(true);
  });
});
