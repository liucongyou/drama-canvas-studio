import {afterEach, describe, expect, it, vi} from 'vitest';

type ApiModule = typeof import('./api');
let apiModule: ApiModule | undefined;


afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});


describe('createMediaDownloadUrl', () => {
  it('keeps download tickets on the configured API host', async () => {
    vi.stubGlobal('window', {
      __CANVAS_API_BASE__: 'https://api.example.com',
      location: {pathname: '/'},
    });
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      url: '/api/downloads/media/ticket-token',
      expiresIn: 600,
      downloadName: '铁西硬汉_EP07_段13.mp4',
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    }));
    vi.stubGlobal('fetch', fetchMock);
    apiModule = await import('./api');
    vi.stubGlobal('window', {
      __CANVAS_API_BASE__: 'https://api.example.com',
      location: {pathname: '/'},
    });

    await expect(apiModule.createMediaDownloadUrl(
      'project-1',
      'node-1',
      'video:task-1',
      '铁西硬汉_EP07_段13.mp4',
    )).resolves.toMatchObject({
      url: 'https://api.example.com/api/downloads/media/ticket-token',
    });
    expect(fetchMock).toHaveBeenCalledWith(
      'https://api.example.com/api/projects/project-1/downloads',
      expect.objectContaining({method: 'POST'}),
    );
  });
});
