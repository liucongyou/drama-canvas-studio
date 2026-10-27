const apiBase = 'http://127.0.0.1:8890';

async function api(pathname, options = {}) {
  const response = await fetch(apiBase + pathname, {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${response.status} ${payload.error || JSON.stringify(payload)}`);
  return payload;
}

const created = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title: '并发保存丢数据回归测试'}),
});
const projectId = created.id;
const nodes = Array.from({length: 24}, (_, index) => ({
  id: `concurrent-${index}`,
  type: index % 2 === 0 ? 'image' : 'video',
  title: `并发节点 ${index}`,
  position: {x: index * 10, y: index * 20},
  metadata: {
    prompt: `unique-prompt-${index}`,
    status: 'idle',
    content: '',
    refs: [],
    params: {marker: `unique-${index}`},
  },
}));

try {
  await api(`/api/projects/${projectId}/nodes`, {
    method: 'PUT',
    body: JSON.stringify({nodes}),
  });

  await Promise.all(nodes.map((node, index) => api(`/api/projects/${projectId}/nodes/${node.id}`, {
    method: 'PUT',
    body: JSON.stringify({
      metadata: {
        prompt: `updated-prompt-${index}`,
        params: {
          ...node.metadata.params,
          provider: 'ark',
          model: index % 2 === 0 ? 'doubao-seedream-5-0-pro' : 'doubao-seedance-2-0',
        },
      },
    }),
  })));

  const project = await api(`/api/projects/${projectId}`);
  const byId = new Map(project.payload.nodes.map((node) => [node.id, node]));
  const missing = [];
  for (let index = 0; index < nodes.length; index += 1) {
    const node = byId.get(`concurrent-${index}`);
    const params = node?.metadata?.params || {};
    if (node?.metadata?.prompt !== `updated-prompt-${index}`
      || params.marker !== `unique-${index}`
      || !params.model) {
      missing.push(index);
    }
  }
  if (missing.length) throw new Error(`并发保存覆盖了节点：${missing.join(',')}`);

  const conflictNodeId = nodes[0].id;
  const beforeConflict = await api(`/api/projects/${projectId}`);
  const beforeNode = beforeConflict.payload.nodes.find((node) => node.id === conflictNodeId);
  const baseUpdatedAt = beforeNode.updatedAt;
  await api(`/api/projects/${projectId}/nodes/${conflictNodeId}`, {
    method: 'PUT',
    body: JSON.stringify({
      baseUpdatedAt,
      metadata: {prompt: 'remote-tab-wins'},
    }),
  });
  const staleResponse = await fetch(`${apiBase}/api/projects/${projectId}/nodes/${conflictNodeId}`, {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      baseUpdatedAt,
      metadata: {prompt: 'stale-tab-must-not-overwrite'},
    }),
  });
  const stalePayload = await staleResponse.json().catch(() => ({}));
  if (staleResponse.status !== 409 || stalePayload.code !== 'NODE_EDIT_CONFLICT') {
    throw new Error(`expected conflict rejection, got ${staleResponse.status} ${JSON.stringify(stalePayload)}`);
  }
  const afterConflict = await api(`/api/projects/${projectId}`);
  const afterNode = afterConflict.payload.nodes.find((node) => node.id === conflictNodeId);
  if (afterNode?.metadata?.prompt !== 'remote-tab-wins') {
    throw new Error(`stale write overwrote remote prompt: ${afterNode?.metadata?.prompt}`);
  }

  console.log(JSON.stringify({
    ok: true,
    projectId,
    nodes: nodes.length,
    concurrentWritesPreserved: true,
    staleWriteRejected: true,
  }, null, 2));
} finally {
  await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
}
