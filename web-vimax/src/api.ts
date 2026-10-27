// api.ts — 自研画布适配层：同签名对接本地后端（/api/projects/...）
// 数据映射：payload(nodes/connections) ↔ Canvas / CanvasMediaNode
// 生成：轮询器检测 generating 节点 → POST generate → done/error 事件

declare global {
  interface Window {
    __CANVAS_API_BASE__?: string;
  }
}

const API_BASE = String(
  (typeof window !== 'undefined' && window.__CANVAS_API_BASE__)
  || import.meta.env.VITE_API_BASE_URL
  || '',
).replace(/\/+$/, '');

export function apiUrl(path: string): string {
  if (/^https?:\/\//i.test(path) || path.startsWith('//')) return path;
  if (!API_BASE) return path;
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`;
}

export const AUTH_REQUIRED_EVENT = 'canvas-auth-required';

export type AgentEvent = {
  type: string;
  message?: string;
  sessionId?: string;
  [k: string]: unknown;
};

export type CanvasAssetItem = {
  idx?: number;
  name: string;
  image_url?: string;
  audio_url?: string;
  description?: string;
};

export type CanvasShot = {
  idx: number;
  ep_id?: string | null;
  segment_id: number | null;
  char_idxs: number[];
  scene_idxs: number[];
  prop_idxs: number[];
  voice_idxs: number[];
  cam_idx: number | null;
  transition_type: string | null;
  duration: number | null;
  visual_desc: string | null;
  visual_summary: string | null;
  ff_desc: string | null;
  lf_desc: string | null;
  motion_desc: string | null;
  audio_desc: string | null;
  action_breakdown: string | null;
  motion_summary: string | null;
  video_prompt: string | null;
  camera_lang: string | null;
  person_layers: string | null;
  keyframe_url: string | null;
  clip_url: string | null;
  clip_stale?: boolean;
  has_description: boolean;
};

export type CanvasSegment = {
  segment_id: number;
  ep_id?: string;
  title: string;
  duration: number | null;
  shot_idxs: number[];
  scene_id: string | null;
  asset_binding: string | null;
  end_state: string | null;
  clip_url?: string | null;
  has_segment_video?: boolean;
  entry_state?: string | null;
  change_timeline?: string | null;
  exit_state?: string | null;
  staging_and_axis?: string | null;
  style_lighting?: string | null;
  forbidden?: string | null;
  fixed_block?: string | null;
  group_bridge?: string | null;
  inheritance_rules?: string | null;
};

export type CanvasEp = {
  ep_id: string;
  title: string;
  progress: unknown;
  storyboard_ready: boolean;
  shot_count: number | null;
  described_count?: number | null;
  keyframes: number;
  clips: number;
  final_ready: boolean;
  status: string;
  updated_at: string | null;
  final_video_url?: string | null;
  segments_total?: number | null;
  segments_rendered?: number | null;
  partial_render?: boolean;
};

export type CanvasMeta = {
  session_id: string;
  current_ep: string | null;
  episodes: CanvasEp[];
  overview?: boolean;
};

export type Canvas = {
  meta: CanvasMeta;
  characters: CanvasAssetItem[];
  scenes: CanvasAssetItem[];
  props: CanvasAssetItem[];
  voices: CanvasAssetItem[];
  shots: CanvasShot[];
  segments: CanvasSegment[];
};

export type CanvasNote = {id: string; text: string; x: number; y: number; updatedAt: string};

export type CanvasMediaNodeKind = 'text' | 'image' | 'audio' | 'video';

// 资产引用（@资产）：nodeId 指向本项目资产节点，url 已生成时直接引用
export type CanvasAssetRef = {nodeId: string; label: string; url?: string};
export type CanvasVideoVersion = {
  url: string;
  taskId?: string;
  generatedAt?: string;
  actualDuration?: number;
  validationStatus?: string;
};

// 生成参数（详细属性面板：模型/尺寸/清晰度/比例/推理强度/引用/音色）
export type CanvasMediaParams = {
  /** 供应商目录 ID（不是显示名称）。旧节点可缺省，由后端按模型 ID 迁移。 */
  provider?: string;
  model?: string;
  size?: string;        // 如 1536x1536
  clarity?: string;     // 1K / 1.5K / 2K / 4K
  ratio?: string;       // 16:9 / 9:16 / 4:3 / 3:4 / 1:1 / 21:9
  guidance?: string;    // 推理强度
  resolution?: string;  // 480P / 720P / 1080P（video）
  duration?: number;    // 秒（video）
  count?: number;       // 生成数量（供应商支持时）
  refs?: CanvasAssetRef[];   // 资产/参考图引用
  voiceRef?: CanvasAssetRef; // 角色音色引用（旧版单值字段，仅为兼容历史数据保留，读取请用下面两个）
  /**
   * ★★ 音色引用的**多值**字段（第 17 轮修「连了 4 个节点，弹窗只显示 1 个引用」）。
   *
   * 根因：voiceRef 是**单值**字段。用户把一个视频卡连到 4 个音频节点时，
   * linkRef 每连一条就整体覆盖 voiceRef，最后只剩第 4 个 —— 前 3 个静默丢失，
   * 界面上就表现为"明明连了 4 个，引用资产里只显示 1 个"。
   * 而视频本来就需要多个角色音色（一段戏里钱万金、陈大强各说各的），
   * 单值字段从设计上就装不下。
   *
   * 兼容约定：历史数据只有 voiceRef（单值）。读取端一律走 voiceRefList()，
   * 它会把老字段并入数组；写入端写 voiceRefs，同时把 voiceRef 同步成第一项
   * （老代码/后端还有读 voiceRef 的分支，不能直接断掉）。
   */
  voiceRefs?: CanvasAssetRef[];
  ttsVoice?: string;    // audio 节点音色
  ttsModel?: string;
  ttsMode?: 'design' | 'speech';
  voiceId?: string;
  outputFormat?: 'wav' | 'mp3' | string;
  sampleRate?: number;
  previewDuration?: number;
  previewText?: string;
  voicePrompt?: string;
  generateAudio?: boolean;
  watermark?: boolean;
  seed?: number;
  mediaVersions?: CanvasVideoVersion[];
  mediaVersionIndex?: number;
  videoVersions?: CanvasVideoVersion[];
  videoVersionIndex?: number;
  // 文本节点自定义尺寸（右下角拖拽缩放后写回；缺省用 200×120）
  textW?: number;
  textH?: number;
  // 文本节点生成参数：模型走 model（与图/视频共用），这里是最大输出 token 数
  maxTokens?: number;
  temperature?: number;
  // 文本节点生成的 system 提示（角色/世界观约束），留空用默认
  system?: string;
};

export type ModelParamSpec = {
  type?: 'select' | 'range' | 'text' | 'boolean' | string;
  options?: Array<string | number>;
  min?: number;
  max?: number;
  step?: number;
  multiple?: boolean;
  default?: unknown;
  api?: string;
};

export type ModelPricingRule = {
  unit?: 'second' | 'image' | 'video' | 'request' | string;
  rates?: Record<string, number>;
  ratesWithVideo?: Record<string, number>;
  fixed?: number;
  note?: string;
  noteWithVideo?: string;
};

export type ModelPricing = {
  currency?: string;
  image?: ModelPricingRule;
  video?: ModelPricingRule;
  audio?: ModelPricingRule;
  text?: ModelPricingRule;
};

export type ModelSpec = {
  id: string;
  providerId: string;
  capability: CanvasMediaNodeKind;
  label: string;
  remoteModel: string;
  adapter: string;
  description: string;
  badge: string;
  enabled: boolean;
  defaultParams: CanvasMediaParams;
  params: Record<string, ModelParamSpec>;
  limits: {
    maxRefs?: number;
    maxImages?: number;
    maxVideos?: number;
    maxAudios?: number;
    maxTextRefs?: number;
    maxPromptChars?: number;
  };
  pricing?: ModelPricing;
  builtin?: boolean;
};

export type ProviderSpec = {
  id: string;
  name: string;
  protocol: 'ark' | 'openai' | 'dashscope' | string;
  base_url: string;
  workspace_id?: string;
  enabled: boolean;
  has_api_key: boolean;
  api_key_hint?: string;
  builtin?: boolean;
};

export type ModelCatalog = {
  providers: ProviderSpec[];
  models: ModelSpec[];
  storage?: StorageSpec;
};

export type StorageSpec = {
  enabled: boolean;
  provider: 'tos' | string;
  bucket: string;
  region: string;
  endpoint: string;
  internal_endpoint: string;
  prefix: string;
  url_ttl_seconds: number;
  auto_persist_images: boolean;
  require_for_ark_portrait: boolean;
  has_access_key: boolean;
  access_key_hint?: string;
  has_secret_key: boolean;
  secret_key_hint?: string;
  ready: boolean;
};

export type GenerationTask = {
  id: string;
  project_id: string;
  node_id: string;
  title: string;
  kind: CanvasMediaNodeKind;
  status: 'queued' | 'submitting' | 'running' | 'succeeded' | 'failed' | 'interrupted';
  phase?: string;
  progress?: number;
  provider?: string;
  model?: string;
  provider_task_id?: string;
  attempt?: number;
  queue_position?: number | null;
  error?: string;
  error_kind?: 'local_validation' | 'provider' | '';
  created_at: string;
  started_at?: string;
  updated_at?: string;
  finished_at?: string;
};

export type CanvasMediaNode = {
  id: string;
  kind: CanvasMediaNodeKind;
  title: string;
  prompt: string;
  mediaUrl?: string;
  status?: 'idle' | 'generating' | 'done' | 'failed';
  lastError?: string;
  generationStartedAt?: string;
  generationFinishedAt?: string;
  providerTaskStatus?: string;
  providerTaskPhase?: string;
  providerTaskProgress?: number;
  episodeId?: string;
  actualDuration?: number;
  videoValidation?: {
    status?: string;
    message?: string;
    duration?: number;
    width?: number;
    height?: number;
    hasAudio?: boolean;
  };
  storage?: {
    status?: 'stored' | 'failed' | string;
    provider?: string;
    bucket?: string;
    key?: string;
    region?: string;
    size?: number;
    sha256?: string;
    crc64?: string;
    storedAt?: string;
    originalBytes?: boolean;
    error?: string;
  };
  storageError?: string;
  assetKey?: string;
  promptStale?: boolean;
  promptStaleReason?: string;
  sourcePromptUpdatedAt?: string;
  sharedEpisodeCount?: number;
  reusedAsset?: {
    assetKey?: string;
    sourceProjectId?: string;
    sourceNodeId?: string;
    sourceTitle?: string;
    content?: string;
  };
  x: number;
  y: number;
  updatedAt: string;
  refIds?: string[];
  params?: CanvasMediaParams;
};

/**
 * 统一读取节点的「音色引用列表」。
 *
 * ★ 为什么要有这个函数（第 17 轮修）：`voiceRef` 是单值字段，用户把一个视频卡
 *   连到 4 个音频节点时，后连的会覆盖先连的，最后只剩 1 个 —— 这就是
 *   「明明连线的是 4 个节点，为什么引用资产只显示一个」的根因。
 *
 * 语义：voiceRefs（新，多值）优先；voiceRef（老，单值）并入去重。
 * 返回顺序 = 用户连线的先后顺序，**不做排序**（顺序决定"谁是主音色"）。
 */
export function voiceRefList(p?: CanvasMediaParams): CanvasAssetRef[] {
  const out: CanvasAssetRef[] = [];
  const seen = new Set<string>();
  for (const r of [...(p?.voiceRefs || []), ...(p?.voiceRef ? [p.voiceRef] : [])]) {
    if (!r?.nodeId || seen.has(r.nodeId)) continue;
    seen.add(r.nodeId);
    out.push(r);
  }
  return out;
}

/**
 * 写入音色引用的规范入口：数组写 voiceRefs，同时把 voiceRef 同步成第一项。
 *
 * 为什么要同步老字段：后端 `_generate` 的视频分支仍读 `params.voiceRef`（单值），
 * 直接只写 voiceRefs 会让"音色参考"整段失效。两边必须同时成立。
 */
export function withVoiceRefs(p: CanvasMediaParams, list: CanvasAssetRef[]): CanvasMediaParams {
  const next: CanvasMediaParams = {...p};
  const seen = new Set<string>();
  const uniq = list.filter((r) => {
    if (!r?.nodeId || seen.has(r.nodeId)) return false;
    seen.add(r.nodeId);
    return true;
  });
  if (uniq.length) {
    next.voiceRefs = uniq;
    next.voiceRef = uniq[0];
  } else {
    delete next.voiceRefs;
    delete next.voiceRef;
  }
  return next;
}

/**
 * Unified generation-version list for image, audio and video nodes.
 * videoVersions remains a read fallback for existing projects.
 */
export function mediaVersionList(node: CanvasMediaNode): CanvasVideoVersion[] {
  if (node.kind === 'text') return [];
  const source = node.params?.mediaVersions
    || (node.kind === 'video' ? node.params?.videoVersions : [])
    || [];
  const seen = new Set<string>();
  const versions = source.filter((version) => {
    const url = String(version?.url || '').trim();
    if (!url || seen.has(url)) return false;
    seen.add(url);
    return true;
  });
  if (node.mediaUrl && !seen.has(node.mediaUrl)) versions.push({url: node.mediaUrl});
  return versions;
}

export type QualityReportDimension = {
  score: number;
  weight: number;
  passed: boolean;
  message: string;
};
export type QualityReport = {
  available: boolean;
  aggregated?: boolean;
  stale?: boolean;
  report_path?: string;
  overall_score: number;
  passed: boolean;
  gate_threshold: number;
  shot_count: number;
  segment_count: number;
  blocking?: string[];
  levels?: Record<string, number>;
  dimensions: Record<string, QualityReportDimension>;
  shot_issues?: Record<number, string[]>;
  summary: string;
  top_issues: string[];
  revision_suggestions: string[];
};

// ---------- 基础 ----------
function friendlyRequestError(status: number, raw: string): string {
  const text = String(raw || '').trim();
  const lower = text.toLowerCase();
  if (lower.includes('tos_account_overdue') || (text.includes('TOS') && text.includes('欠费'))) {
    return text;
  }
  if (status === 429 || lower.includes('http 429') || lower.includes('too many') || lower.includes('rate limit') || text.includes('过于频繁')) {
    return '请求过于频繁，供应商正在限流。请等待 30–60 秒后再试，不要重复连续提交。';
  }
  if (lower.includes('insufficient') || lower.includes('balance') || lower.includes('quota') || text.includes('余额') || text.includes('额度') || text.includes('欠费')) {
    return '供应商账户余额或额度不足。请先检查对应供应商账户与 API Key，再重新生成。';
  }
  if (lower.includes('auth') || lower.includes('invalid api key') || status === 401 || status === 403) {
    return '供应商鉴权失败。请在「模型接口」中检查 API Key、工作空间和模型权限。';
  }
  if (
    lower.includes('reference')
    || lower.includes('image')
    || lower.includes('audio')
    || lower.includes('video')
    || text.includes('参考素材')
    || text.includes('参考图片')
    || text.includes('参考音频')
    || text.includes('参考视频')
    || text.includes('当前模型最多接受')
    || text.includes('参考素材缺失')
  ) {
    return `参考素材不符合当前模型要求。请检查图片/音频数量、格式、大小和槽位顺序。${text ? `详细信息：${text.slice(0, 240)}` : ''}`;
  }
  if (lower.includes('timeout') || lower.includes('timed out') || status === 504 || text.includes('超时')) {
    return '供应商响应超时。任务可能仍在处理中，请先到任务中心查看，确认失败后再重新提交。';
  }
  if (status >= 500) {
    return `供应商服务暂时异常（HTTP ${status}）。请稍后重试；已有任务 ID 时优先在任务中心继续等待。`;
  }
  return text || `请求失败（HTTP ${status}）`;
}

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(apiUrl(url), {
    ...init,
    credentials: 'include',
    headers: {'Content-Type': 'application/json', ...(init.headers || {})},
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && typeof window !== 'undefined') {
      window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
    }
    const raw = (payload as {error?: string}).error || `请求失败，HTTP ${response.status}`;
    throw new Error(friendlyRequestError(response.status, raw));
  }
  return payload as T;
}

export async function getAuthSession(): Promise<{
  authenticated: boolean;
  username?: string;
  tenantId?: string;
  slug?: string;
}> {
  return request('/api/auth/session');
}

export async function loginAccount(username: string, password: string): Promise<{
  authenticated: boolean;
  username?: string;
  tenantId?: string;
  slug?: string;
}> {
  return request('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({username, password}),
  });
}

export async function logoutAccount(): Promise<{ok: boolean}> {
  return request('/api/auth/logout', {method: 'POST', body: '{}'});
}

// ---------- local service compatibility ----------
// The standalone canvas uses projects, while the embedded ViMax shell may still
// probe the session endpoint. Keep the read path tolerant of a transient restart.
export async function getSessions(): Promise<{activeSessionId?: string; sessions: unknown[]}> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      return await request('/api/sessions');
    } catch (error) {
      lastError = error;
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new Error('本地服务响应超时，正在自动重连，请稍后重试。');
      }
      if (attempt === 0) await new Promise((resolve) => setTimeout(resolve, 80));
    }
  }
  if (lastError instanceof Error && lastError.name === 'AbortError') {
    throw new Error('本地服务响应超时，正在自动重连，请稍后重试。');
  }
  throw lastError instanceof Error ? lastError : new Error('本地服务连接失败');
}

// ---------- model provider catalog ----------
export async function getModelCatalog(): Promise<ModelCatalog> {
  return request('/api/model-catalog');
}

export async function getGenerationTasks(projectId: string, limit = 80): Promise<GenerationTask[]> {
  const data = await request<{tasks: GenerationTask[]}>(
    `/api/generation-tasks?projectId=${encodeURIComponent(projectId)}&limit=${limit}`,
  );
  return data.tasks || [];
}

export async function cancelGenerationTask(taskId: string): Promise<{ok: boolean; taskId: string}> {
  return request(`/api/generation-tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: 'POST',
    body: '{}',
  });
}

