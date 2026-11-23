import type {AgentRun} from './agentApi';

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function normalizeAgentRun(value: unknown): AgentRun | null {
  const raw = record(value);
  const id = typeof raw.id === 'string' ? raw.id.trim() : '';
  if (!id) return null;
  return {
    ...raw,
    id,
    projectId: String(raw.projectId || ''),
    skillId: String(raw.skillId || ''),
    skillVersion: String(raw.skillVersion || ''),
    prompt: String(raw.prompt || ''),
    status: String(raw.status || 'unknown'),
    error: String(raw.error || ''),
    messages: list(raw.messages) as AgentRun['messages'],
    steps: list(raw.steps) as AgentRun['steps'],
    stages: list(raw.stages) as AgentRun['stages'],
    artifacts: list(raw.artifacts) as AgentRun['artifacts'],
    toolCalls: list(raw.toolCalls) as AgentRun['toolCalls'],
  } as AgentRun;
}

export function normalizeAgentRuns(value: unknown): AgentRun[] {
  const seen = new Set<string>();
  const runs: AgentRun[] = [];
  for (const item of list(value)) {
    const run = normalizeAgentRun(item);
    if (!run || seen.has(run.id)) continue;
    seen.add(run.id);
    runs.push(run);
  }
  return runs;
}

export function upsertAgentRun(current: unknown, value: unknown): AgentRun[] {
  const runs = normalizeAgentRuns(current);
  const next = normalizeAgentRun(value);
  if (!next) return runs;
  return [next, ...runs.filter((run) => run.id !== next.id)];
}
