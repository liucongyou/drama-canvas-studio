// api.ts — 自研画布适配层：同签名对接本地后端（/api/projects/...）
// 数据映射：payload(nodes/connections) ↔ Canvas / CanvasMediaNode
// 生成：轮询器检测 generating 节点 → POST generate → done/error 事件

import {latestTaskByNode} from './generationPresentation';
import {resolveMediaVersionIndex} from './mediaVersions';
import {withRequestStatus} from './requestRetry';
import {shareTokenInPath} from './projectShare';
import {
  buildAuditQuery,
  type AuditEventPage,
  type AuditEventQuery,
} from './auditQuery';

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

/**
 * 画布事件流地址。since 用于断线重连时补齐缺失事件；不传则从服务端保留的
 * 最近事件开始补发（EventSource 会自动带上 Last-Event-ID，二选一都行）。
 */
export function projectEventsUrl(sessionId: string, since?: number): string {
  const suffix = typeof since === 'number' && since > 0 ? `?since=${since}` : '';
  return apiUrl(`/api/projects/${encodeURIComponent(sessionId)}/events${suffix}`);
}

export function shareEventsUrl(token: string, since?: number): string {
  const suffix = typeof since === 'number' && since > 0 ? `?since=${since}` : '';
  return apiUrl(`/api/share/${encodeURIComponent(token)}/events${suffix}`);
}

export function projectCursorStorageKey(sessionId: string): string {
  return `vimax.canvas.event-cursor.${sessionId}`;
}

/**
 * Browser-facing media URL. Uploaded and generated assets stay stored as
 * canonical `/uploads/...` paths, but native <img>/<video>/<audio> requests
 * must hit the API host where the HttpOnly session cookie was issued.
 */
export function mediaSourceUrl(raw: unknown): string {
  const value = String(raw || '').trim();
  if (!value || /^(?:data:|blob:)/i.test(value)) return value;
  if (value.startsWith('/uploads/')) {
    const token = currentShareToken();
    if (token) return shareMediaSourceUrl(token, value);
    return apiUrl(value);
  }
  return value;
}

export function shareMediaSourceUrl(token: string, raw: unknown): string {
  const value = String(raw || '').trim();
  if (!value || /^(?:data:|blob:)/i.test(value)) return value;
  let filename = '';
  if (value.startsWith('/uploads/')) {
    filename = value.slice('/uploads/'.length);
  } else if (API_BASE) {
    const prefix = `${API_BASE.replace(/\/+$/, '')}/uploads/`;
    if (value.startsWith(prefix)) filename = value.slice(prefix.length);
  }
  if (!filename) return value;
  if (!filename || filename.includes('/') || filename.includes('\\')) return '';
  return apiUrl(
    `/api/share/${encodeURIComponent(token)}/media/${encodeURIComponent(filename)}`,
  );
}

function mediaStorageUrl(raw: unknown): string {
  const value = String(raw || '').trim();
  if (!value) return '';
  if (value.startsWith('/uploads/')) return value;
  if (!API_BASE) return value;
  const prefix = `${API_BASE.replace(/\/+$/, '')}/uploads/`;
  return value.startsWith(prefix) ? value.slice(API_BASE.length) : value;
}

function normalizeProjectMediaUrls<T>(value: T): T {
  if (typeof value === 'string') return mediaSourceUrl(value) as T;
  if (Array.isArray(value)) return value.map((item) => normalizeProjectMediaUrls(item)) as T;
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .map(([key, item]) => [key, normalizeProjectMediaUrls(item)]),
    ) as T;
  }
  return value;
}

function normalizeSharedProjectMediaUrls<T>(value: T, token: string): T {
  if (typeof value === 'string') return shareMediaSourceUrl(token, value) as T;
  if (Array.isArray(value)) {
    return value.map((item) => normalizeSharedProjectMediaUrls(item, token)) as T;
  }
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .map(([key, item]) => [key, normalizeSharedProjectMediaUrls(item, token)]),
    ) as T;
  }
  return value;
}

function normalizeMediaForStorage<T>(value: T): T {
  if (typeof value === 'string') return mediaStorageUrl(value) as T;
  if (Array.isArray(value)) return value.map((item) => normalizeMediaForStorage(item)) as T;
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .map(([key, item]) => [key, normalizeMediaForStorage(item)]),
    ) as T;
  }
  return value;
}

function normalizeParamsForStorage(params?: CanvasMediaParams): CanvasMediaParams | undefined {
  if (!params) return params;
  return normalizeMediaForStorage(params);
}

export const AUTH_REQUIRED_EVENT = 'canvas-auth-required';

export type AgentEvent = {
  type: string;
  message?: string;
  sessionId?: string;
  [k: string]: unknown;
};

export type ProjectShareState = {
  id: string;
  projectId: string;
  createdBy: string;
  createdAt: string;
  expiresAt: string;
  revokedAt: string;
  active: boolean;
  role: 'viewer' | 'editor';
  label: string;
  canWrite: boolean;
};

export async function getProjectShares(projectId: string): Promise<{
  shares: ProjectShareState[];
}> {
  return request(`/api/projects/${encodeURIComponent(projectId)}/shares`);
}

export async function getSharedProject(token: string): Promise<{
  share: {
    projectId: string;
    expiresAt: string;
    role: 'viewer' | 'editor';
    label: string;
    canWrite: boolean;
    readOnly: boolean;
  };
  project: {
    id: string;
    title: string;
    payload: unknown;
    revision: number;
  };
}> {
  return request(`/api/share/${encodeURIComponent(token)}/project`);
}

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
  project_title?: string;
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

export type CanvasNote = {
  id: string;
  episodeId: string;
  text: string;
  x: number;
  y: number;
  updatedAt: string;
};

export type CanvasMediaNodeKind = 'text' | 'image' | 'audio' | 'video';

// 资产引用（@资产）：nodeId 是节点键，assetId 是跨集稳定的资产身份。
export type CanvasAssetRef = {
  nodeId: string;
  assetId?: string;
  seriesId?: string;
  variantKey?: string;
  label: string;
  url?: string;
};
export type CanvasVideoVersion = {
  url: string;
  /** Stable identity for this generated/imported media version. */
  versionId?: string;
  taskId?: string;
  generatedAt?: string;
  provider?: string;
  model?: string;
  providerResultUrl?: string;
  actualDuration?: number;
  validationStatus?: string;
  sourceType?: 'local' | 'generated' | string;
  fileName?: string;
  mime?: string;
  size?: number;
  sha256?: string;
  width?: number;
  height?: number;
  storage?: CanvasMediaNode['storage'];
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
  /** Current media version pointer. Legacy projects fall back to index. */
  activeVersionId?: string;
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
    maxConcurrency?: number;
  };
  pricing?: ModelPricing;
  builtin?: boolean;
};

export type DiscoveredModelSpec = {
  id: string;
  providerId: string;
  capability: 'image' | 'video' | 'audio' | 'text' | 'unknown';
  adapter: string;
  remoteModel: string;
  label: string;
  description: string;
  badge: string;
  enabled: boolean;
  defaultParams: CanvasMediaParams;
  params: Record<string, ModelParamSpec>;
  limits: Record<string, unknown>;
  pricing: Record<string, unknown>;
  alreadyConfigured?: boolean;
};

export type ProviderSpec = {
  id: string;
  name: string;
  protocol: 'ark' | 'openai' | 'dashscope' | string;
  base_url: string;
  workspace_id?: string;
  icon?: string;
  enabled: boolean;
  has_api_key: boolean;
  api_key_hint?: string;
  builtin?: boolean;
};

export type ModelCatalog = {
  providers: ProviderSpec[];
  models: ModelSpec[];
  storage?: StorageSpec;
  budget?: BudgetConfig;
};

export type BudgetConfig = {
  enabled: boolean;
  currency: 'CNY' | 'USD' | string;
  dailyLimit: number;
  monthlyLimit: number;
  maxRequestCost: number;
  allowUnknownCost: boolean;
};

export type BudgetStatus = {
  budget: BudgetConfig;
  dayStart: string;
  monthStart: string;
  daySpend: number;
  monthSpend: number;
  spendCurrency: string;
};

export type StorageSpec = {
  enabled: boolean;
  provider: 'tos' | 'aliyun_oss' | 'qiniu' | string;
  bucket: string;
  region: string;
  endpoint: string;
  internal_endpoint: string;
  public_domain: string;
  prefix: string;
  url_ttl_seconds: number;
  auto_persist_images: boolean;
  auto_persist_videos: boolean;
  auto_persist_audio: boolean;
  require_for_ark_portrait: boolean;
  has_access_key: boolean;
  access_key_hint?: string;
  has_secret_key: boolean;
  secret_key_hint?: string;
  ready: boolean;
  active?: boolean;
  configured?: boolean;
  tos_ready: boolean;
  oss_ready: boolean;
  qiniu_ready: boolean;
  last_test_ok?: boolean | null;
  last_test_error?: string;
  last_test_at?: string;
  profiles?: Record<string, {
    bucket: string;
    region: string;
    endpoint: string;
    internal_endpoint: string;
    public_domain: string;
    prefix: string;
    url_ttl_seconds: number;
    has_access_key: boolean;
    access_key_hint?: string;
    has_secret_key: boolean;
    secret_key_hint?: string;
    last_test_ok?: boolean | null;
    last_test_error?: string;
    last_test_at?: string;
  }>;
};

export type GenerationTask = {
  id: string;
  project_id: string;
  node_id: string;
  client_operation_id?: string;
  title: string;
  kind: CanvasMediaNodeKind;
  status: 'queued' | 'submitting' | 'running' | 'succeeded' | 'failed' | 'interrupted';
  phase?: string;
  progress?: number;
  provider?: string;
  model?: string;
  provider_task_id?: string;
  attempt?: number;
  estimated_cost?: number;
  cost_currency?: string;
  queue_position?: number | null;
  error?: string;
  error_kind?: 'local_validation' | 'provider' | '';
  delivery_status?: 'pending' | 'materializing' | 'ready' | 'failed' | string;
  leaseHealthy?: boolean;
  outcome?: {
    phase?: 'running' | 'unknown' | 'ready' | 'failed' | string;
    regenerate?: 'allowed' | 'confirm' | 'blocked' | string;
    reason?: string;
    requiresConfirmation?: boolean;
    providerTaskId?: string;
    providerAccepted?: boolean;
  };
  created_at: string;
  started_at?: string;
  updated_at?: string;
  finished_at?: string;
  progress_updated_at?: string;
};

export type CanvasMediaNode = {
  id: string;
  kind: CanvasMediaNodeKind;
  title: string;
  prompt: string;
  mediaUrl?: string;
  /** Persisted video cover. Never used as the playable video source. */
  thumbUrl?: string;
  /** Runtime readiness of the stored URL; not persisted as generation state. */
  mediaAvailability?: 'unknown' | 'available' | 'missing';
  status?: 'idle' | 'generating' | 'done' | 'failed';
  lastError?: string;
  generationStartedAt?: string;
  generationFinishedAt?: string;
  providerTaskStatus?: string;
  providerTaskPhase?: string;
  providerTaskProgress?: number;
  providerTaskProgressChangedAt?: string;
  providerTaskId?: string;
  providerTaskStatusRaw?: string;
  providerSubmittedAt?: string;
  providerResultUrl?: string;
  // 供应商已出结果、本机还没落盘归档：此时卡片已可显示，但任务行仍是 running
  finalizePending?: boolean;
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
    url?: string;
  };
  storageError?: string;
  source?: {
    type?: 'local' | 'generated' | string;
    fileName?: string;
    mime?: string;
    size?: number;
    uploadedAt?: string;
    sha256?: string;
    width?: number;
    height?: number;
  };
  /** Transient command flag: allows one explicit local media import to replace content. */
  _mediaImport?: boolean;
  /** Transient command flag: allows one explicit switch to a known media version. */
  _mediaVersionSelection?: boolean;
  assetKey?: string;
  assetId?: string;
  seriesId?: string;
  variantKey?: string;
  semanticCode?: string;
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
  positionUpdatedAt?: string;
  refIds?: string[];
  params?: CanvasMediaParams;
};

