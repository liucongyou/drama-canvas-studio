import {
  voiceRefList,
  withVoiceRefs,
  type CanvasMediaNode,
  type CanvasMediaParams,
  type GenerationTask,
  type ModelCatalog,
  type ModelSpec,
} from './api';
import {generationCostAmount, generationCostHint} from './modelPricing';
import {
  isGenerationTaskActive,
  latestTaskByNode,
} from './generationPresentation';

export type BatchGenerationState = 'ready' | 'running' | 'blocked';
export type BatchGenerationBlocker = 'reference_limit' | null;
export type CanvasGenerationEdge = {source: string; target: string};

export type ReferenceLimitEvaluation = {
  node: CanvasMediaNode;
  issue: string;
  message: string;
  refCounts: {image: number; video: number; text: number; audio: number};
  voiceCount: number;
  totalRefs: number;
};

export type BatchGenerationCandidate = {
  node: CanvasMediaNode;
  model: ModelSpec | null;
  state: BatchGenerationState;
  reason: string;
  blocker: BatchGenerationBlocker;
  cost: number | null;
  costLabel: string;
  hasVideoInput: boolean;
};

export const BATCH_GENERATION_KINDS = new Set(['image', 'audio', 'video']);

function canonicalModelId(value?: string): string {
  const raw = String(value || '').trim();
  const aliases: Record<string, string> = {
    'doubao-seedream-5-0-pro-260628': 'doubao-seedream-5-0-pro',
    'doubao-seedance-2-0-260128': 'doubao-seedance-2-0',
    'doubao-seedance-2-0-mini-260615': 'doubao-seedance-2-0-mini',
    'doubao-seedance-2-0-fast-260128': 'doubao-seedance-2-0-fast',
    'doubao-seedance-2-5-260628': 'doubao-seedance-2-5',
    'cosyvoice-v3.5-flash': 'cosyvoice-v3-5-flash',
  };
  return aliases[raw] || raw;
}

export function modelMaxRefs(model?: ModelSpec | null): number {
  const keys = ['maxImages', 'maxVideos', 'maxAudios', 'maxTextRefs'] as const;
  const categoryTotal = keys
    .reduce((sum, key) => {
      const value = Number(model?.limits?.[key]);
      return sum + (Number.isFinite(value) && value > 0 ? Math.floor(value) : 0);
    }, 0);
  if (categoryTotal > 0) return categoryTotal;
  const value = Number(model?.limits?.maxRefs);
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : 100;
}

export function resolveGenerationModel(node: CanvasMediaNode, catalog: ModelCatalog | null): ModelSpec | null {
  const candidates = (catalog?.models || []).filter(
    (model) => model.enabled !== false && model.capability === node.kind,
  );
  const stored = canonicalModelId(node.params?.model || node.params?.ttsModel);
  return candidates.find((model) => model.id === stored || model.remoteModel === stored)
    || candidates.find((model) => model.providerId === node.params?.provider)
    || candidates[0]
    || null;
}

/**
 * Build the exact reference set that submission will use. Video nodes inherit
 * connected media just like `onGenerateMediaNode`, including links drawn in
 * either direction. Keeping this pure makes the prompt editor and batch dialog
 * agree before the user pays for a request.
 */
export function applyGenerationReferences(
  node: CanvasMediaNode,
  pool: CanvasMediaNode[],
  edges: CanvasGenerationEdge[],
  model: ModelSpec | null,
): CanvasMediaNode {
  const params: CanvasMediaParams = {...(node.params || {})};
  const original: CanvasMediaNode = {...node, params};
  if (node.kind !== 'video') return original;

  const refs = [...(params.refs || [])];
  let voices = voiceRefList(params);
  const refLimit = modelMaxRefs(model);
  const audioLimit = Number(model?.limits?.maxAudios || 3);
  const flowId = `media-${node.id}`;
  const byFlowId = new Map(pool.map((item) => [`media-${item.id}`, item]));

  for (const edge of edges) {
    if (edge.source !== flowId && edge.target !== flowId) continue;
    const otherFlowId = edge.target === flowId ? edge.source : edge.target;
    const source = byFlowId.get(otherFlowId);
    if (!source || source.id === node.id) continue;
    // An outgoing video link is timeline order, not a reference back into this node.
    if (edge.target !== flowId && source.kind === 'video') continue;

    if (
      source.kind !== 'audio'
      && !refs.some((ref) => ref.nodeId === source.id)
      && refs.length + voices.length < refLimit
    ) {
      refs.push({nodeId: source.id, label: source.title || source.kind, url: source.mediaUrl});
    }
    if (
      source.kind === 'audio'
      && !voices.some((ref) => ref.nodeId === source.id)
      && refs.length + voices.length < refLimit
      && voices.length < audioLimit
    ) {
      voices = [...voices, {nodeId: source.id, label: source.title || source.kind, url: source.mediaUrl}];
    }
  }

  return {
    ...node,
    params: withVoiceRefs({...params, refs}, voices),
  };
}

