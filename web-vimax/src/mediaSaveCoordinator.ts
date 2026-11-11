import {
  classifyWorkspaceRetryError,
  type RetryErrorKind,
} from './requestRetry';
import type {CanvasMediaNode} from './api';

export type MediaSaveRetryDecision = {
  retry: boolean;
  kind: RetryErrorKind;
  delayMs: number;
  attempt: number;
};

const SAVE_RETRY_DELAYS_MS = [1_000, 2_000, 5_000, 15_000, 30_000, 60_000];

function errorRecord(error: unknown): {status: number; code: string} {
  if (!error || typeof error !== 'object') return {status: 0, code: ''};
  const record = error as {status?: unknown; statusCode?: unknown; code?: unknown};
  const rawStatus = record.status ?? record.statusCode;
  const status = Number(rawStatus);
  return {
    status: Number.isFinite(status) ? status : 0,
    code: String(record.code || '').trim().toUpperCase(),
  };
}

export function isMediaSaveConflict(error: unknown): boolean {
  const {status, code} = errorRecord(error);
  return code === 'NODE_EDIT_CONFLICT' || (
    status === 409
    && error instanceof Error
    && error.message.includes('另一个标签页')
  );
}

export function generationOwnsAllNodes(
  nodes: Array<{id: string}>,
  generationNodeIds: ReadonlySet<string>,
): boolean {
  return nodes.length > 0
    && nodes.every((node) => generationNodeIds.has(node.id));
}

/**
 * Rebase a local edit onto the current server revision. Generation-owned
 * fields come from the server; the user's prompt/params draft is reapplied.
 */
export function rebaseLocalMediaNode(
  local: CanvasMediaNode,
  remote: CanvasMediaNode,
): CanvasMediaNode {
  return {
    ...remote,
    ...local,
    params: {...(remote.params || {}), ...(local.params || {})},
    updatedAt: remote.updatedAt,
  };
}

export function mediaSaveRetryDecision(input: {
  error: unknown;
  attempt: number;
  online?: boolean;
}): MediaSaveRetryDecision {
  const attempt = Math.max(1, Math.floor(input.attempt));
  const kind = classifyWorkspaceRetryError(input.error, input.online ?? true);
  if (!['network', 'server', 'rate_limit'].includes(kind)) {
    return {retry: false, kind, delayMs: 0, attempt};
  }
  return {
    retry: true,
    kind,
    delayMs: SAVE_RETRY_DELAYS_MS[
      Math.min(attempt - 1, SAVE_RETRY_DELAYS_MS.length - 1)
    ],
    attempt,
  };
}
