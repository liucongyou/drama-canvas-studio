const base = 'http://127.0.0.1:8890';

async function api(path, options = {}) {
  const response = await fetch(base + path, {
    ...options,
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${response.status} ${payload.error || JSON.stringify(payload)}`);
  return payload;
}

const suffix = Date.now().toString(36);
const providerId = `smoke-${suffix}`;
const modelId = `smoke-text-${suffix}`;

try {
  await api('/api/providers', {
    method: 'POST',
    body: JSON.stringify({
      provider: {
        id: providerId,
        name: '冒烟测试供应商',
        protocol: 'openai',
        base_url: 'https://example.test/v1',
        api_key: 'test-secret-value',
        enabled: true,
      },
    }),
  });
  const afterProvider = await api('/api/model-catalog');
  const provider = afterProvider.providers.find((item) => item.id === providerId);
  if (!provider || !provider.has_api_key || provider.api_key_hint?.includes('test-secret-value')) {
    throw new Error('供应商保存或密钥脱敏异常');
  }

  await api('/api/models', {
    method: 'POST',
    body: JSON.stringify({
      model: {
        id: modelId,
        providerId,
        capability: 'text',
        adapter: 'openai_chat',
        remoteModel: 'custom-chat-model',
        label: '自定义文本模型',
        description: '供未来供应商复用 OpenAI Chat 协议',
        badge: 'Custom',
        enabled: true,
        defaultParams: {maxTokens: 2048, temperature: 0.5},
        params: {
          maxTokens: {type: 'range', min: 128, max: 8192, step: 128},
          temperature: {type: 'range', min: 0, max: 2, step: 0.1},
        },
        limits: {maxRefs: 8, maxTextRefs: 8},
      },
    }),
  });
  const afterModel = await api('/api/model-catalog');
  const model = afterModel.models.find((item) => item.id === modelId);
  if (!model || model.providerId !== providerId || model.adapter !== 'openai_chat') {
    throw new Error('自定义模型保存异常');
  }

  console.log(JSON.stringify({ok: true, providerId, modelId, keyRedacted: true}, null, 2));
} finally {
  await api(`/api/providers/${encodeURIComponent(providerId)}`, {method: 'DELETE'}).catch(() => {});
}
