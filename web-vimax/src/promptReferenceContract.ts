import type {
  CanvasAssetRef,
  CanvasMediaNode,
  CanvasMediaNodeKind,
} from './api';
import {promptMentionMatches} from './promptMentions';

export type PromptReferenceCandidate = CanvasAssetRef & {
  kind: CanvasMediaNodeKind;
};

export type PromptReferenceIssue = {
  code: 'AMBIGUOUS_MENTION' | 'INVALID_MENTION';
  label: string;
  message: string;
  start: number;
  end: number;
};

export type PromptReferenceResolution = {
  refs: CanvasAssetRef[];
  voices: CanvasAssetRef[];
  matchedNodeIds: string[];
  issues: PromptReferenceIssue[];
};

function toPromptRef(candidate: PromptReferenceCandidate): CanvasAssetRef {
  const {kind: _kind, ...ref} = candidate;
  return ref;
}
export function promptReferenceCandidates(
  pool: CanvasMediaNode[],
  refs: CanvasAssetRef[] = [],
  voices: CanvasAssetRef[] = [],
): PromptReferenceCandidate[] {
  const byNodeId = new Map<string, PromptReferenceCandidate>();
  const poolById = new Map(pool.map((node) => [node.id, node]));

  for (const ref of [...refs, ...voices]) {
    const node = poolById.get(ref.nodeId);
    if (!node || byNodeId.has(node.id)) continue;
    byNodeId.set(node.id, {
      nodeId: node.id,
      ...(node.assetId ? {assetId: node.assetId} : {}),
      ...(node.seriesId ? {seriesId: node.seriesId} : {}),
      ...(node.variantKey ? {variantKey: node.variantKey} : {}),
      label: node.title || ref.label || node.kind,
      ...(node.mediaUrl || ref.url ? {url: node.mediaUrl || ref.url} : {}),
      kind: node.kind,
    });
  }

  for (const node of pool) {
    if (byNodeId.has(node.id) || !node.title) continue;
    byNodeId.set(node.id, {
      nodeId: node.id,
      ...(node.assetId ? {assetId: node.assetId} : {}),
      ...(node.seriesId ? {seriesId: node.seriesId} : {}),
      ...(node.variantKey ? {variantKey: node.variantKey} : {}),
      label: node.title,
      ...(node.mediaUrl ? {url: node.mediaUrl} : {}),
      kind: node.kind,
    });
  }

  return [...byNodeId.values()];
}

/**
 * Resolve the explicit @ mentions that are allowed into this generation.
 *
 * The prompt is the user-facing contract, while nodeId/assetId/version metadata
 * remains the machine contract sent to the provider. Links and historical refs
 * are only candidates; they never enter the request unless the prompt names
 * them with @.
 */
export function resolveExplicitPromptReferences(
  prompt: string,
  candidates: PromptReferenceCandidate[],
): PromptReferenceResolution {
  const normalizedPrompt = String(prompt || '');
  const byLabel = new Map<string, PromptReferenceCandidate[]>();
  const uniqueByNode = new Map<string, PromptReferenceCandidate>();
  for (const candidate of candidates) {
    if (!candidate.nodeId || !candidate.label || uniqueByNode.has(candidate.nodeId)) continue;
    uniqueByNode.set(candidate.nodeId, candidate);
    const group = byLabel.get(candidate.label) || [];
    group.push(candidate);
    byLabel.set(candidate.label, group);
  }

  const matches = promptMentionMatches(normalizedPrompt, [...uniqueByNode.values()]);
  const refs: CanvasAssetRef[] = [];
  const voices: CanvasAssetRef[] = [];
  const matchedNodeIds: string[] = [];
  const issues: PromptReferenceIssue[] = [];
  const seenNodeIds = new Set<string>();

  for (const match of matches) {
    const group = byLabel.get(match.label) || [];
    if (group.length > 1) {
      issues.push({
        code: 'AMBIGUOUS_MENTION',
        label: match.label,
        message: `引用 @${match.label} 对应多个资产，请重新从 @ 选择器指定唯一资产`,
        start: match.start,
        end: match.end,
      });
      continue;
    }
    const candidate = group[0];
    if (!candidate || seenNodeIds.has(candidate.nodeId)) continue;
    seenNodeIds.add(candidate.nodeId);
    matchedNodeIds.push(candidate.nodeId);
    (candidate.kind === 'audio' ? voices : refs).push(toPromptRef(candidate));
  }

  // A typed @ is an explicit request. Never silently drop an unresolved one
  // and never fall back to scanning all canvas links.
  const matchedRanges = matches.map((match) => [match.start, match.end] as const);
  const tokenPattern = /@([^\s<>，。！？；、,:：()（）\[\]【】]+)/g;
  for (const token of normalizedPrompt.matchAll(tokenPattern)) {
    const start = token.index ?? 0;
    const end = start + token[0].length;
    if (start > 0 && normalizedPrompt[start - 1] === '<') continue;
    if (matchedRanges.some(([from, to]) => start >= from && start < to)) continue;
    issues.push({
      code: 'INVALID_MENTION',
      label: token[1],
      message: `无法解析引用 @${token[1]}，请从 @ 选择器重新选择资产`,
      start,
      end,
    });
  }

  // A lone trailing @ is still an incomplete explicit reference. It would
  // otherwise quietly submit with no media.
  const trailingAt = normalizedPrompt.match(/@\s*$/);
  if (trailingAt?.index !== undefined && !matchedRanges.some(([from]) => from === trailingAt.index)) {
    issues.push({
      code: 'INVALID_MENTION',
      label: '',
      message: '提示词末尾的 @ 尚未选择资产，请从 @ 选择器完成引用或删除它',
      start: trailingAt.index,
      end: normalizedPrompt.length,
    });
  }

  issues.sort((left, right) => left.start - right.start);
  return {refs, voices, matchedNodeIds, issues};
}
