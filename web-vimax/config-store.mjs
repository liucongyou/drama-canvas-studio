import {readFile, rename, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {parse, stringify} from 'yaml';

const SECTION_FIELDS = {
  llm: ['model_provider', 'model', 'base_url', 'max_tokens', 'temperature', 'reasoning_effort'],
  image: ['model', 'base_url'],
  video: ['model', 'base_url', 'aspect_ratio', 'resolution', 'fps', 'duration', 'subtitles'],
  embedding: ['model_provider', 'model', 'base_url'],
  reranker: ['model', 'base_url'],
  bailian: ['model', 'base_url', 'workspace_id'],
  tos: ['bucket', 'region', 'prefix'],
  storyboard: ['shots_per_batch', 'batch_concurrency'],
};

// 思考档白名单：'default' 表示清空该配置、走模型默认（与 Python 端 llm_reasoning_effort() 同步）
const REASONING_EFFORT_CHOICES = ['default', 'none', 'minimal', 'low', 'medium', 'high', 'max'];
// 中文档位名，仅用于界面与报错文案；存盘的仍是英文原值
const REASONING_EFFORT_LABELS = {
  default: '模型默认',
  none: '关闭思考',
  minimal: '极低思考',
  low: '低思考',
  medium: '中等思考',
  high: '高思考',
  max: '最高思考',
};
// storyboard 段的数值型字段：[最小值, 最大值, 中文名]
const STORYBOARD_NUMERIC_FIELDS = {
  shots_per_batch: [2, 200, '单批镜头数'],
  batch_concurrency: [1, 8, '并发路数'],
};

export async function readAgentConfig(repoRoot) {
  const {payload} = await loadConfig(repoRoot);
  return publicConfig(payload);
}

export async function saveAgentConfig(repoRoot, input) {
  if (!input || typeof input !== 'object' || !input.sections || typeof input.sections !== 'object') {
    throw new Error('Configuration sections are required');
  }
  const {configPath, payload} = await loadConfig(repoRoot);
  for (const [section, fields] of Object.entries(SECTION_FIELDS)) {
    const update = input.sections[section];
    if (!update || typeof update !== 'object') continue;
    const current = payload[section] && typeof payload[section] === 'object' ? payload[section] : {};
    for (const field of fields) {
      if (!(field in update)) continue;
      const raw = typeof update[field] === 'string' ? update[field].trim()
        : (typeof update[field] === 'number' && Number.isFinite(update[field]) ? String(update[field]) : '');
      if (raw === '') continue; // 留空表示保留原配置，避免误覆盖
      if (section === 'video' && (field === 'fps' || field === 'duration') && /^\d+$/.test(raw)) {
        current[field] = Number(raw);
        continue;
      }
      if (section === 'video' && field === 'subtitles') {
        // 开关：'1'/'true'/'on' → true，'0'/'false'/'off' → false；留空保留原值
        const normalized = String(update[field]).trim().toLowerCase();
        if (normalized === '') continue;
        current[field] = ['1', 'true', 'yes', 'on'].includes(normalized);
        continue;
      }
      if (section === 'llm' && field === 'max_tokens') {
        // 单次输出上限：实测 deepseek-flash 合法区间 [1, 393216]，这里收紧到 [256, 393216]
        if (!/^\d+$/.test(raw)) throw new Error('llm.max_tokens 必须是正整数');
        const tokens = Number(raw);
        if (tokens < 256 || tokens > 393_216) throw new Error('llm.max_tokens 需在 256 ~ 393216 之间');
        current[field] = tokens;
        continue;
      }
      if (section === 'llm' && field === 'temperature') {
        if (!/^-?\d+(?:\.\d+)?$/.test(raw)) throw new Error('llm.temperature 必须是数字');
        const temperature = Number(raw);
        if (!(temperature >= 0 && temperature <= 2)) throw new Error('llm.temperature 需在 0 ~ 2 之间');
        current[field] = temperature;
        continue;
      }
      if (section === 'llm' && field === 'reasoning_effort') {
        const normalized = raw.toLowerCase();
        if (!REASONING_EFFORT_CHOICES.includes(normalized)) {
          const labels = REASONING_EFFORT_CHOICES.map((choice) => `${REASONING_EFFORT_LABELS[choice]}（${choice}）`);
          throw new Error(`llm.reasoning_effort 只能是 ${labels.join(' / ')}`);
        }
        current[field] = normalized === 'default' ? '' : normalized;
        continue;
      }
      if (section === 'storyboard') {
        const spec = STORYBOARD_NUMERIC_FIELDS[field];
        if (!spec) continue;
        if (!/^\d+$/.test(raw)) throw new Error(`storyboard.${field} 必须是正整数`);
        const numeric = Number(raw);
        if (numeric < spec[0] || numeric > spec[1]) {
          throw new Error(`storyboard.${field}（${spec[2]}）需在 ${spec[0]} ~ ${spec[1]} 之间`);
        }
        current[field] = numeric;
        continue;
      }
      current[field] = validatedValue(update[field], `${section}.${field}`, 2_048);
    }
    if (typeof update.api_key === 'string' && update.api_key.trim()) {
      current.api_key = validatedValue(update.api_key, `${section}.api_key`, 8_192);
    }
    payload[section] = current;
  }
  const temporaryPath = `${configPath}.${process.pid}.tmp`;
  await writeFile(temporaryPath, stringify(payload, {lineWidth: 0}), {mode: 0o600});
  await rename(temporaryPath, configPath);
  return publicConfig(payload);
}

async function loadConfig(repoRoot) {
  const configPath = path.join(repoRoot, 'configs', 'agent.local.yaml');
  let text = '';
  try {
    text = await readFile(configPath, 'utf8');
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
  }
  const payload = text ? parse(text) : {};
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new Error('configs/agent.local.yaml must be a YAML mapping');
  }
  return {configPath, payload};
}

