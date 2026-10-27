import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';

const apiBase = 'http://127.0.0.1:8890';
const root = path.resolve(import.meta.dirname, '..', '..');
const suffix = Date.now().toString(36);
const providerId = `local-e2e-${suffix}`;
const ids = {
  image: `local-image-${suffix}`,
  video: `local-video-${suffix}`,
  text: `local-text-${suffix}`,
  audio: `local-audio-${suffix}`,
};
const calls = [];
const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';

function readBody(request) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    request.on('data', (chunk) => chunks.push(chunk));
    request.on('end', () => {
      const text = Buffer.concat(chunks).toString('utf8');
      try { resolve(text ? JSON.parse(text) : {}); } catch { resolve({raw: text}); }
    });
    request.on('error', reject);
  });
}

const mock = http.createServer(async (request, response) => {
  const url = new URL(request.url || '/', 'http://127.0.0.1');
  const body = await readBody(request);
  calls.push({method: request.method, path: url.pathname, body});

  if (request.method === 'GET' && url.pathname === '/api/v1/models') {
    response.writeHead(200, {'Content-Type': 'application/json'});
    response.end(JSON.stringify({data: []}));
    return;
  }
  if (url.pathname === '/result.png' || url.pathname === '/result.mp4') {
    const video = url.pathname.endsWith('.mp4');
    response.writeHead(200, {'Content-Type': video ? 'video/mp4' : 'image/png'});
    response.end(video ? Buffer.from('mock-mp4-bytes') : Buffer.from(png, 'base64'));
    return;
  }
  if (request.method === 'POST' && url.pathname === '/api/v1/images/generations') {
    response.writeHead(200, {'Content-Type': 'application/json'});
    response.end(JSON.stringify({data: [{url: `${mockBase}/result.png`}]}));
    return;
  }
  if (request.method === 'POST' && url.pathname === '/api/v1/contents/generations/tasks') {
    response.writeHead(200, {'Content-Type': 'application/json'});
    response.end(JSON.stringify({id: 'local-task-1'}));
    return;
  }
  if (request.method === 'GET' && url.pathname === '/api/v1/contents/generations/tasks/local-task-1') {
    response.writeHead(200, {'Content-Type': 'application/json'});
    response.end(JSON.stringify({status: 'succeeded', content: {video_url: `${mockBase}/result.mp4`}}));
    return;
  }
  if (request.method === 'POST' && url.pathname === '/api/v1/chat/completions') {
    response.writeHead(200, {'Content-Type': 'application/json'});
    response.end(JSON.stringify({choices: [{message: {content: 'LOCAL_TEXT_OK'}}]}));
    return;
  }
  if (request.method === 'POST' && url.pathname === '/api/v1/services/audio/tts/SpeechSynthesizer') {
    response.writeHead(200, {'Content-Type': 'application/json'});
    response.end(JSON.stringify({output: {audio: {data: Buffer.from('mock-wav-bytes').toString('base64')}}}));
    return;
  }
  response.writeHead(404, {'Content-Type': 'application/json'});
  response.end(JSON.stringify({error: 'not found'}));
});

await new Promise((resolve) => mock.listen(0, '127.0.0.1', resolve));
const mockBase = `http://127.0.0.1:${mock.address().port}`;

