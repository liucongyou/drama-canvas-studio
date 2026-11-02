import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';


const source = readFileSync(
  resolve(__dirname, 'CanvasView.tsx'),
  'utf8',
);


describe('generation retry wiring', () => {
  it('applies the tested release policy inside the failed submission branch', () => {
    expect(source).toContain('shouldReleaseOperationIdAfterError');
    expect(source).toMatch(
      /const definitiveFailure = shouldReleaseOperationIdAfterError\(error\);/,
    );
    expect(source).toMatch(
      /if \(definitiveFailure\) \{\s*releaseGenerationOperationId\(pendingOperationIdRef\.current, node\.id\);\s*\}/,
    );
  });

  it('keeps the idempotency claim outside the asynchronous request closure', () => {
    const claim = source.indexOf('const clientOperationId = claimOperationId(node, params);');
    const asyncRequest = source.indexOf('void (async () => {', claim);
    expect(claim).toBeGreaterThan(-1);
    expect(asyncRequest).toBeGreaterThan(claim);
  });
});
