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

const project = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title: '删除引用级联清理测试'}),
});
const projectId = project.id;
const imageRef = {nodeId: 'delete-image-source', label: '角色｜甲'};
const voiceRef = {nodeId: 'delete-voice-source', label: '音频｜甲'};

try {
  await api(`/api/projects/${projectId}/nodes`, {
    method: 'PUT',
    body: JSON.stringify({
      nodes: [
        {
          id: imageRef.nodeId,
          type: 'image',
          title: imageRef.label,
          position: {x: 0, y: 0},
          metadata: {
            prompt: '角色图',
            status: 'done',
            content: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
          },
        },
        {
          id: voiceRef.nodeId,
          type: 'audio',
          title: voiceRef.label,
          position: {x: 0, y: 240},
          metadata: {prompt: '角色音色', status: 'idle'},
        },
        {
          id: 'delete-target-video',
          type: 'video',
          title: '删除清理目标',
          position: {x: 420, y: 0},
          metadata: {
            prompt: `@${imageRef.label} @${voiceRef.label} 镜头推进`,
            status: 'idle',
            refs: [imageRef],
            params: {
              provider: 'ark',
              model: 'doubao-seedance-2-0',
              refs: [imageRef],
              voiceRefs: [voiceRef],
              voiceRef,
            },
          },
        },
      ],
    }),
  });

  await api(`/api/projects/${projectId}/nodes/${imageRef.nodeId}`, {method: 'DELETE'});
  let current = await api(`/api/projects/${projectId}`);
  let target = current.payload.nodes.find((node) => node.id === 'delete-target-video');
  if ((target.metadata.refs || []).some((ref) => ref.nodeId === imageRef.nodeId)
    || (target.metadata.params.refs || []).some((ref) => ref.nodeId === imageRef.nodeId)
    || String(target.metadata.prompt).includes(`@${imageRef.label}`)) {
    throw new Error('删除图片源节点后仍残留普通引用');
  }

  await api(`/api/projects/${projectId}/nodes/${voiceRef.nodeId}`, {method: 'DELETE'});
  current = await api(`/api/projects/${projectId}`);
  target = current.payload.nodes.find((node) => node.id === 'delete-target-video');
  if ((target.metadata.params.voiceRefs || []).some((ref) => ref.nodeId === voiceRef.nodeId)
    || target.metadata.params.voiceRef?.nodeId === voiceRef.nodeId
    || String(target.metadata.prompt).includes(`@${voiceRef.label}`)) {
    throw new Error('删除音色源节点后仍残留音色引用');
  }

  console.log(JSON.stringify({
    ok: true,
    projectId,
    imageRefRemoved: true,
    voiceRefRemoved: true,
    prompt: target.metadata.prompt,
  }, null, 2));
} finally {
  await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
}
