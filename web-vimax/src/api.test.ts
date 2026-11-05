import {afterEach, describe, expect, it, vi} from 'vitest';
import {AUTH_REQUIRED_EVENT, activeMediaVersionIndex, createCanvasEpisode, friendlyMediaError, generateCanvasNodeNow, getCanvas, getCanvasEpisodeWorkspace, getCanvasMediaNodes, getCanvasWorkspace, getManagedTenantAudit, getModelCatalog, getQualityReport, getSessions, getWorkspaceIndex, getWorkspaceMediaNodes, isLiveProviderGeneration, loginAccount, mediaVersionList, patchMediaNodesFields, projectCursorStorageKey, projectEventsUrl, saveCanvasEpisodeViewport, saveCanvasMediaNodes, saveCanvasMediaNodePositions, shareEventsUrl, subscribeToEvents} from './api';

describe('project event cursor', () => {
  it('reconnects SSE from the last rendered events for this project', () => {
    expect(projectEventsUrl('project-1', 42)).toContain('/events?since=42');
    expect(shareEventsUrl('share-token', 42))
      .toContain('/api/share/share-token/events?since=42');
    expect(projectCursorStorageKey('project-1'))
      .toBe('vimax.canvas.event-cursor.project-1');
  });
});

describe('friendlyMediaError', () => {
  it('renders persisted provider errors without deriving a card outcome', () => {
    const message = friendlyMediaError(
      '供应商请求返回 HTTP 503，但没有返回任务号；'
      + '提交结果未确认，可能已被供应商受理。'
      + '详情：{"error":{"code":"server_error","message":"Task request failed"}}',
    );
    expect(message).toContain('HTTP 503');
    expect(message).toContain('Task request failed');
    expect(message).not.toContain('可能已经受理');
  });

  it('renders persisted timeout errors without claiming background work', () => {
    const write = friendlyMediaError('供应商接口连接失败：The write operation timed out');
    const read = friendlyMediaError('供应商接口连接失败：The read operation timed out');
    expect(write).toContain('write operation timed out');
    expect(read).toContain('read operation timed out');
    expect(write).not.toContain('后台仍在继续处理');
    expect(read).not.toContain('后台仍在继续处理');
  });

  it('treats a recoverable provider task as still generating', () => {
    expect(isLiveProviderGeneration({
      providerTaskStatus: 'in_progress',
      lastError: '',
    })).toBe(true);
    expect(isLiveProviderGeneration({
      providerTaskStatus: 'failed',
      lastError: '执行租约已过期，但已有供应商任务号；系统会继续查询原任务',
    })).toBe(true);
    expect(isLiveProviderGeneration({
      providerTaskStatus: 'failed',
      lastError: '供应商明确返回任务不存在（HTTP 404）',
    })).toBe(false);
    expect(isLiveProviderGeneration({
      status: 'failed',
      providerTaskStatus: 'uncertain',
      lastError: '供应商请求已发出，但未收到可确认结果',
    })).toBe(false);
    expect(isLiveProviderGeneration({
      status: 'done',
      mediaUrl: '/uploads/completed.mp4',
      providerTaskStatus: 'running',
      lastError: '',
    })).toBe(false);
    expect(isLiveProviderGeneration({
      status: 'done',
      mediaUrl: '/uploads/completed.mp4',
      providerTaskStatus: 'completed',
      lastError: '',
    })).toBe(false);
  });
});

