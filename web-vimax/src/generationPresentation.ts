import type {GenerationTask} from './api';

export type OptimisticGenerationSubmission = {
  startedAt: string;
  operationId: string;
};

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
    && (task.outcome?.phase === 'running' || task.outcome?.phase === 'fetching')
    && task.outcome?.regenerate === 'blocked'
  );
}

export function retainOptimisticGenerationSubmissions(
  tasks: GenerationTask[],
  optimistic: ReadonlyMap<string, OptimisticGenerationSubmission>,
  now = Date.now(),
  ttlMs = 5 * 60 * 1000,
): Map<string, OptimisticGenerationSubmission> {
  const retained = new Map(optimistic);
  const latestByNode = latestTaskByNode(tasks);
  for (const [nodeId, submission] of optimistic) {
    const startedMs = Date.parse(submission.startedAt);
    if (!Number.isFinite(startedMs) || now - startedMs > ttlMs) {
      retained.delete(nodeId);
      continue;
    }
    const persistedOperation = tasks.some(
      (task) => (
        String(task.node_id || '').trim() === nodeId
        && String(task.client_operation_id || '').trim() === submission.operationId
      ),
    );
    if (persistedOperation) {
      // Only the exact operation may release the local submission lock.
      // Comparing browser time with a server timestamp lets an older terminal
      // task hide a new regeneration before the new task row is visible.
      retained.delete(nodeId);
    }
  }
  return retained;
}

export function projectGenerationState(
  tasks: GenerationTask[],
  optimistic: ReadonlyMap<string, OptimisticGenerationSubmission>,
  now = Date.now(),
  ttlMs = 5 * 60 * 1000,
): {
  activeNodeIds: Set<string>;
  latestByNode: Map<string, GenerationTask>;
  retainedOptimistic: Map<string, OptimisticGenerationSubmission>;
} {
  const retainedOptimistic = retainOptimisticGenerationSubmissions(
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

/**
 * While the task ledger says a node is active, a workspace snapshot may still
 * be older than the submit response. Keep the card in its busy projection
 * until a terminal task snapshot releases the node.
 */
export function preserveActiveGenerationStatus<T extends {
  id: string;
  status?: string;
  providerTaskStatus?: string;
}>(
  remote: T,
  activeNodeIds: ReadonlySet<string>,
): T {
  if (!activeNodeIds.has(remote.id) || remote.status === 'generating') {
    return remote;
  }
  return {
    ...remote,
    status: 'generating',
    providerTaskStatus: remote.providerTaskStatus || 'queued',
  };
}

export function mediaGenerationVisualState(input: {
  status?: string;
  mediaUrl?: string;
  finalizePending?: boolean;
  providerLive?: boolean;
  generationBusy?: boolean;
}): {
  hasRenderableMedia: boolean;
  busy: boolean;
  resumableInBackground: boolean;
  submitLocked: boolean;
  failed: boolean;
} {
  const hasRenderableMedia = Boolean(input.mediaUrl);
  // The backend node row is the only card outcome authority. generationBusy
  // is a short local submission lock; providerLive may only extend an already
  // generating row, never override a persisted terminal state.
  const generating = input.status === 'generating';
  const failed = input.status === 'failed';
  return {
    hasRenderableMedia,
    busy: (
      generating
      && !failed
      && input.finalizePending !== true
    ),
    resumableInBackground: false,
    submitLocked: (generating && !failed) || input.generationBusy === true,
    failed,
  };
}

export function isSettledMediaCard(input: {
  status?: string;
  mediaUrl?: string;
  mediaMissing?: boolean;
  providerLive?: boolean;
}): boolean {
  return (
    input.status === 'done'
    && Boolean(input.mediaUrl)
    && input.mediaMissing !== true
    && input.providerLive !== true
  );
}
