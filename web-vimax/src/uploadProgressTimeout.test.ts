import {afterEach, describe, expect, it, vi} from 'vitest';
import {uploadWorkspaceFileWithProgress} from './api';


afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});


describe('upload progress watchdog', () => {
  it('aborts an upload that stops making progress', async () => {
    vi.useFakeTimers();

    class FakeUploadRequest {
      upload = {};
      status = 0;
      responseText = '';
      onabort: (() => void) | null = null;
      onerror: (() => void) | null = null;
      onload: (() => void) | null = null;
      open = vi.fn();
      setRequestHeader = vi.fn();
      send = vi.fn();
      abort = vi.fn(() => this.onabort?.());
    }

    const xhr = new FakeUploadRequest();
    vi.stubGlobal('XMLHttpRequest', vi.fn(() => xhr));

    const pending = uploadWorkspaceFileWithProgress(
      'project-1',
      new File(['video'], 'stalled.mp4', {type: 'video/mp4'}),
      {idleTimeoutMs: 15_000},
    );
    const rejection = pending.catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(15_000);

    await expect(rejection).resolves.toEqual(
      expect.objectContaining({message: expect.stringContaining('上传长时间没有进度')}),
    );
    expect(xhr.abort).toHaveBeenCalledOnce();
  });
});
