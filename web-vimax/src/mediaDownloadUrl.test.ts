import {readFileSync} from 'node:fs';
import {describe, expect, it} from 'vitest';


describe('createMediaDownloadUrl contract', () => {
  it('keeps download tickets on the configured API host', () => {
    const source = readFileSync(new URL('./api.ts', import.meta.url), 'utf8');
    const start = source.indexOf('export async function createMediaDownloadUrl');
    const end = source.indexOf('\nexport ', start + 1);
    const implementation = source.slice(start, end === -1 ? undefined : end);

    expect(implementation).toContain(
      '`/api/projects/${encodeURIComponent(sessionId)}/downloads`',
    );
    expect(implementation).toContain("url: apiUrl(String(ticket.url || ''))");
    expect(implementation).not.toContain('fetch(');
  });
});
