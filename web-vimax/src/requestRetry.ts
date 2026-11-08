export type RetryErrorKind =
  | 'offline'
  | 'rate_limit'
  | 'server'
  | 'network'
  | 'auth'
  | 'client'
  | 'unknown';

type RetryStatusError = Error & {
  status?: number;
  statusCode?: number;
  code?: string;
};

export type WorkspaceRetryDecision = {
  retry: boolean;
  kind: RetryErrorKind;
  delayMs: number;
};

const RETRY_BASE_MS = 1_500;
const RETRY_MAX_MS = 30_000;

function numericStatus(error: unknown): number {
  if (!error || typeof error !== 'object') return 0;
  const value = (error as {status?: unknown; statusCode?: unknown}).status
    ?? (error as {statusCode?: unknown}).statusCode;
  const status = Number(value);
  return Number.isFinite(status) ? status : 0;
}

export function withRequestStatus(
  error: unknown,
  status: number,
  code = '',
): RetryStatusError {
  const next = (
    error instanceof Error
      ? error
      : new Error(String(error || `HTTP ${status}`))
  ) as RetryStatusError;
  next.status = status;
  if (code) next.code = code;
  return next;
}

function errorText(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error ?? '');
}

export function classifyWorkspaceRetryError(
  error: unknown,
  online = true,
): RetryErrorKind {
  if (!online) return 'offline';
  const status = numericStatus(error);
  const text = errorText(error).toLowerCase();
  if (
    status === 401
    || status === 403
    || text.includes('鉴权失败')
    || text.includes('authentication required')
    || text.includes('unauthorized')
    || text.includes('forbidden')
  ) {
    return 'auth';
  }
  if (status === 429 || text.includes('http 429') || text.includes('rate limit')) {
    return 'rate_limit';
  }
  if (status >= 500) return 'server';
  if (status >= 400) return 'client';
  if (
    text.includes('failed to fetch')
    || text.includes('networkerror')
    || text.includes('network error')
    || text.includes('load failed')
    || text.includes('connection reset')
    || text.includes('econnreset')
    || text.includes('enotfound')
    || text.includes('econnrefused')
    || text.includes('网络连接')
    || text.includes('连接失败')
  ) {
    return 'network';
  }
  return 'unknown';
}

export function workspaceRetryDecision(input: {
  error: unknown;
  attempt: number;
  online?: boolean;
}): WorkspaceRetryDecision {
  const kind = classifyWorkspaceRetryError(input.error, input.online ?? true);
  const retryable = kind === 'network' || kind === 'server' || kind === 'rate_limit';
  if (!retryable) return {retry: false, kind, delayMs: 0};
  const attempt = Math.max(1, Math.floor(input.attempt));
  if (attempt > 4) return {retry: false, kind, delayMs: 0};
  return {
    retry: true,
    kind,
    delayMs: Math.min(RETRY_MAX_MS, RETRY_BASE_MS * (2 ** (attempt - 1))),
  };
}
