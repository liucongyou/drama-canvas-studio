import {describe, expect, it} from 'vitest';
import {
  classifyWorkspaceRetryError,
  withRequestStatus,
  workspaceRetryDecision,
} from './requestRetry';

describe('workspace request retry policy', () => {
  it('never retries authentication, client or unknown errors', () => {
    const transportError = withRequestStatus(new Error('登录过期'), 401);
    expect(transportError.status).toBe(401);
    expect(workspaceRetryDecision({
      error: transportError,
      attempt: 1,
    }).retry).toBe(false);
    expect(workspaceRetryDecision({
      error: Object.assign(new Error('bad request'), {status: 400}),
      attempt: 1,
    }).retry).toBe(false);
    expect(workspaceRetryDecision({
      error: new Error('layout parse failed'),
      attempt: 1,
    }).retry).toBe(false);
  });

  it('retries transient server errors with capped exponential backoff', () => {
    expect(workspaceRetryDecision({
      error: Object.assign(new Error('bad gateway'), {status: 502}),
      attempt: 1,
    })).toMatchObject({retry: true, kind: 'server', delayMs: 1500});
    expect(workspaceRetryDecision({
      error: Object.assign(new Error('bad gateway'), {status: 502}),
      attempt: 3,
    })).toMatchObject({retry: true, kind: 'server', delayMs: 6000});
    expect(workspaceRetryDecision({
      error: Object.assign(new Error('bad gateway'), {status: 502}),
      attempt: 5,
    })).toMatchObject({retry: false, kind: 'server', delayMs: 0});
  });

  it('pauses offline and waits for the online event', () => {
    expect(classifyWorkspaceRetryError(new Error('Failed to fetch'), false)).toBe('offline');
    expect(workspaceRetryDecision({
      error: new Error('Failed to fetch'),
      attempt: 1,
      online: false,
    })).toMatchObject({retry: false, kind: 'offline', delayMs: 0});
  });

  it('retries network and 429, but not a context-free browser TimeoutError', () => {
    expect(workspaceRetryDecision({
      error: new TypeError('Failed to fetch'),
      attempt: 1,
    })).toMatchObject({retry: true, kind: 'network', delayMs: 1500});
    expect(workspaceRetryDecision({
      error: Object.assign(new Error('too many requests'), {status: 429}),
      attempt: 1,
    })).toMatchObject({retry: true, kind: 'rate_limit', delayMs: 1500});
    expect(workspaceRetryDecision({
      error: new DOMException('signal timed out', 'TimeoutError'),
      attempt: 1,
    }).retry).toBe(false);
  });
});
