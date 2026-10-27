import {afterEach, describe, expect, it, vi} from 'vitest';
import {AUTH_REQUIRED_EVENT, generateCanvasNodeNow, getCanvasMediaNodes, getModelCatalog, getQualityReport, getSessions, mediaVersionList, subscribeToEvents} from './api';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('api request recovery', () => {
  it('retries transient read failures', async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(new Response(JSON.stringify({activeSessionId: 'session-1', sessions: []}), {
        status: 200,
        headers: {'Content-Type': 'application/json'},
      }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(getSessions()).resolves.toEqual({activeSessionId: 'session-1', sessions: []});
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('turns an aborted request into a readable local-service message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new DOMException('signal is aborted without reason', 'AbortError')));

    await expect(getSessions()).rejects.toThrow('本地服务响应超时，正在自动重连，请稍后重试。');
  });

  it('maps a reference-count validation error before the generic HTTP 500 message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: '当前模型最多接受 5 个参考图片，实际 6 个',
    }), {
      status: 400,
      headers: {'Content-Type': 'application/json'},
    })));

    const message = await getModelCatalog().catch((error) => String(error));
    expect(message).toContain('参考素材不符合当前模型要求');
    expect(message).not.toContain('供应商服务暂时异常');
  });

  it('uses the compact position for the selected episode', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: 'project-1',
      title: '测试项目',
      payload: {
        nodes: [{
          id: 'role-1',
          type: 'image',
          title: '角色｜赵铁柱（图1）',
          position: {x: 880, y: 1200},
          metadata: {status: 'done', content: '/uploads/role.webp', params: {ratio: '1:1'}},
        }],
        connections: [],
        episodeImports: [{id: 'EP002', nodeIds: ['role-1'], videoNodeIds: []}],
        episodeLayouts: {EP002: {'role-1': {x: 0, y: 0}}},
      },
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    })));

    const nodes = await getCanvasMediaNodes('project-1', 'EP002');
    expect(nodes).toHaveLength(1);
    expect(nodes[0]).toMatchObject({id: 'role-1', x: 0, y: 0});
  });

  it('does not show a sequence video as a generation ref unless the prompt names it', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: 'project-1',
      title: '测试项目',
      payload: {
        nodes: [
          {
            id: 'video-1',
            type: 'video',
            title: '段1 · 前序',
            metadata: {status: 'idle', params: {ratio: '9:16'}},
          },
          {
            id: 'video-2',
            type: 'video',
            title: '段2 · 当前',
            metadata: {
              prompt: '当前画面，没有声明要参考前序视频。',
              status: 'idle',
              params: {ratio: '9:16', refs: [{nodeId: 'video-1', label: '段1 · 前序'}]},
            },
          },
        ],
        connections: [{fromNodeId: 'video-1', toNodeId: 'video-2'}],
        episodeImports: [{id: 'EP007', nodeIds: ['video-1', 'video-2'], videoNodeIds: ['video-1', 'video-2']}],
      },
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    })));

    const nodes = await getCanvasMediaNodes('project-1', 'EP007');
    const current = nodes.find((node) => node.id === 'video-2');
    expect(current?.params?.refs ?? []).toEqual([]);
  });

  it('uses one media version list for image, audio and video', () => {
    const base = {
      id: 'media-1',
      title: '素材',
      prompt: '',
      status: 'done' as const,
      x: 0,
      y: 0,
      updatedAt: '2026-10-26T00:00:00Z',
    };
    const image = mediaVersionList({
      ...base,
      kind: 'image',
      mediaUrl: '/uploads/v2.png',
      params: {
        mediaVersions: [{url: '/uploads/v1.png'}, {url: '/uploads/v2.png'}],
        mediaVersionIndex: 1,
      },
    });
    const audio = mediaVersionList({
      ...base,
      kind: 'audio',
      mediaUrl: '/uploads/v3.mp3',
      params: {
        mediaVersions: [{url: '/uploads/v1.mp3'}, {url: '/uploads/v3.mp3'}],
      },
    });
    const video = mediaVersionList({
      ...base,
      kind: 'video',
      mediaUrl: '/uploads/v2.mp4',
      params: {
        videoVersions: [{url: '/uploads/v1.mp4'}, {url: '/uploads/v2.mp4'}],
      },
    });
    expect(image).toHaveLength(2);
    expect(audio).toHaveLength(2);
    expect(video).toHaveLength(2);
  });

  it('computes the gate badge from passed segments instead of a fixed score', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      passed: false,
      results: [
        {ok: true, title: '段1'},
        {ok: false, title: '段2'},
      ],
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    })));
    const report = await getQualityReport('project-1', 'EP001');
    expect(report.overall_score).toBe(50);
    expect(report.levels?.fail).toBe(1);
  });

  it('emits immediate completion for a direct second generation request', async () => {
    const events: Array<Record<string, unknown>> = [];
    const unsubscribe = subscribeToEvents((event) => events.push(event), () => {});
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      url: '/uploads/generated-second.png',
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(generateCanvasNodeNow('project-1', 'node-1')).resolves.toMatchObject({
      deferred: false,
      url: '/uploads/generated-second.png',
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(events).toContainEqual(expect.objectContaining({
      type: 'done',
      sessionId: 'project-1',
      nodeId: 'node-1',
      url: '/uploads/generated-second.png',
    }));
    unsubscribe();
  });

  it('does not treat a provider 401 as an expired canvas session', async () => {
    const dispatchEvent = vi.fn();
    vi.stubGlobal('window', {dispatchEvent});
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: '供应商鉴权失败：无效令牌',
      code: 'GENERATION_PROVIDER_ERROR',
    }), {
      status: 401,
      headers: {'Content-Type': 'application/json'},
    })));

    await expect(generateCanvasNodeNow('project-1', 'node-1')).rejects.toThrow('供应商鉴权失败');
    expect(dispatchEvent).not.toHaveBeenCalled();
  });

  it('logs out when the server reports an expired account session', async () => {
    const dispatchEvent = vi.fn();
    vi.stubGlobal('window', {dispatchEvent});
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: '请先登录',
      code: 'ACCOUNT_AUTH_REQUIRED',
    }), {
      status: 401,
      headers: {'Content-Type': 'application/json'},
    })));

    await expect(getSessions()).rejects.toThrow();
    expect(dispatchEvent).toHaveBeenCalledWith(expect.any(Event));
    expect((dispatchEvent.mock.calls[0][0] as Event).type).toBe(AUTH_REQUIRED_EVENT);
  });
});
