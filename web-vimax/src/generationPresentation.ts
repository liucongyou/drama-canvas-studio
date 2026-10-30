import type {GenerationTask} from './api';

const STATUS_WITHOUT_TRUSTED_PROGRESS = new Set([
  'queued',
  'submitting',
  'pending',
  'created',
  'submitted',
  'starting',
]);

export const PROVIDER_PROGRESS_STALE_MS = 20_000;

export function generationPhaseLabel(raw: unknown): string {
  const text = String(raw || '').trim();
  const withoutPercent = text.replace(/\s*\d+(?:\.\d+)?\s*%\s*$/, '').trim();
  return withoutPercent.replace(/^排队中/, '生成中');
}

export function generationProgressLabel(
  phase: unknown,
  status: unknown,
  progress: unknown,
  changedAt?: unknown,
  now = Date.now(),
): string {
  const label = generationPhaseLabel(phase) || '生成中';
  if (!hasTrustedProviderProgress(status, progress, changedAt, now)) {
    return label;
  }
  return `${label} ${Math.round(Number(progress))}%`;
}

export function hasTrustedProviderProgress(
  status: unknown,
  progress: unknown,
  changedAt?: unknown,
  now = Date.now(),
): boolean {
  const normalized = String(status || '').trim().toLowerCase();
  const numeric = Number(progress);
  const changedTime = Date.parse(String(changedAt || ''));
  const fresh = Number.isFinite(changedTime)
    ? now - changedTime <= PROVIDER_PROGRESS_STALE_MS
    : false;
  return Boolean(
    normalized
    && !STATUS_WITHOUT_TRUSTED_PROGRESS.has(normalized)
    && fresh
    && Number.isFinite(numeric)
    && numeric > 0
    && numeric < 100,
  );
}

export function generationDurationLabel(task: GenerationTask): string {
  const waiting = task.status === 'queued' || !task.started_at;
  const start = Date.parse(
    waiting
      ? (task.created_at || '')
      : String(task.started_at || task.created_at || ''),
  );
  if (!Number.isFinite(start)) return '';
  const end = task.finished_at ? Date.parse(task.finished_at) : Date.now();
  const seconds = Math.max(
    0,
    Math.floor(((Number.isFinite(end) ? end : Date.now()) - start) / 1000),
  );
  const label = task.finished_at ? '完成于' : waiting ? '等待' : '运行';
  return `${label} ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

function taskTimestamp(task: GenerationTask): number {
  const value = Date.parse(
    String(task.updated_at || task.created_at || ''),
  );
  return Number.isFinite(value) ? value : 0;
}

export function latestTaskByNode(
  tasks: GenerationTask[],
): Map<string, GenerationTask> {
  const latest = new Map<string, GenerationTask>();
  for (const task of tasks) {
    const nodeId = String(task.node_id || '').trim();
    if (!nodeId) continue;
    const current = latest.get(nodeId);
    if (!current) {
      latest.set(nodeId, task);
      continue;
    }
    const taskTime = taskTimestamp(task);
    const currentTime = taskTimestamp(current);
    if (
      taskTime > currentTime
      || (
        taskTime === currentTime
        && String(task.id) > String(current.id)
      )
    ) {
      latest.set(nodeId, task);
    }
  }
  return latest;
}

export function activeGenerationNodeIds(
  tasks: GenerationTask[],
): Set<string> {
  // Project the set from the current task snapshot instead of merging task
  // snapshots forever. A worker crash or a late missing terminal event would
  // otherwise leave an active node id cached in the UI indefinitely.
  return new Set(
    [...latestTaskByNode(tasks).entries()]
      .filter(([, task]) => isGenerationTaskActive(task))
      .map(([nodeId]) => nodeId),
  );
}

export function isGenerationTaskActive(task: GenerationTask): boolean {
  if (['queued', 'submitting', 'running'].includes(task.status)) return true;
  return (
    task.status === 'interrupted'
    && Boolean(String(task.provider_task_id || '').trim())
    && task.outcome?.phase === 'running'
    && task.outcome?.regenerate === 'blocked'
  );
}

export function retainOptimisticGenerationIds(
  tasks: GenerationTask[],
  optimistic: ReadonlyMap<string, string>,
  now = Date.now(),
  ttlMs = 5 * 60 * 1000,
): Map<string, string> {
  const retained = new Map(optimistic);
  const latestByNode = latestTaskByNode(tasks);
  for (const [nodeId, startedAt] of optimistic) {
    const startedMs = Date.parse(startedAt);
    if (!Number.isFinite(startedMs) || now - startedMs > ttlMs) {
      retained.delete(nodeId);
      continue;
    }
    const latestTask = latestByNode.get(nodeId);
    if (latestTask && taskTimestamp(latestTask) >= startedMs) {
      // A persisted task now owns this node. Terminal failure must win over a
      // local optimistic lock; an active task is projected separately.
      retained.delete(nodeId);
    }
  }
  return retained;
}

export function projectGenerationState(
  tasks: GenerationTask[],
  optimistic: ReadonlyMap<string, string>,
  now = Date.now(),
  ttlMs = 5 * 60 * 1000,
): {
  activeNodeIds: Set<string>;
  latestByNode: Map<string, GenerationTask>;
  retainedOptimistic: Map<string, string>;
} {
  const retainedOptimistic = retainOptimisticGenerationIds(
    tasks,
    optimistic,
    now,
    ttlMs,
  );
  const activeNodeIds = activeGenerationNodeIds(tasks);
  for (const nodeId of retainedOptimistic.keys()) {
    activeNodeIds.add(nodeId);
  }
  return {
    activeNodeIds,
    latestByNode: latestTaskByNode(tasks),
    retainedOptimistic,
  };
}

export function liveGenerationCount(
  tasks: GenerationTask[],
): number {
  return [...latestTaskByNode(tasks).values()]
    .filter(isGenerationTaskActive).length;
}
