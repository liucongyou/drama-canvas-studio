import {describe, expect, it} from 'vitest';
import {promptMentionMatches} from './promptMentions';

const mentions = [
  {label: '角色｜钱万金（图2）', kind: 'image' as const},
  {label: '角色｜老会计（图19）', kind: 'image' as const},
];

describe('prompt mention matching', () => {
  it('does not turn dialogue character names into media chips', () => {
    const prompt = [
      '【镜头2】画面主体：@角色｜老会计（图19）按住账本。',
      '台词：【老会计（拉住·压得极低·急）："那半本真账，要是在钱万金手里，你可得当心。"】',
      '台词：钱万金（急）："站住。"',
    ].join('\n');
    const matched = promptMentionMatches(prompt, mentions);
    expect(matched.map((item) => item.label)).toEqual([
      '角色｜老会计（图19）',
    ]);
  });

  it('keeps explicit @ references exact', () => {
    const prompt = '@角色｜钱万金（图2）站在门口。';
    const matched = promptMentionMatches(prompt, mentions);
    expect(matched).toHaveLength(1);
    expect(matched[0].exact).toBe(true);
  });
});
