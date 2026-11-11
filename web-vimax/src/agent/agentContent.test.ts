import {describe, expect, it} from 'vitest';
import {normalizeContentBlocks, shouldCollapseAgentContent} from './AgentMessageContent';

describe('agent content blocks', () => {
  it('keeps long markdown and JSON blocks', () => {
    const longText = '长'.repeat(5000);
    const blocks = normalizeContentBlocks([
      {type: 'markdown', text: longText},
      {type: 'json', data: {assetId: 'character:陈大强:default'}},
    ]);
    expect(blocks).toHaveLength(2);
    expect(blocks[0]).toMatchObject({type: 'markdown', text: longText});
    expect(blocks[1]).toMatchObject({type: 'json'});
  });

  it('falls back to text without dropping content', () => {
    expect(normalizeContentBlocks(undefined, '完整回复')).toEqual([
      {type: 'text', text: '完整回复'},
    ]);
  });

  it('only collapses long content and keeps the full text available', () => {
    const short = normalizeContentBlocks(undefined, '短回复');
    const long = normalizeContentBlocks(undefined, '展开前'.repeat(120));
    expect(shouldCollapseAgentContent(short)).toBe(false);
    expect(shouldCollapseAgentContent(long)).toBe(true);
    expect(long[0]).toMatchObject({type: 'text'});
  });
});