export async function saveProviderConfig(provider: Record<string, unknown>): Promise<ModelCatalog> {
  return request('/api/providers', {method: 'POST', body: JSON.stringify({provider})});
}

export async function deleteProviderConfig(providerId: string): Promise<ModelCatalog> {
  return request(`/api/providers/${encodeURIComponent(providerId)}`, {method: 'DELETE'});
}

export async function saveModelConfig(model: Record<string, unknown>): Promise<ModelCatalog> {
  return request('/api/models', {method: 'POST', body: JSON.stringify({model})});
}

export async function deleteModelConfig(modelId: string): Promise<ModelCatalog> {
  return request(`/api/models/${encodeURIComponent(modelId)}`, {method: 'DELETE'});
}

export async function testProviderConfig(providerId: string): Promise<{ok: boolean; message: string}> {
  return request('/api/providers/test', {
    method: 'POST',
    body: JSON.stringify({providerId}),
  });
}

export async function getStorageConfig(): Promise<StorageSpec> {
  const payload = await request<{storage: StorageSpec}>('/api/storage');
  return payload.storage;
}

export async function saveStorageConfig(
  storage: Record<string, unknown>,
): Promise<StorageSpec> {
  const payload = await request<{ok: boolean; storage: StorageSpec}>('/api/storage', {
    method: 'POST',
    body: JSON.stringify({storage}),
  });
  return payload.storage;
}

