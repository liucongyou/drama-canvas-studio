import {describe, expect, it} from 'vitest';
import {
  auditRequestContext,
  buildAuditQuery,
  formatAuditEntry,
  formatAuditOperation,
} from './auditQuery';

describe('audit query', () => {
  it('omits empty filters and clamps page size', () => {
    expect(buildAuditQuery({
      projectId: ' project-a ',
      actor: '',
      limit: 5000,
    })).toBe('projectId=project-a&limit=500');
  });

  it('encodes timestamps and pagination cursor', () => {
    const query = buildAuditQuery({
      since: '2026-11-04T00:00:00Z',
      beforeId: 'event-before',
      limit: 50,
    });
    expect(query).toContain('since=2026-11-04T00%3A00%3A00Z');
    expect(query).toContain('beforeId=event-before');
    expect(query).toContain('limit=50');
  });

  it('supports tenant audit filtering by recipe identity', () => {
    expect(buildAuditQuery({recipeId: 'handoff-ensemble-focus'}))
      .toBe('recipeId=handoff-ensemble-focus');
  });

  it('shows the operation and batch identifiers used for paid-task tracing', () => {
    expect(formatAuditOperation({operationId: 'op-1'})).toBe('op-1');
    expect(formatAuditEntry({entry: 'batch', batchId: 'batch-1'})).toBe('batch:batch-1');
    expect(formatAuditEntry({entry: 'single'})).toBe('single');
  });

  it('reads request correlation from top-level or nested audit context', () => {
    expect(auditRequestContext({requestId: 'req-1', requestPath: '/api/x'}))
      .toEqual({requestId: 'req-1', requestPath: '/api/x'});
    expect(auditRequestContext({
      auditContext: {requestId: 'req-2', requestPath: '/api/y'},
    })).toEqual({requestId: 'req-2', requestPath: '/api/y'});
  });
});