export function friendlyMediaError(raw?: string): string {
  const text = String(raw || '').trim();
  if (!text) return '';
  const lower = text.toLowerCase();
  if (lower.includes('overdue') || lower.includes('insufficient') || lower.includes('balance') || lower.includes('quota') || text.includes('余额') || text.includes('欠费') || text.includes('额度')) {
    return '供应商账户余额或额度不足。请先充值或更换供应商，再重新生成。';
  }
  if (lower.includes('invalid api key') || lower.includes('unauthorized') || lower.includes('forbidden') || text.includes('鉴权失败')) {
    return '供应商鉴权失败。请检查 API Key、工作空间和模型权限。';
  }
  if (lower.includes('http 429') || lower.includes('rate limit') || lower.includes('too many') || text.includes('过于频繁')) {
    return '供应商限流。请等待 30–60 秒后再试，不要连续重复提交。';
  }
  if (lower.includes('video_queue_full') || text.includes('视频队列已满') || lower.includes('queue_full')) {
    return '供应商视频队列已满，本次未创建任务。请等待 30–60 秒后再试。';
  }
  if (lower.includes('model_not_found') || lower.includes('no available channel') || text.includes('无可用通道')) {
    return '供应商没有该模型的可用通道，请检查模型是否启用或分组路由是否正确。';
  }
  return text;
}

export function isLiveProviderGeneration(
  node?: Pick<CanvasMediaNode, 'providerTaskStatus' | 'lastError' | 'mediaUrl' | 'status' | 'finalizePending'> | null,
): boolean {
  if (!node) return false;
  if (node.status === 'done' && node.mediaUrl) return false;
  if (node.finalizePending && node.mediaUrl) return false;
  // A terminal card status is stronger than a provider phase string. The
  // persisted error/uncertain state is final from the UI's perspective and must
  // not be turned back into an infinite spinner by the status pairing below.
  if (node.status === 'failed' && String(node.providerTaskStatus || '').toLowerCase() !== 'failed') {
    return false;
  }
  const status = String(node.providerTaskStatus || '').trim().toLowerCase();
  if (['queued', 'pending', 'created', 'submitted', 'running', 'processing', 'retrying', 'in_progress'].includes(status)) {
    return true;
  }
  const error = String(node.lastError || '');
  return /执行租约已过期|供应商任务仍在执行/.test(error);
}

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

export function assetRefFromNode(
  node: Pick<CanvasMediaNode, 'id' | 'title' | 'kind' | 'mediaUrl' | 'assetId' | 'seriesId' | 'variantKey'>,
  label?: string,
): CanvasAssetRef {
  return {
    nodeId: node.id,
    ...(node.assetId ? {assetId: node.assetId} : {}),
    ...(node.seriesId ? {seriesId: node.seriesId} : {}),
    ...(node.variantKey ? {variantKey: node.variantKey} : {}),
    label: String(label || node.title || node.kind),
    ...(node.mediaUrl ? {url: node.mediaUrl} : {}),
  };
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
  const versions = source.map((version, index) => ({
    ...version,
    versionId: version?.versionId
      || `${version?.taskId || version?.generatedAt || 'legacy'}:${version?.url || index}`,
    url: mediaSourceUrl(version?.url),
  })).filter((version) => {
    const url = version.url.trim();
    const versionId = String(version.versionId || '');
    if (!url || seen.has(versionId)) return false;
    seen.add(versionId);
    return true;
  });
  const currentUrl = mediaSourceUrl(node.mediaUrl);
  const currentVersionId = String(node.params?.activeVersionId || '').trim();
  const currentUrlIndex = currentUrl
    ? versions.findIndex((version) => version.url === currentUrl)
    : -1;
  if (
    currentUrl
    && currentUrlIndex < 0
  ) {
    versions.push({
      versionId: currentVersionId || `current:${currentUrl}`,
      url: currentUrl,
    });
  }
  return versions;
}

export function activeMediaVersionIndex(
  node: CanvasMediaNode,
  versions = mediaVersionList(node),
): number {
  return resolveMediaVersionIndex(
    mediaSourceUrl(node.mediaUrl),
    versions,
    {
      activeVersionId: node.params?.activeVersionId,
      index: node.params?.mediaVersionIndex ?? node.params?.videoVersionIndex,
    },
  );
}

/**
 * Apply an explicit version selection without letting its old prompt pointer
 * overwrite the active media pointer during concurrent workspace refresh.
 */
export function preserveSelectedMediaVersion(
  local: CanvasMediaNode,
  remote: CanvasMediaNode,
): Partial<CanvasMediaNode> {
  const localParams = local.params || {};
  const remoteParams = remote.params || {};
  const locallySelected = local._mediaVersionSelection === true;
  const localCurrentIndex = activeMediaVersionIndex(local);
  const remoteCurrentIndex = activeMediaVersionIndex(remote);
  const currentIndex = locallySelected
    ? localCurrentIndex
    : remoteCurrentIndex;
  const merged: Partial<CanvasMediaNode> = {
    prompt: local.prompt,
    params: {
      ...remoteParams,
      ...localParams,
      ...(remoteParams.mediaVersions
        ? {
            mediaVersions: remoteParams.mediaVersions,
            mediaVersionIndex: currentIndex,
            activeVersionId: locallySelected
              ? localParams.activeVersionId
              : remoteParams.activeVersionId,
          }
        : {}),
      ...(remoteParams.videoVersions
        ? {
            videoVersions: remoteParams.videoVersions,
            videoVersionIndex: currentIndex,
            activeVersionId: locallySelected
              ? localParams.activeVersionId
              : remoteParams.activeVersionId,
          }
        : {}),
    },
  };
  if (!locallySelected) return merged;
  return {
    ...merged,
    mediaUrl: local.mediaUrl,
    params: {
      ...merged.params,
      activeVersionId: localParams.activeVersionId,
    },
    actualDuration: local.actualDuration,
    storage: local.storage,
    providerResultUrl: local.providerResultUrl,
    source: local.source,
  };
}

export type QualityReportDimension = {
  score: number;
  weight: number;
  passed: boolean;
  message: string;
  segment_number?: number;
  detail?: string;
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
function friendlyRequestError(status: number, raw: string, url = ''): string {
  const text = String(raw || '').trim();
  const lower = text.toLowerCase();
  const providerRequest = (
    url.includes('/generate')
    || url.includes('/model-catalog')
    || url.includes('/storage')
    || lower.includes('provider')
    || lower.includes('supplier')
    || lower.includes('供应商')
  );
  if (lower.includes('tos_account_overdue') || (text.includes('TOS') && text.includes('欠费'))) {
    return text;
  }
  if (status === 429 || lower.includes('http 429') || lower.includes('too many') || lower.includes('rate limit') || text.includes('过于频繁')) {
    return '请求过于频繁，供应商正在限流。请等待 30–60 秒后再试，不要重复连续提交。';
  }
  if (lower.includes('insufficient') || lower.includes('balance') || lower.includes('quota') || text.includes('余额') || text.includes('额度') || text.includes('欠费')) {
    return '供应商账户余额或额度不足。请先检查对应供应商账户与 API Key，再重新生成。';
  }
  if (lower.includes('read operation timed out') || lower.includes('timed out')) {
    return '供应商提交接口超时，未取得任务 ID。系统已结束本次提交，请先更新对应供应商 API Key 并测试连接，再重新生成。';
  }
  if (lower.includes('auth') || lower.includes('invalid api key') || status === 401 || status === 403) {
    return '供应商鉴权失败。请在「模型接口」中检查 API Key、工作空间和模型权限。';
  }
  if (
    lower.includes('model_not_found')
    || lower.includes('no available channel')
    || text.includes('无可用通道')
  ) {
    return `供应商没有该模型的可用通道。请在「模型接口」中检查模型是否已启用、分组或路由是否正确。详细信息：${text.slice(0, 240)}`;
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
    if (providerRequest) {
      return `供应商服务暂时异常（HTTP ${status}）。请稍后重试；已有任务 ID 时优先在任务中心继续等待。`;
    }
    return `服务端暂时无法完成请求（HTTP ${status}）。画布内容仍保留在当前页面，请稍后重试保存。`;
  }
  return text || `请求失败（HTTP ${status}）`;
}

/**
 * 默认请求超时。
 *
 * 没有超时的 `fetch` 在网络半开、代理缓冲响应、后端线程卡在供应商调用时会**永久挂起**：
 * Promise 既不 resolve 也不 reject，调用方的 `finally` 永远不执行。生成链路踩到这条时
 * 后果最重——卡片永久停在「生成中」，模块级 `inflight` 锁不清空，该节点在本次页面
 * 会话内再也提交不上去，用户只能刷新。60 秒对绝大多数接口都足够宽松，个别长任务
 * 用 `timeoutMs` 显式放宽，传 0 表示不限。
 */
const DEFAULT_REQUEST_TIMEOUT_MS = 60_000;

function currentShareToken(): string {
  if (typeof window === 'undefined' || !window.location) return '';
  return shareTokenInPath(window.location.pathname);
}

function currentShareRequestHeaders(): Record<string, string> {
  const token = currentShareToken();
  return token ? {'X-Share-Token': token} : {};
}

async function request<T>(
  url: string,
  init: RequestInit & {timeoutMs?: number} = {},
): Promise<T> {
  const {timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS, ...rest} = init;
  const controller = timeoutMs > 0 ? new AbortController() : null;
  const external = rest.signal;
  const onExternalAbort = () => controller?.abort();
  let timedOut = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let rejectTimeout: ((error: unknown) => void) | undefined;
  const timeoutGuard = (
    timeoutMs > 0 && controller
      ? new Promise<never>((_resolve, reject) => {
          rejectTimeout = reject;
        })
      : null
  );
  if (controller && external) {
    if (external.aborted) controller.abort();
    else external.addEventListener('abort', onExternalAbort, {once: true});
  }
  if (controller && timeoutMs > 0) {
    timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
      rejectTimeout?.(withRequestStatus(
        new Error(`请求超过 ${Math.round(timeoutMs / 1000)} 秒没有响应，请检查网络后重试`),
        408,
      ));
    }, timeoutMs);
  }
  let response: Response;
  let payload: unknown;
  try {
    const operation = (async () => {
      const fetched = await fetch(apiUrl(url), {
        ...rest,
        ...(controller ? {signal: controller.signal} : {}),
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          ...currentShareRequestHeaders(),
          ...(rest.headers || {}),
        },
      });
      // Keep the same timeout budget through body consumption. Some runtimes
      // do not reject a response stream when its fetch signal aborts, so the
      // outer timeoutGuard below also guarantees the whole operation settles.
      const body = await fetched.json().catch(() => ({}));
      return {fetched, body};
    })();
    const completed = (
      timeoutGuard
        ? await Promise.race([operation, timeoutGuard])
        : await operation
    );
    response = completed.fetched;
    payload = completed.body;
  } catch (error) {
    if (timedOut) {
      throw withRequestStatus(
        new Error(`请求超过 ${Math.round(timeoutMs / 1000)} 秒没有响应，请检查网络后重试`),
        408,
      );
    }
    throw error;
  } finally {
    if (timer) clearTimeout(timer);
    external?.removeEventListener('abort', onExternalAbort);
  }
  if (!response.ok) {
    const code = String((payload as {code?: string}).code || '');
    // Provider auth failures are also HTTP 401, but they do not mean the
    // canvas login session expired. Only the server's auth guards may logout.
    if (
      response.status === 401
      && !url.startsWith('/api/share/')
      && typeof window !== 'undefined'
      && ['ACCOUNT_AUTH_REQUIRED', 'TENANT_AUTH_REQUIRED'].includes(code)
    ) {
      window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
    }
    const raw = (payload as {error?: string}).error || `请求失败，HTTP ${response.status}`;
    if (raw === 'not found') {
      throw withRequestStatus(
        new Error(`接口不存在（HTTP 404）：${url.split('?', 1)[0]}`),
        response.status,
      );
    }
    const preserveServerMessage = (
      code.startsWith('AUTH_')
      || code.startsWith('ACCOUNT_')
      || code === 'ADMIN_REQUIRED'
      || code === 'PASSWORD_TOO_SHORT'
      || code === 'CURRENT_PASSWORD_INVALID'
      || code === 'USE_CHANGE_PASSWORD'
      || code === 'ORIGIN_REJECTED'
      || code === 'CSRF_REJECTED'
    );
    throw withRequestStatus(
      new Error(
        preserveServerMessage
          ? raw
          : friendlyRequestError(response.status, raw, url),
      ),
      response.status,
    );
  }
  return payload as T;
}