export async function testStorageConfig(): Promise<{ok: boolean; message: string}> {
  return request('/api/storage/test', {method: 'POST', body: '{}'});
}

type Payload = {
  nodes?: PayloadNode[];
  connections?: {fromNodeId: string; toNodeId: string}[];
  title?: string;
  extras?: {notes?: CanvasNote[]};
  episodeId?: string;
  episodeTitle?: string;
  episodeImports?: Array<{
    id: string;
    title?: string;
    nodeIds?: string[];
    videoNodeIds?: string[];
    connectionIds?: string[];
    importedAt?: string;
  }>;
  episodeLayouts?: Record<string, Record<string, {x: number; y: number}>>;
};
type PayloadNode = {
  id: string;
  type: string;
  title?: string;
  position?: {x: number; y: number};
  updatedAt?: string;
  metadata?: Record<string, unknown> & {
    prompt?: string; content?: string; status?: string;
    refs?: CanvasAssetRef[]; params?: CanvasMediaParams; lastError?: string; shotPatch?: Record<string, unknown>;
    promptStale?: boolean; promptStaleReason?: string; sourcePromptUpdatedAt?: string;
    generationStartedAt?: string; generationFinishedAt?: string;
    providerTaskStatus?: string; providerTaskPhase?: string; providerTaskProgress?: number;
    actualDuration?: number; videoValidation?: Record<string, unknown>;
    episodeId?: string;
  };
};

