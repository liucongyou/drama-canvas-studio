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
