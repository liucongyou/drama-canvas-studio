import {afterEach, describe, expect, it, vi} from 'vitest';
import {AUTH_REQUIRED_EVENT, createCanvasEpisode, friendlyMediaError, generateCanvasNodeNow, getCanvas, getCanvasMediaNodes, getCanvasWorkspace, getModelCatalog, getQualityReport, getSessions, loginAccount, mediaVersionList, saveCanvasMediaNodes, saveCanvasMediaNodePositions, subscribeToEvents} from './api';

describe('friendlyMediaError', () => {
  it('translates persisted write/read timeout errors before rendering', () => {
    const write = friendlyMediaError('供应商接口连接失败：The write operation timed out');
    expect(write).toContain('后台仍在继续处理');
    expect(write).not.toContain('未创建供应商任务');
    expect(write).not.toContain('timed out');
    expect(friendlyMediaError('供应商接口连接失败：The read operation timed out')).toBe(write);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe('api request recovery', () => {
  it('creates a manual canvas episode through the project endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      episode: {id: 'EP002', title: '第二集'},
      episodes: ['EP001', 'EP002'],
      revision: 9,
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(createCanvasEpisode('project-1', {
      episodeId: '2',
      title: '第二集',
    })).resolves.toMatchObject({
      episode: {id: 'EP002'},
      revision: 9,
    });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/projects/project-1/episodes'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({episodeId: '2', title: '第二集'}),
      }),
    );
  });

  it('lists a manually created empty episode before it has video nodes', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: 'project-1',
      title: '手动集数项目',
      payload: {
        nodes: [],
        connections: [],
        episodeImports: [
          {id: 'EP001', title: '第01集', nodeIds: [], videoNodeIds: []},
          {id: 'EP002', title: '第02集 · 待制作', nodeIds: [], videoNodeIds: []},
        ],
      },
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    })));

    const canvas = await getCanvas('project-1', 'EP002');
    expect('empty' in canvas).toBe(false);
    if ('empty' in canvas) return;
    expect(canvas.meta.current_ep).toBe('EP002');
    expect(canvas.meta.episodes.map((episode) => episode.ep_id)).toEqual(['EP001', 'EP002']);
    expect(canvas.meta.episodes[1]).toMatchObject({
      ep_id: 'EP002',
      title: '第02集 · 待制作',
      status: 'empty',
      shot_count: 0,
    });
  });

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

  it('keeps provider model_not_found separate from reference errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: '供应商接口HTTP 503：{"code":"model_not_found","message":"No available channel for model demo-image"}',
    }), {
      status: 503,
      headers: {'Content-Type': 'application/json'},
    })));

    const message = await getModelCatalog().catch((error) => String(error));
    expect(message).toContain('没有该模型的可用通道');
    expect(message).not.toContain('参考素材不符合');
  });

  it('maps provider submit timeouts to an actionable retry message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: '供应商接口连接失败：The read operation timed out',
    }), {
      status: 504,
      headers: {'Content-Type': 'application/json'},
    })));

    const message = await getModelCatalog().catch((error) => String(error));
    expect(message).toContain('未取得任务 ID');
    expect(message).toContain('测试连接');
    expect(message).not.toContain('The read operation timed out');
  });

  it('keeps account login errors separate from provider auth failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: '账号或密码错误',
      code: 'AUTH_INVALID',
    }), {
      status: 401,
      headers: {'Content-Type': 'application/json'},
    })));

    await expect(loginAccount('a365879766', 'wrong-password'))
      .rejects.toThrow('账号或密码错误');
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
          positionUpdatedAt: '2026-10-28T13:59:00Z',
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
    expect(nodes[0]).toMatchObject({
      id: 'role-1',
      x: 0,
      y: 0,
      positionUpdatedAt: '2026-10-28T13:59:00Z',
    });
  });

  it('loads canvas, notes and media nodes from one project snapshot', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: 'project-1',
      title: '测试项目',
      payload: {
        title: '测试项目',
        extras: {
          notes: [{id: 'note-1', text: '备注', x: 0, y: 0, updatedAt: '2026-10-28T00:00:00Z'}],
        },
        nodes: [{
          id: 'video-1',
          type: 'video',
          title: '段1',
          position: {x: 10, y: 20},
          metadata: {prompt: '第一段', status: 'idle', params: {ratio: '9:16'}},
        }],
        connections: [],
        episodeImports: [{
          id: 'EP001',
          title: '第01集',
          nodeIds: ['video-1'],
          videoNodeIds: ['video-1'],
        }],
      },
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    }));
    vi.stubGlobal('fetch', fetchMock);

    const workspace = await getCanvasWorkspace('project-1', 'EP001');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(workspace.canvas).not.toHaveProperty('empty');
    expect(workspace.notes).toHaveLength(1);
    expect(workspace.mediaNodes).toMatchObject([{id: 'video-1', x: 10, y: 20}]);
  });

  it('keeps the last usable video visible while the node is regenerating', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: 'project-regenerating',
      title: '重新生成项目',
      payload: {
        nodes: [{
          id: 'video-1',
          type: 'video',
          title: '段1',
          position: {x: 0, y: 0},
          metadata: {
            prompt: '重新生成这一段',
            status: 'generating',
            content: '/uploads/previous-video.mp4',
            providerTaskStatus: 'in_progress',
            params: {ratio: '9:16', model: 'agnes-video-2.5-flash'},
          },
        }],
        connections: [],
        episodeImports: [],
      },
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    })));

    const nodes = await getCanvasMediaNodes('project-regenerating');

    expect(nodes).toHaveLength(1);
    expect(nodes[0]).toMatchObject({
      id: 'video-1',
      status: 'generating',
      mediaUrl: '/uploads/previous-video.mp4',
      providerTaskStatus: 'in_progress',
    });
  });

  it('treats a newly created empty project as an editable canvas', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: 'project-empty',
      title: '空白项目',
      payload: {
        title: '空白项目',
        nodes: [],
        connections: [],
        episodeImports: [],
        extras: {notes: []},
      },
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    })));

    const workspace = await getCanvasWorkspace('project-empty', null);

    expect(workspace.canvas).not.toHaveProperty('empty');
    if (!('empty' in workspace.canvas)) {
      expect(workspace.canvas.meta.episodes).toMatchObject([{ep_id: 'EP001', shot_count: 0}]);
      expect(workspace.canvas.shots).toEqual([]);
    }
    expect(workspace.notes).toEqual([]);
    expect(workspace.mediaNodes).toEqual([]);
  });

  it('sends position-only moves without conflict preconditions', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      updated: 1,
      unchanged: [],
      missing: [],
      updates: [{
        nodeId: 'role-1',
        position: {x: 240, y: 180},
        positionUpdatedAt: '2026-10-28T14:00:01Z',
        updatedAt: '2026-10-28T14:00:01Z',
      }],
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    }));
    vi.stubGlobal('fetch', fetchMock);

    await saveCanvasMediaNodePositions(
      'project-1',
      [{
        id: 'role-1',
        position: {x: 240, y: 180},
      }],
      'EP002',
    );

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.positions[0]).toEqual({
      id: 'role-1',
      position: {x: 240, y: 180},
    });
    expect(body.positions[0].basePosition).toBeUndefined();
    expect(body.positions[0].basePositionUpdatedAt).toBeUndefined();
  });

  it('marks generic media-node position as creation-only, not a position write', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      nodes: 1,
      created: 0,
      updates: [],
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    }));
    vi.stubGlobal('fetch', fetchMock);

    await saveCanvasMediaNodes('project-1', [{
      id: 'role-1',
      kind: 'image',
      title: '角色｜赵铁柱（图1）',
      prompt: '角色定妆',
      status: 'idle',
      x: 320,
      y: 240,
      updatedAt: '2026-10-28T14:00:00Z',
    }], 'EP002');

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.nodes[0].position).toEqual({x: 320, y: 240});
    expect(body.nodes[0].positionWrite).toBeUndefined();
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

  it('sends the current prompt and params with the generation request', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      url: '/uploads/generated-current.png',
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    }));
    vi.stubGlobal('fetch', fetchMock);

    await generateCanvasNodeNow('project-1', 'node-1', {
      prompt: '角色定妆，正面半身像',
      params: {provider: 'agnes-ai', model: 'agnes-image-2.5-flash', ratio: '16:9'},
    });

    const requestBody = JSON.parse(String(fetchMock.mock.calls[0][1].body));
    expect(requestBody).toMatchObject({
      prompt: '角色定妆，正面半身像',
      params: {
        provider: 'agnes-ai',
        model: 'agnes-image-2.5-flash',
        ratio: '16:9',
      },
    });
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

  it('preserves an origin rejection instead of reporting provider auth failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: '跨站请求已拒绝',
      code: 'ORIGIN_REJECTED',
    }), {
      status: 403,
      headers: {'Content-Type': 'application/json'},
    })));

    await expect(saveCanvasMediaNodePositions('project-1', [])).rejects.toThrow('跨站请求已拒绝');
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

  it('routes tenant media through the API host and stores canonical upload paths', async () => {
    vi.resetModules();
    vi.stubGlobal('window', {__CANVAS_API_BASE__: 'https://yjapi.pduoduo.vip'});
    const remoteApi = await import('./api');
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ok: true}), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    }));
    vi.stubGlobal('fetch', fetchMock);

    expect(remoteApi.mediaSourceUrl('/uploads/generated.png')).toBe(
      'https://yjapi.pduoduo.vip/uploads/generated.png',
    );

    await remoteApi.saveCanvasMediaNodes('project-1', [{
      id: 'media-1',
      kind: 'image',
      title: '角色｜赵铁柱（图1）',
      prompt: '',
      mediaUrl: 'https://yjapi.pduoduo.vip/uploads/generated.png',
      status: 'done',
      x: 0,
      y: 0,
      updatedAt: '2026-10-27T00:00:00Z',
      params: {
        ratio: '16:9',
        mediaVersions: [
          {url: 'https://yjapi.pduoduo.vip/uploads/generated.png'},
        ],
      },
    }], 'EP002');

    const requestBody = JSON.parse(String(fetchMock.mock.calls[0][1].body));
    expect(requestBody.episodeId).toBe('EP002');
    expect(requestBody.nodes[0].metadata.content).toBe('/uploads/generated.png');
    expect(requestBody.nodes[0].metadata.params.mediaVersions[0].url).toBe('/uploads/generated.png');
  });
});
