import {apiUrl} from '../api';

export type AgentModel = {
  id: string;
  label: string;
  providerId?: string;
  providerName?: string;
  hasApiKey?: boolean;
};

export type AgentLock = {
  key: string;
  label: string;
  options: string[];
  default?: string;
};

export type AgentRecipe = {
  id: string;
  version: string;
  kind: 'creative' | 'tool' | string;
  category: string;
  title: string;
  description: string;
  summary?: string;
  models: string[];
  requiredSlots: string[];
  contentSha256: string;
  matchReason?: string;
};

export type AgentRecipeBinding = {
  id: string;
  version: string;
  sha256?: string;
  role: 'primary' | 'modifier' | string;
  segmentNo: number;
  slots: Record<string, string>;
  missingSlots?: string[];
  matchReason?: string;
};

export type AgentSkill = {
  id: string;
  name: string;
  activeVersion?: string;
  version?: string;
  routes?: Array<{id: string; name: string}>;
  locks?: AgentLock[];
  manifest?: {raw?: {description?: string; permissions?: Record<string, unknown>}};
};

export type AgentFile = {
  id: string;
  name: string;
  size: number;
  chars: number;
};

export type AgentMessage = {
  id: number;
  role: string;
  content: string;
  blocks?: AgentContentBlock[];
  conversationId?: string;
  runId?: string;
  sequenceNo?: number;
  createdAt?: string;
};

export type AgentContentBlock =
  | {type: 'text'; text: string}
  | {type: 'markdown'; text: string}
  | {type: 'json'; data: unknown}
  | {type: 'tool'; name: string; summary?: string; status?: string}
  | {type: 'artifact'; artifactId: string}
  | {type: 'notice'; level: 'info' | 'warning' | 'error'; code?: string; message: string};

export type AgentStage = {
  id?: number;
  name: string;
  status: string;
  detail?: Record<string, unknown>;
  createdAt?: string;
};

export type AgentArtifact = {
  id: number;
  kind: string;
  payload: Record<string, unknown>;
  createdAt?: string;
};

export type AgentRun = {
  id: string;
  projectId: string;
  skillId: string;
  skillVersion: string;
  modelId?: string;
  routeId?: string;
  locks?: Record<string, string>;
  fileIds?: string[];
  referenceNodeIds?: string[];
  recipeBindings?: AgentRecipeBinding[];
  conversationId?: string;
  prompt: string;
  status: string;
  error: string;
  errorCode?: string;
  currentStage?: string;
  cancelRequested?: boolean;
  createdAt?: string;
  updatedAt?: string;
  messages: AgentMessage[];
  steps: AgentStage[];
  stages: AgentStage[];
  artifacts: AgentArtifact[];
  toolCalls?: Array<{
    id: number;
    name: string;
    arguments: Record<string, unknown>;
    status: string;
    result: Record<string, unknown>;
    resultBytes?: number;
    errorCode?: string;
    startedAt?: string;
    finishedAt?: string;
  }>;
};

export type AgentConversation = {
  id: string;
  tenantId: string;
  accountId: string;
  projectId: string;
  title: string;
  status: string;
  activeRunId?: string;
  createdAt?: string;
  updatedAt?: string;
  messages?: AgentMessage[];
};

export type AgentDraft = {
  text: string;
  attachments: AgentFile[];
  references: Array<{id?: string; nodeId?: string; assetId?: string; seriesId?: string; variantKey?: string; title?: string; displayName?: string; type?: string; thumb?: string}>;
  skillId?: string;
  routeId?: string;
  modelId?: string;
  locks?: Record<string, string>;
  recipeBindings?: AgentRecipeBinding[];
  updatedAt?: string;
};

export type AgentSkillDetail = {
  skill: AgentSkill;
  entry: string;
  locks?: AgentLock[];
  versions?: Array<{version: string; active: boolean; createdAt?: string; permissions?: Record<string, unknown>}>;
};

const ENV = (import.meta as ImportMeta & {env?: Record<string, string>}).env || {};

export function agentBaseUrl(): string {
  if (ENV.VITE_AGENT_URL) return ENV.VITE_AGENT_URL.replace(/\/$/, '');
  return 'api';
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 120_000);
  let response: Response;
  try {
    const base = agentBaseUrl();
    response = await fetch(
      base === 'api' ? apiUrl(path) : base + path,
      {
        credentials: 'include',
        ...init,
        signal: init?.signal || controller.signal,
        headers: init && init.body instanceof FormData
          ? init.headers
          : {'Content-Type': 'application/json', ...(init && init.headers ? init.headers : {})},
      },
    );
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new Error('请求超时，任务可能仍在后台执行，请刷新会话恢复');
    }
    throw err;
  } finally {
    window.clearTimeout(timeout);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = (data as {detail?: unknown; error?: string}).detail;
    const message = typeof detail === 'string'
      ? detail
      : detail && typeof detail === 'object' && 'error' in detail
        ? String((detail as {error?: string}).error)
        : (data as {error?: string}).error || '请求失败 HTTP ' + response.status;
    throw new Error(message);
  }
  return data as T;
}

