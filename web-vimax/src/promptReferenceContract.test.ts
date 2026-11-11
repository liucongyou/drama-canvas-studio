import {describe, expect, it} from 'vitest';
import type {CanvasMediaNode} from './api';
import {
  promptReferenceCandidates,
  resolveExplicitPromptReferences,
} from './promptReferenceContract';

function node(
  id: string,
  title: string,
  kind: CanvasMediaNode['kind'],
  mediaUrl = `/uploads/${id}.png`,
): CanvasMediaNode {
  return {
    id,
    title,
    kind,
    prompt: '',
    mediaUrl,
    assetId: `asset-${id}`,
    x: 0,
    y: 0,
    updatedAt: '2026-11-10T00:00:00.000Z',
  };
}

describe('explicit prompt reference contract', () => {
  const chen = node('chen', '角色｜陈大强（图4）', 'image');
  const jia = node('jia', '角色｜贾富贵（图5）', 'image');
  const voice = node('voice', '音色｜陈大强', 'audio', '/uploads/voice.mp3');

  it('submits only the explicitly mentioned asset, even when links contain more', () => {
    const pool = [chen, jia, voice];
    const refs = [chen, jia].map((item) => ({
      nodeId: item.id,
      assetId: item.assetId,
      label: item.title,
      url: item.mediaUrl,
    }));
    const resolved = resolveExplicitPromptReferences(
      '@角色｜陈大强（图4）走进车间。',
      promptReferenceCandidates(pool, refs),
    );

    expect(resolved.issues).toEqual([]);
    expect(resolved.refs.map((ref) => ref.nodeId)).toEqual(['chen']);
    expect(resolved.voices).toEqual([]);
  });

  it('orders supplier references by their first prompt occurrence', () => {
    const resolved = resolveExplicitPromptReferences(
      '先看到@角色｜贾富贵（图5），再看到@角色｜陈大强（图4）。',
      promptReferenceCandidates([chen, jia]),
    );

    expect(resolved.refs.map((ref) => ref.nodeId)).toEqual(['jia', 'chen']);
  });

  it('does not reference an asset when the name is plain text without @', () => {
    const resolved = resolveExplicitPromptReferences(
      '角色｜陈大强（图4）走进车间。',
      promptReferenceCandidates([chen, jia]),
    );

    expect(resolved.refs).toEqual([]);
    expect(resolved.voices).toEqual([]);
    expect(resolved.issues).toEqual([]);
  });

  it('routes an explicitly mentioned audio asset into voiceRefs', () => {
    const resolved = resolveExplicitPromptReferences(
      '@音色｜陈大强说这句台词。',
      promptReferenceCandidates([chen, voice]),
    );

    expect(resolved.refs).toEqual([]);
    expect(resolved.voices.map((ref) => ref.nodeId)).toEqual(['voice']);
  });

  it('blocks ambiguous duplicate labels instead of guessing', () => {
    const duplicate = node('chen-2', '角色｜陈大强（图4）', 'image');
    const resolved = resolveExplicitPromptReferences(
      '@角色｜陈大强（图4）走进车间。',
      promptReferenceCandidates([chen, duplicate]),
    );

    expect(resolved.refs).toEqual([]);
    expect(resolved.issues[0].code).toBe('AMBIGUOUS_MENTION');
  });

  it('blocks a typed @ that resolves to no asset', () => {
    const resolved = resolveExplicitPromptReferences(
      '@不存在角色 走进车间。',
      promptReferenceCandidates([chen]),
    );

    expect(resolved.issues[0].code).toBe('INVALID_MENTION');
    expect(resolved.issues[0].message).toContain('@不存在角色');
  });

  it('explains an incomplete @ at the end of a prompt', () => {
    const resolved = resolveExplicitPromptReferences(
      '参考角色 @',
      promptReferenceCandidates([chen]),
    );

    expect(resolved.refs).toEqual([]);
    expect(resolved.issues[0].code).toBe('INVALID_MENTION');
  });

});
