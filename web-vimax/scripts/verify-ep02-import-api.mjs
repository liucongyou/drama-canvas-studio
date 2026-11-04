import fs from 'node:fs/promises';

const base = (process.env.CANVAS_BASE || 'http://127.0.0.1:8890').replace(/\/$/, '');
const payloadPath = process.env.FEED_PATH
  || 'C:/Users/liuch/WorkBuddy/2026-10-28-17-51-22/铁西硬汉/铁西硬汉_第02集_画布载荷.json';

async function api(path, options = {}) {
  const response = await fetch(base + path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(`${response.status} ${body.error || JSON.stringify(body)}`);
  }
  return body;
}

let projectId = '';
try {
  const text = (await fs.readFile(payloadPath, 'utf8')).replace(/^\uFEFF/, '');
  const feed = JSON.parse(text);
  const project = await api('/api/projects', {
    method: 'POST',
    body: JSON.stringify({title: 'EP02 导入 HTTP 验收'}),
  });
  projectId = project.id;

  const preview = await api(`/api/projects/${projectId}/import/preview`, {
    method: 'POST',
    body: JSON.stringify({
      feed_json: feed,
      source: {name: '铁西硬汉_第02集_画布载荷.json'},
    }),
  });
  const commit = await api(`/api/projects/${projectId}/import`, {
    method: 'POST',
    body: JSON.stringify({
      feed_json: feed,
      source: {name: '铁西硬汉_第02集_画布载荷.json'},
      if_revision: preview.revision,
    }),
  });
  const nodes = await api(
    `/api/projects/${projectId}/workspace-nodes?episodeId=EP002`,
  );
  const audioNodes = nodes.nodes.filter((node) => node.type === 'audio');
  const videoNodes = nodes.nodes.filter((node) => node.type === 'video');
  const voiceRefs = videoNodes.flatMap((node) => (
    node.metadata?.params?.voiceRefs || []
  ));
  const voiceEdges = nodes.connections.filter((connection) => (
    audioNodes.some((audio) => audio.id === connection.fromNodeId)
    && videoNodes.some((video) => video.id === connection.toNodeId)
  ));
  const result = {
    projectId,
    previewEpisodeId: preview.episodeId,
    commitEpisodeId: commit.episodeId,
    readiness: commit.readiness,
    nodeCount: nodes.nodes.length,
    videoCount: videoNodes.length,
    audioCount: audioNodes.length,
    voiceRefCount: voiceRefs.length,
    voiceEdgeCount: voiceEdges.length,
    warnings: commit.warnings || [],
  };
  console.log(JSON.stringify(result, null, 2));
  if (preview.episodeId !== 'EP002' || commit.episodeId !== 'EP002') {
    throw new Error('episode id mismatch');
  }
  if (videoNodes.length !== 8) throw new Error('expected 8 video nodes');
  if (voiceRefs.length === 0) throw new Error('voice refs missing');
  if (voiceEdges.length === 0) throw new Error('voice edges missing');
} finally {
  if (projectId) {
    await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
  }
}
