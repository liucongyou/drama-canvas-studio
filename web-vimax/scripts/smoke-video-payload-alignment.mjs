import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';

const apiBase = 'http://127.0.0.1:8890';
const root = path.resolve(import.meta.dirname, '..', '..');
const suffix = Date.now().toString(36);
const providerId = `payload-provider-${suffix}`;
const modelId = `payload-video-${suffix}`;
const imageData = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';
const audioData = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAESsAABErAAABAAgAZGF0YQAAAAA=';
let captured = null;
let mockBase = '';

function readBody(request) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    request.on('data', (chunk) => chunks.push(chunk));
    request.on('end', () => {
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}')); }
      catch (error) { reject(error); }
    });
    request.on('error', reject);
  });
}

const mock = http.createServer(async (request, response) => {
  const url = new URL(request.url || '/', 'http://127.0.0.1');
  if (request.method === 'POST' && url.pathname === '/api/v1/contents/generations/tasks') {
    captured = await readBody(request);
    response.writeHead(200, {'Content-Type': 'application/json'});
    response.end(JSON.stringify({id: 'payload-task-1'}));
    return;
  }
  if (request.method === 'GET' && url.pathname === '/api/v1/contents/generations/tasks/payload-task-1') {
    response.writeHead(200, {'Content-Type': 'application/json'});
    response.end(JSON.stringify({status: 'succeeded', content: {video_url: `${mockBase}/result.mp4`}}));
    return;
  }
  if (request.method === 'GET' && url.pathname === '/result.mp4') {
    response.writeHead(200, {'Content-Type': 'video/mp4'});
    response.end(Buffer.from('payload-mp4'));
    return;
  }
  response.writeHead(404, {'Content-Type': 'application/json'});
  response.end(JSON.stringify({error: 'not found'}));
});
await new Promise((resolve) => mock.listen(0, '127.0.0.1', resolve));
mockBase = `http://127.0.0.1:${mock.address().port}`;

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
let generatedUrl = '';
try {
  await api('/api/providers', {
    method: 'POST',
    body: JSON.stringify({
      provider: {
        id: providerId, name: '视频出参对齐本地供应商', protocol: 'openai',
        base_url: `${mockBase}/api/v1`, api_key: 'local-test', enabled: true,
      },
    }),
  });
  await api('/api/models', {
    method: 'POST',
    body: JSON.stringify({
      model: {
        id: modelId, providerId, capability: 'video', adapter: 'ark_video',
        remoteModel: 'local-alignment-video', label: '本地视频出参对齐', badge: 'TEST',
        enabled: true,
        defaultParams: {ratio: '16:9', resolution: '720P', duration: 12},
        params: {
          ratio: {options: ['16:9', '9:16']},
          resolution: {options: ['480P', '720P', '1080P']},
          duration: {min: 4, max: 30, step: 1},
        },
        limits: {maxRefs: 9, maxImages: 5, maxVideos: 3, maxAudios: 3, maxPromptChars: 8000},
      },
    }),
  });
  const project = await api('/api/projects', {
    method: 'POST',
    body: JSON.stringify({title: '视频出参对齐测试'}),
  });
  projectId = project.id;
  await api(`/api/projects/${projectId}/nodes`, {
    method: 'PUT',
    body: JSON.stringify({
      nodes: [
        {
          id: 'align-image', type: 'image', title: '角色｜画面甲', position: {x: 0, y: 0},
          metadata: {prompt: '角色图', status: 'done', content: imageData},
        },
        {
          id: 'align-audio', type: 'audio', title: '音频｜声音甲', position: {x: 0, y: 240},
          metadata: {prompt: '低沉男声', status: 'done', content: audioData},
        },
        {
          id: 'align-text', type: 'text', title: '设定｜全局', position: {x: 0, y: 480},
          metadata: {prompt: '全局设定：保持冷色电影光。', status: 'done', content: ''},
        },
        {
          id: 'align-video', type: 'video', title: '待验证视频', position: {x: 500, y: 0},
          metadata: {
            prompt: '镜头推进 @角色｜画面甲 @音频｜声音甲',
            status: 'idle',
            refs: [
              {nodeId: 'align-image', label: '过期旧标签', url: 'https://stale.invalid/old-image.png'},
              {nodeId: 'align-text', label: '过期设定', url: ''},
            ],
            params: {
              provider: providerId, model: modelId,
              ratio: '16:9', resolution: '720P', duration: 12,
              guidance: '高', generateAudio: false, watermark: true, seed: 12345,
              refs: [
                {nodeId: 'align-image', label: '过期旧标签', url: 'https://stale.invalid/old-image.png'},
                {nodeId: 'align-text', label: '过期设定', url: ''},
              ],
              voiceRefs: [
                {nodeId: 'align-audio', label: '过期音色', url: 'https://stale.invalid/old-voice.wav'},
              ],
              voiceRef: {nodeId: 'align-audio', label: '过期音色', url: 'https://stale.invalid/old-voice.wav'},
            },
          },
        },
      ],
    }),
  });

  await api(`/api/projects/${projectId}/nodes/align-video/generate`, {
    method: 'POST',
    body: '{}',
  });
  if (!captured) throw new Error('供应商没有收到视频请求');
  const content = Array.isArray(captured.content) ? captured.content : [];
  const text = String(content.find((item) => item.type === 'text')?.text || '');
  const image = content.find((item) => item.type === 'image_url')?.image_url?.url || '';
  const audio = content.find((item) => item.type === 'audio_url')?.audio_url?.url || '';

  if (!text.startsWith('镜头推进 @角色｜画面甲 @音频｜声音甲')) throw new Error(`提示词未原样投喂：${text}`);
  if (!text.includes('角色｜画面甲') || !text.includes('音频｜声音甲')) throw new Error('资产/音色名称未进入投喂提示词');
  if (!text.includes('全局设定：保持冷色电影光。')) throw new Error('文本引用内容未进入投喂提示词');
  if (image !== imageData) throw new Error('图片引用没有使用源节点当前内容');
  if (audio !== audioData) throw new Error('音色引用没有使用源节点当前内容');
  if (text.includes('stale.invalid')) throw new Error('陈旧 ref.url 被错误投喂');
  if (captured.model !== 'local-alignment-video') throw new Error(`模型错误：${captured.model}`);
  if (captured.ratio !== '16:9' || captured.resolution !== '720p' || captured.duration !== 12) {
    throw new Error(`比例/分辨率/秒数错误：${captured.ratio}/${captured.resolution}/${captured.duration}`);
  }
  if (captured.generate_audio !== false || captured.watermark !== true || captured.seed !== 12345) {
    throw new Error(`音频/水印/种子错误：${captured.generate_audio}/${captured.watermark}/${captured.seed}`);
  }
  const types = content.map((item) => item.type);
  if (types.indexOf('image_url') < 0 || types.indexOf('audio_url') < 0) throw new Error(`参考素材类型缺失：${types}`);

  const saved = await api(`/api/projects/${projectId}`);
  const node = saved.payload.nodes.find((item) => item.id === 'align-video');
  if (node.metadata.status !== 'done' || !node.metadata.content) throw new Error('视频生成状态未落盘');
  generatedUrl = String(node.metadata.content);
  console.log(JSON.stringify({
    ok: true,
    projectId,
    model: captured.model,
    ratio: captured.ratio,
    resolution: captured.resolution,
    duration: captured.duration,
    generateAudio: captured.generate_audio,
    watermark: captured.watermark,
    seed: captured.seed,
    contentTypes: types,
    prompt: text,
    imageUsesCurrentSource: image === imageData,
    audioUsesCurrentSource: audio === audioData,
    status: node.metadata.status,
  }, null, 2));
} finally {
  if (projectId) await api(`/api/projects/${projectId}`, {method: 'DELETE'}).catch(() => {});
  await api(`/api/providers/${encodeURIComponent(providerId)}`, {method: 'DELETE'}).catch(() => {});
  if (generatedUrl.startsWith('/uploads/')) {
    await fs.unlink(path.join(root, 'data', 'uploads', path.basename(generatedUrl))).catch(() => {});
  }
  await new Promise((resolve) => mock.close(resolve));
}