function publicConfig(payload) {
  const sections = {};
  for (const [section, fields] of Object.entries(SECTION_FIELDS)) {
    const source = payload[section] && typeof payload[section] === 'object' ? payload[section] : {};
    const result = {};
    for (const field of fields) {
      const value = source[field];
      if (typeof value === 'boolean') {
        result[field] = value ? '1' : '0';
      } else {
        result[field] = (typeof value === 'string' || typeof value === 'number') ? String(value) : '';
      }
    }
    result.api_key = '';
    result.has_api_key = Boolean(typeof source.api_key === 'string' && source.api_key.trim());
    result.api_key_hint = result.has_api_key ? hintOf(String(source.api_key).trim()) : '';
    sections[section] = result;
  }
  return {sections};
}

function validatedValue(value, label, maxLength) {
  if (typeof value !== 'string') throw new Error(`${label} must be a string`);
  const normalized = value.trim();
  if (normalized.length > maxLength) throw new Error(`${label} is too long`);
  if (label.endsWith('.base_url') && normalized && !/^https?:\/\//i.test(normalized)) {
    throw new Error(`${label} must use http:// or https://`);
  }
  return normalized;
}

function hintOf(key) {
  // 密钥指纹只用于确认当前生效的 key，任何长度都不能返回完整密钥。
  if (key.length <= 8) return '••••••';
  if (key.length <= 12) return `${key.slice(0, 2)}…${key.slice(-2)}`;
  return `${key.slice(0, 6)}…${key.slice(-4)}`;
}

const HTTP_STATUS_TEXT = {
  400: '请求参数错误',
  401: 'API 密钥无效或已过期',
  403: '无权限访问',
  404: '接口地址/路径不存在',
  405: '接口不支持该请求方式',
  408: '请求超时',
  429: '请求频率受限',
  500: '服务端内部错误',
  502: '网关错误',
  503: '服务暂不可用',
  504: '网关超时',
};

async function fetchWithTimeout(url, options, timeoutMs = 12000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, {...options, signal: controller.signal});
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 测试某一配置段的连通性（只读探测，不产生计费/创建动作）。
 * 统一用 OpenAI 兼容的 GET {base_url}/models 探测密钥与端点；
 * 方舟（/api/v3）、DeepSeek、百炼（/api/v1）均支持该端点。
 * TOS 需要签名请求，不做自动探测。
 */
export async function testSectionConfig(repoRoot, section) {
  const {payload} = await loadConfig(repoRoot);
  const cfg = payload[section] && typeof payload[section] === 'object' ? payload[section] : null;
  if (!cfg) return {ok: false, message: '该配置段尚未配置，请先填写并保存'};
  const baseUrl = String(cfg.base_url || '').trim().replace(/\/+$/, '');
  const apiKey = String(cfg.api_key || '').trim();
  const model = String(cfg.model || '').trim();
  if (!baseUrl) return {ok: false, message: '缺少接口地址（base_url），请先填写'};
  if (!apiKey) return {ok: false, message: '缺少 API 密钥，请先填写并保存'};
  if (section === 'tos') {
    return {ok: false, message: '对象存储需要签名请求，无法一键探测；请通过“渲染后转存”实际验证'};
  }
  const headers = {Authorization: `Bearer ${apiKey}`};
  try {
    const response = await fetchWithTimeout(`${baseUrl}/models`, {method: 'GET', headers});
    const text = await response.text();
    let detail = '';
    try {
      const json = JSON.parse(text);
      detail = json?.error?.message || json?.message || json?.code || '';
    } catch { /* 非 JSON 响应体，忽略 */ }
    if (response.status === 200) {
      let modelInfo = model ? `，模型 ${model}` : '';
      try {
        const json = JSON.parse(text);
        if (Array.isArray(json?.data)) modelInfo += `（服务可用 ${json.data.length} 个模型）`;
      } catch { /* ignore */ }
      return {ok: true, message: `连接成功${modelInfo}`};
    }
    const reason = HTTP_STATUS_TEXT[response.status] || `HTTP ${response.status}`;
    return {ok: false, message: `连接失败：${reason}${detail ? `（${detail}）` : ''}`};
  } catch (error) {
    if (error?.name === 'AbortError') {
      return {ok: false, message: '连接超时（12 秒无响应），请检查接口地址、网络或防火墙'};
    }
    if (error?.cause?.code === 'ENOTFOUND') return {ok: false, message: '域名无法解析，请检查接口地址是否正确'};
    if (error?.cause?.code === 'ECONNREFUSED') return {ok: false, message: '连接被拒绝，请检查地址与端口'};
    if (error?.cause?.code === 'CERT_HAS_EXPIRED') return {ok: false, message: '证书已过期，请检查服务端证书'};
    return {ok: false, message: `请求失败：${error?.message || String(error)}`};
  }
}