export async function getAuthSession(): Promise<{
  authenticated: boolean;
  username?: string;
  tenantId?: string;
  slug?: string;
  role?: 'admin' | 'member' | string;
  isAdmin?: boolean;
  /** 本机单用户模式（无账号表）：服务端只在 CANVAS_AUTH_MODE != accounts 时回 true。 */
  local?: boolean;
  preferences?: {
    canvasBackgroundMode?: 'dots' | 'lines' | 'blank';
    theme?: 'light' | 'dark' | 'immersive' | 'forest' | 'copper';
    [key: string]: unknown;
  };
}> {
  return request('/api/auth/session');
}

export async function saveTenantPreferences(updates: {
  canvasBackgroundMode?: 'dots' | 'lines' | 'blank';
  theme?: 'light' | 'dark' | 'immersive' | 'forest' | 'copper';
}): Promise<{
  ok: boolean;
  preferences: {
    canvasBackgroundMode?: 'dots' | 'lines' | 'blank';
    theme?: 'light' | 'dark' | 'immersive' | 'forest' | 'copper';
    [key: string]: unknown;
  };
}> {
  return request('/api/preferences', {
    method: 'PUT',
    body: JSON.stringify(updates),
  });
}

export async function loginAccount(username: string, password: string): Promise<{
  authenticated: boolean;
  username?: string;
  tenantId?: string;
  slug?: string;
  role?: 'admin' | 'member' | string;
  isAdmin?: boolean;
  // 后端登录响应与 /api/auth/session 对称返回偏好；缺这个字段会让
  // setPreferences(session.preferences || {}) 永远落空。
  preferences?: {
    canvasBackgroundMode?: 'dots' | 'lines' | 'blank';
    theme?: 'light' | 'dark' | 'immersive' | 'forest' | 'copper';
    [key: string]: unknown;
  };
}> {
  return request('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({username, password}),
  });
}

export type ManagedAccount = {
  id: string;
  username: string;
  role: string;
  status: string;
  tenantId: string;
  slug: string;
  createdAt: string;
  lastLoginAt?: string;
  lastSeenAt?: string;
  online?: boolean;
  lockedUntil?: string;
};

export type ManagedAccountsSnapshot = {
  accounts: ManagedAccount[];
  onlineCount: number;
  onlineUsers: Array<{
    id: string;
    username: string;
    lastSeenAt?: string;
  }>;
  generatedAt: string;
};

export async function getManagedAccounts(): Promise<ManagedAccountsSnapshot> {
  const payload = await request<{
    accounts: ManagedAccount[];
    onlineCount?: number;
    onlineUsers?: ManagedAccountsSnapshot['onlineUsers'];
    generatedAt?: string;
  }>('/api/admin/accounts');
  const accounts = payload.accounts || [];
  return {
    accounts,
    onlineCount: Number(payload.onlineCount || accounts.filter((item) => item.online).length),
    onlineUsers: payload.onlineUsers || accounts
      .filter((item) => item.online)
      .map((item) => ({id: item.id, username: item.username, lastSeenAt: item.lastSeenAt})),
    generatedAt: payload.generatedAt || new Date().toISOString(),
  };
}

export async function getManagedTenantAudit(
  tenantId: string,
  query: AuditEventQuery = {},
): Promise<AuditEventPage> {
  const normalized = String(tenantId || '').trim();
  if (!normalized) throw new Error('查看审计必须指定租户');
  const suffix = buildAuditQuery(query);
  return request(
    `/api/admin/tenants/${encodeURIComponent(normalized)}/audit${suffix ? `?${suffix}` : ''}`,
  );
}

export async function createProjectShare(
  projectId: string,
  ttlDays = 7,
  role: 'viewer' | 'editor' = 'viewer',
  label = '',
): Promise<{
  id: string;
  projectId: string;
  url: string;
  token: string;
  expiresAt: string;
  role: 'viewer' | 'editor';
  label: string;
  canWrite: boolean;
  readOnly: boolean;
}> {
  return request(
    `/api/projects/${encodeURIComponent(projectId)}/shares`,
    {
      method: 'POST',
      body: JSON.stringify({ttlDays, role, label}),
    },
  );
}

export async function revokeProjectShare(
  projectId: string,
  shareId: string,
): Promise<{ok: boolean; shareId: string}> {
  return request(
    `/api/projects/${encodeURIComponent(projectId)}/shares/${encodeURIComponent(shareId)}`,
    {method: 'DELETE'},
  );
}

export async function getAuditHealth(): Promise<{
  auditBytes: number;
  databaseBytes: number;
  freeBytes: number;
  lastAuditAt: string;
}> {
  return request('/api/admin/audit/health');
}

export async function createManagedAccount(username: string, password: string): Promise<{
  ok: boolean;
  accountId: string;
  username: string;
  tenantId: string;
  slug: string;
}> {
  return request('/api/admin/accounts', {
    method: 'POST',
    body: JSON.stringify({username, password}),
  });
}

export async function resetManagedAccountPassword(accountId: string, password: string): Promise<{
  ok: boolean;
  accountId: string;
  username: string;
}> {
  return request(`/api/admin/accounts/${encodeURIComponent(accountId)}/reset-password`, {
    method: 'POST',
    body: JSON.stringify({password}),
  });
}

export async function setManagedAccountStatus(
  accountId: string,
  status: 'active' | 'disabled',
): Promise<{ok: boolean; accountId: string; username: string; status: string}> {
  return request(`/api/admin/accounts/${encodeURIComponent(accountId)}/status`, {
    method: 'POST',
    body: JSON.stringify({status}),
  });
}

