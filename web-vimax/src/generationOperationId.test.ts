import {describe, expect, it} from 'vitest';
import {
  claimGenerationOperationId,
  generationIntentSignature,
  newOperationId,
  releaseGenerationOperationId,
  shouldReleaseOperationIdAfterError,
  type OperationIdRecord,
} from './generationOperationId';

function signatureOf(prompt: string, params: Record<string, unknown> = {ratio: '9:16'}) {
  return generationIntentSignature({kind: 'video', prompt, params});
}

describe('generation intent signature', () => {
  it('treats identical prompt and params as the same intent', () => {
    expect(signatureOf('雨夜街头')).toBe(signatureOf('雨夜街头'));
  });

  it('changes when the prompt changes', () => {
    expect(signatureOf('雨夜街头')).not.toBe(signatureOf('雨夜街头，霓虹'));
  });

  it('changes when a param changes', () => {
    expect(signatureOf('雨夜街头', {ratio: '9:16'}))
      .not.toBe(signatureOf('雨夜街头', {ratio: '16:9'}));
  });

  it('never throws on params that cannot be serialized', () => {
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    expect(() => generationIntentSignature({kind: 'video', prompt: 'x', params: circular}))
      .not.toThrow();
  });
});

describe('claiming a generation operation id', () => {
  it('reuses the pending id so a retry cannot be billed twice', () => {
    const store = new Map<string, OperationIdRecord>();
    const signature = signatureOf('雨夜街头');
    const first = claimGenerationOperationId(store, 'media-1', signature, () => 'op-1');
    const retry = claimGenerationOperationId(store, 'media-1', signature, () => 'op-2');

    expect(retry).toBe('op-1');
    expect(first).toBe('op-1');
  });

  it('issues a new id once the user changes the prompt', () => {
    const store = new Map<string, OperationIdRecord>();
    claimGenerationOperationId(store, 'media-1', signatureOf('雨夜街头'), () => 'op-1');
    const changed = claimGenerationOperationId(store, 'media-1', signatureOf('霓虹雨夜'), () => 'op-2');

    expect(changed).toBe('op-2');
    expect(store.get('media-1')).toMatchObject({operationId: 'op-2'});
  });

  it('keeps nodes independent', () => {
    const store = new Map<string, OperationIdRecord>();
    claimGenerationOperationId(store, 'media-1', signatureOf('a'), () => 'op-1');
    const other = claimGenerationOperationId(store, 'media-2', signatureOf('a'), () => 'op-2');

    expect(other).toBe('op-2');
    expect(store.size).toBe(2);
  });

  it('starts a fresh id after a confirmed submission releases the key', () => {
    const store = new Map<string, OperationIdRecord>();
    const signature = signatureOf('雨夜街头');
    claimGenerationOperationId(store, 'media-1', signature, () => 'op-1');
    releaseGenerationOperationId(store, 'media-1');
    const regenerated = claimGenerationOperationId(store, 'media-1', signature, () => 'op-2');

    expect(regenerated).toBe('op-2');
  });

  it('produces a non-empty fallback id when crypto.randomUUID is unavailable', () => {
    const original = globalThis.crypto;
    try {
      // 老版本内核 / 非安全上下文下 randomUUID 不存在，必须仍有可用键。
      Object.defineProperty(globalThis, 'crypto', {value: undefined, configurable: true});
      expect(newOperationId()).toMatch(/^canvas-operation-\d+-[a-z0-9]+$/);
    } finally {
      Object.defineProperty(globalThis, 'crypto', {value: original, configurable: true});
    }
  });
});

describe('failed generation retry policy', () => {
  it('releases the key after a definitive 4xx rejection', () => {
    const error = Object.assign(new Error('供应商明确拒绝参数'), {status: 400});
    expect(shouldReleaseOperationIdAfterError(error)).toBe(true);
  });

  it('keeps the key after a timeout because the task may already exist', () => {
    const error = Object.assign(new Error('请求超过 300 秒没有响应'), {status: 408});
    expect(shouldReleaseOperationIdAfterError(error)).toBe(false);
  });

  it('keeps the key after provider-uncertain 5xx or an unknown transport failure', () => {
    expect(shouldReleaseOperationIdAfterError(
      Object.assign(new Error('供应商请求返回 HTTP 503，但没有返回任务号'), {status: 503}),
    )).toBe(false);
    expect(shouldReleaseOperationIdAfterError(
      new TypeError('Failed to fetch'),
    )).toBe(false);
  });

  it('keeps the key for a 4xx that explicitly reports an uncertain submission', () => {
    expect(shouldReleaseOperationIdAfterError(
      Object.assign(new Error('提交结果未确认，可能已被供应商受理'), {status: 409}),
    )).toBe(false);
  });
});