export function evaluateReferenceLimits(
  node: CanvasMediaNode,
  pool: CanvasMediaNode[],
  edges: CanvasGenerationEdge[],
  model: ModelSpec | null,
): ReferenceLimitEvaluation {
  const effectiveNode = applyGenerationReferences(node, pool, edges, model);
  const params = effectiveNode.params || {};
  const kindById = new Map(pool.map((item) => [item.id, item.kind]));
  const refCounts = {image: 0, video: 0, text: 0, audio: 0};
  for (const ref of params.refs || []) {
    const kind = kindById.get(String(ref.nodeId || ''));
    if (kind && kind in refCounts) refCounts[kind] += 1;
  }
  const voiceCount = voiceRefList(params).length;
  const totalRefs = (params.refs || []).length + voiceCount;
  const maxRefs = modelMaxRefs(model);
  const maxImages = model?.limits?.maxImages;
  const maxVideos = model?.limits?.maxVideos;
  const maxAudios = model?.limits?.maxAudios;
  const maxTextRefs = model?.limits?.maxTextRefs;
  const issue = (
    maxImages !== undefined && refCounts.image > Number(maxImages)
      ? `最多 ${maxImages} 张参考图，当前 ${refCounts.image} 张`
      : maxVideos !== undefined && refCounts.video > Number(maxVideos)
        ? `最多 ${maxVideos} 个参考视频，当前 ${refCounts.video} 个`
        : maxTextRefs !== undefined && refCounts.text > Number(maxTextRefs)
          ? `最多 ${maxTextRefs} 个文本引用，当前 ${refCounts.text} 个`
          : maxAudios !== undefined && voiceCount > Number(maxAudios)
            ? `最多 ${maxAudios} 个音色，当前 ${voiceCount} 个`
            : totalRefs > maxRefs
              ? `最多 ${maxRefs} 个参考素材，当前 ${totalRefs} 个`
              : ''
  );
  const modelLabel = model?.label || model?.id || '当前模型';
  return {
    node: effectiveNode,
    issue,
    message: issue ? `${modelLabel}${issue}。请移除超出部分或切换模型` : '',
    refCounts,
    voiceCount,
    totalRefs,
  };
}

function mediaOutputReady(node: CanvasMediaNode): boolean {
  if (node.kind === 'text') return Boolean(node.prompt.trim());
  if ((node.kind === 'image' || node.kind === 'audio') && node.promptStale) return false;
  if (node.mediaAvailability === 'missing') return false;
  return Boolean(String(node.mediaUrl || '').trim());
}

function dependencyBlockers(node: CanvasMediaNode, pool: CanvasMediaNode[]) {
  const params = node.params || {};
  const refs = [...(params.refs || []), ...voiceRefList(params)];
  const byId = new Map(pool.map((item) => [item.id, item]));
  const seen = new Set<string>();
  const blockers: string[] = [];
  for (const ref of refs) {
    const nodeId = String(ref.nodeId || '');
    if (!nodeId || seen.has(nodeId)) continue;
    seen.add(nodeId);
    const source = byId.get(nodeId);
    if (!source) {
      blockers.push(`${ref.label || nodeId}（引用节点不存在）`);
      continue;
    }
    if (mediaOutputReady(source)) continue;
    blockers.push(`${source.title || source.kind}（${source.promptStale ? '提示词已更新，需重新生成' : '尚未生成'}）`);
  }
  return blockers;
}