async function api(pathname, options = {}) {
  const response = await fetch(apiBase + pathname, {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${response.status} ${payload.error || JSON.stringify(payload)}`);
  return payload;
}

let projectId = '';
const generatedFiles = [];
try {
  await api('/api/providers', {
    method: 'POST',
    body: JSON.stringify({
      provider: {
        id: providerId,
        name: '本地无计费 E2E',
        protocol: 'openai',
        base_url: `${mockBase}/api/v1`,
        api_key: 'local-test-key',
        enabled: true,
      },
    }),
  });

  const modelPayloads = [
    {
      id: ids.image, capability: 'image', adapter: 'ark_image', remoteModel: `${providerId}-image`,
      label: '本地图片模型', badge: 'IMG',
      defaultParams: {ratio: '1:1', size: '1536x1536', watermark: false},
      params: {ratio: {options: ['1:1']}, size: {type: 'text'}},
      limits: {maxRefs: 4, maxImages: 4, maxPromptChars: 4000},
    },
    {
      id: ids.video, capability: 'video', adapter: 'ark_video', remoteModel: `${providerId}-video`,
      label: '本地视频模型', badge: 'VID',
      defaultParams: {ratio: '9:16', resolution: '480P', duration: 4},
      params: {
        ratio: {options: ['9:16']},
        resolution: {options: ['480P']},
        duration: {min: 4, max: 15, step: 1},
      },
      limits: {maxRefs: 4, maxImages: 2, maxVideos: 2, maxAudios: 2, maxPromptChars: 8000},
    },
    {
      id: ids.text, capability: 'text', adapter: 'openai_chat', remoteModel: `${providerId}-text`,
      label: '本地文本模型', badge: 'TXT',
      defaultParams: {maxTokens: 512, temperature: 0.4},
      params: {
        maxTokens: {min: 64, max: 4096, step: 64},
        temperature: {min: 0, max: 2, step: 0.1},
      },
      limits: {maxRefs: 4, maxTextRefs: 4, maxPromptChars: 32000},
    },
    {
      id: ids.audio, capability: 'audio', adapter: 'dashscope_tts', remoteModel: `${providerId}-tts`,
      label: '本地音色模型', badge: 'TTS',
      defaultParams: {ttsMode: 'speech', voiceId: 'local-voice', outputFormat: 'wav', sampleRate: 24000},
      params: {
        ttsMode: {options: ['speech']},
        outputFormat: {options: ['wav']},
        sampleRate: {options: ['24000']},
      },
      limits: {maxRefs: 4, maxImages: 2, maxTextRefs: 2, maxPromptChars: 4000},
    },
  ];
  for (const model of modelPayloads) {
    await api('/api/models', {
      method: 'POST',
      body: JSON.stringify({model: {...model, providerId, enabled: true, description: 'local e2e'}}),
    });
  }

  const project = await api('/api/projects', {
    method: 'POST',
    body: JSON.stringify({title: '本地供应商全链路生成测试'}),
  });
  projectId = project.id;
  await api(`/api/projects/${projectId}/nodes`, {
    method: 'PUT',
    body: JSON.stringify({
      nodes: [
        {
          id: 'local-source-image',
          type: 'image',
          title: '参考｜本地角色',
          position: {x: 0, y: 0},
          metadata: {
            prompt: '参考角色',
            status: 'done',
            content: `data:image/png;base64,${png}`,
          },
        },
        {
          id: 'local-image-target',
          type: 'image',
          title: '本地图片生成',
          position: {x: 320, y: 0},
          metadata: {
            prompt: '生成图片',
            status: 'generating',
            refs: [{nodeId: 'local-source-image', label: '参考｜本地角色'}],
            params: {provider: providerId, model: ids.image, ratio: '1:1', size: '1536x1536'},
          },
        },
        {
          id: 'local-video-target',
          type: 'video',
          title: '本地视频生成',
          position: {x: 640, y: 0},
          metadata: {
            prompt: '生成视频',
            status: 'generating',
            refs: [{nodeId: 'local-source-image', label: '参考｜本地角色'}],
            params: {
              provider: providerId,
              model: ids.video,
              ratio: '9:16',
              resolution: '480P',
              duration: 4,
              generateAudio: true,
            },
          },
        },
        {
          id: 'local-text-target',
          type: 'text',
          title: '本地文本生成',
          position: {x: 960, y: 0},
          metadata: {
            prompt: '生成一段文本',
            status: 'generating',
            params: {provider: providerId, model: ids.text, maxTokens: 512, temperature: 0.4},
          },
        },
        {
          id: 'local-audio-target',
          type: 'audio',
          title: '本地音色生成',
          position: {x: 1280, y: 0},
          metadata: {
            prompt: '这是本地音色测试台词',
            status: 'generating',
            params: {
              provider: providerId,
              model: ids.audio,
              ttsMode: 'speech',
              voiceId: 'local-voice',
              outputFormat: 'wav',
              sampleRate: 24000,
            },
          },
        },
      ],
    }),
  });

  const endpoints = ['local-image-target', 'local-video-target', 'local-text-target', 'local-audio-target'];
  const responses = {};
  for (const nodeId of endpoints) {
    responses[nodeId] = await api(`/api/projects/${projectId}/nodes/${nodeId}/generate`, {
      method: 'POST',
      body: '{}',
    });
  }

  const current = await api(`/api/projects/${projectId}`);
  const byId = new Map(current.payload.nodes.map((node) => [node.id, node]));
  for (const nodeId of endpoints) {
    const node = byId.get(nodeId);
    if (node?.metadata?.status !== 'done') throw new Error(`${nodeId} 未完成：${JSON.stringify(node?.metadata)}`);
    if (node?.metadata?.production?.provider !== providerId) throw new Error(`${nodeId} 供应商路由错误`);
  }
  if (byId.get('local-text-target')?.metadata?.prompt !== 'LOCAL_TEXT_OK') {
    throw new Error('文本节点未写回生成正文');
  }
  for (const nodeId of ['local-image-target', 'local-video-target', 'local-audio-target']) {
    const content = String(byId.get(nodeId)?.metadata?.content || '');
    if (!content.startsWith('/uploads/')) throw new Error(`${nodeId} 未缓存为本地资产：${content}`);
    generatedFiles.push(content);
  }

  const imageCall = calls.find((call) => call.method === 'POST' && call.path.endsWith('/images/generations'));
  const videoCall = calls.find((call) => call.method === 'POST' && call.path.endsWith('/contents/generations/tasks'));
  const textCall = calls.find((call) => call.method === 'POST' && call.path.endsWith('/chat/completions'));
  const audioCall = calls.find((call) => call.method === 'POST' && call.path.endsWith('/SpeechSynthesizer'));
  if (imageCall?.body?.size !== '1536x1536') throw new Error('图片参数未进入供应商请求');
  if (videoCall?.body?.ratio !== '9:16' || videoCall?.body?.duration !== 4) throw new Error('视频参数未进入供应商请求');
  if (textCall?.body?.max_tokens !== 512 || textCall?.body?.temperature !== 0.4) throw new Error('文本参数未进入供应商请求');
  if (audioCall?.body?.input?.voice !== 'local-voice') throw new Error('音色参数未进入供应商请求');

  console.log(JSON.stringify({
    ok: true,
    projectId,
    providerId,
    completed: endpoints.length,
    localAssets: generatedFiles.length,
    text: 'LOCAL_TEXT_OK',
    providerPayloadsChecked: true,
  }, null, 2));
} finally {
  if (projectId) await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
  await api(`/api/providers/${encodeURIComponent(providerId)}`, {method: 'DELETE'}).catch(() => {});
  for (const file of generatedFiles) {
    if (file.startsWith('/uploads/')) {
      await fs.unlink(path.join(root, 'data', 'uploads', path.basename(file))).catch(() => {});
    }
  }
  await new Promise((resolve) => mock.close(resolve));
}