describe('friendlyRequestError', () => {
  it('does not label a canvas save 502 as a provider failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: 'internal server error',
    }), {
      status: 502,
      headers: {'Content-Type': 'application/json'},
    })));

    await expect(
      saveCanvasMediaNodePositions(
        'project-1',
        [{id: 'node-1', position: {x: 20, y: 30}}],
        'EP001',
      ),
    ).rejects.toThrow(/服务端暂时无法完成请求/);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe('api request recovery', () => {
  it('keeps a video thumbUrl separate from the playable video URL', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: 'project-1',
      title: 'author:user:project:1',
      payload: {
        nodes: [{
          id: 'video-1',
          type: 'video',
          title: 'video',
          metadata: {
            content: '/uploads/video.mp4',
            params: {thumbUrl: '/uploads/video-cover.jpg'},
          },
        }],
        connections: [],
        episodeImports: [],
      },
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    })));
    const [node] = await getCanvasMediaNodes('project-1', 'EP001');
    expect(node.mediaUrl).toBe('/uploads/video.mp4');
    expect(node.thumbUrl).toBe('/uploads/video-cover.jpg');
  });

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

  it('persists a per-episode viewport through the dedicated endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      viewport: {x: -120, y: 48, zoom: 0.72},
      episodeId: 'EP004',
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(saveCanvasEpisodeViewport(
      'project-1',
      {x: -120, y: 48, zoom: 0.72},
      'EP004',
    )).resolves.toMatchObject({
      episodeId: 'EP004',
      viewport: {zoom: 0.72},
    });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/projects/project-1/viewport'),
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({
          viewport: {x: -120, y: 48, zoom: 0.72},
          episodeId: 'EP004',
        }),
      }),
    );
  });

  it('rejects project or episode-less canvas writes instead of saving to a null scope', async () => {
    await expect(saveCanvasMediaNodes('project-1', [], '')).rejects.toThrow(
      '新建节点必须指定所属集',
    );
    await expect(saveCanvasMediaNodePositions('project-1', [], '')).rejects.toThrow(
      '保存节点位置必须指定所属集',
    );
    await expect(saveCanvasEpisodeViewport(
      'project-1',
      {x: 0, y: 0, zoom: 1},
      '',
    )).rejects.toThrow('保存视口必须指定所属集');
    await expect(getCanvasMediaNodes('project-1', '')).rejects.toThrow(
      '读取画布节点必须指定集号',
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

  it('gives up on a request that never answers instead of hanging forever', async () => {
    // 没有超时的 fetch 在网络半开时会永久挂起：Promise 既不 resolve 也不 reject，
    // 调用方 finally 不执行。生成链路一旦踩到，卡片永久停在「生成中」且该节点
    // 在本次会话内再也提交不上去。这里用假计时器把 60 秒推过去，锁住兜底行为。
    vi.useFakeTimers();
    try {
      const fetchMock = vi.fn((_url: string, init?: RequestInit) => new Promise((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => {
          reject(new DOMException('signal is aborted without reason', 'AbortError'));
        });
      }));
      vi.stubGlobal('fetch', fetchMock);

      const pending = getModelCatalog().catch((error) => String(error));
      await vi.advanceTimersByTimeAsync(60_000);
      const message = await pending;

      expect(message).toContain('60 秒没有响应');
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(fetchMock.mock.calls[0][1]?.signal).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps the timeout active while a stalled response body is read', async () => {
    vi.useFakeTimers();
    try {
      const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
        const body = new ReadableStream({
          start(controller) {
            init?.signal?.addEventListener('abort', () => {
              controller.error(new DOMException('body aborted', 'AbortError'));
            });
          },
        });
        return Promise.resolve(new Response(body, {
          status: 200,
          headers: {'Content-Type': 'application/json'},
        }));
      });
      vi.stubGlobal('fetch', fetchMock);

      const pending = getModelCatalog().catch((error) => String(error));
      await vi.advanceTimersByTimeAsync(60_000);

      await expect(pending).resolves.toContain('60 秒没有响应');
    } finally {
      vi.useRealTimers();
    }
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

  it('loads project snapshot and task truth together for the initial canvas', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const target = String(url);
      const body = target.includes('/api/generation-tasks')
        ? {tasks: []}
        : target.includes('/workspace-index')
          ? {
              id: 'project-1',
              title: '测试项目',
              revision: 1,
              updatedAt: '2026-10-28T00:00:00Z',
              projectMeta: {title: '测试项目', extras: {notes: [{id: 'note-1', text: '备注', x: 0, y: 0, updatedAt: '2026-10-28T00:00:00Z'}]}},
              episodes: [{
                id: 'EP001',
                title: '第01集',
                nodeIds: ['video-1'],
                shot_count: 7,
                described_count: 7,
              }],
              episodeViewports: {EP001: {x: 12, y: 34, zoom: 0.66}},
            }
          : target.includes('/workspace-nodes')
            ? {
                episodeId: 'EP001',
                episode: {id: 'EP001', title: '第01集', nodeIds: ['video-1']},
                nodes: [{
                  id: 'video-1',
                  type: 'video',
                  title: '段1',
                  position: {x: 10, y: 20},
                  metadata: {prompt: '第一段', status: 'idle', params: {ratio: '9:16'}},
                }],
                connections: [],
                layouts: {},
                notes: [{
                  id: 'note-1',
                  episodeId: 'EP001',
                  text: '备注',
                  x: 0,
                  y: 0,
                  updatedAt: '2026-10-28T00:00:00Z',
                }],
              }
            : target.includes('/api/projects/project-1')
              ? {
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
              episodeViewports: {
                EP001: {x: 12, y: 34, zoom: 0.66},
              },
              episodeImports: [{
                id: 'EP001',
                title: '第01集',
                nodeIds: ['video-1'],
                videoNodeIds: ['video-1'],
              }],
            },
                }
              : {
                  error: 'not found',
                };
      const status = body && 'error' in body ? 404 : 200;
      return Promise.resolve(new Response(JSON.stringify(body), {
        status,
        headers: {'Content-Type': 'application/json'},
      }));
    });
    vi.stubGlobal('fetch', fetchMock);

    const workspace = await getCanvasWorkspace('project-1', 'EP001');

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(workspace.canvas).not.toHaveProperty('empty');
    expect(workspace.notes).toHaveLength(1);
    expect(workspace.canvas.meta.episodes[0]).toMatchObject({
      ep_id: 'EP001',
      shot_count: 7,
      described_count: 7,
    });
    expect(workspace.mediaNodes).toMatchObject([{id: 'video-1', x: 10, y: 20}]);
    expect(workspace.activeEpisodeId).toBe('EP001');
    expect(workspace.episodeViewports).toMatchObject({
      EP001: {zoom: 0.66},
    });
    expect(workspace.tasks).toEqual([]);
  });

  it('rejects an aborted workspace load instead of presenting it as an empty project', async () => {
    const abortError = new DOMException('aborted', 'AbortError');
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(abortError));
    const controller = new AbortController();
    controller.abort();
    await expect(
      getCanvasWorkspace('project-1', 'EP001', controller.signal),
    ).rejects.toMatchObject({name: 'AbortError'});
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

    const nodes = await getCanvasMediaNodes('project-regenerating', 'EP001');

    expect(nodes).toHaveLength(1);
    expect(nodes[0]).toMatchObject({
      id: 'video-1',
      status: 'generating',
      mediaUrl: '/uploads/previous-video.mp4',
      providerTaskStatus: 'in_progress',
    });
  });

  it('keeps the last usable video visible after a regeneration failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: 'project-failed-regeneration',
      title: '失败重生成项目',
      payload: {
        nodes: [{
          id: 'video-1',
          type: 'video',
          title: '段4',
          position: {x: 0, y: 0},
          metadata: {
            prompt: '重新生成这一段',
            status: 'failed',
            content: '/uploads/previous-video.mp4',
            lastError: '供应商明确返回任务不存在（HTTP 404）',
            providerTaskStatus: 'failed',
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

    const nodes = await getCanvasMediaNodes('project-failed-regeneration', 'EP001');

    expect(nodes).toHaveLength(1);
    expect(nodes[0]).toMatchObject({
      id: 'video-1',
      status: 'failed',
      mediaUrl: '/uploads/previous-video.mp4',
    });
  });

  it('keeps a failed result after refresh instead of flipping it to done', async () => {
    // Regression: the workspace projection used to return `status: 'done'`
    // for any node that had a mediaUrl, wiping the persisted failure and its
    // reason. After a page refresh the card looked successful again even
    // though the supplier had rejected the new run (e.g. HTTP 503 queue full).
    const failedNode = {
      id: 'video-failed',
      type: 'video',
      title: '段3 · 念一遍',
      position: {x: 0, y: 0},
      metadata: {
        prompt: '重新生成',
        status: 'failed',
        content: '/uploads/previous-video.mp4',
        lastError: '供应商接口 HTTP 503：供应商视频队列已满，本次未创建任务',
        providerTaskStatus: 'failed',
        providerTaskPhase: '生成失败',
        params: {ratio: '9:16', model: 'agnes-video-2.5-flash'},
      },
    };
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      const target = String(url);
      const body = target.includes('/api/generation-tasks')
        ? {tasks: []}
        : target.includes('/workspace-index')
          ? {
              id: 'project-failed-refresh',
              title: '失败刷新',
              revision: 1,
              updatedAt: '2026-11-01T00:00:00Z',
              projectMeta: {},
              episodes: [{
                id: 'EP001',
                title: '第01集',
                nodeIds: [failedNode.id],
              }],
              episodeViewports: {},
            }
          : target.includes('/workspace-nodes')
            ? {
                episodeId: 'EP001',
                episode: {
                  id: 'EP001',
                  title: '第01集',
                  nodeIds: [failedNode.id],
                },
                nodes: [failedNode],
                connections: [],
                layouts: {},
                notes: [],
              }
            : {
                id: 'project-failed-refresh',
                title: '失败刷新',
                payload: {
                  nodes: [failedNode],
                  connections: [],
                  episodeImports: [{
                    id: 'EP001',
                    title: '第01集',
                    nodeIds: [failedNode.id],
                    videoNodeIds: [failedNode.id],
                  }],
                },
              };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200,
        headers: {'Content-Type': 'application/json'},
      }));
    }));

    const workspace = await getCanvasWorkspace(
      'project-failed-refresh',
      'EP001',
    );
    expect(workspace.mediaNodes[0]).toMatchObject({
      status: 'failed',
      mediaUrl: '/uploads/previous-video.mp4',
    });
    expect(workspace.mediaNodes[0].lastError).toContain('队列已满');
  });

  it('treats a newly created empty project as an editable canvas', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      const target = String(url);
      const body = target.includes('/api/generation-tasks')
        ? {tasks: []}
        : target.includes('/workspace-index')
          ? {
              id: 'project-empty',
              title: '空白项目',
              revision: 1,
              updatedAt: '2026-10-31T00:00:00Z',
              projectMeta: {title: '空白项目', extras: {notes: []}},
              episodes: [],
              episodeViewports: {},
            }
          : target.includes('/workspace-nodes')
            ? {
                episodeId: 'EP001',
                episode: {id: 'EP001', title: '空白项目', nodeIds: []},
                nodes: [],
                connections: [],
                layouts: {},
              }
            : {
                id: 'project-empty',
                title: '空白项目',
                payload: {
                  title: '空白项目',
                  nodes: [],
                  connections: [],
                  episodeImports: [],
                  extras: {notes: []},
                },
              };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200,
        headers: {'Content-Type': 'application/json'},
      }));
    }));

    const workspace = await getCanvasWorkspace('project-empty', null);

    expect(workspace.canvas).not.toHaveProperty('empty');
    if (!('empty' in workspace.canvas)) {
      expect(workspace.canvas.meta.episodes).toMatchObject([{ep_id: 'EP001', shot_count: 0}]);
      expect(workspace.canvas.shots).toEqual([]);
    }
    expect(workspace.notes).toEqual([]);
    expect(workspace.mediaNodes).toEqual([]);
  });

  it('reads read-only shares through the same backend node projection', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const target = String(url);
      const body = target.includes('/api/share/share-token/workspace-index')
        ? {
            id: 'project-shared',
            title: '共享项目',
            revision: 3,
            updatedAt: '2026-11-04T00:00:00Z',
            projectMeta: {title: '共享项目'},
            episodes: [{
              id: 'EP001',
              title: '第一集',
              nodeIds: ['image-1'],
              videoNodeIds: [],
            }],
            episodeViewports: {},
          }
        : target.includes('/api/share/share-token/workspace-nodes')
          ? {
              episodeId: 'EP001',
              episode: {id: 'EP001', title: '第一集'},
              nodes: [{
                id: 'image-1',
                type: 'image',
                title: '角色｜甲（图1）',
                metadata: {
                  status: 'done',
                  content: '/uploads/shared.png',
                },
              }],
              connections: [],
              layouts: {},
              notes: [],
            }
          : null;
      if (!body) throw new Error(`unexpected share URL: ${target}`);
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200,
        headers: {'Content-Type': 'application/json'},
      }));
    });
    vi.stubGlobal('fetch', fetchMock);

    const workspace = await getCanvasWorkspace('share:share-token', 'EP001');

    expect(workspace.mediaNodes).toHaveLength(1);
    expect(workspace.mediaNodes[0].mediaUrl).toBe('/uploads/shared.png');
    expect(workspace.tasks).toEqual([]);
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      '/api/share/share-token/workspace-index',
      '/api/share/share-token/workspace-nodes?episodeId=EP001',
    ]);
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

  it('keeps ordinary node saves free of generation-owned state', async () => {
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
      status: 'failed',
      mediaUrl: '/uploads/stale.png',
      lastError: 'stale task error',
      generationStartedAt: '2026-10-28T13:59:00Z',
      generationFinishedAt: '2026-10-28T14:00:00Z',
      providerTaskStatus: 'failed',
      providerTaskPhase: '生成失败',
      providerTaskProgress: 50,
      x: 320,
      y: 240,
      updatedAt: '2026-10-28T14:00:00Z',
      params: {
        ratio: '16:9',
        mediaVersions: [{url: '/uploads/stale.png'}],
        mediaVersionIndex: 0,
      },
    }], 'EP002');

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.nodes[0].position).toEqual({x: 320, y: 240});
    expect(body.nodes[0].positionWrite).toBeUndefined();
    expect(body.nodes[0].metadata).toEqual({
      prompt: '角色定妆',
      refs: [],
      params: {ratio: '16:9'},
    });
    expect(body.nodes[0].metadata).not.toHaveProperty('status');
    expect(body.nodes[0].metadata).not.toHaveProperty('content');
    expect(body.nodes[0].metadata).not.toHaveProperty('lastError');
    expect(body.nodes[0].metadata).not.toHaveProperty('generationStartedAt');
    expect(body.nodes[0].metadata).not.toHaveProperty('generationFinishedAt');
    expect(body.nodes[0].metadata).not.toHaveProperty('providerTaskStatus');
    expect(body.nodes[0].metadata).not.toHaveProperty('providerTaskPhase');
    expect(body.nodes[0].metadata).not.toHaveProperty('providerTaskProgress');
    expect(body.nodes[0].metadata.params).not.toHaveProperty('mediaVersions');
    expect(body.nodes[0].metadata.params).not.toHaveProperty('mediaVersionIndex');
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

  it('keeps referenced shared audio while isolating episodes', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: 'project-shared-audio',
      title: '共享音频',
      payload: {
        nodes: [
          {
            id: 'video-ep001',
            type: 'video',
            title: '段1',
            metadata: {
              status: 'idle',
              params: {
                ratio: '9:16',
                refs: [{nodeId: 'image-ep001', label: '角色｜甲（图1）'}],
                voiceRefs: [{nodeId: 'audio-shared', label: '音频｜甲（图1）'}],
              },
            },
          },
          {
            id: 'video-ep002',
            type: 'video',
            title: '段2',
            metadata: {
              status: 'idle',
              params: {
                ratio: '9:16',
                refs: [],
                voiceRefs: [{nodeId: 'audio-ep002', label: '音频｜乙（图2）'}],
              },
            },
          },
          {
            id: 'image-ep001',
            type: 'image',
            title: '角色｜甲（图1）',
            metadata: {status: 'done', content: '/uploads/a.png'},
          },
          {
            id: 'image-ep002',
            type: 'image',
            title: '角色｜乙（图2）',
            metadata: {status: 'done', content: '/uploads/b.png'},
          },
          {
            id: 'audio-shared',
            type: 'audio',
            title: '音频｜甲（图1）',
            metadata: {status: 'done', content: '/uploads/a.wav'},
          },
          {
            id: 'audio-ep002',
            type: 'audio',
            title: '音频｜乙（图2）',
            metadata: {status: 'done', content: '/uploads/b.wav'},
          },
        ],
        connections: [],
        episodeImports: [
          {id: 'EP001', nodeIds: ['video-ep001'], videoNodeIds: ['video-ep001']},
          {id: 'EP002', nodeIds: ['video-ep002'], videoNodeIds: ['video-ep002']},
        ],
      },
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    })));

    const nodes = await getCanvasMediaNodes('project-shared-audio', 'EP001');

    expect(nodes.map((node) => node.id).sort()).toEqual([
      'audio-shared',
      'image-ep001',
      'video-ep001',
    ]);
  });

  it('omits storage and task receipts from ordinary metadata patches', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    }));
    vi.stubGlobal('fetch', fetchMock);
    const storage = {
      status: 'stored',
      provider: 'aliyun_oss',
      bucket: 'yjhuabu',
      key: 'drama-canvas/image/hash.jpg',
    };

    await patchMediaNodesFields('project-1', [{
      id: 'image-1',
      kind: 'image',
      title: '本地图片',
      prompt: '',
      status: 'done',
      mediaUrl: '/uploads/hash.jpg',
      storage,
      storageError: '',
      x: 0,
      y: 0,
      updatedAt: '2026-10-31T01:00:00Z',
    }], 'EP010');

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.episodeId).toBe('EP010');
    expect(body.nodes[0].metadata).toMatchObject({
      prompt: '',
      refs: [],
      params: {},
    });
    expect(body.nodes[0].metadata).not.toHaveProperty('status');
    expect(body.nodes[0].metadata).not.toHaveProperty('content');
    expect(body.nodes[0].metadata).not.toHaveProperty('storage');
    expect(body.nodes[0].metadata).not.toHaveProperty('storageError');
  });

  it('persists receipts only for an explicit local media import', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ok: true,
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    }));
    vi.stubGlobal('fetch', fetchMock);
    const storage = {
      status: 'stored',
      provider: 'aliyun_oss',
      bucket: 'yjhuabu',
      key: 'drama-canvas/image/hash.jpg',
    };

    await patchMediaNodesFields('project-1', [{
      id: 'image-1',
      kind: 'image',
      title: '本地图片',
      prompt: '',
      status: 'done',
      mediaUrl: '/uploads/hash.jpg',
      storage,
      storageError: '',
      source: {type: 'local', fileName: 'hash.jpg'},
      x: 0,
      y: 0,
      updatedAt: '2026-10-31T01:00:00Z',
      _mediaImport: true,
    }], 'EP010');

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body.nodes[0].metadata).toMatchObject({
      _mediaImport: true,
      status: 'done',
      content: '/uploads/hash.jpg',
      storage,
      storageError: '',
      source: {type: 'local', fileName: 'hash.jpg'},
    });
  });

  it('projects an active task onto the initial media snapshot atomically', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const target = String(url);
      const body = target.includes('/api/generation-tasks')
        ? {
            tasks: [{
              id: 'task-1',
              project_id: 'project-1',
              node_id: 'image-1',
              status: 'running',
              phase: '生成中',
              progress: 20,
              created_at: '2026-10-31T01:00:00Z',
              updated_at: '2026-10-31T01:00:01Z',
            }],
          }
        : target.includes('/workspace-index')
          ? {
            id: 'project-1',
            title: '测试项目',
              revision: 1,
              updatedAt: '2026-10-31T01:00:00Z',
              projectMeta: {extras: {notes: []}},
              episodes: [{id: 'EP001', title: '第01集', nodeIds: ['image-1'], videoNodeIds: []}],
              episodeViewports: {},
            }
          : {
              episodeId: 'EP001',
              episode: {id: 'EP001', title: '第01集', nodeIds: ['image-1']},
              nodes: [{
                id: 'image-1',
                type: 'image',
                title: '图片',
                metadata: {status: 'generating'},
              }],
              connections: [],
              layouts: {},
              notes: [],
            };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200,
        headers: {'Content-Type': 'application/json'},
      }));
    });
    vi.stubGlobal('fetch', fetchMock);

    const workspace = await getCanvasWorkspace('project-1', 'EP001');

    expect(workspace.mediaNodes).toHaveLength(1);
    expect(workspace.tasks).toHaveLength(1);
    expect(workspace.mediaNodes[0].status).toBe('generating');
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('reuses the preloaded workspace index instead of fetching it twice', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const target = String(url);
      const body = target.includes('/api/generation-tasks')
        ? {tasks: []}
        : target.includes('/workspace-index')
          ? {
              id: 'project-shared-index',
              title: '共享索引',
              revision: 1,
              updatedAt: '2026-11-03T00:00:00Z',
              projectMeta: {extras: {notes: []}},
              episodes: [{
                id: 'EP001',
                title: '第01集',
                nodeIds: [],
                videoNodeIds: [],
              }],
              episodeViewports: {},
            }
          : {
              episodeId: 'EP001',
              episode: {
                id: 'EP001',
                title: '第01集',
                nodeIds: [],
              },
              nodes: [],
              connections: [],
              layouts: {},
              notes: [],
            };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200,
        headers: {'Content-Type': 'application/json'},
      }));
    });
    vi.stubGlobal('fetch', fetchMock);

    const index = await getWorkspaceIndex('project-shared-index');
    const workspace = await getCanvasWorkspace(
      'project-shared-index',
      'EP001',
      undefined,
      index,
    );

    expect(workspace.activeEpisodeId).toBe('EP001');
    expect(fetchMock.mock.calls.filter(
      ([url]) => String(url).includes('/workspace-index'),
    )).toHaveLength(1);
  });

  it('loads a target episode without fetching the full workspace index', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const target = String(url);
      const body = target.includes('/workspace-nodes')
        ? {
            episodeId: 'EP003',
            episode: {id: 'EP003', title: '第03集'},
            nodes: [{
              id: 'image-3',
              type: 'image',
              title: '第三集图片',
              metadata: {
                status: 'done',
                content: '/uploads/ep3.jpg',
                episodeId: 'EP003',
              },
            }],
            connections: [],
            layouts: {},
            notes: [],
          }
        : {tasks: []};
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200,
        headers: {'Content-Type': 'application/json'},
      }));
    });
    vi.stubGlobal('fetch', fetchMock);

    const workspace = await getCanvasEpisodeWorkspace('project-1', 'EP003');

    expect(workspace.activeEpisodeId).toBe('EP003');
    expect(workspace.mediaNodes.map((node) => node.id)).toEqual(['image-3']);
    expect(fetchMock.mock.calls.some(
      ([url]) => String(url).includes('/workspace-index'),
    )).toBe(false);
    expect(fetchMock.mock.calls.filter(
      ([url]) => String(url).includes('/workspace-nodes?episodeId=EP003'),
    )).toHaveLength(1);
  });

  it('renders the backend node row without deriving card state from tasks', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const body = String(url).includes('/api/generation-tasks')
        ? {
            tasks: [{
              id: 'failed-1',
              project_id: 'project-1',
              node_id: 'image-1',
              status: 'failed',
              phase: '执行失联',
              error: '执行线程已失联且没有供应商任务号；本次可安全重新生成。',
              created_at: '2026-10-31T00:00:00Z',
              updated_at: '2026-10-31T00:01:00Z',
              finished_at: '2026-10-31T00:01:00Z',
            }],
          }
        : {
            id: 'project-1',
            title: '测试项目',
            payload: {
              nodes: [{
                id: 'image-1',
                type: 'image',
                title: '失联图片',
                metadata: {status: 'generating'},
              }],
              connections: [],
              episodeImports: [{id: 'EP001', nodeIds: ['image-1'], videoNodeIds: []}],
            },
          };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200,
        headers: {'Content-Type': 'application/json'},
      }));
    });
    vi.stubGlobal('fetch', fetchMock);

    const workspace = await getCanvasWorkspace('project-1', 'EP001');

    expect(workspace.mediaNodes[0]).toMatchObject({
      status: 'generating',
    });
    expect(workspace.tasks).toHaveLength(1);
    expect(workspace.tasks[0].status).toBe('failed');
  });

  it('reads a compact backend projection for modal dependency reconciliation', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      episodeId: 'EP010',
      episode: {id: 'EP010', title: '第10集'},
      nodes: [{
        id: 'image-10',
        type: 'image',
        title: '道具｜大喇叭（图8）',
        metadata: {
          status: 'done',
          content: '/uploads/generated-new.jpg',
          promptStale: false,
          promptStaleReason: '',
          params: {
            ratio: '16:9',
            mediaVersions: [
              {url: '/uploads/generated-old.jpg'},
              {url: '/uploads/generated-new.jpg'},
            ],
            mediaVersionIndex: 1,
          },
        },
      }],
      connections: [],
      layouts: {},
      notes: [],
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    }));
    vi.stubGlobal('fetch', fetchMock);

    const nodes = await getWorkspaceMediaNodes('project-1', 'ep010');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      '/workspace-nodes?episodeId=EP010',
    );
    expect(nodes).toHaveLength(1);
    expect(nodes[0]).toMatchObject({
      id: 'image-10',
      status: 'done',
      mediaUrl: '/uploads/generated-new.jpg',
      promptStale: false,
    });
    expect(nodes[0].params?.mediaVersions).toEqual([
      {url: '/uploads/generated-old.jpg'},
      {url: '/uploads/generated-new.jpg'},
    ]);
  });

  it('returns task progress without changing the backend node row', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const body = String(url).includes('/api/generation-tasks')
        ? {
            tasks: [{
              id: 'interrupted-1',
              project_id: 'project-1',
              node_id: 'image-1',
              status: 'interrupted',
              phase: '结果待确认',
              error: '执行租约已过期，但已有供应商任务号',
              provider_task_id: 'provider-image-1',
              created_at: '2026-10-31T00:00:00Z',
              updated_at: '2026-10-31T00:01:00Z',
              finished_at: '',
            }],
          }
        : {
            id: 'project-1',
            title: '测试项目',
            payload: {
              nodes: [{
                id: 'image-1',
                type: 'image',
                title: '图像生成中',
                metadata: {status: 'generating'},
              }],
              connections: [],
              episodeImports: [{id: 'EP001', nodeIds: ['image-1'], videoNodeIds: []}],
            },
          };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200,
        headers: {'Content-Type': 'application/json'},
      }));
    });
    vi.stubGlobal('fetch', fetchMock);

    const workspace = await getCanvasWorkspace('project-1', 'EP001');

    expect(workspace.mediaNodes[0]).toMatchObject({
      status: 'generating',
    });
    expect(workspace.tasks[0].provider_task_id).toBe('provider-image-1');
    expect(workspace.tasks[0].status).toBe('interrupted');
  });

  it('shows a downloaded image even when task reconciliation is still pending', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      const body = String(url).includes('/api/generation-tasks')
        ? {
            tasks: [{
              id: 'interrupted-media',
              project_id: 'project-1',
              node_id: 'image-1',
              status: 'interrupted',
              phase: '结果待确认',
              provider_task_id: 'provider-image-done',
              created_at: '2026-10-31T00:00:00Z',
              updated_at: '2026-10-31T00:01:00Z',
              finished_at: '',
            }],
          }
        : {
            id: 'project-1',
            title: '测试项目',
            payload: {
              nodes: [{
                id: 'image-1',
                type: 'image',
                title: '已落盘图片',
                metadata: {
                  status: 'generating',
                  content: '/uploads/done.jpg',
                },
              }],
              connections: [],
              episodeImports: [{id: 'EP001', nodeIds: ['image-1'], videoNodeIds: []}],
            },
          };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200,
        headers: {'Content-Type': 'application/json'},
      }));
    }));

    const workspace = await getCanvasWorkspace('project-1', 'EP001');

    expect(workspace.mediaNodes[0]).toMatchObject({
      status: 'generating',
      mediaUrl: '/uploads/done.jpg',
    });
  });

  it('does not synthesize an uncertain node message in the frontend', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
      const body = String(url).includes('/api/generation-tasks')
        ? {tasks: []}
        : {
            id: 'project-1',
            title: '测试项目',
            payload: {
              nodes: [{
                id: 'image-1',
                type: 'image',
                title: '待确认图片',
                metadata: {
                  status: 'error',
                  providerTaskStatus: 'uncertain',
                  lastError: '供应商请求已发出，但未收到可确认结果',
                },
              }],
              connections: [],
              episodeImports: [{id: 'EP001', nodeIds: ['image-1'], videoNodeIds: []}],
            },
          };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200,
        headers: {'Content-Type': 'application/json'},
      }));
    }));

    const workspace = await getCanvasWorkspace('project-1', 'EP001');

    expect(workspace.mediaNodes[0]).toMatchObject({
      status: 'failed',
      providerTaskStatus: 'uncertain',
    });
    expect(workspace.mediaNodes[0].providerTaskPhase).toBeUndefined();
  });

  it('does not use another project episode when switching projects', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const target = String(url);
      const body = target.includes('/api/generation-tasks')
        ? {tasks: []}
        : target.includes('/workspace-index')
          ? {
              id: 'project-2',
              title: '新项目',
              revision: 1,
              updatedAt: '2026-10-31T00:00:00Z',
              projectMeta: {},
              episodes: [{id: 'EP001', title: '第01集', nodeIds: ['image-new']}],
              episodeViewports: {},
            }
          : target.includes('/workspace-nodes')
            ? {
                episodeId: 'EP001',
                episode: {id: 'EP001', title: '第01集', nodeIds: ['image-new']},
                nodes: [{
                  id: 'image-new',
                  type: 'image',
                  title: '新项目图片',
                  metadata: {
                    status: 'done',
                    content: '/uploads/new.png',
                    episodeId: 'EP001',
                  },
                }],
                connections: [],
                layouts: {},
                notes: [],
              }
            : {
            id: 'project-2',
            title: '新项目',
            payload: {
              nodes: [{
                id: 'image-new',
                type: 'image',
                title: '新项目图片',
                metadata: {
                  status: 'done',
                  content: '/uploads/new.png',
                  episodeId: 'EP001',
                },
              }],
              connections: [],
              episodeImports: [{
                id: 'EP001',
                nodeIds: ['image-new'],
                videoNodeIds: [],
              }],
            },
              };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200,
        headers: {'Content-Type': 'application/json'},
      }));
    });
    vi.stubGlobal('fetch', fetchMock);

    const workspace = await getCanvasWorkspace('project-2', 'EP010');

    expect(workspace.mediaNodes.map((node) => node.id)).toEqual(['image-new']);
    expect(workspace.canvas).not.toHaveProperty('empty');
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(
      fetchMock.mock.calls.map((call) => String(call[0])).join(' ')
    ).toContain('episodeId=EP001');
  });

  it('falls back to the first project episode when no episode is selected', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const target = String(url);
      const body = target.includes('/api/generation-tasks')
        ? {tasks: []}
        : target.includes('/workspace-index')
          ? {
              id: 'project-empty',
              title: '空项目',
              revision: 1,
              updatedAt: '2026-10-31T00:00:00Z',
              projectMeta: {},
              episodes: [{id: 'EP001', title: '第01集', nodeIds: ['image-1']}],
              episodeViewports: {},
            }
          : target.includes('/workspace-nodes')
            ? {
                episodeId: 'EP001',
                episode: {id: 'EP001', title: '第01集', nodeIds: ['image-1']},
                nodes: [{
                  id: 'image-1',
                  type: 'image',
                  title: '图片',
                  metadata: {status: 'done'},
                }],
                connections: [],
                layouts: {},
              }
            : {
            id: 'project-empty',
            title: '空项目',
            payload: {
              nodes: [{
                id: 'image-1',
                type: 'image',
                title: '图片',
                metadata: {status: 'done'},
              }],
              connections: [],
              episodeImports: [{id: 'EP001', nodeIds: ['image-1'], videoNodeIds: []}],
            },
              };
      return Promise.resolve(new Response(JSON.stringify(body), {
        status: 200,
        headers: {'Content-Type': 'application/json'},
      }));
    });
    vi.stubGlobal('fetch', fetchMock);

    const workspace = await getCanvasWorkspace('project-empty', '');

    expect(workspace.mediaNodes.map((node) => node.id)).toEqual(['image-1']);
    expect(
      fetchMock.mock.calls.map((call) => String(call[0])).join(' ')
    ).toContain('episodeId=EP001');
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

  it('includes the current generated URL even when version metadata is stale', () => {
    const versions = mediaVersionList({
      id: 'media-1',
      kind: 'image',
      title: '素材',
      prompt: '',
      status: 'done',
      mediaUrl: 'https://cdn.example.com/final.png',
      x: 0,
      y: 0,
      updatedAt: '2026-10-31T09:00:00Z',
      params: {
        mediaVersions: [{url: '/uploads/old.png'}],
        mediaVersionIndex: 0,
      },
    });

    expect(versions.map((version) => version.url)).toEqual([
      '/uploads/old.png',
      'https://cdn.example.com/final.png',
    ]);
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

    await expect(generateCanvasNodeNow('project-1', 'node-1', {
      episodeId: 'EP001',
    })).resolves.toMatchObject({
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

  it('forwards the final storage URL and receipt with the synchronous done event', async () => {
    const events: Array<Record<string, unknown>> = [];
    const unsubscribe = subscribeToEvents((event) => events.push(event), () => {});
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      nodeId: 'node-1',
      status: 'done',
      url: 'https://cdn.example.com/final.png',
      providerTaskStatus: 'completed',
      providerTaskPhase: '已完成',
      providerTaskProgress: 100,
      generationFinishedAt: '2026-10-31T09:00:00Z',
      storage: {
        status: 'stored',
        provider: 'qiniu',
        url: 'https://cdn.example.com/final.png',
      },
      storageError: '',
      params: {
        mediaVersions: [{
          url: 'https://cdn.example.com/final.png',
          sourceType: 'generated',
        }],
        mediaVersionIndex: 0,
      },
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await generateCanvasNodeNow('project-1', 'node-1', {
      episodeId: 'EP001',
    });

    expect(result).toMatchObject({
      deferred: false,
      url: 'https://cdn.example.com/final.png',
      providerTaskStatus: 'completed',
      storage: {status: 'stored', provider: 'qiniu'},
    });
    expect(events).toContainEqual(expect.objectContaining({
      type: 'done',
      sessionId: 'project-1',
      nodeId: 'node-1',
      url: 'https://cdn.example.com/final.png',
      providerTaskStatus: 'completed',
      providerTaskPhase: '已完成',
      providerTaskProgress: 100,
      storage: expect.objectContaining({status: 'stored', provider: 'qiniu'}),
    }));
    unsubscribe();
  });

  it('treats a live provider task returned as accepted 202 as still generating', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      accepted: true,
      taskId: 'local-task-1',
      status: 'pending',
      code: 'PROVIDER_OUTCOME_UNCERTAIN',
    }), {
      status: 202,
      headers: {'Content-Type': 'application/json'},
    })));

    await expect(generateCanvasNodeNow('project-2', 'node-2', {
      episodeId: 'EP001',
    })).resolves.toMatchObject({
      deferred: true,
      accepted: true,
      taskId: 'local-task-1',
    });
  });

  it('forwards generation source metadata to the backend audit', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      accepted: true,
      taskId: 'task-source-audit',
      status: 'pending',
    }), {
      status: 202,
      headers: {'Content-Type': 'application/json'},
    }));
    vi.stubGlobal('fetch', fetchMock);

    await generateCanvasNodeNow('project-1', 'node-1', {
      episodeId: 'EP001',
      entry: 'batch',
      batchId: 'batch-test-1',
    });

    const requestBody = JSON.parse(String(fetchMock.mock.calls[0][1].body));
    expect(requestBody.entry).toBe('batch');
    expect(requestBody.batchId).toBe('batch-test-1');
  });

  it('queries tenant audit through the administrator-scoped endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      events: [],
      nextBeforeId: '',
      hasMore: false,
      tenantId: 'tenant-alpha',
      retention: {maxRows: 50000, ttlDays: 180},
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    }));
    vi.stubGlobal('fetch', fetchMock);

    await getManagedTenantAudit('tenant-alpha', {
      action: 'generation',
      limit: 100,
    });

    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/admin/tenants/tenant-alpha/audit?action=generation&limit=100',
    );
  });

  it('queries one HTTP request across the current tenant audit log', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      events: [],
      nextBeforeId: '',
      hasMore: false,
      tenantId: 'owner',
      retention: {maxRows: 50000, ttlDays: 180},
    }), {
      status: 200,
      headers: {'Content-Type': 'application/json'},
    }));
    vi.stubGlobal('fetch', fetchMock);

    await getManagedTenantAudit('owner', {
      requestId: 'req-123',
      limit: 100,
    });

    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/admin/tenants/owner/audit?requestId=req-123&limit=100',
    );
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
      episodeId: 'EP001',
    });

    const requestBody = JSON.parse(String(fetchMock.mock.calls[0][1].body));
    expect(requestBody).toMatchObject({
      prompt: '角色定妆，正面半身像',
      episodeId: 'EP001',
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

    await expect(generateCanvasNodeNow('project-1', 'node-1', {
      episodeId: 'EP001',
    })).rejects.toThrow('供应商鉴权失败');
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

    await expect(saveCanvasMediaNodePositions('project-1', [], 'EP001')).rejects.toThrow('跨站请求已拒绝');
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
    }], 'EP002', {includeMediaState: true});

    const requestBody = JSON.parse(String(fetchMock.mock.calls[0][1].body));
    expect(requestBody.episodeId).toBe('EP002');
    expect(requestBody.nodes[0].metadata.content).toBe('/uploads/generated.png');
    expect(requestBody.nodes[0].metadata.params.mediaVersions[0].url).toBe('/uploads/generated.png');
  });

  it('keeps distinct versions that share the same URL', () => {
    const versions = mediaVersionList({
      id: 'image-1',
      kind: 'image',
      title: '图片',
      prompt: '',
      mediaUrl: '/uploads/shared.png',
      status: 'done',
      x: 0,
      y: 0,
      updatedAt: '2026-11-04T00:00:00Z',
      params: {
        activeVersionId: 'image:task-b',
        mediaVersions: [
          {
            versionId: 'image:task-a',
            taskId: 'task-a',
            url: '/uploads/shared.png',
          },
          {
            versionId: 'image:task-b',
            taskId: 'task-b',
            url: '/uploads/shared.png',
          },
        ],
      },
    });

    expect(versions).toHaveLength(2);
    expect(versions.map((version) => version.versionId)).toEqual([
      'image:task-a',
      'image:task-b',
    ]);
  });

  it('selects the version by the current media URL when activeVersionId is stale', () => {
    const node = {
      id: 'video-1',
      kind: 'video' as const,
      title: '视频',
      prompt: '',
      mediaUrl: '/uploads/v2.mp4',
      status: 'done' as const,
      x: 0,
      y: 0,
      updatedAt: '2026-11-05T00:00:00Z',
      params: {
        activeVersionId: 'video:task-old',
        mediaVersionIndex: 1,
        videoVersionIndex: 1,
        mediaVersions: [
          {
            versionId: 'video:task-old',
            url: '/uploads/v1.mp4',
          },
          {
            versionId: 'video:task-new',
            url: '/uploads/v2.mp4',
          },
        ],
      },
    };

    expect(activeMediaVersionIndex(node)).toBe(1);
    expect(mediaVersionList(node)).toHaveLength(2);
  });
});