export const agentApi = {
  bootstrap: (projectId: string) => request<{tenantId: string; accountId: string; conversations: AgentConversation[]}>('/api/agent/bootstrap?projectId=' + encodeURIComponent(projectId)),
  createConversation: (projectId: string, title = '', conversationId = '') => request<{ok: boolean; conversation: AgentConversation}>('/api/agent/conversations', {
    method: 'POST',
    body: JSON.stringify({projectId, title, conversationId}),
  }),
  conversation: (id: string) => request<{conversation: AgentConversation}>('/api/agent/conversations/' + encodeURIComponent(id)),
  draft: (id: string) => request<{draft: AgentDraft}>('/api/agent/conversations/' + encodeURIComponent(id) + '/draft'),
  saveDraft: (id: string, draft: Partial<AgentDraft>) => request<{ok: boolean; draft: AgentDraft}>('/api/agent/conversations/' + encodeURIComponent(id) + '/draft', {
    method: 'PUT',
    body: JSON.stringify(draft),
  }),
  appendMessage: (id: string, body: {content: AgentContentBlock[]; skillId: string; modelId?: string; routeId?: string; locks?: Record<string, string>; fileIds?: string[]; referenceNodeIds?: string[]; recipeBindings?: AgentRecipeBinding[]}, idempotencyKey: string) => request<{ok: boolean; idempotent?: boolean; run: AgentRun; message?: AgentMessage}>('/api/agent/conversations/' + encodeURIComponent(id) + '/messages', {
    method: 'POST',
    headers: {'Idempotency-Key': idempotencyKey},
    body: JSON.stringify(body),
  }),
  models: () => request<{models: AgentModel[]; defaultModelId?: string}>('/api/agent/models'),
  skills: () => request<{skills: AgentSkill[]}>('/api/agent/skills'),
  recipes: (params: {query?: string; category?: string; kind?: string; model?: string; cursor?: number; limit?: number} = {}) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== '') query.set(key, String(value));
    });
    const suffix = query.toString();
    return request<{recipes: AgentRecipe[]; total: number; nextCursor: number | null; catalog: Record<string, unknown>}>('/api/agent/recipes' + (suffix ? '?' + suffix : ''));
  },
  recipe: (id: string, version = '') => request<{recipe: Record<string, unknown>; catalog: Record<string, unknown>; execution: Record<string, unknown>}>('/api/agent/recipes/' + encodeURIComponent(id) + (version ? '?version=' + encodeURIComponent(version) : '')),
  skill: (id: string) => request<AgentSkillDetail>('/api/agent/skills/' + encodeURIComponent(id)),
  versions: (id: string) => request<{versions: AgentSkillDetail['versions']}>('/api/agent/skills/' + encodeURIComponent(id) + '/versions'),
  activate: (id: string, version: string) => request('/api/agent/skills/' + encodeURIComponent(id) + '/activate', {
    method: 'POST',
    body: JSON.stringify({version}),
  }),
  uploadSkill: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<{ok: boolean; skill: AgentSkill}>('/api/agent/skills/upload', {method: 'POST', body: form});
  },
  uploadFile: (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<{ok: boolean; file: AgentFile}>('/api/agent/files/upload', {method: 'POST', body: form});
  },
  project: (id: string) => request<{project: {id?: string; title?: string; payload?: {title?: string; nodes?: Array<Record<string, unknown>>}} }>('/api/agent/projects/' + encodeURIComponent(id)),
  runs: (projectId: string, conversationId?: string) => request<{runs: AgentRun[]}>('/api/agent/runs?projectId=' + encodeURIComponent(projectId) + (conversationId ? '&conversationId=' + encodeURIComponent(conversationId) : '')),
  createRun: (body: {projectId: string; skillId: string; prompt: string; modelId?: string; routeId?: string; locks?: Record<string, string>; fileIds?: string[]; referenceNodeIds?: string[]; recipeBindings?: AgentRecipeBinding[]; conversationId?: string}) => request<{ok: boolean; run: AgentRun}>('/api/agent/runs', {
    method: 'POST',
    headers: {'Idempotency-Key': 'run-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 9)},
    body: JSON.stringify(body),
  }),
  run: (id: string) => request<{run: AgentRun}>('/api/agent/runs/' + encodeURIComponent(id)),
  importRun: (id: string) => request<{ok: boolean; run: AgentRun}>('/api/agent/runs/' + encodeURIComponent(id) + '/import', {method: 'POST', body: '{}'}),
  cancel: (id: string) => request('/api/agent/runs/' + encodeURIComponent(id) + '/cancel', {method: 'POST', body: '{}'}),
  eventsUrl: (id: string) => {
    const path = '/api/agent/runs/' + encodeURIComponent(id) + '/events';
    return ENV.VITE_AGENT_URL
      ? agentBaseUrl() + path
      : apiUrl(path);
  },
};