async function getProject(pid: string): Promise<{id: string; payload: Payload; title?: string}> {
  return request(`/api/projects/${encodeURIComponent(pid)}`);
}

function urlOf(node?: PayloadNode): string {
  const m = node?.metadata || {};
  // Keep the last successful asset visible while regenerating or after a failed
  // regeneration. Status describes the latest attempt; content describes the
  // last usable result and must not be discarded until a new result replaces it.
  return (m.content as string) || '';
}

// ---------- getCanvas ----------
export async function getCanvas(sessionId: string, epId?: string | null): Promise<Canvas | {empty: true}> {
  const proj = await getProject(sessionId).catch(() => null);
  if (!proj || !proj.payload || !proj.payload.nodes?.length) return {empty: true};
  const payload = proj.payload;
  const nodes = payload.nodes || [];
  const title = proj.title || proj.payload.title || '画布工作流';

  const split = (prefix: string) =>
    nodes.filter(n => (n.title || '').startsWith(prefix)).map((n, i) => ({
      idx: i + 1,
      name: (n.title || '').replace(/^[^｜|]*[｜|]\s*/, ''),
      image_url: urlOf(n) || undefined,
      description: n.metadata?.prompt ? n.metadata.prompt.slice(0, 400) : undefined,
    }));
  const characters = split('角色');
  const scenes = split('场景');
  const props = split('道具');
  const voices = nodes.filter(n => n.type === 'audio').map((n, i) => ({
    idx: i + 1,
    name: (n.title || '').replace(/^[^｜|]*[｜|]\s*/, ''),
    audio_url: urlOf(n) || undefined,
    description: n.metadata?.prompt ? n.metadata.prompt.slice(0, 400) : undefined,
  }));

  const allVideos = nodes.filter(n => n.type === 'video');
  const rawGroups = Array.isArray(payload.episodeImports) ? payload.episodeImports : [];
  const episodeGroups = rawGroups.length > 0
    ? rawGroups
    : [{
        id: String(payload.episodeId || 'EP001'),
        title,
        nodeIds: nodes.map((node) => node.id),
        videoNodeIds: allVideos.map((node) => node.id),
      }];
  const segments: CanvasSegment[] = [];
  const shots: CanvasShot[] = [];
  const eps: CanvasEp[] = [];
  let globalShotIdx = 0;
  for (const group of [...episodeGroups].sort((left, right) => String(left.id).localeCompare(String(right.id), undefined, {numeric: true}))) {
    const allowedIds = new Set(group.nodeIds || []);
    const declaredVideoIds = new Set(group.videoNodeIds || []);
    const groupVideos = allVideos
      .filter((node) => declaredVideoIds.size > 0
        ? declaredVideoIds.has(node.id)
        : allowedIds.has(node.id) || node.metadata?.episodeId === group.id)
      .sort((left, right) => Number(left.position?.y || 0) - Number(right.position?.y || 0));
    const groupSegments: CanvasSegment[] = [];
    const groupShots: CanvasShot[] = [];
    groupVideos.forEach((node, localIndex) => {
      const shotIdx = globalShotIdx++;
      const segmentId = localIndex + 1;
      groupSegments.push({
        segment_id: segmentId,
        ep_id: group.id,
        title: node.title || `段${segmentId}`,
        duration: null,
        shot_idxs: [shotIdx],
        scene_id: null,
        asset_binding: node.metadata?.prompt ? node.metadata.prompt.slice(0, 240) : null,
        end_state: null,
        clip_url: urlOf(node) || null,
        has_segment_video: !!urlOf(node),
        fixed_block: node.metadata?.prompt || null,
      });
      groupShots.push({
        idx: shotIdx,
        ep_id: group.id,
        segment_id: segmentId,
        char_idxs: [],
        scene_idxs: [],
        prop_idxs: [],
        voice_idxs: [],
        cam_idx: null,
        transition_type: null,
        duration: null,
        visual_desc: node.metadata?.prompt || null,
        visual_summary: null,
        ff_desc: null,
        lf_desc: null,
        motion_desc: null,
        audio_desc: null,
        action_breakdown: null,
        motion_summary: null,
        video_prompt: node.metadata?.prompt || null,
        camera_lang: null,
        person_layers: null,
        keyframe_url: null,
        clip_url: urlOf(node) || null,
        has_description: true,
      });
    });
    segments.push(...groupSegments);
    shots.push(...groupShots);
    eps.push({
      ep_id: group.id,
      title: group.title || `${group.id} · ${title}`,
      progress: null,
      storyboard_ready: true,
      shot_count: groupShots.length,
      described_count: groupShots.length,
      keyframes: 0,
      clips: groupShots.filter((shot) => shot.clip_url).length,
      final_ready: false,
      status: groupShots.length ? 'ready' : 'empty',
      updated_at: group.importedAt || null,
      segments_total: groupSegments.length,
      segments_rendered: groupSegments.filter((segment) => segment.clip_url).length,
    });
  }
  const currentEp = epId && eps.some((episode) => episode.ep_id === epId)
    ? epId
    : (eps[0]?.ep_id || 'EP001');

  return {
    meta: {session_id: sessionId, current_ep: currentEp, episodes: eps, overview: false},
    characters, scenes, props, voices, shots, segments,
  };
}