export function activeBatchGenerationNodeIds(
  tasks: GenerationTask[],
  busyNodeIds: Iterable<string> = [],
): Set<string> {
  const active = new Set(busyNodeIds);
  for (const task of latestTaskByNode(tasks).values()) {
    if (isGenerationTaskActive(task)) {
      active.add(task.node_id);
    }
  }
  return active;
}

export function evaluateBatchGenerationNode(
  node: CanvasMediaNode,
  pool: CanvasMediaNode[],
  catalog: ModelCatalog | null,
  activeNodeIds: Set<string>,
  edges: CanvasGenerationEdge[] = [],
): BatchGenerationCandidate {
  const model = resolveGenerationModel(node, catalog);
  const references = evaluateReferenceLimits(node, pool, edges, model);
  const effectiveNode = references.node;
  const hasVideoInput = (effectiveNode.params?.refs || []).some((ref) => {
    const source = pool.find((item) => item.id === ref.nodeId);
    return source?.kind === 'video';
  });
  const estimateNode: CanvasMediaNode = {
    ...effectiveNode,
    params: {
      ...(model?.defaultParams || {}),
      ...(effectiveNode.params || {}),
      ...(model ? {provider: model.providerId, model: model.id} : {}),
    },
  };
  const cost = generationCostAmount(estimateNode, model, {hasVideoInput});
  const costLabel = generationCostHint(estimateNode, model, {hasVideoInput});

  if (node.status === 'generating' || activeNodeIds.has(node.id)) {
    return {
      node,
      model,
      state: 'running',
      reason: '正在生成',
      blocker: null,
      cost,
      costLabel,
      hasVideoInput,
    };
  }
  if (!catalog) {
    return {node, model, state: 'blocked', reason: '模型目录尚未加载', blocker: null, cost, costLabel, hasVideoInput};
  }
  if (!model) {
    return {node, model, state: 'blocked', reason: '没有可用模型', blocker: null, cost, costLabel, hasVideoInput};
  }
  const provider = catalog.providers.find((item) => item.id === model.providerId);
  if (!provider || provider.enabled === false) {
    return {node, model, state: 'blocked', reason: '供应商不可用', blocker: null, cost, costLabel, hasVideoInput};
  }
  if (!provider.has_api_key) {
    return {node, model, state: 'blocked', reason: `供应商「${provider.name}」未配置 API Key`, blocker: null, cost, costLabel, hasVideoInput};
  }
  if (!node.prompt.trim()) {
    return {node, model, state: 'blocked', reason: '提示词为空', blocker: null, cost, costLabel, hasVideoInput};
  }
  const maxPromptChars = Number(model.limits?.maxPromptChars || 0);
  if (maxPromptChars > 0 && node.prompt.length > maxPromptChars) {
    return {
      node,
      model,
      state: 'blocked',
      reason: `提示词超过 ${maxPromptChars} 字符`,
      blocker: null,
      cost,
      costLabel,
      hasVideoInput,
    };
  }
  if (references.issue) {
    return {
      node,
      model,
      state: 'blocked',
      reason: references.message,
      blocker: 'reference_limit',
      cost,
      costLabel,
      hasVideoInput,
    };
  }
  const blockers = dependencyBlockers(effectiveNode, pool);
  if (blockers.length > 0) {
    const suffix = blockers.length > 2 ? ` 等 ${blockers.length} 项` : '';
    return {
      node,
      model,
      state: 'blocked',
      reason: `引用未就绪：${blockers.slice(0, 2).join('、')}${suffix}`,
      blocker: null,
      cost,
      costLabel,
      hasVideoInput,
    };
  }
  return {
    node,
    model,
    state: 'ready',
    reason: '可生成',
    blocker: null,
    cost,
    costLabel,
    hasVideoInput,
  };
}

export function evaluateBatchGeneration(
  targets: CanvasMediaNode[],
  pool: CanvasMediaNode[],
  catalog: ModelCatalog | null,
  tasks: GenerationTask[],
  busyNodeIds: Iterable<string> = [],
  edges: CanvasGenerationEdge[] = [],
): BatchGenerationCandidate[] {
  const activeNodeIds = activeBatchGenerationNodeIds(tasks, busyNodeIds);
  return targets
    .filter((node) => BATCH_GENERATION_KINDS.has(node.kind))
    .map((node) => evaluateBatchGenerationNode(node, pool, catalog, activeNodeIds, edges));
}
