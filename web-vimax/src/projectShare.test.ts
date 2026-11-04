import {describe, expect, it} from 'vitest';
import {normalizeShareToken, shareTokenInPath} from './projectShare';

describe('project share path', () => {
  it('extracts a token without leaking query or fragment data', () => {
    expect(shareTokenInPath('/share/token-123?ep=EP001')).toBe('token-123');
  });

  it('returns an empty token for ordinary project routes', () => {
    expect(shareTokenInPath('/')).toBe('');
  });

  it('normalizes whitespace', () => {
    expect(normalizeShareToken('  token  ')).toBe('token');
  });
});