// ---------- media nodes ----------
function toMediaNode(n: PayloadNode): CanvasMediaNode {
  const m = n.metadata || {};
  const rawParams = (m.params || {}) as Record<string, unknown>;
  const voiceRefs = voiceRefList(rawParams as CanvasMediaParams);
  const kind = (n.type === 'text' ? 'text' : n.type === 'audio' ? 'audio' : n.type === 'video' ? 'video' : 'image') as CanvasMediaNode['kind'];
  const rawPreviewUrl = urlOf(n)
    || (typeof rawParams.thumbUrl === 'string' ? rawParams.thumbUrl : '')
    || (typeof (m as Record<string, unknown>).thumbnail === 'string' ? String((m as Record<string, unknown>).thumbnail) : '');
  const previewUrl = /^(?:https?:\/\/|\/uploads\/|data:|blob:)/.test(rawPreviewUrl) ? rawPreviewUrl : '';
  return {
    id: n.id,
    kind,
    title: n.title || '',
    prompt: m.prompt || '',
    mediaUrl: previewUrl || undefined,
    status: ((m.status as string) || 'idle') as CanvasMediaNode['status'],
    lastError: typeof m.lastError === 'string' ? m.lastError : undefined,
    generationStartedAt: typeof m.generationStartedAt === 'string' ? m.generationStartedAt : undefined,
    generationFinishedAt: typeof m.generationFinishedAt === 'string' ? m.generationFinishedAt : undefined,
    providerTaskStatus: typeof m.providerTaskStatus === 'string' ? m.providerTaskStatus : undefined,
    providerTaskPhase: typeof m.providerTaskPhase === 'string' ? m.providerTaskPhase : undefined,
    providerTaskProgress: Number.isFinite(Number(m.providerTaskProgress)) ? Number(m.providerTaskProgress) : undefined,
    episodeId: typeof m.episodeId === 'string' ? m.episodeId : undefined,
    actualDuration: Number.isFinite(Number(m.actualDuration)) ? Number(m.actualDuration) : undefined,
    videoValidation: m.videoValidation && typeof m.videoValidation === 'object'
      ? m.videoValidation as CanvasMediaNode['videoValidation']
      : undefined,
    storage: m.storage && typeof m.storage === 'object'
      ? m.storage as CanvasMediaNode['storage']
      : undefined,
    storageError: typeof m.storageError === 'string' ? m.storageError : undefined,
    assetKey: typeof m.assetKey === 'string' ? m.assetKey : undefined,
    promptStale: m.promptStale === true,
    promptStaleReason: typeof m.promptStaleReason === 'string' ? m.promptStaleReason : undefined,
    sourcePromptUpdatedAt: typeof m.sourcePromptUpdatedAt === 'string' ? m.sourcePromptUpdatedAt : undefined,
    reusedAsset: m.reusedAsset && typeof m.reusedAsset === 'object'
      ? m.reusedAsset as CanvasMediaNode['reusedAsset']
      : undefined,
    x: n.position?.x || 0,
    y: n.position?.y || 0,
    updatedAt: n.updatedAt || new Date().toISOString(),
    refIds: [...new Set([
      ...(m.refs || []).map((r: CanvasAssetRef) => r.nodeId),
      ...voiceRefs.map((r) => r.nodeId),
    ])],
    params: {...(m.params || {}), refs: m.refs || []},
  };
}

// 用户显式移除过的引用（targetId::refNodeId）持久化黑名单。
// 为什么需要：payload.connections（后端关系表）在 getCanvasMediaNodes 里会被重新合并进 params.refs，
// 所以只在画布上剪断连线 / 在弹窗里点 ✕ 都不够 —— 刷新一次就被 connections 补回来了，
// 表现为"图片删不掉"。黑名单让"我确实删过它"这件事跨刷新生效。
export function removedRefsKey(sessionId: string) {
  return `vimax.canvas.removedRefs.${sessionId}`;
}
export function loadRemovedRefs(sessionId: string): Set<string> {
  try {
    const raw = localStorage.getItem(removedRefsKey(sessionId));
    return new Set(raw ? (JSON.parse(raw) as string[]) : []);
  } catch {
    return new Set();
  }
}
function saveRemovedRefs(sessionId: string, set: Set<string>) {
  try {
    // 只保留最近 500 条，避免长期使用无限增长
    localStorage.setItem(removedRefsKey(sessionId), JSON.stringify([...set].slice(-500)));
  } catch {
    /* localStorage 不可用时静默 */
  }
}
export function markRefRemoved(sessionId: string, targetId: string, refNodeId: string) {
  const set = loadRemovedRefs(sessionId);
  set.add(`${targetId}::${refNodeId}`);
  saveRemovedRefs(sessionId, set);
}
export function clearRefRemoved(sessionId: string, targetId: string, refNodeId: string) {
  const set = loadRemovedRefs(sessionId);
  set.delete(`${targetId}::${refNodeId}`);
  saveRemovedRefs(sessionId, set);
}

