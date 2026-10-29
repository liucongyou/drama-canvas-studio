import {describe, expect, it} from 'vitest';
import {evaluateBatchGeneration} from './batchGeneration';
import type {CanvasMediaNode, GenerationTask, ModelCatalog, ModelSpec} from './api';

const model: ModelSpec = {
  id: 'image-model',
  providerId: 'provider-1',
  capability: 'image',
  label: 'Image Model',
  remoteModel: 'image-model-remote',
  adapter: 'ark_image',
  description: '',
  badge: '',
  enabled: true,
  defaultParams: {ratio: '16:9', clarity: '2K'},
  params: {},
  limits: {maxPromptChars: 100},
  pricing: {image: {unit: 'image', rates: {default: 0.2}}},
};

const videoModel: ModelSpec = {
  id: 'video-model',
  providerId: 'provider-1',
  capability: 'video',
  label: 'Video Model',
  remoteModel: 'video-model-remote',
  adapter: 'ark_video',
  description: '',
  badge: '',
  enabled: true,
  defaultParams: {ratio: '9:16', resolution: '480P', duration: 5},
  params: {},
  limits: {maxImages: 5, maxVideos: 1, maxAudios: 3, maxTextRefs: 2, maxPromptChars: 1000},
  pricing: {video: {unit: 'second', rates: {default: 0.1}}},
};

const catalog: ModelCatalog = {
  providers: [{
    id: 'provider-1',
    name: 'Provider 1',
    protocol: 'ark',
    base_url: 'https://example.com',
    enabled: true,
    has_api_key: true,
  }],
  models: [model, videoModel],
};

function node(id: string, patch: Partial<CanvasMediaNode> = {}): CanvasMediaNode {
  return {
    id,
    kind: 'image',
    title: id,
    prompt: '角色定妆，正面半身像',
    status: 'idle',
    x: 0,
    y: 0,
    updatedAt: '2026-10-27T00:00:00Z',
    params: {provider: 'provider-1', model: 'image-model', ratio: '16:9', clarity: '2K'},
    ...patch,
  };
}

describe('batch generation evaluation', () => {
  it('selects ready nodes while keeping missing prompts and running tasks visible', () => {
    const running: GenerationTask = {
      id: 'task-1',
      project_id: 'project-1',
      node_id: 'node-2',
      title: 'node-2',
      kind: 'image',
      status: 'running',
      created_at: '2026-10-27T00:00:00Z',
    };
    const result = evaluateBatchGeneration(
      [node('node-1'), node('node-2'), node('node-3', {prompt: ''})],
      [node('node-1'), node('node-2'), node('node-3')],
      catalog,
      [running],
    );
    expect(result.map((item) => item.state)).toEqual(['ready', 'running', 'blocked']);
    expect(result[2].reason).toBe('提示词为空');
    expect(result[0].cost).toBe(0.2);
  });

  it('blocks nodes whose referenced asset has not been generated', () => {
    const source = node('source', {status: 'idle', mediaUrl: undefined});
    const target = node('target', {
      params: {
        provider: 'provider-1',
        model: 'image-model',
        refs: [{nodeId: 'source', label: '角色｜赵铁柱（图1）'}],
      },
    });
    const [candidate] = evaluateBatchGeneration([target], [source, target], catalog, []);
    expect(candidate.state).toBe('blocked');
    expect(candidate.reason).toContain('引用未就绪');
  });

  it('blocks a model whose provider has no API key', () => {
    const [candidate] = evaluateBatchGeneration(
      [node('node-1')],
      [node('node-1')],
      {...catalog, providers: [{...catalog.providers[0], has_api_key: false}]},
      [],
    );
    expect(candidate.state).toBe('blocked');
    expect(candidate.reason).toContain('API Key');
  });

  it('blocks image reference counts above the per-kind model limit', () => {
    const sources = Array.from({length: 6}, (_, index) => node(`image-${index}`, {
      mediaUrl: `https://example.com/${index}.png`,
    }));
    const target = node('target', {
      params: {
        provider: 'provider-1',
        model: 'image-model',
        refs: sources.map((source) => ({nodeId: source.id, label: source.title})),
      },
    });
    const [candidate] = evaluateBatchGeneration(
      [target],
      [...sources, target],
      {...catalog, models: [{...model, limits: {maxImages: 5, maxPromptChars: 100}}]},
      [],
    );
    expect(candidate.state).toBe('blocked');
    expect(candidate.blocker).toBe('reference_limit');
    expect(candidate.reason).toContain('最多 5 张参考图，当前 6 张');
  });

  it('blocks total reference counts above maxRefs when no category total exists', () => {
    const sources = Array.from({length: 4}, (_, index) => node(`image-${index}`, {
      mediaUrl: `https://example.com/${index}.png`,
    }));
    const target = node('target', {
      params: {
        provider: 'provider-1',
        model: 'image-model',
        refs: sources.map((source) => ({nodeId: source.id, label: source.title})),
      },
    });
    const [candidate] = evaluateBatchGeneration(
      [target],
      [...sources, target],
      {...catalog, models: [{...model, limits: {maxRefs: 3, maxPromptChars: 100}}]},
      [],
    );
    expect(candidate.state).toBe('blocked');
    expect(candidate.reason).toContain('最多 3 个参考素材，当前 4 个');
  });

  it('blocks video voice references above maxAudios', () => {
    const voices = Array.from({length: 4}, (_, index) => node(`voice-${index}`, {
      kind: 'audio',
      mediaUrl: `https://example.com/${index}.mp3`,
    }));
    const target = node('target', {
      kind: 'video',
      params: {
        provider: 'provider-1',
        model: 'video-model',
        voiceRefs: voices.map((source) => ({nodeId: source.id, label: source.title})),
      },
    });
    const [candidate] = evaluateBatchGeneration([target], [...voices, target], catalog, []);
    expect(candidate.state).toBe('blocked');
    expect(candidate.blocker).toBe('reference_limit');
    expect(candidate.reason).toContain('最多 3 个音色，当前 4 个');
  });

  it('counts automatically linked references before allowing batch submission', () => {
    const sources = Array.from({length: 6}, (_, index) => node(`image-${index}`, {
      mediaUrl: `https://example.com/${index}.png`,
    }));
    const target = node('target', {
      kind: 'video',
      params: {provider: 'provider-1', model: 'video-model', refs: []},
    });
    const edges = sources.map((source) => ({
      source: `media-${source.id}`,
      target: `media-${target.id}`,
    }));
    const [candidate] = evaluateBatchGeneration(
      [target],
      [...sources, target],
      catalog,
      [],
      [],
      edges,
    );
    expect(candidate.state).toBe('blocked');
    expect(candidate.reason).toContain('最多 5 张参考图，当前 6 张');
  });

  it('keeps a node ready at the exact per-kind reference limit', () => {
    const sources = Array.from({length: 5}, (_, index) => node(`image-${index}`, {
      mediaUrl: `https://example.com/${index}.png`,
    }));
    const target = node('target', {
      kind: 'video',
      params: {
        provider: 'provider-1',
        model: 'video-model',
        refs: sources.map((source) => ({nodeId: source.id, label: source.title})),
      },
    });
    const [candidate] = evaluateBatchGeneration([target], [...sources, target], catalog, []);
    expect(candidate.state).toBe('ready');
    expect(candidate.blocker).toBeNull();
  });
});
