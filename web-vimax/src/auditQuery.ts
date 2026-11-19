export type AuditEventQuery = {
  projectId?: string;
  action?: string;
  actor?: string;
  status?: string;
  since?: string;
  until?: string;
  limit?: number;
  beforeId?: string;
  requestId?: string;
  recipeId?: string;
};

export type AuditEventItem = {
  id: string;
  tenantId: string;
  actor: string;
  action: string;
  targetType: string;
  targetId: string;
  status: string;
  details: Record<string, unknown>;
  createdAt: string;
};

export type AuditEventPage = {
  events: AuditEventItem[];
  nextBeforeId: string;
  hasMore: boolean;
  tenantId: string;
  retention: {
    maxRows: number;
    ttlDays: number;
  };
};

function compact(value: unknown): string {
  return String(value ?? '').trim();
}

export function buildAuditQuery(query: AuditEventQuery): string {
  const params = new URLSearchParams();
  for (const key of ['projectId', 'action', 'actor', 'status', 'since', 'until', 'beforeId', 'requestId', 'recipeId'] as const) {
    const value = compact(query[key]);
    if (value) params.set(key, value);
  }
  if (query.limit !== undefined) {
    params.set('limit', String(Math.max(1, Math.min(500, Math.floor(query.limit)))));
  }
  return params.toString();
}

export function formatAuditOperation(details: Record<string, unknown>): string {
  return compact(details.operationId || details.clientOperationId);
}

export function formatAuditEntry(details: Record<string, unknown>): string {
  const entry = compact(details.entry) || 'unknown';
  if (entry !== 'batch') return entry;
  return compact(details.batchId) ? `batch:${compact(details.batchId)}` : 'batch';
}

export function auditRequestContext(details: Record<string, unknown>): {
  requestId: string;
  requestPath: string;
} {
  const nested = details.auditContext && typeof details.auditContext === 'object'
    ? details.auditContext as Record<string, unknown>
    : {};
  return {
    requestId: compact(details.requestId || nested.requestId),
    requestPath: compact(details.requestPath || nested.requestPath),
  };
}
