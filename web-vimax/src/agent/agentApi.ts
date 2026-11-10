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
  createdAt?: string;
};

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
  conversationId?: string;
  prompt: string;
  status: string;
  error: string;
  createdAt?: string;
  updatedAt?: string;
  messages: AgentMessage[];
  steps: AgentStage[];
  stages: AgentStage[];
  artifacts: AgentArtifact[];
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
  return '';
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(agentBaseUrl() + path, {
    credentials: 'same-origin',
    ...init,
    headers: init && init.body instanceof FormData
      ? init.headers
      : {'Content-Type': 'application/json', ...(init && init.headers ? init.headers : {})},
  });
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
  models: () => request<{models: AgentModel[]; defaultModelId?: string}>('/api/agent/models'),
  skills: () => request<{skills: AgentSkill[]}>('/api/agent/skills'),
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
  createRun: (body: {projectId: string; skillId: string; prompt: string; modelId?: string; routeId?: string; locks?: Record<string, string>; fileIds?: string[]; referenceNodeIds?: string[]; conversationId?: string}) => request<{ok: boolean; run: AgentRun}>('/api/agent/runs', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  run: (id: string) => request<{run: AgentRun}>('/api/agent/runs/' + encodeURIComponent(id)),
  importRun: (id: string) => request<{ok: boolean; run: AgentRun}>('/api/agent/runs/' + encodeURIComponent(id) + '/import', {method: 'POST', body: '{}'}),
  cancel: (id: string) => request('/api/agent/runs/' + encodeURIComponent(id) + '/cancel', {method: 'POST', body: '{}'}),
  eventsUrl: (id: string) => agentBaseUrl() + '/api/agent/runs/' + encodeURIComponent(id) + '/events',
};