export async function getCanvasMediaNodes(sessionId: string, epId?: string | null): Promise<CanvasMediaNode[]> {
  const proj = await getProject(sessionId).catch(() => null);
  if (!proj?.payload?.nodes) return [];
  // 文本节点（type === 'text'）此前被整类过滤掉，导致：① ＋添加里没有文本节点入口，
  // ② 导入生成的剧本头卡等文本资产在画布上根本不显示。现已作为一等公民纳入。
  const allNodes = proj.payload.nodes;
  const episodeGroup = epId
    ? (proj.payload.episodeImports || []).find((group) => group.id === epId)
    : null;
  const allowedIds = episodeGroup ? new Set(episodeGroup.nodeIds || []) : null;
  const nodes = allowedIds
    ? allNodes.filter((node) => allowedIds.has(node.id))
    : allNodes;
  // 连线数据源：项目 connections（fromNodeId=资产图/音色 → toNodeId=视频/音频消费节点）
  const nodeIds = new Set(nodes.map((node) => node.id));
  const episodeUsageCount = new Map<string, number>();
  for (const group of proj.payload.episodeImports || []) {
    for (const nodeId of group.nodeIds || []) {
      episodeUsageCount.set(nodeId, (episodeUsageCount.get(nodeId) || 0) + 1);
    }
  }
  const conns: {fromNodeId: string; toNodeId: string}[] = (proj.payload.connections || [])
    .filter((conn) => nodeIds.has(conn.fromNodeId) && nodeIds.has(conn.toNodeId));
  const typeById = new Map(nodes.map(n => [n.id, n.type]));
  // ★ 真实节点名表：引用 label **必须**用真实节点名（如「角色｜钱万金（图2）」），
  //   不能用「资产图 / 角色音色」这类泛称。
  //   为什么是致命问题：后端 _generate 会把 refs[].label 原样拼进喂给模型的 prompt
  //   （generate_image 拼「参考图N: <label> <url>」，generate_video 的 voice 走 label），
  //   7 张参考图全叫「资产图」时，模型完全无法分辨哪张是谁，生成结果必然对不上。
  //   这正是用户反复反馈「连线后引用资产对不上」的根因之一。
  const titleById = new Map(nodes.map(n => [n.id, n.title || '']));
  const labelOf = (id: string) => {
    const t = titleById.get(id);
    if (t && t.trim()) return t;
    return typeById.get(id) === 'audio' ? '角色音色' : '资产图';
  };
  // 用户显式删过的 (目标节点::引用节点)：不要再从 connections 补回来
  const removed = loadRemovedRefs(sessionId);
  const episodePositions = epId
    ? proj.payload.episodeLayouts?.[epId]
    : undefined;
  return nodes.map((node) => ({
    ...toMediaNode(node),
    sharedEpisodeCount: episodeUsageCount.get(node.id) || 1,
  })).map(mn => {
    const position = episodePositions?.[mn.id];
    if (position) mn = {...mn, x: position.x, y: position.y};
    // ★★ 第 17 轮修（用户原话「图片和音色引用显示了，那文本和视频没有引用显示」）：
    //   这里原本是 `if (mn.kind !== 'video' && mn.kind !== 'audio') return mn;`，
    //   即**图片和文本节点直接原样返回**，connections 里的关系永远不参与合并。
    //   而用户的连线是任意方向的：把剧本文本连到一张图片、把前段视频连到下一张图，
    //   这些关系全都进不了 params.refs，弹窗自然显示不出来。
    //   现在对所有非 audio 节点都做合并（audio 的音色语义单独走 voiceRef）。
    if (mn.kind === 'audio') return mn;
    const linked: string[] = [];
    const linkedVoices: string[] = [];
    for (const c of conns) {
      if (c.toNodeId !== mn.id) continue;
      if (removed.has(`${mn.id}::${c.fromNodeId}`)) continue;
      const srcType = typeById.get(c.fromNodeId);
      // 所有节点都能吃图片/文本/视频作为参考（图生图、风格说明、动作衔接）；
      // 视频额外能吃音频（音色），音频关系单独并入 voiceRefs/refIds。
      if (srcType === 'image' || srcType === 'text') linked.push(c.fromNodeId);
      if (srcType === 'video') {
        const sourceTitle = titleById.get(c.fromNodeId) || '';
        if (sourceTitle && String(mn.prompt || '').includes(sourceTitle)) linked.push(c.fromNodeId);
      }
      if (srcType === 'audio' && mn.kind === 'video') linkedVoices.push(c.fromNodeId);
    }
    // 已删除的引用还要从节点自带的 params.refs 里滤掉
    // （手动连线曾把 ref 写进节点，只靠 connections 过滤是不够的）
    const ownRefs = (mn.params?.refs || []).filter(r => !removed.has(`${mn.id}::${r.nodeId}`));
    // ★ label 就地修正（无论有没有 connections 都要做）：历史数据里的「资产图 / 角色音色」
    //   泛称会被后端原样拼进喂给模型的 prompt，必须换成真实节点名。
    //   只改 label、不动顺序（顺序决定第一张主参考图）。
    const fixedOwn = ownRefs.map(r => {
      const real = labelOf(r.nodeId);
      return r.label === real ? r : {...r, label: real};
    });
    const labelChanged = fixedOwn.some((r, i) => r !== ownRefs[i]);
    const known = new Set(ownRefs.map(r => r.nodeId));
    const extra = linked.filter(id => !known.has(id)).map(id => ({
      nodeId: id,
      label: labelOf(id),
    }));
    // 去重：同一张图片节点可能同时出现在 params.refs（手动连线写入）与
    // connections（后端关系表）里，合并后会产生重复 refId → React Flow 渲染出
    // 两条 id 相同的媒体引用边，控制台刷满 "Encountered two children with the same key"。
    const mergedRefs = [...new Map([...fixedOwn, ...extra].map(r => [r.nodeId, r])).values()];
    const ownVoices = voiceRefList(mn.params).filter(r => !removed.has(`${mn.id}::${r.nodeId}`));
    const knownVoice = new Set(ownVoices.map(r => r.nodeId));
    const mergedVoices = [
      ...ownVoices,
      ...linkedVoices.filter(id => !knownVoice.has(id)).map(id => ({nodeId: id, label: labelOf(id)})),
    ];
    const voiceChanged = mergedVoices.length !== voiceRefList(mn.params).length
      || mergedVoices.some((r, i) => r !== voiceRefList(mn.params)[i]);
    if (linked.length === 0 && linkedVoices.length === 0 && fixedOwn.length === (mn.params?.refs || []).length
      && !labelChanged && !voiceChanged && ownVoices.length === voiceRefList(mn.params).length) {
      return mn;
    }
    const params = withVoiceRefs({...mn.params, refs: mergedRefs}, mergedVoices);
    const refIds = [...new Set([
      ...(mn.refIds || []),
      ...linked,
      ...linkedVoices,
      ...mergedVoices.map((r) => r.nodeId),
    ])];
    return {...mn, refIds, params};
  });
}

export async function saveCanvasMediaNodes(sessionId: string, nodes: CanvasMediaNode[]) {
  // 批量保存：一次性 PUT 全部节点。
  // 旧实现按节点逐个 PUT，而后端每次写库都要重写整个 DB（实测 ~250ms/次），
  // 22 个节点全量保存需 5~6 秒，新建节点排在数组末尾常常还没轮到就被刷新掉 ——
  // 表现为"点了添加节点，刷新就没了"。批量接口只写一次库。
  //
  // ★★ 第 16 轮发现并修掉的**隐蔽数据覆盖**（这是「引用一直对不上」的深层原因之一）：
  //   全量 PUT 会把「前端内存里那一份**旧坐标**」整批写回服务端。
  //   于是任何一次「改提示词 / 点弹窗 / 删引用 / 上传素材」——
  //   哪怕跟位置毫无关系 —— 都会顺手把**别人（另一个标签页、自动化脚本、之前的拖拽）**
  //   刚写好的位置覆盖回旧值。实测复现：手动把某节点位置 PUT 纠正后，
  //   一个后台运行的页面在下一次自动保存时又把旧坐标刷了回去（差值 77px，
  //   且每次只覆盖位置、不覆盖其它字段，所以极难察觉）。
  //   修法：**只提交真正变过的字段**。位置没变就不带 position，
  //   后端是深合并（缺 field 即保留原值），于是"没动过的东西永远不会被写坏"。
  const payload = nodes.map((n) => {
    // 用 refIds 长度等轻量信号判断不靠谱；这里直接显式标记：
    // 调用方若只改了 metadata（prompt/refs/status），会通过 patchMediaNode 走单节点路径。
    // 全量保存只用于"节点集合发生变化"（新增/删除），此时位置必须一起带上。
    return {
      id: n.id,
      type: n.kind,
      title: n.title,
      baseUpdatedAt: n.updatedAt,
      // 拖动位置持久化：写回载荷 node.position，刷新/切集后媒体卡片保持拖过的位置
      position: {x: n.x ?? 0, y: n.y ?? 0},
      metadata: {
        prompt: n.prompt,
        status: n.status || 'idle',
        content: n.mediaUrl || '',
        refs: n.params?.refs || [],
        params: n.params || {},
        generationStartedAt: n.generationStartedAt,
        generationFinishedAt: n.generationFinishedAt,
        providerTaskStatus: n.providerTaskStatus,
        providerTaskPhase: n.providerTaskPhase,
        providerTaskProgress: n.providerTaskProgress,
        actualDuration: n.actualDuration,
        videoValidation: n.videoValidation,
        // JSON.stringify drops undefined keys, so a successful retry used to
        // leave the previous 429 message in backend metadata forever. Send an
        // explicit null for non-failed states so the server-side merge clears it.
        lastError: n.status === 'failed' ? (n.lastError || '生成失败') : null,
      },
    };
  });
  await request(`/api/projects/${encodeURIComponent(sessionId)}/nodes`, {
    method: 'PUT', body: JSON.stringify({nodes: payload}),
  });
  return {ok: true};
}