export async function logoutAccount(): Promise<{ok: boolean}> {
  return request('/api/auth/logout', {method: 'POST', body: '{}'});
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<{ok: boolean}> {
  return request('/api/auth/change-password', {
    method: 'POST',
    body: JSON.stringify({currentPassword, newPassword}),
  });
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

export async function getGenerationTasks(
  projectId: string,
  limit = 80,
  episodeId?: string | null,
): Promise<GenerationTask[]> {
  const episodeQuery = episodeId
    ? `&episodeId=${encodeURIComponent(episodeId)}`
    : '';
  const data = await request<{tasks: GenerationTask[]}>(
    `/api/generation-tasks?projectId=${encodeURIComponent(projectId)}&limit=${limit}${episodeQuery}`,
  );
  return data.tasks || [];
}

export type ProjectVersion = {
  id: string;
  title: string;
  nodeCount: number;
  connectionCount: number;
  revision: number;
  kind: 'auto' | 'manual' | string;
  createdAt: string;
};

export type ProjectVersionPreview = ProjectVersion & {
  currentRevision: number;
  diff: {
    nodesAddedByRestore: number;
    nodesRemovedByRestore: number;
    promptsChanged: number;
    mediaChanged: number;
  };
};

export async function getProjectVersions(projectId: string): Promise<{
  currentRevision: number;
  versions: ProjectVersion[];
}> {
  return request(`/api/projects/${encodeURIComponent(projectId)}/versions`);
}

export async function createProjectVersion(
  projectId: string,
  title: string,
  ifRevision?: number,
): Promise<{version: ProjectVersion; currentRevision: number}> {
  return request(`/api/projects/${encodeURIComponent(projectId)}/versions`, {
    method: 'POST',
    body: JSON.stringify({title, if_revision: ifRevision}),
  });
}

export async function previewProjectVersion(
  projectId: string,
  versionId: string,
): Promise<ProjectVersionPreview> {
  return request(
    `/api/projects/${encodeURIComponent(projectId)}/versions/${encodeURIComponent(versionId)}`,
  );
}

export async function restoreProjectVersion(
  projectId: string,
  versionId: string,
  ifRevision: number,
): Promise<{id: string; title: string; revision: number; restoredFrom: string}> {
  return request(
    `/api/projects/${encodeURIComponent(projectId)}/versions/${encodeURIComponent(versionId)}/restore`,
    {
      method: 'POST',
      body: JSON.stringify({if_revision: ifRevision}),
    },
  );
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

export async function discoverProviderModels(providerId: string): Promise<{
  ok: boolean;
  providerId: string;
  models: DiscoveredModelSpec[];
  count: number;
}> {
  return request(`/api/providers/${encodeURIComponent(providerId)}/models`, {
    method: 'POST',
    body: '{}',
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

export async function activateStorage(
  provider: 'tos' | 'aliyun_oss' | 'qiniu',
): Promise<StorageSpec> {
  const payload = await request<{ok: boolean; storage: StorageSpec}>('/api/storage/activate', {
    method: 'POST',
    body: JSON.stringify({provider}),
  });
  return payload.storage;
}

export async function deactivateStorage(): Promise<StorageSpec> {
  const payload = await request<{ok: boolean; storage: StorageSpec}>('/api/storage/deactivate', {
    method: 'POST',
    body: '{}',
  });
  return payload.storage;
}

export async function testStorageConfig(provider: 'tos' | 'aliyun_oss' | 'qiniu' = 'tos'): Promise<{ok: boolean; message: string; url?: string}> {
  return request('/api/storage/test', {
    method: 'POST',
    body: JSON.stringify({provider}),
  });
}

export async function getBudgetStatus(): Promise<BudgetStatus> {
  return request('/api/budget');
}

export async function saveBudgetConfig(budget: BudgetConfig): Promise<{ok: boolean} & BudgetStatus> {
  return request('/api/budget', {
    method: 'POST',
    body: JSON.stringify({budget}),
  });
}

type Payload = {
  nodes?: PayloadNode[];
  connections?: {id?: string; fromNodeId: string; toNodeId: string}[];
  notes?: CanvasNote[];
  episodeViewports?: Record<string, unknown>;
  title?: string;
  extras?: Record<string, unknown>;
  episodeId?: string;
  episodeTitle?: string;
  episodeImports?: Array<{
    id: string;
    title?: string;
    nodeIds?: string[];
    videoNodeIds?: string[];
    connectionIds?: string[];
    importedAt?: string;
    shot_count?: number;
    described_count?: number;
    storyboard_ready?: boolean;
  }>;
  episodeLayouts?: Record<string, Record<string, {x: number; y: number}>>;
};
type PayloadNode = {
  id: string;
  type: string;
  title?: string;
  position?: {x: number; y: number};
  updatedAt?: string;
  positionUpdatedAt?: string;
  metadata?: Record<string, unknown> & {
    prompt?: string; content?: string; status?: string;
    refs?: CanvasAssetRef[]; params?: CanvasMediaParams; lastError?: string; shotPatch?: Record<string, unknown>;
    promptStale?: boolean; promptStaleReason?: string; sourcePromptUpdatedAt?: string;
    generationStartedAt?: string; generationFinishedAt?: string;
    providerTaskStatus?: string; providerTaskPhase?: string; providerTaskProgress?: number; providerTaskProgressChangedAt?: string;
    providerTaskId?: string; providerTaskStatusRaw?: string;
    actualDuration?: number; videoValidation?: Record<string, unknown>;
    episodeId?: string;
  };
};

export type ProjectResponse = {
  id: string;
  payload: Payload;
  title?: string;
  revision?: number;
};

export type WorkspaceIndex = {
  id: string;
  title: string;
  revision: number;
  updatedAt: string;
  projectMeta: Record<string, unknown>;
  episodes: Array<{
    id: string;
    title?: string;
    nodeIds?: string[];
    videoNodeIds?: string[];
    connectionIds?: string[];
    [key: string]: unknown;
  }>;
  episodeViewports: Record<string, unknown>;
};

export type WorkspaceProjectMeta = {
  id: string;
  title: string;
  revision: number;
  updatedAt: string;
  projectMeta: Record<string, unknown>;
  episodes: WorkspaceIndex['episodes'];
  episodeViewports: Record<string, unknown>;
};

export type WorkspaceFacts = {
  canvas: Canvas | {empty: true};
  notes: CanvasNote[];
  mediaNodes: CanvasMediaNode[];
  episodeViewports: Record<string, unknown>;
  activeEpisodeId: string | null;
  connections: Array<{
    id?: string;
    fromNodeId: string;
    toNodeId: string;
    source?: string;
  }>;
  tasks: GenerationTask[];
};

export type WorkspaceNodes = {
  episodeId: string;
  episode: Record<string, unknown>;
  nodes: PayloadNode[];
  connections: Array<{id?: string; fromNodeId: string; toNodeId: string; source?: string}>;
  layouts: Record<string, {x: number; y: number}>;
  notes?: CanvasNote[];
};

export async function getWorkspaceIndex(
  sessionId: string,
  signal?: AbortSignal,
): Promise<WorkspaceIndex> {
  const route = sessionId.startsWith('share:')
    ? `/api/share/${encodeURIComponent(sessionId.slice(6))}/workspace-index`
    : `/api/projects/${encodeURIComponent(sessionId)}/workspace-index`;
  return request<WorkspaceIndex>(route, {signal}).then((workspace) => (
    sessionId.startsWith('share:')
      ? normalizeSharedProjectMediaUrls(workspace, sessionId.slice(6))
      : normalizeProjectMediaUrls(workspace)
  ));
}

function workspaceReadSessionId(sessionId: string): string {
  return sessionId.startsWith('share:') ? sessionId.slice(6) : sessionId;
}

export async function getWorkspaceNodes(
  sessionId: string,
  episodeId?: string | null,
  signal?: AbortSignal,
): Promise<WorkspaceNodes> {
  const suffix = episodeId
    ? `?episodeId=${encodeURIComponent(episodeId)}`
    : '';
  const route = sessionId.startsWith('share:')
    ? `/api/share/${encodeURIComponent(workspaceReadSessionId(sessionId))}/workspace-nodes${suffix}`
    : `/api/projects/${encodeURIComponent(sessionId)}/workspace-nodes${suffix}`;
  return request<WorkspaceNodes>(route, {signal}).then((workspace) => (
    sessionId.startsWith('share:')
      ? normalizeSharedProjectMediaUrls(workspace, workspaceReadSessionId(sessionId))
      : normalizeProjectMediaUrls(workspace)
  ));
}

/**
 * Read the current episode's card projection without loading the full project
 * document. Modal opens use this so dependency readiness (image/audio/video/
 * text references) is checked against the same backend snapshot the cards use.
 */
export async function getWorkspaceMediaNodes(
  sessionId: string,
  episodeId: string,
  signal?: AbortSignal,
): Promise<CanvasMediaNode[]> {
  const normalizedEpisode = String(episodeId || '').trim().toUpperCase();
  if (!normalizedEpisode) throw new Error('读取画布节点必须指定集号');
  const nodesPayload = await getWorkspaceNodes(
    sessionId,
    normalizedEpisode,
    signal,
  );
  if (
    !Array.isArray(nodesPayload.nodes)
    || !Array.isArray(nodesPayload.connections)
  ) {
    throw withRequestStatus(
      new Error('workspace nodes contract unavailable'),
      501,
    );
  }
  const episodeTitle = String(
    nodesPayload.episode?.title || normalizedEpisode,
  );
  const pseudoProject: ProjectResponse = {
    id: sessionId,
    title: episodeTitle,
    payload: {
      id: sessionId,
      title: episodeTitle,
      nodes: nodesPayload.nodes,
      connections: nodesPayload.connections,
      episodeImports: [{
        ...nodesPayload.episode,
        id: normalizedEpisode,
        title: episodeTitle,
        nodeIds: nodesPayload.nodes
          .map((node) => String(node.id || ''))
          .filter(Boolean),
        videoNodeIds: nodesPayload.nodes
          .filter((node) => node.type === 'video')
          .map((node) => String(node.id || ''))
          .filter(Boolean),
      }],
      episodeLayouts: {
        [normalizedEpisode]: nodesPayload.layouts || {},
      },
      extras: {},
    } as Payload,
  };
  return getCanvasMediaNodes(
    sessionId,
    normalizedEpisode,
    pseudoProject,
  );
}

export async function getWorkspaceFacts(
  sessionId: string,
  episodeId: string,
  signal?: AbortSignal,
): Promise<WorkspaceFacts> {
  return getProjectionCanvasWorkspace(
    sessionId,
    episodeId,
    signal,
  );
}

function workspaceProjectSnapshot(
  index: WorkspaceProjectMeta,
  episodeId: string,
): ProjectResponse {
  const projectMeta = index.projectMeta || {};
  return {
    id: index.id,
    title: index.title,
    revision: index.revision,
    payload: {
      ...(projectMeta as Record<string, unknown>),
      id: index.id,
      title: index.title,
      revision: index.revision,
      updatedAt: index.updatedAt,
      nodes: [],
      connections: [],
      episodeImports: index.episodes,
      episodeViewports: index.episodeViewports,
      episodeLayouts: {},
      extras: (
        projectMeta.extras
        && typeof projectMeta.extras === 'object'
      )
        ? projectMeta.extras as Record<string, unknown>
        : {},
    } as Payload,
  };
}

export function getWorkspaceShell(
  index: WorkspaceProjectMeta,
  episodeId: string,
): {
  canvas: Canvas | {empty: true};
  notes: CanvasNote[];
  mediaNodes: CanvasMediaNode[];
  episodeViewports: Record<string, unknown>;
  activeEpisodeId: string | null;
  connections: Array<{
    id?: string;
    fromNodeId: string;
    toNodeId: string;
    source?: string;
  }>;
  tasks: GenerationTask[];
} {
  const normalizedEpisode = String(episodeId || '').trim().toUpperCase()
    || null;
  const pseudoProject = workspaceProjectSnapshot(
    index,
    normalizedEpisode || 'EP001',
  );
  const canvas = getCanvasSync(
    index.id,
    normalizedEpisode,
    pseudoProject,
  );
  return {
    canvas,
    notes: [],
    mediaNodes: [],
    episodeViewports: index.episodeViewports || {},
    activeEpisodeId: normalizedEpisode,
    connections: [],
    tasks: [],
  };
}

export async function getProject(pid: string): Promise<ProjectResponse> {
  const project = await request<ProjectResponse>(
    `/api/projects/${encodeURIComponent(pid)}`,
  );
  return normalizeProjectMediaUrls(project);
}

function urlOf(node?: PayloadNode): string {
  const m = node?.metadata || {};
  // Keep the last successful asset visible while regenerating or after a failed
  // regeneration. Status describes the latest attempt; content describes the
  // last usable result and must not be discarded until a new result replaces it.
  return (m.content as string) || '';
}

// ---------- getCanvas ----------
export async function getCanvas(
  sessionId: string,
  epId?: string | null,
  projectSnapshot?: ProjectResponse | null,
): Promise<Canvas | {empty: true}> {
  const proj = projectSnapshot === undefined
    ? await getProject(sessionId).catch(() => null)
    : projectSnapshot;
  return getCanvasSync(sessionId, epId, proj);
}

function getCanvasSync(
  sessionId: string,
  epId?: string | null,
  proj?: ProjectResponse | null,
): Canvas | {empty: true} {
  if (!proj || !proj.payload) return {empty: true};
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
      // The workspace index already knows each episode's planned shot count.
      // Deriving all episodes from the current episode's nodes makes every
      // unopened episode look like "0镜" until it is selected.
      storyboard_ready: group.storyboard_ready ?? true,
      shot_count: typeof group.shot_count === 'number'
        ? group.shot_count
        : groupShots.length,
      described_count: typeof group.described_count === 'number'
        ? group.described_count
        : groupShots.length,
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
    meta: {
      session_id: sessionId,
      current_ep: currentEp,
      episodes: eps,
      project_title: title,
      overview: false,
    },
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
  // Video playback must use metadata.content; thumbUrl is only a poster.
  const mediaUrl = kind === 'video'
    ? urlOf(n)
    : rawPreviewUrl;
  const normalizedMediaUrl = /^(?:https?:\/\/|\/uploads\/|\/api\/share\/[^/]+\/media\/|data:|blob:)/.test(mediaUrl)
    ? mediaUrl
    : '';
  const videoThumbUrl = kind === 'video'
    && typeof rawParams.thumbUrl === 'string'
    && /^(?:https?:\/\/|\/uploads\/|\/api\/share\/[^/]+\/media\/|data:|blob:)/.test(rawParams.thumbUrl)
    ? rawParams.thumbUrl
    : undefined;
  return {
    id: n.id,
    kind,
    title: n.title || '',
    prompt: m.prompt || '',
    mediaUrl: normalizedMediaUrl || undefined,
    thumbUrl: kind === 'video' && normalizedMediaUrl ? videoThumbUrl : undefined,
    // Legacy persisted rows may still say "error"; normalize that at the raw
    // boundary before the client-facing union narrows it to terminal states.
    status: (
      m.status === 'error'
        ? 'failed'
        : ((m.status as string) || 'idle')
    ) as CanvasMediaNode['status'],
    lastError: typeof m.lastError === 'string' ? (friendlyMediaError(m.lastError) || undefined) : undefined,
    generationStartedAt: typeof m.generationStartedAt === 'string' ? m.generationStartedAt : undefined,
    generationFinishedAt: typeof m.generationFinishedAt === 'string' ? m.generationFinishedAt : undefined,
    providerTaskStatus: typeof m.providerTaskStatus === 'string' ? m.providerTaskStatus : undefined,
    providerTaskPhase: typeof m.providerTaskPhase === 'string' ? m.providerTaskPhase : undefined,
    providerTaskProgress: Number.isFinite(Number(m.providerTaskProgress)) ? Number(m.providerTaskProgress) : undefined,
    providerTaskProgressChangedAt: typeof m.providerTaskProgressChangedAt === 'string' ? m.providerTaskProgressChangedAt : undefined,
    providerTaskId: typeof m.providerTaskId === 'string' ? m.providerTaskId : undefined,
    providerTaskStatusRaw: typeof m.providerTaskStatusRaw === 'string' ? m.providerTaskStatusRaw : undefined,
    providerSubmittedAt: typeof m.providerSubmittedAt === 'string' ? m.providerSubmittedAt : undefined,
    providerResultUrl: typeof m.providerResultUrl === 'string' ? m.providerResultUrl : undefined,
    finalizePending: m.finalizePending === true,
    episodeId: typeof m.episodeId === 'string' ? m.episodeId : undefined,
    actualDuration: Number.isFinite(Number(m.actualDuration)) ? Number(m.actualDuration) : undefined,
    videoValidation: m.videoValidation && typeof m.videoValidation === 'object'
      ? m.videoValidation as CanvasMediaNode['videoValidation']
      : undefined,
    storage: m.storage && typeof m.storage === 'object'
      ? m.storage as CanvasMediaNode['storage']
      : undefined,
    storageError: typeof m.storageError === 'string' ? m.storageError : undefined,
    source: m.source && typeof m.source === 'object'
      ? m.source as CanvasMediaNode['source']
      : undefined,
    assetKey: typeof m.assetKey === 'string' ? m.assetKey : undefined,
    assetId: typeof m.assetId === 'string' ? m.assetId : undefined,
    seriesId: typeof m.seriesId === 'string' ? m.seriesId : undefined,
    variantKey: typeof m.variantKey === 'string' ? m.variantKey : undefined,
    semanticCode: typeof m.semanticCode === 'string' ? m.semanticCode : undefined,
    promptStale: m.promptStale === true,
    promptStaleReason: typeof m.promptStaleReason === 'string' ? m.promptStaleReason : undefined,
    sourcePromptUpdatedAt: typeof m.sourcePromptUpdatedAt === 'string' ? m.sourcePromptUpdatedAt : undefined,
    reusedAsset: m.reusedAsset && typeof m.reusedAsset === 'object'
      ? m.reusedAsset as CanvasMediaNode['reusedAsset']
      : undefined,
    x: n.position?.x || 0,
    y: n.position?.y || 0,
    updatedAt: n.updatedAt || new Date().toISOString(),
    positionUpdatedAt: typeof n.positionUpdatedAt === 'string' ? n.positionUpdatedAt : undefined,
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

export async function getCanvasMediaNodes(
  sessionId: string,
  epId: string,
  projectSnapshot?: ProjectResponse | null,
): Promise<CanvasMediaNode[]> {
  const normalizedEpisode = String(epId || '').trim().toUpperCase();
  if (!normalizedEpisode) throw new Error('读取画布节点必须指定集号');
  const proj = projectSnapshot === undefined
    ? await getProject(sessionId).catch(() => null)
    : projectSnapshot;
  if (!proj?.payload?.nodes) return [];
  // 文本节点（type === 'text'）此前被整类过滤掉，导致：① ＋添加里没有文本节点入口，
  // ② 导入生成的剧本头卡等文本资产在画布上根本不显示。现已作为一等公民纳入。
  const allNodes = proj.payload.nodes;
  const episodeGroup = normalizedEpisode
    ? (proj.payload.episodeImports || []).find((group) => (
        String(group.id || '').toUpperCase() === normalizedEpisode
      ))
    : null;
  const allowedIds = episodeGroup ? new Set(episodeGroup.nodeIds || []) : null;
  if (allowedIds) {
    const episodeVideoIds = new Set(
      (episodeGroup?.videoNodeIds || []).map((nodeId) => String(nodeId)),
    );
    for (const node of allNodes) {
      if (node.type !== 'video') continue;
      const belongsToEpisode = episodeVideoIds.size > 0
        ? episodeVideoIds.has(String(node.id))
        : allowedIds.has(node.id);
      if (!belongsToEpisode) continue;
      const params = ((node.metadata || {}) as Record<string, unknown>).params;
      const values = params && typeof params === 'object'
        ? params as Record<string, unknown>
        : {};
      for (const key of ['refs', 'voiceRefs']) {
        for (const ref of Array.isArray(values[key]) ? values[key] as Array<Record<string, unknown>> : []) {
          if (ref && typeof ref.nodeId === 'string') allowedIds.add(ref.nodeId);
        }
      }
    }
    for (const connection of proj.payload.connections || []) {
      if (
        connection
        && episodeVideoIds.has(String(connection.toNodeId || ''))
        && connection.fromNodeId
      ) {
        allowedIds.add(String(connection.fromNodeId));
      }
    }
  }
  const ownedIds = new Set(
    (proj.payload.episodeImports || [])
      .filter((group) => (
        String(group.id || '').toUpperCase() === normalizedEpisode
      ))
      .flatMap((group) => group.nodeIds || []),
  );
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
  const assetIdentityById = new Map<string, CanvasMediaNode>(
    nodes.map((node): [string, CanvasMediaNode] => {
      const media = toMediaNode(node);
      return [media.id, media];
    }),
  );
  const labelOf = (id: string) => {
    const t = titleById.get(id);
    if (t && t.trim()) return t;
    return typeById.get(id) === 'audio' ? '角色音色' : '资产图';
  };
  // 用户显式删过的 (目标节点::引用节点)：不要再从 connections 补回来
  const removed = loadRemovedRefs(sessionId);
  const episodePositions = normalizedEpisode
    ? proj.payload.episodeLayouts?.[normalizedEpisode]
    : undefined;
  const converted = nodes.map((node) => ({
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
    const ownRefs = (mn.params?.refs || []).filter((ref) => (
      Boolean(ref.nodeId)
      && typeById.has(ref.nodeId)
      && !removed.has(`${mn.id}::${ref.nodeId}`)
    )).map((ref) => {
      const source = assetIdentityById.get(ref.nodeId);
      const real = labelOf(ref.nodeId);
      return source
        ? {...ref, ...assetRefFromNode(source, real)}
        : {...ref, label: real};
    });
    // ★ label 就地修正（无论有没有 connections 都要做）：历史数据里的「资产图 / 角色音色」
    //   泛称会被后端原样拼进喂给模型的 prompt，必须换成真实节点名。
    //   只改 label、不动顺序（顺序决定第一张主参考图）。
    const fixedOwn = ownRefs;
    const labelChanged = fixedOwn.some((r, i) => r !== ownRefs[i]);
    const known = new Set(ownRefs.map(r => r.nodeId));
    const extra = linked.filter(id => !known.has(id)).map((id) => {
      const source = assetIdentityById.get(id);
      return source
        ? assetRefFromNode(source, labelOf(id))
        : {nodeId: id, label: labelOf(id)};
    });
    // 去重：同一张图片节点可能同时出现在 params.refs（手动连线写入）与
    // connections（后端关系表）里，合并后会产生重复 refId → React Flow 渲染出
    // 两条 id 相同的媒体引用边，控制台刷满 "Encountered two children with the same key"。
    const mergedRefs = [...new Map([...fixedOwn, ...extra].map(r => [r.nodeId, r])).values()];
    const ownVoices = voiceRefList(mn.params).filter((ref) => (
      Boolean(ref.nodeId)
      && typeById.has(ref.nodeId)
      && !removed.has(`${mn.id}::${ref.nodeId}`)
    )).map((ref) => {
      const source = assetIdentityById.get(ref.nodeId);
      return source
        ? {...ref, ...assetRefFromNode(source, ref.label)}
        : ref;
    });
    const knownVoice = new Set(ownVoices.map(r => r.nodeId));
    const mergedVoices = [
      ...ownVoices,
      ...linkedVoices.filter(id => !knownVoice.has(id)).map((id) => {
        const source = assetIdentityById.get(id);
        return source
          ? assetRefFromNode(source, labelOf(id))
          : {nodeId: id, label: labelOf(id)};
      }),
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
  return converted;
}

export async function saveCanvasMediaNodes(
  sessionId: string,
  nodes: CanvasMediaNode[],
  episodeId: string,
  options?: {includeMediaState?: boolean},
) {
  const normalizedEpisode = String(episodeId || '').trim().toUpperCase();
  if (!normalizedEpisode) {
    throw new Error('新建节点必须指定所属集');
  }
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
  const includeMediaState = options?.includeMediaState === true;
  const payload = nodes.map((n) => {
    // 用 refIds 长度等轻量信号判断不靠谱；这里直接显式标记：
    // 调用方若只改了 metadata（prompt/refs/status），会通过 patchMediaNode 走单节点路径。
    // 全量保存只用于"节点集合发生变化"（新增/删除），此时位置必须一起带上。
    return {
      id: n.id,
      type: n.kind,
      title: n.title,
      baseUpdatedAt: n.updatedAt,
      // Used only when the backend creates a node. The server ignores this
      // field for existing nodes; drag/undo persistence uses the dedicated
      // /nodes/positions endpoint instead.
      position: {x: n.x ?? 0, y: n.y ?? 0},
      metadata: editableMetadataForStorage(n, includeMediaState),
    };
  });
  const result = await request<{
    ok: boolean;
    nodes: number;
    created: number;
    updates: Array<{
      id: string;
      updatedAt: string;
      positionUpdatedAt?: string;
    }>;
  }>(`/api/projects/${encodeURIComponent(sessionId)}/nodes`, {
    method: 'PUT',
    body: JSON.stringify({nodes: payload, episodeId: normalizedEpisode}),
  });
  return result;
}

const GENERATION_OWNED_MEDIA_PARAMS = new Set([
  'mediaVersions',
  'mediaVersionIndex',
  'videoVersions',
  'videoVersionIndex',
  'thumbUrl',
]);

function editableParamsForStorage(
  params: CanvasMediaParams | undefined,
  includeMediaState: boolean,
): CanvasMediaParams {
  const normalized = normalizeParamsForStorage(params) || {};
  if (includeMediaState) return normalized;
  return Object.fromEntries(
    Object.entries(normalized).filter(
      ([key]) => !GENERATION_OWNED_MEDIA_PARAMS.has(key),
    ),
  ) as CanvasMediaParams;
}

/**
 * Ordinary node edits only contain user-editable facts. Media bytes, storage
 * receipts, task state and generated versions are written by the backend
 * task pipeline. New local uploads are the one explicit exception.
 */
function editableMetadataForStorage(
  node: CanvasMediaNode,
  includeMediaState: boolean,
): Record<string, unknown> {
  const metadata: Record<string, unknown> = {
    prompt: node.prompt,
    refs: node.params?.refs || [],
    params: editableParamsForStorage(node.params, includeMediaState),
  };
  for (const key of ['assetId', 'seriesId', 'variantKey', 'semanticCode'] as const) {
    const value = node[key];
    if (typeof value === 'string' && value) metadata[key] = value;
  }
  if (node.episodeId) metadata.episodeId = node.episodeId;
  if (!includeMediaState) return metadata;
  return {
    ...metadata,
    status: node.status || 'idle',
    content: mediaStorageUrl(node.mediaUrl),
    ...(node.source ? {source: node.source} : {}),
    ...(node.storage !== undefined ? {storage: node.storage} : {}),
    ...(node.storageError !== undefined ? {storageError: node.storageError} : {}),
    ...(node.actualDuration !== undefined
      ? {actualDuration: node.actualDuration}
      : {}),
    ...(node.videoValidation ? {videoValidation: node.videoValidation} : {}),
  };
}

function mediaPatchMetadataForStorage(
  node: CanvasMediaNode,
): Record<string, unknown> {
  const localImport = node._mediaImport === true;
  const versionSelection = node._mediaVersionSelection === true;
  const includeMediaState = localImport || versionSelection;
  const metadata = editableMetadataForStorage(node, localImport);
  if (localImport) {
    return {
      ...metadata,
      _mediaImport: true,
    };
  }
  if (versionSelection) {
    const params = metadata.params as CanvasMediaParams;
    return {
      ...metadata,
      _mediaVersionSelection: true,
      content: mediaStorageUrl(node.mediaUrl),
      status: node.status || 'idle',
      ...(node.source ? {source: node.source} : {}),
      ...(node.storage !== undefined ? {storage: node.storage} : {}),
      ...(node.providerResultUrl !== undefined
        ? {providerResultUrl: node.providerResultUrl}
        : {}),
      ...(node.actualDuration !== undefined
        ? {actualDuration: node.actualDuration}
        : {}),
      params: {
        ...params,
        ...(node.params?.activeVersionId
          ? {activeVersionId: node.params.activeVersionId}
          : {}),
        ...(node.params?.mediaVersionIndex !== undefined
          ? {mediaVersionIndex: node.params.mediaVersionIndex}
          : {}),
        ...(node.params?.videoVersionIndex !== undefined
          ? {videoVersionIndex: node.params.videoVersionIndex}
          : {}),
      },
    };
  }
  return metadata;
}

export type CanvasPositionMove = {
  id: string;
  position: {x: number; y: number};
  seq?: number;
};

export async function saveCanvasMediaNodePositions(
  sessionId: string,
  moves: CanvasPositionMove[],
  epId: string,
  writeOwner?: string,
) {
  const normalizedEpisode = String(epId || '').trim().toUpperCase();
  if (!normalizedEpisode) {
    throw new Error('保存节点位置必须指定所属集');
  }
  return request<{
    ok: boolean;
    updated: number;
    unchanged: string[];
    missing: string[];
    updates: Array<{
      nodeId: string;
      position: {x: number; y: number};
      positionUpdatedAt: string;
      updatedAt: string;
    }>;
  }>(`/api/projects/${encodeURIComponent(sessionId)}/nodes/positions`, {
    method: 'PUT',
    body: JSON.stringify({
      episodeId: normalizedEpisode,
      positions: moves,
      writeOwner,
    }),
  });
}

export async function createMediaDownloadUrl(
  sessionId: string,
  nodeId: string,
  versionId: string,
  downloadName: string,
): Promise<{url: string; expiresIn?: number; downloadName?: string}> {
  const ticket = await request<{
    url: string;
    expiresIn?: number;
    downloadName?: string;
  }>(
    `/api/projects/${encodeURIComponent(sessionId)}/downloads`,
    {
      method: 'POST',
      body: JSON.stringify({nodeId, versionId, downloadName}),
      timeoutMs: 30_000,
    },
  );
  return {
    ...ticket,
    url: apiUrl(String(ticket.url || '')),
  };
}

export async function saveCanvasEpisodeViewport(
  sessionId: string,
  viewport: {x: number; y: number; zoom: number},
  epId: string,
  options?: {keepalive?: boolean},
) {
  const normalizedEpisode = String(epId || '').trim().toUpperCase();
  if (!normalizedEpisode) {
    throw new Error('保存视口必须指定所属集');
  }
  return request<{
    ok: boolean;
    viewport: {x: number; y: number; zoom: number};
    episodeId: string;
  }>(`/api/projects/${encodeURIComponent(sessionId)}/viewport`, {
    method: 'PUT',
    body: JSON.stringify({
      viewport,
      episodeId: normalizedEpisode,
    }),
    keepalive: options?.keepalive === true,
  });
}

export async function saveCanvasConnections(
  sessionId: string,
  connections: Array<{id: string; fromNodeId: string; toNodeId: string}>,
  episodeId: string,
) {
  const normalizedEpisode = String(episodeId || '').trim().toUpperCase();
  if (!normalizedEpisode) {
    throw new Error('保存连线必须指定所属集');
  }
  return request<{
    ok: boolean;
    total: number;
    manual: number;
    removed: number;
    droppedInvalid?: number;
  }>(
    `/api/projects/${encodeURIComponent(sessionId)}/connections`,
    {
      method: 'PUT',
      body: JSON.stringify({
        episodeId: normalizedEpisode,
        connections,
      }),
    },
  );
}

export async function createCanvasEpisode(
  sessionId: string,
  input?: {episodeId?: string; title?: string},
): Promise<{
  ok: boolean;
  episode: {id: string; title: string};
  episodes: string[];
  revision: number | null;
}> {
  return request(`/api/projects/${encodeURIComponent(sessionId)}/episodes`, {
    method: 'POST',
    body: JSON.stringify(input || {}),
  });
}

export async function deleteCanvasEpisode(
  sessionId: string,
  episodeId: string,
): Promise<{
  ok: boolean;
  removed: string;
  episodes: string[];
  revision: number | null;
}> {
  return request(`/api/projects/${encodeURIComponent(sessionId)}/episodes/${encodeURIComponent(episodeId)}`, {
    method: 'DELETE',
  });
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
  episodeId: string,
  options?: {keepalive?: boolean},
): Promise<{ok: boolean}> {
  const normalizedEpisode = String(episodeId || '').trim().toUpperCase();
  if (!normalizedEpisode) {
    throw new Error('保存节点必须指定所属集');
  }
  const body = {
    episodeId: normalizedEpisode,
    nodes: nodes.map((node) => ({
      id: node.id,
      type: node.kind,
      title: node.title,
      baseUpdatedAt: node.updatedAt,
      // 特意不传 position：让后端保留库里的当前坐标
      metadata: mediaPatchMetadataForStorage(node),
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
  episodeId: string,
): Promise<{ok: boolean}> {
  return patchMediaNodesFields(sessionId, [node], episodeId);
}

// ---------- notes ----------
// 删除单个媒体/文本节点：必须有对应后端调用，否则只改本地 state，
// 刷新后节点会从 payload 里回来（后端此前没有 DELETE /nodes/{nid} 路由）。
export async function deleteCanvasMediaNode(sessionId: string, nodeId: string) {
  const res = await fetch(
    apiUrl(`/api/projects/${encodeURIComponent(sessionId)}/nodes/${encodeURIComponent(nodeId)}`),
    {
      method: 'DELETE',
      credentials: 'include',
      headers: currentShareRequestHeaders(),
    },
  );
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw withRequestStatus(
      new Error((payload as {error?: string}).error || `删除失败，HTTP ${res.status}`),
      res.status,
    );
  }
  return {ok: true};
}

export async function getCanvasNotes(
  sessionId: string,
  episodeId: string,
  _projectSnapshot?: ProjectResponse | null,
): Promise<CanvasNote[]> {
  const normalized = String(episodeId || '').trim().toUpperCase();
  if (!normalized) throw new Error('读取备注必须指定集号');
  const data = await request<{notes: CanvasNote[]}>(
    `/api/projects/${encodeURIComponent(sessionId)}/notes?episodeId=${encodeURIComponent(normalized)}`,
  );
  return (data.notes || []).map((note) => ({
    ...note,
    episodeId: normalized,
  }));
}

async function getProjectionCanvasWorkspace(
  sessionId: string,
  epId?: string | null,
  signal?: AbortSignal,
  workspaceIndexPromise?: Promise<WorkspaceProjectMeta>,
  episodeKnownFromUrl = false,
  prevalidatedNodesPromise?: Promise<WorkspaceNodes | null> | null,
): Promise<WorkspaceFacts> {
  const requestedEpisode = String(epId || '').trim().toUpperCase();
  const earlyNodesPromise = prevalidatedNodesPromise
    || (
      episodeKnownFromUrl && requestedEpisode
        ? getWorkspaceNodes(sessionId, requestedEpisode, signal)
        : null
    );
  const index = await (
    workspaceIndexPromise || getWorkspaceIndex(sessionId, signal)
  );
  if (!Array.isArray(index.episodes)) {
    throw withRequestStatus(
      new Error('workspace index contract unavailable'),
      501,
    );
  }
  const episodes = index.episodes.length > 0
    ? index.episodes
    : [{
        id: 'EP001',
        title: index.title || '画布工作流',
        nodeIds: [],
        videoNodeIds: [],
      }];
  // The URL is a navigation hint, but it must never silently override the
  // project's persisted current episode. Older clients could arrive with a
  // stale EP001 URL and then load EP001 even though the project itself was on
  // EP003, causing repeated project switches and wrong episode writes.
  const projectEpisodeHint = String(
    (index.projectMeta as Record<string, unknown> | undefined)?.episodeId
    || '',
  ).trim().toUpperCase();
  const preferredEpisode = requestedEpisode || projectEpisodeHint;
  const currentEpisode = episodes.some((item) => (
    String(item.id || '').toUpperCase() === preferredEpisode
  ))
    ? preferredEpisode
    : String(episodes[0]?.id || 'EP001').toUpperCase();
  // Render the graph first; task history is auxiliary and reconciles again
  // through its own polling cadence.
  const preloadedNodes = earlyNodesPromise
    ? await earlyNodesPromise
    : null;
  const nodesPayload = (
    preloadedNodes && currentEpisode === requestedEpisode
      ? preloadedNodes
      : await getWorkspaceNodes(sessionId, currentEpisode, signal)
  );
  if (
    !nodesPayload
    || !Array.isArray(nodesPayload.nodes)
    || !Array.isArray(nodesPayload.connections)
  ) {
    throw withRequestStatus(
      new Error('workspace nodes contract unavailable'),
      501,
    );
  }
  const projectMeta = index.projectMeta || {};
  const pseudoProject: ProjectResponse = {
    id: index.id,
    title: index.title,
    payload: {
      ...(projectMeta as Record<string, unknown>),
      title: index.title,
      nodes: nodesPayload.nodes,
      connections: nodesPayload.connections,
      episodeImports: episodes,
      episodeViewports: index.episodeViewports,
      episodeLayouts: {
        [currentEpisode]: nodesPayload.layouts || {},
      },
      extras: (
        projectMeta.extras
        && typeof projectMeta.extras === 'object'
      )
        ? projectMeta.extras as Record<string, unknown>
        : {},
    } as Payload,
  };
  const canvas = await getCanvas(
    sessionId,
    currentEpisode,
    pseudoProject,
  );
  const notesPromise = Array.isArray(nodesPayload.notes)
    ? Promise.resolve(nodesPayload.notes)
    : sessionId.startsWith('share:')
      ? request<{notes: CanvasNote[]}>(
          `/api/share/${encodeURIComponent(workspaceReadSessionId(sessionId))}/notes?episodeId=${encodeURIComponent(currentEpisode)}`,
        ).then((result) => result.notes || [])
      : getCanvasNotes(sessionId, currentEpisode);
  // The projection response already contains this episode's payload nodes.
  // Reusing pseudoProject avoids issuing /api/projects/{id} again solely to
  // reproject nodes that are already in memory. Fallback mode above still
  // reads the complete project once when the projection is unavailable.
  const mediaNodesPromise = getCanvasMediaNodes(
    sessionId,
    currentEpisode,
    pseudoProject,
  );
  // These independent reads share one round-trip window. Previously notes and
  // media were awaited sequentially, then task history added another blocking
  // request after the graph was already ready.
  const tasksPromise = sessionId.startsWith('share:')
    ? Promise.resolve([] as GenerationTask[])
    : getGenerationTasks(sessionId, 80, currentEpisode).catch(() => []);
  const [notes, mediaNodes, tasks] = await Promise.all([
    notesPromise,
    mediaNodesPromise,
    tasksPromise,
  ]);
  return {
    canvas,
    notes,
    mediaNodes,
    episodeViewports: index.episodeViewports || {},
    activeEpisodeId: currentEpisode,
    tasks,
    connections: nodesPayload.connections,
  };
}

export async function getCanvasWorkspace(
  sessionId: string,
  epId?: string | null,
  signal?: AbortSignal,
  workspaceIndexPromise?: Promise<WorkspaceProjectMeta>,
  episodeKnownFromUrl = false,
  prevalidatedNodesPromise?: Promise<WorkspaceNodes | null> | null,
): Promise<{
  canvas: Canvas | {empty: true};
  notes: CanvasNote[];
  mediaNodes: CanvasMediaNode[];
  episodeViewports: Record<string, unknown>;
  activeEpisodeId: string | null;
  connections: Array<{
    id?: string;
    fromNodeId: string;
    toNodeId: string;
    source?: string;
  }>;
  tasks: GenerationTask[];
}> {
  if (sessionId.startsWith('share:')) {
    return getProjectionCanvasWorkspace(
      sessionId,
      epId,
      signal,
      workspaceIndexPromise,
      episodeKnownFromUrl,
      prevalidatedNodesPromise,
    );
  }

  try {
    return await getProjectionCanvasWorkspace(
      sessionId,
      epId,
      signal,
      workspaceIndexPromise,
      episodeKnownFromUrl,
      prevalidatedNodesPromise,
    );
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw error;
    }
    if (error && typeof error === 'object') {
      const status = Number(
        (error as {status?: unknown; statusCode?: unknown}).status
        ?? (error as {statusCode?: unknown}).statusCode,
      );
      // Only fall back when the projection contract is unavailable. A real
      // auth/server failure must remain visible rather than being masked by a
      // second full-payload request.
      if (![404, 405, 501].includes(status)) {
        throw error;
      }
    }
    // Rollout fallback: older application servers do not expose projection
    // endpoints yet. Keep the complete payload path available for one release.
  }
  const project = await request<ProjectResponse>(
    `/api/projects/${encodeURIComponent(sessionId)}`,
    {signal},
  )
    .then(normalizeProjectMediaUrls)
    .catch((error) => {
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw error;
      }
      return null;
    });
  const requestedEpisode = String(epId || '').trim().toUpperCase();
  const projectEpisodes = new Set(
    Array.isArray(project?.payload?.episodeImports)
      ? project.payload.episodeImports.map((group) => String(group?.id || '').toUpperCase())
      : [],
  );
  // A project switch can inherit the previous project's episode from the URL.
  // Querying that stale episode makes media loading fall back to the whole
  // project, then a second load runs after the episode is corrected.
  const effectiveEpisode = requestedEpisode && projectEpisodes.has(requestedEpisode)
    ? requestedEpisode
    : '';
  const currentEpisode = effectiveEpisode
    || String(
      project?.payload?.episodeId
      || project?.payload?.episodeImports?.[0]?.id
      || '',
    ).trim().toUpperCase()
    || null;
  const mediaEpisode = currentEpisode || 'EP001';
  const canvas = await getCanvas(sessionId, currentEpisode, project);
  const [notes, mediaNodes, tasks] = await Promise.all([
    getCanvasNotes(sessionId, mediaEpisode),
    getCanvasMediaNodes(sessionId, mediaEpisode, project),
    getGenerationTasks(sessionId, 80, mediaEpisode),
  ]);
  return {
    canvas,
    notes,
    mediaNodes,
    episodeViewports: project?.payload?.episodeViewports || {},
    activeEpisodeId: currentEpisode,
    tasks,
    connections: Array.isArray(project?.payload?.connections)
      ? project.payload.connections
      : [],
  };
}

export async function getCanvasEpisodeWorkspace(
  sessionId: string,
  episodeId: string,
  signal?: AbortSignal,
): Promise<{
  canvas: Canvas | {empty: true};
  notes: CanvasNote[];
  mediaNodes: CanvasMediaNode[];
  activeEpisodeId: string;
  connections: Array<{
    id?: string;
    fromNodeId: string;
    toNodeId: string;
    source?: string;
  }>;
  tasks: GenerationTask[];
}> {
  const normalizedEpisode = String(episodeId || '').trim().toUpperCase();
  if (!normalizedEpisode) throw new Error('切换画布必须指定集号');
  if (sessionId.startsWith('share:')) {
    const workspace = await getCanvasWorkspace(
      sessionId,
      normalizedEpisode,
      signal,
    );
    return {
      canvas: workspace.canvas,
      notes: workspace.notes,
      mediaNodes: workspace.mediaNodes,
      activeEpisodeId: workspace.activeEpisodeId || normalizedEpisode,
      connections: workspace.connections,
      tasks: [],
    };
  }
  const nodesPayload = await getWorkspaceNodes(
    sessionId,
    normalizedEpisode,
    signal,
  );
  if (
    !Array.isArray(nodesPayload.nodes)
    || !Array.isArray(nodesPayload.connections)
  ) {
    throw withRequestStatus(
      new Error('workspace nodes contract unavailable'),
      501,
    );
  }
  const pseudoProject: ProjectResponse = {
    id: sessionId,
    title: String(nodesPayload.episode?.title || normalizedEpisode),
    payload: {
      id: sessionId,
      title: String(nodesPayload.episode?.title || normalizedEpisode),
      nodes: nodesPayload.nodes,
      connections: nodesPayload.connections,
      episodeImports: [
        {
          ...nodesPayload.episode,
          id: normalizedEpisode,
          title: String(
            nodesPayload.episode?.title || normalizedEpisode,
          ),
          nodeIds: nodesPayload.nodes
            .map((node) => String(node.id || ''))
            .filter(Boolean),
          videoNodeIds: nodesPayload.nodes
            .filter((node) => node.type === 'video')
            .map((node) => String(node.id || ''))
            .filter(Boolean),
        },
      ],
      episodeLayouts: {[normalizedEpisode]: nodesPayload.layouts || {}},
      extras: {},
    } as Payload,
  };
  const [canvas, mediaNodes, tasks] = await Promise.all([
    getCanvas(sessionId, normalizedEpisode, pseudoProject),
    getCanvasMediaNodes(sessionId, normalizedEpisode, pseudoProject),
    getGenerationTasks(sessionId, 80, normalizedEpisode).catch(() => []),
  ]);
  return {
    canvas,
    notes: Array.isArray(nodesPayload.notes) ? nodesPayload.notes : [],
    mediaNodes,
    activeEpisodeId: normalizedEpisode,
    connections: nodesPayload.connections,
    tasks,
  };
}

export async function saveCanvasNotes(
  sessionId: string,
  notes: CanvasNote[],
  episodeId: string,
  ifRevision?: number | null,
) {
  const normalizedEpisode = String(
    episodeId || '',
  ).trim().toUpperCase();
  if (!normalizedEpisode) throw new Error('保存备注必须指定集号');
  if (notes.some((note) => note.episodeId !== normalizedEpisode)) {
    throw new Error('备注集合包含不同集号，拒绝保存');
  }
  await request(`/api/projects/${encodeURIComponent(sessionId)}/notes?episodeId=${encodeURIComponent(normalizedEpisode)}`, {
    method: 'POST',
    body: JSON.stringify({
      episodeId: normalizedEpisode,
      notes,
      ...(ifRevision !== undefined && ifRevision !== null
        ? {if_revision: ifRevision}
        : {}),
    }),
  });
  return {ok: true};
}

export async function getSharedCanvasWorkspace(
  token: string,
  episodeId = '',
): Promise<{
  canvas: Canvas | {empty: true};
  notes: CanvasNote[];
  mediaNodes: CanvasMediaNode[];
  connections: Array<{id?: string; fromNodeId: string; toNodeId: string}>;
}> {
  const shareSession = shareWorkspaceSessionId(token);
  const workspace = await getCanvasWorkspace(
    shareSession,
    episodeId,
  );
  return {
    canvas: workspace.canvas,
    notes: workspace.notes,
    mediaNodes: workspace.mediaNodes,
    connections: workspace.connections,
  };
}

export function shareWorkspaceSessionId(token: string): string {
  const normalized = String(token || '').trim();
  return normalized.startsWith('share:')
    ? normalized
    : `share:${normalized}`;
}

// ---------- shot patch（段/镜头编辑 → 存回节点 metadata.shotPatch） ----------
export async function saveCanvasShot(sessionId: string, shotIdx: number, patch: Record<string, unknown>, epId: string) {
  const normalizedEpisode = String(epId || '').trim().toUpperCase();
  if (!normalizedEpisode) throw new Error('保存镜头必须指定所属集');
  const proj = await getProject(sessionId).catch(() => null);
  const episodeGroup = (proj?.payload?.episodeImports || []).find((group) => (
    String(group.id || '').toUpperCase() === normalizedEpisode
  ));
  const episodeNodeIds = new Set(episodeGroup?.nodeIds || []);
  const videos = (proj?.payload?.nodes || []).filter((node) => (
    node.type === 'video' && episodeNodeIds.has(node.id)
  ));
  const node = videos[shotIdx];
  if (!node) return {ok: false};
  await request(`/api/projects/${encodeURIComponent(sessionId)}/nodes/${encodeURIComponent(node.id)}`, {
    method: 'PUT',
    body: JSON.stringify({
      episodeId: normalizedEpisode,
      metadata: {shotPatch: patch},
    }),
  }).catch(() => {});
  return {ok: true};
}

// ---------- quality report（挂接机检 → QualityReport） ----------
type GateCheckResult = {
  ok: boolean;
  title: string;
  imageOk?: boolean;
  imageStaleOk?: boolean;
  audioOk?: boolean;
  script?: string[];
  linked?: string[];
  staleLinked?: string[];
  voiceScript?: string[];
  voiceRefs?: string[];
  voiceLinked?: string[];
};

async function gateCheck(sessionId: string, epId?: string | null): Promise<{passed: boolean; results: GateCheckResult[]}> {
  const suffix = epId ? `?episodeId=${encodeURIComponent(epId)}` : '';
  return request(`/api/projects/${encodeURIComponent(sessionId)}/gate-check${suffix}`, {method: 'POST', body: '{}'});
}

function gateCheckMismatchDetail(result: GateCheckResult): string {
  const reasons: string[] = [];
  if (result.imageOk === false) {
    const script = (result.script || []).join('、') || '无';
    const linked = (result.linked || []).join('、') || '无';
    reasons.push(`画面引用不一致（稿面 ${script}；实挂 ${linked}）`);
  }
  if ((result.staleLinked || []).length > 0) {
    const stale = (result.staleLinked || []).join('、');
    reasons.push(`存在残余素材连线（${stale}），请在画布断开后再提交`);
  }
  if (result.audioOk === false) {
    const script = (result.voiceScript || []).join('、') || '无';
    const refs = (result.voiceRefs || []).join('、') || '无';
    const linked = (result.voiceLinked || []).join('、') || '无';
    reasons.push(`音色挂接不一致（稿面 ${script}；音色引用 ${refs}；实挂 ${linked}）`);
  }
  return reasons.join('；');
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
  const failureDetails = new Map<string, string>();
  (g.results || []).forEach((r, i) => {
    const detail = r.ok ? '' : gateCheckMismatchDetail(r);
    if (detail) failureDetails.set(r.title, detail);
    dimensions[`段${i + 1}·挂接`] = {
      score: r.ok ? 100 : 0,
      weight: 1,
      passed: r.ok,
      message: r.title,
      segment_number: i + 1,
      detail,
    };
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
    top_issues: fails.map(f => (
      failureDetails.get(f.title)
        ? `${f.title}：${failureDetails.get(f.title)}`
        : f.title
    )),
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
 * 单项媒体生成：请求直达供应商并等待本次结果，视频仍按供应商任务轮询。
 * 不能用 HTTP 先回 202 再在进程内换线程，进程重启会让已付费任务失联。
 */
export async function generateCanvasNodeNow(
  sessionId: string,
  nodeId: string,
  payload?: {
    prompt?: string;
    params?: CanvasMediaParams;
    clientOperationId?: string;
    episodeId?: string;
    entry?: string;
    batchId?: string;
  },
) {
  const normalizedEpisode = String(payload?.episodeId || '').trim().toUpperCase();
  if (!normalizedEpisode) throw new Error('提交生成必须指定所属集');
  const key = `${sessionId}::${nodeId}`;
  if (inflight.has(key)) {
    return {deferred: true, reason: '该节点正在提交生成'};
  }
  inflight.add(key);
  try {
    const result = await request(`/api/projects/${encodeURIComponent(sessionId)}/nodes/${encodeURIComponent(nodeId)}/generate`, {
      method: 'POST',
      body: JSON.stringify({...payload, episodeId: normalizedEpisode}),
      // 提交接口正常情况下几百毫秒就返回任务号；同步模式下服务端要等供应商首响。
      // 放宽到 5 分钟，既有兜底又不会把慢响应误判成挂死。
      timeoutMs: 300_000,
    }) as {
      accepted?: boolean;
      taskId?: string;
      url?: string;
      text?: string;
      nodeId?: string;
      params?: CanvasMediaParams;
      storage?: CanvasMediaNode['storage'];
      storageError?: string;
      source?: CanvasMediaNode['source'];
      providerResultUrl?: string;
      finalizePending?: boolean;
      status?: string;
      providerTaskStatus?: string;
      providerTaskPhase?: string;
      providerTaskProgress?: number;
      generationFinishedAt?: string;
      code?: string;
      replayed?: boolean;
    };
    if (result.accepted) {
      startPolling(sessionId);
      return {
        deferred: true,
        accepted: true,
        taskId: result.taskId,
        reason: result.replayed ? '原生成任务已找回' : '生成请求已受理',
      };
    }
    emit({
      type: 'done',
      sessionId,
      nodeId: result.nodeId || nodeId,
      url: result.url,
      text: result.text,
      params: result.params,
      storage: result.storage,
      storageError: result.storageError,
      source: result.source,
      providerResultUrl: result.providerResultUrl,
      finalizePending: result.finalizePending,
      providerTaskStatus: result.providerTaskStatus,
      providerTaskPhase: result.providerTaskPhase,
      providerTaskProgress: result.providerTaskProgress,
      generationFinishedAt: result.generationFinishedAt,
    });
    return {deferred: false, ...result};
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (message.includes('GENERATION_IN_PROGRESS') || message.includes('正在生成，请勿重复提交')) {
      startPolling(sessionId);
      return {deferred: true, reason: message};
    }
    throw error;
  } finally {
    inflight.delete(key);
  }
}

export async function recoverCanvasNodeMedia(
  sessionId: string,
  nodeId: string,
): Promise<{
  restored: boolean;
  reason?: string;
  content?: string;
  taskId?: string;
}> {
  return request(
    `/api/projects/${encodeURIComponent(sessionId)}/nodes/${encodeURIComponent(nodeId)}/recover-media`,
    {
      method: 'POST',
      body: '{}',
      timeoutMs: 300_000,
    },
  );
}

const listeners = new Set<(event: AgentEvent) => void>();
let pollTimer: ReturnType<typeof setInterval> | null = null;
const pollSessions = new Set<string>();
const inflight = new Set<string>();   // sessionId::nodeId，跨项目也不会串号

export function subscribeToEvents(onEvent: (event: AgentEvent) => void, onConnection: (connected: boolean) => void) {
  listeners.add(onEvent);
  onConnection(true);
  if (pollSessions.size > 0 && !pollTimer) ensurePollTimer();
  return () => {
    listeners.delete(onEvent);
  };
}

function emit(event: AgentEvent) {
  if (event.type === 'done' && typeof event.url === 'string') {
    event = {...event, url: mediaSourceUrl(event.url)};
  }
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
    const project = await getProject(sessionId);
    proj = project?.payload && typeof project.payload === 'object'
      ? project.payload
      : null;
  } catch {
    return;
  }
  if (!proj) return;
  const generating = (proj.nodes || []).find((node) => {
    // Media nodes are submitted exactly once by generateCanvasNodeNow. The
    // old recovery poller only sees a persisted "generating" flag and cannot
    // know whether another tab owns the request, so letting it POST here
    // produces duplicate paid submissions and misleading "准备提交" states.
    return node.type === 'text' && node.metadata?.status === 'generating';
  });
  if (generating && generating.metadata?.status === 'generating') {
    const key = `${sessionId}::${generating.id}`;
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
          // The persisted task/node projection remains authoritative. A busy
          // response is not a terminal card state and must never be rendered
          // as a frontend-produced failure.
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
    pollSessions.delete(sessionId);
    stopPollTimerIfIdle();
  }
}

function startPolling(sessionId: string) {
  pollSessions.add(sessionId);
  ensurePollTimer();
  // Recovery polling observes text jobs only. Media production has one POST
  // path in generateCanvasNodeNow and reconciles from persisted node/task state.
  void pollProjectOnce(sessionId);
}

// ---------- upload ----------
// 真上传：原始文件流 → 后端 /api/upload/stream 落盘 → 返回 /uploads/xxx。
// 旧实现是 `URL.createObjectURL(file)`，造出来的 blob: 地址只在当前页面会话有效，
// 刷新即 404 → 拖进画布的图全变破图（用户实测反馈过）。
// 失败时**不**回退到 blob：宁可在画布上提示上传失败，也不要给一个刷新就会坏的地址。
export type UploadFileResult = {
  url: string;
  name: string;
  size: number;
  mime: string;
  sha256?: string;
  kind?: 'image' | 'audio' | 'video';
  assetId?: string;
  seriesId?: string;
  variantKey?: string;
  storage?: {
    provider?: string;
    bucket?: string;
    key?: string;
    status?: string;
    url?: string;
    storedAt?: string;
  };
  remoteUrl?: string;
};

export function uploadWorkspaceFileWithProgress(
  sessionId: string,
  file: File,
  options?: {
    signal?: AbortSignal;
    onProgress?: (loaded: number, total: number, percent: number) => void;
    idleTimeoutMs?: number;
  },
): Promise<UploadFileResult> {
  return new Promise((resolve, reject) => {
    if (options?.signal?.aborted) {
      reject(new DOMException('上传已取消', 'AbortError'));
      return;
    }
    const xhr = new XMLHttpRequest();
    const idleTimeoutMs = Math.max(15_000, options?.idleTimeoutMs ?? 120_000);
    let idleTimer: ReturnType<typeof setTimeout> | undefined;
    let timedOut = false;
    const clearIdleTimer = () => {
      if (idleTimer !== undefined) {
        clearTimeout(idleTimer);
        idleTimer = undefined;
      }
    };
    const armIdleTimer = () => {
      clearIdleTimer();
      idleTimer = setTimeout(() => {
        timedOut = true;
        xhr.abort();
      }, idleTimeoutMs);
    };
    const cleanup = () => {
      clearIdleTimer();
      options?.signal?.removeEventListener('abort', abort);
    };
    const abort = () => xhr.abort();
    options?.signal?.addEventListener('abort', abort, {once: true});
    xhr.open('POST', apiUrl('/api/upload/stream'));
    xhr.withCredentials = true;
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.setRequestHeader('X-File-Name', encodeURIComponent(file.name));
    xhr.setRequestHeader('X-File-Type', file.type || 'application/octet-stream');
    const shareHeaders = currentShareRequestHeaders();
    for (const [name, value] of Object.entries(shareHeaders)) {
      xhr.setRequestHeader(name, value);
    }
    xhr.upload.onprogress = (event) => {
      armIdleTimer();
      const total = event.lengthComputable ? event.total : file.size;
      const percent = total > 0 ? Math.min(100, Math.round((event.loaded / total) * 100)) : 0;
      options?.onProgress?.(event.loaded, total, percent);
    };
    xhr.onerror = () => {
      cleanup();
      reject(new Error('上传连接失败，请检查网络后重试'));
    };
    xhr.onabort = () => {
      cleanup();
      reject(timedOut
        ? new Error('上传长时间没有进度，已自动停止；请检查网络后重试')
        : new DOMException('上传已取消', 'AbortError'));
    };
    xhr.onload = () => {
      cleanup();
      let payload: UploadFileResult & {error?: string} = {
        url: '',
        name: file.name,
        size: file.size,
        mime: file.type,
      };
      try {
        payload = JSON.parse(xhr.responseText || '{}');
      } catch {
        // Keep the generic error below.
      }
      if (xhr.status < 200 || xhr.status >= 300 || !payload.url) {
        reject(new Error(payload.error || `上传失败（HTTP ${xhr.status}）`));
        return;
      }
      resolve({
        ...payload,
        url: mediaSourceUrl(payload.url),
      });
    };
    armIdleTimer();
    xhr.send(file);
  });
}

export async function uploadWorkspaceFile(sessionId: string, file: File) {
  return uploadWorkspaceFileWithProgress(sessionId, file);
}

export async function uploadProviderIcon(file: File): Promise<string> {
  const uploaded = await uploadWorkspaceFileWithProgress('provider-icon', file);
  return mediaStorageUrl(uploaded.url);
}

export default {};
