const base = 'http://127.0.0.1:8890';

async function api(pathname, options = {}) {
  const response = await fetch(base + pathname, {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
  });
  const payload = await response.json().catch(() => ({}));
  return {response, payload};
}

const created = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title: '生成前依赖拦截回归测试'}),
});
if (!created.response.ok) throw new Error(JSON.stringify(created.payload));
const projectId = created.payload.id;
const imageId = 'preflight-image-idle';
const videoId = 'preflight-video';

try {
  const saved = await api(`/api/projects/${projectId}/nodes`, {
    method: 'PUT',
    body: JSON.stringify({
      nodes: [
        {
          id: imageId,
          type: 'image',
          title: '角色｜测试（图1）',
          position: {x: 0, y: 0},
          metadata: {prompt: '测试角色', status: 'idle', content: '', params: {}},
        },
        {
          id: videoId,
          type: 'video',
          title: '段1 · 依赖未就绪',
          position: {x: 400, y: 0},
          metadata: {
            prompt: '参考@角色｜测试（图1）。台词：无',
            status: 'idle',
            content: '',
            refs: [{nodeId: imageId, label: '角色｜测试（图1）'}],
            params: {
              provider: 'ark',
              model: 'doubao-seedance-2-0',
              ratio: '9:16',
              resolution: '480P',
              duration: 5,
              refs: [{nodeId: imageId, label: '角色｜测试（图1）'}],
            },
          },
        },
      ],
    }),
  });
  if (!saved.response.ok) throw new Error(JSON.stringify(saved.payload));

  const generated = await api(`/api/projects/${projectId}/nodes/${videoId}/generate`, {
    method: 'POST',
    body: '{}',
  });
  if (generated.response.status !== 409 || generated.payload.code !== 'REFERENCE_NOT_READY') {
    throw new Error(`expected REFERENCE_NOT_READY, got ${generated.response.status} ${JSON.stringify(generated.payload)}`);
  }

  const tasks = await api(`/api/generation-tasks?projectId=${encodeURIComponent(projectId)}&limit=20`);
  if (!tasks.response.ok) throw new Error(JSON.stringify(tasks.payload));
  if ((tasks.payload.tasks || []).length !== 0) {
    throw new Error(`preflight created an unusable task: ${JSON.stringify(tasks.payload.tasks)}`);
  }

  const project = await api(`/api/projects/${projectId}`);
  const video = project.payload.payload.nodes.find((node) => node.id === videoId);
  if (!video || video.metadata?.status === 'generating' || video.metadata?.status === 'failed') {
    throw new Error(`blocked preflight mutated node status: ${JSON.stringify(video?.metadata)}`);
  }

  await api(`/api/projects/${projectId}/nodes/${videoId}`, {
    method: 'PUT',
    body: JSON.stringify({
      metadata: {status: 'failed', lastError: '参考素材缺失 1 项：角色未生成'},
    }),
  });
  const localProjectList = await api('/api/projects');
  const localProject = (localProjectList.payload.projects || []).find((item) => item.id === projectId);
  if (localProject?.local_blocked_count !== 1 || localProject?.failed_count !== 0) {
    throw new Error(`local validation was counted as provider failure: ${JSON.stringify(localProject)}`);
  }

  await api(`/api/projects/${projectId}/nodes/${videoId}`, {
    method: 'PUT',
    body: JSON.stringify({
      metadata: {status: 'failed', lastError: '供应商接口 HTTP500: upstream error'},
    }),
  });
  const providerProjectList = await api('/api/projects');
  const providerProject = (providerProjectList.payload.projects || []).find((item) => item.id === projectId);
  if (providerProject?.local_blocked_count !== 0 || providerProject?.failed_count !== 1) {
    throw new Error(`provider failure classification mismatch: ${JSON.stringify(providerProject)}`);
  }

  console.log(JSON.stringify({
    ok: true,
    code: generated.payload.code,
    missing: generated.payload.missing,
    taskCount: 0,
    nodeStatus: video.metadata?.status || 'idle',
    localBlockedClassified: true,
    providerFailureClassified: true,
  }, null, 2));
} finally {
  await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
}