export async function saveCanvasMediaNodePositions(sessionId: string, nodes: CanvasMediaNode[], epId?: string | null) {
  await request(`/api/projects/${encodeURIComponent(sessionId)}/nodes/positions`, {
    method: 'PUT',
    body: JSON.stringify({
      episodeId: epId || null,
      positions: nodes.map((node) => ({
        id: node.id,
        position: {x: node.x ?? 0, y: node.y ?? 0},
        baseUpdatedAt: node.updatedAt,
      })),
    }),
  });
  return {ok: true};
}

/**
 * 单节点局部保存：只提交这一个节点的**非位置**字段。
 *
 * ★ 为什么必须有这个函数（第 16 轮）：上面那个全量保存会连带提交 24 个节点的位置，
 *   任何一次"改提示词""删引用""上传素材"都会把整张画布的位置写一遍 ——
 *   一旦前端内存里的位置是旧的（另一个标签页刚拖过、脚本刚改过），
 *   就会把别人的改动静默覆盖掉。生成素材、改引用这类操作**只该动 metadata**。
 *   不带 position 字段时后端深合并会保留原位置（见 canvas_store.update_project）。
 */
export async function patchMediaNodesFields(
  sessionId: string,
  nodes: CanvasMediaNode[],
  options?: {keepalive?: boolean},
): Promise<{ok: boolean}> {
  const body = {
    nodes: nodes.map((node) => ({
      id: node.id,
      type: node.kind,
      title: node.title,
      baseUpdatedAt: node.updatedAt,
      // 特意不传 position：让后端保留库里的当前坐标
      metadata: {
        prompt: node.prompt,
        status: node.status || 'idle',
        content: node.mediaUrl || '',
        refs: node.params?.refs || [],
        params: node.params || {},
        generationStartedAt: node.generationStartedAt,
        generationFinishedAt: node.generationFinishedAt,
        providerTaskStatus: node.providerTaskStatus,
        providerTaskPhase: node.providerTaskPhase,
        providerTaskProgress: node.providerTaskProgress,
        actualDuration: node.actualDuration,
        videoValidation: node.videoValidation,
        lastError: node.status === 'failed' ? (node.lastError || '生成失败') : null,
      },
    })),
  };
  await request(`/api/projects/${encodeURIComponent(sessionId)}/nodes`, {
    method: 'PUT',
    body: JSON.stringify(body),
    keepalive: options?.keepalive === true,
  });
  return {ok: true};
}

export async function patchMediaNodeFields(
  sessionId: string,
  node: CanvasMediaNode,
): Promise<{ok: boolean}> {
  return patchMediaNodesFields(sessionId, [node]);
}

// ---------- notes ----------
// 删除单个媒体/文本节点：必须有对应后端调用，否则只改本地 state，
// 刷新后节点会从 payload 里回来（后端此前没有 DELETE /nodes/{nid} 路由）。
export async function deleteCanvasMediaNode(sessionId: string, nodeId: string) {
  const res = await fetch(apiUrl(`/api/projects/${encodeURIComponent(sessionId)}/nodes/${encodeURIComponent(nodeId)}`), {method: 'DELETE', credentials: 'include'});
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw new Error((payload as {error?: string}).error || `删除失败，HTTP ${res.status}`);
  }
  return {ok: true};
}

export async function getCanvasNotes(sessionId: string): Promise<CanvasNote[]> {
  const proj = await getProject(sessionId).catch(() => null);
  return proj?.payload?.extras?.notes || [];
}

export async function saveCanvasNotes(sessionId: string, notes: CanvasNote[]) {
  // 后端 server.py 仅在 do_POST 中实现 /api/projects/{id}/notes（写回 payload.extras.notes），
  // 没有对应的 PUT 处理器；此前前端误用 PUT 导致 404，备注创建后无法落库、刷新即丢失。
  await request(`/api/projects/${encodeURIComponent(sessionId)}/notes`, {
    method: 'POST', body: JSON.stringify({notes}),
  });
  return {ok: true};
}

// ---------- shot patch（段/镜头编辑 → 存回节点 metadata.shotPatch） ----------
export async function saveCanvasShot(sessionId: string, shotIdx: number, patch: Record<string, unknown>, epId?: string | null) {
  const proj = await getProject(sessionId).catch(() => null);
  const videos = proj?.payload?.nodes?.filter(n => n.type === 'video') || [];
  const node = videos[shotIdx];
  if (!node) return {ok: false};
  await request(`/api/projects/${encodeURIComponent(sessionId)}/nodes/${encodeURIComponent(node.id)}`, {
    method: 'PUT', body: JSON.stringify({metadata: {shotPatch: patch}}),
  }).catch(() => {});
  return {ok: true};
}

// ---------- quality report（挂接机检 → QualityReport） ----------
async function gateCheck(sessionId: string, epId?: string | null): Promise<{passed: boolean; results: {ok: boolean; title: string}[]}> {
  const suffix = epId ? `?episodeId=${encodeURIComponent(epId)}` : '';
  return request(`/api/projects/${encodeURIComponent(sessionId)}/gate-check${suffix}`, {method: 'POST', body: '{}'});
}

export async function getQualityReport(sessionId: string, epId?: string | null): Promise<QualityReport> {
  const g = await gateCheck(sessionId, epId).catch(() => null);
  if (!g) {
    return {available: false, overall_score: 0, passed: false, gate_threshold: 80,
      shot_count: 0, segment_count: 0, dimensions: {}, summary: '机检不可用', top_issues: [], revision_suggestions: []};
  }
  const segs = g.results?.length || 0;
  const fails = (g.results || []).filter(r => !r.ok);
  const passCount = Math.max(0, segs - fails.length);
  const dimensions: Record<string, QualityReportDimension> = {};
  (g.results || []).forEach((r, i) => {
    dimensions[`段${i + 1}·挂接`] = {score: r.ok ? 100 : 0, weight: 1, passed: r.ok, message: r.title};
  });
  return {
    available: true,
    overall_score: segs ? Math.round((passCount / segs) * 100) : 0,
    passed: !!g.passed,
    gate_threshold: 80,
    shot_count: segs,
    segment_count: segs,
    blocking: fails.map(f => `挂接不一致：${f.title}`),
    levels: {fail: fails.length, warn: 0, info: 0},
    dimensions,
    summary: g.passed ? '挂接机检全部 PASS，可以生成' : '存在段级挂接不一致，建议核对后再生成',
    top_issues: fails.map(f => f.title),
    revision_suggestions: fails.length ? ['回到投喂稿修正 @//图 引用，重新导入后机检'] : [],
  };
}

