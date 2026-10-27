const base = 'http://127.0.0.1:8890';

async function api(path, options = {}) {
  const response = await fetch(base + path, {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
  });
  const payload = await response.json().catch(() => ({}));
  return {status: response.status, payload};
}

const created = await api('/api/projects', {
  method: 'POST',
  body: JSON.stringify({title: '模型参数契约冒烟测试'}),
});
if (created.status !== 200 || !created.payload.id) throw new Error('临时项目创建失败');
const projectId = created.payload.id;
const existingImage = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';

try {
  const nodes = [
    {
      id: 'contract-image',
      type: 'image',
      title: '图片参数测试',
      position: {x: 0, y: 0},
      metadata: {
        prompt: '',
        status: 'done',
        content: existingImage,
        refs: [],
        params: {
          provider: 'ark',
          model: 'doubao-seedream-5-0-pro',
          ratio: '16:9',
          clarity: '2K',
          size: '2048x1152',
          guidance: '高',
          watermark: false,
        },
      },
    },
    {
      id: 'contract-video',
      type: 'video',
      title: '视频参数测试',
      position: {x: 320, y: 0},
      metadata: {
        prompt: '',
        status: 'idle',
        refs: [],
        voiceRefs: [],
        params: {
          provider: 'ark',
          model: 'doubao-seedance-2-5',
          ratio: '9:16',
          resolution: '1080P',
          duration: 30,
          generateAudio: true,
          watermark: false,
        },
      },
    },
    {
      id: 'contract-audio',
      type: 'audio',
      title: '音色参数测试',
      position: {x: 640, y: 0},
      metadata: {
        prompt: '',
        status: 'idle',
        refs: [],
        params: {
          provider: 'aliyun_bailian',
          model: 'cosyvoice-v3-5-flash',
          ttsMode: 'speech',
          voiceId: 'longxiaochun',
          outputFormat: 'wav',
          sampleRate: 24000,
        },
      },
    },
    {
      id: 'contract-text',
      type: 'text',
      title: '文本参数测试',
      position: {x: 960, y: 0},
      metadata: {
        prompt: '',
        status: 'idle',
        refs: [],
        params: {
          provider: 'teamorouter',
          model: 'deepseek-flash-1017',
          maxTokens: 3000,
          temperature: 0.7,
        },
      },
    },
  ];
  const saved = await api(`/api/projects/${projectId}/nodes`, {
    method: 'PUT',
    body: JSON.stringify({nodes}),
  });
  if (saved.status !== 200) throw new Error(`参数写入失败：${JSON.stringify(saved.payload)}`);

  const project = await api(`/api/projects/${projectId}`);
  if (project.status !== 200) throw new Error('项目回读失败');
  const byId = new Map(project.payload.payload.nodes.map((node) => [node.id, node]));
  const expected = {
    'contract-image': ['ark', 'doubao-seedream-5-0-pro', '16:9', '2K', '高'],
    'contract-video': ['ark', 'doubao-seedance-2-5', '9:16', '1080P', 30],
    'contract-audio': ['aliyun_bailian', 'cosyvoice-v3-5-flash', 'speech', 'longxiaochun'],
    'contract-text': ['teamorouter', 'deepseek-flash-1017', 3000, 0.7],
  };
  const checks = {
    'contract-image': (params) => params.provider === expected['contract-image'][0]
      && params.model === expected['contract-image'][1]
      && params.ratio === expected['contract-image'][2]
      && params.clarity === expected['contract-image'][3]
      && params.guidance === expected['contract-image'][4],
    'contract-video': (params) => params.provider === expected['contract-video'][0]
      && params.model === expected['contract-video'][1]
      && params.ratio === expected['contract-video'][2]
      && params.resolution === expected['contract-video'][3]
      && params.duration === expected['contract-video'][4],
    'contract-audio': (params) => params.provider === expected['contract-audio'][0]
      && params.model === expected['contract-audio'][1]
      && params.ttsMode === expected['contract-audio'][2]
      && params.voiceId === expected['contract-audio'][3],
    'contract-text': (params) => params.provider === expected['contract-text'][0]
      && params.model === expected['contract-text'][1]
      && params.maxTokens === expected['contract-text'][2]
      && params.temperature === expected['contract-text'][3],
  };
  for (const [id, predicate] of Object.entries(checks)) {
    const params = byId.get(id)?.metadata?.params || {};
    if (!predicate(params)) throw new Error(`${id} 参数回读不一致：${JSON.stringify(params)}`);
  }

  for (const id of ['contract-image', 'contract-video', 'contract-audio', 'contract-text']) {
    const generated = await api(`/api/projects/${projectId}/nodes/${id}/generate`, {
      method: 'POST',
      body: '{}',
    });
    if (generated.status !== 500 || !String(generated.payload.error || '').includes('提示词为空')) {
      throw new Error(`${id} 空提示词门禁异常：${generated.status} ${JSON.stringify(generated.payload)}`);
    }
  }

  const failedProject = await api(`/api/projects/${projectId}`);
  const failedImage = failedProject.payload.payload.nodes.find((node) => node.id === 'contract-image');
  if (failedImage?.metadata?.content !== existingImage) {
    throw new Error('生成失败后丢失了上一版可用图片');
  }
  if (failedImage?.metadata?.status !== 'failed' || !failedImage?.metadata?.lastError) {
    throw new Error(`生成失败状态或原因未持久化：${JSON.stringify(failedImage?.metadata)}`);
  }

  console.log(JSON.stringify({
    ok: true,
    projectId,
    nodes: nodes.length,
    paramsRoundTrip: true,
    emptyPromptGuard: true,
    previousContentPreserved: true,
    failureReasonPersisted: failedImage.metadata.lastError,
  }, null, 2));
} finally {
  await api(`/api/projects/${projectId}`, {method: 'DELETE'});
}
