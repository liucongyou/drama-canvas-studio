import {describe, expect, it} from 'vitest';

import {parseUploadFileResponse} from './api';

const file = {name: 'role.png', size: 48, type: 'image/png'};

describe('upload response fallback', () => {
  it('accepts a successful durable upload', () => {
    const parsed = parseUploadFileResponse(200, JSON.stringify({
      ok: true,
      url: '/uploads/role.png',
      name: file.name,
      size: file.size,
      mime: file.type,
      storage: {provider: 'tos', status: 'stored'},
    }), file);

    expect(parsed.error).toBeUndefined();
    expect(parsed.result?.url).toBe('/uploads/role.png');
    expect(parsed.result?.storage?.provider).toBe('tos');
  });

  it('continues with the local file when object storage fails', () => {
    const parsed = parseUploadFileResponse(502, JSON.stringify({
      error: '文件已保存在本地，但对象存储上传失败：timeout',
      code: 'STORAGE_UPLOAD_FAILED',
      localUrl: '/uploads/local-role.png',
    }), file);

    expect(parsed.error).toBeUndefined();
    expect(parsed.result?.url).toBe('/uploads/local-role.png');
    expect(parsed.result?.storageWarning).toContain('对象存储上传失败');
  });

  it('still rejects a real upload failure', () => {
    const parsed = parseUploadFileResponse(400, JSON.stringify({
      error: '文件内容与扩展名不匹配',
    }), file);

    expect(parsed.result).toBeUndefined();
    expect(parsed.error).toBe('文件内容与扩展名不匹配');
  });
});