export async function recomputeQualityReport(sessionId: string, epId?: string | null): Promise<QualityReport> {
  return getQualityReport(sessionId, epId);
}

// ---------- sendMessage / events（轮询器驱动生成） ----------
// 语义：sendMessage 立即返回；生成由 subscribeToEvents 轮询器检测 generating 节点执行
export async function sendMessage(sessionId: string, text: string): Promise<{reply: string; ok: boolean}> {
  startPolling(sessionId);   // 生成按钮 → 轮询器检测 generating 节点并执行
  return {reply: '已提交（生成由画布轮询器执行）', ok: true};
}

/**
 * 单项媒体生成：由用户点击直接触发，不再等待 2.2s 轮询器“发现”节点。
 * 这样二次生成能立刻进入提交态，并在同一个 Promise 返回后立即显示成功/失败。
 */
export async function generateCanvasNodeNow(sessionId: string, nodeId: string) {
  const key = `${sessionId}::${nodeId}`;
  if (inflight.has(key)) {
    return {deferred: true, reason: '该节点正在提交生成'};
  }
  waitingForBackend.delete(key);
  inflight.add(key);
  try {
    const result = await request(`/api/projects/${encodeURIComponent(sessionId)}/nodes/${encodeURIComponent(nodeId)}/generate`, {
      method: 'POST',
      body: '{}',
    }) as {url?: string; text?: string};
    emit({
      type: 'done',
      sessionId,
      nodeId,
      url: result.url,
      text: result.text,
    });
    return {deferred: false, ...result};
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (message.includes('GENERATION_IN_PROGRESS') || message.includes('正在生成，请勿重复提交')) {
      waitingForBackend.add(key);
      startPolling(sessionId);
      return {deferred: true, reason: message};
    }
    throw error;
  } finally {
    inflight.delete(key);
  }
}

const listeners = new Set<(event: AgentEvent) => void>();
let pollTimer: ReturnType<typeof setInterval> | null = null;
const pollSessions = new Set<string>();
const inflight = new Set<string>();   // sessionId::nodeId，跨项目也不会串号
const waitingForBackend = new Set<string>(); // 409 后只等后端完成，不再重复 POST

export function subscribeToEvents(onEvent: (event: AgentEvent) => void, onConnection: (connected: boolean) => void) {
  listeners.add(onEvent);
  onConnection(true);
  if (pollSessions.size > 0 && !pollTimer) ensurePollTimer();
  return () => {
    listeners.delete(onEvent);
  };
}

function emit(event: AgentEvent) {
  listeners.forEach(fn => { try { fn(event); } catch { /* ignore */ } });
}

// 按项目登记轮询：空闲后自动停止，切项目不会把轮询器永久绑在旧 session 上。
function ensurePollTimer() {
  if (pollTimer) return;
  pollTimer = setInterval(() => {
    for (const sessionId of [...pollSessions]) void pollProjectOnce(sessionId);
  }, 2200);
}

function stopPollTimerIfIdle() {
  if (pollSessions.size > 0 || !pollTimer) return;
  clearInterval(pollTimer);
  pollTimer = null;
}

async function pollProjectOnce(sessionId: string) {
  if (!pollSessions.has(sessionId)) return;
  let proj: Payload | null = null;
  try {
    proj = (await getProject(sessionId)).payload;
  } catch {
    return;
  }
  const generating = (proj.nodes || []).find(n => {
    const t = (n.title || '');
    // 文本节点也走同一生成管线，避免文本卡永久停在 generating。
    return (n.type === 'image' || n.type === 'video' || n.type === 'audio' || n.type === 'text')
      && n.metadata?.status === 'generating'
      && !(t.startsWith('音频') && n.metadata?.status !== 'generating');
  });
  if (generating && generating.metadata?.status === 'generating') {
    const key = `${sessionId}::${generating.id}`;
    if (waitingForBackend.has(key)) return;
    if (!inflight.has(key)) {
      inflight.add(key);
      try {
        const r = await request(`/api/projects/${encodeURIComponent(sessionId)}/nodes/${encodeURIComponent(generating.id)}/generate`, {
          method: 'POST', body: '{}',
        });
        emit({
          type: 'done',
          sessionId,
          nodeId: generating.id,
          url: (r as {url?: string}).url,
          text: (r as {text?: string}).text,
        });
      } catch (e) {
        const message = (e as Error).message || String(e);
        if (message.includes('GENERATION_IN_PROGRESS') || message.includes('正在生成，请勿重复提交')) {
          // Another request/tab owns this generation. Keep polling backend truth
          // instead of marking it failed or submitting a duplicate paid task.
          waitingForBackend.add(key);
          return;
        }
        emit({type: 'error', sessionId, nodeId: generating.id, message});
      } finally {
        inflight.delete(key);
      }
    }
    return;
  }
  const hasInflight = [...inflight].some(key => key.startsWith(`${sessionId}::`));
  if (!hasInflight) {
    for (const key of [...waitingForBackend]) {
      if (key.startsWith(`${sessionId}::`)) waitingForBackend.delete(key);
    }
    pollSessions.delete(sessionId);
    stopPollTimerIfIdle();
  }
}

function startPolling(sessionId: string) {
  pollSessions.add(sessionId);
  ensurePollTimer();
  // Do not wait a full interval before the first scan; the UI has already
  // persisted status=generating before calling sendMessage.
  void pollProjectOnce(sessionId);
}

// ---------- upload ----------
// 真上传：base64 → 后端 /api/upload 落盘 → 返回 /uploads/xxx 的稳定 URL。
// 旧实现是 `URL.createObjectURL(file)`，造出来的 blob: 地址只在当前页面会话有效，
// 刷新即 404 → 拖进画布的图全变破图（用户实测反馈过）。
// 失败时**不**回退到 blob：宁可在画布上提示上传失败，也不要给一个刷新就会坏的地址。
const MAX_UPLOAD_BYTES = 24 * 1024 * 1024;

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result || ''));
    fr.onerror = () => reject(new Error('读取文件失败'));
    fr.readAsDataURL(file);
  });
}

export async function uploadWorkspaceFile(sessionId: string, file: File) {
  if (file.size > MAX_UPLOAD_BYTES) {
    throw new Error(`文件过大（${(file.size / 1048576).toFixed(1)}MB > 24MB）`);
  }
  const dataUrl = await readFileAsDataUrl(file);
  if (!dataUrl.startsWith('data:')) throw new Error('读取文件失败');
  const res = await fetch(apiUrl('/api/upload'), {
    method: 'POST',
    credentials: 'include',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: file.name, dataUrl}),
  });
  const out = (await res.json().catch(() => ({}))) as {url?: string; name?: string; error?: string};
  if (!res.ok || !out.url) throw new Error(out.error || `上传失败（HTTP ${res.status}）`);
  return {url: out.url, name: out.name || file.name};
}

export default {};
