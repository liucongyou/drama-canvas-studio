import {useEffect, useLayoutEffect, useRef, useState} from 'react';
import {dialog} from './GDialog';
import {filterDiscoveredModels} from './modelDiscoveryFilter';
import {Box, Braces, Check, ChevronDown, Plus, RefreshCw, Save, Search, Trash2, Upload, X} from 'lucide-react';
import {
  deleteModelConfig,
  deleteProviderConfig,
  discoverProviderModels,
  getModelCatalog,
  mediaSourceUrl,
  saveModelConfig,
  saveProviderConfig,
  testProviderConfig,
  uploadProviderIcon,
  type DiscoveredModelSpec,
  type ModelCatalog,
  type ModelParamSpec,
  type ModelPricing,
  type ModelSpec,
  type ProviderSpec,
} from './api';

type ProviderDraft = {
  id: string;
  name: string;
  protocol: string;
  base_url: string;
  workspace_id: string;
  icon: string;
  api_key: string;
  enabled: boolean;
};

type ModelDraft = {
  id: string;
  providerId: string;
  capability: string;
  adapter: string;
  remoteModel: string;
  label: string;
  description: string;
  badge: string;
  enabled: boolean;
  defaultParams: string;
  params: string;
  limits: string;
  pricing: string;
};

const EMPTY_PROVIDER: ProviderDraft = {
  id: '',
  name: '',
  protocol: 'openai',
  base_url: '',
  workspace_id: '',
  icon: '',
  api_key: '',
  enabled: true,
};

const EMPTY_MODEL: ModelDraft = {
  id: '',
  providerId: '',
  capability: 'image',
  adapter: 'openai_image',
  remoteModel: '',
  label: '',
  description: '',
  badge: 'M',
  enabled: true,
  defaultParams: '{}',
  params: '{}',
  limits: '{}',
  pricing: '{}',
};

const PROVIDER_ICON_MAX_BYTES = 5 * 1024 * 1024;
const PROVIDER_ICON_TYPES = new Set([
  'image/png',
  'image/jpeg',
  'image/webp',
  'image/gif',
]);
const PROVIDER_ICON_EXTENSIONS = /\.(?:png|jpe?g|webp|gif)$/i;

export function providerIconFileError(file: {name: string; type: string; size: number}): string {
  if (!PROVIDER_ICON_TYPES.has(String(file.type || '').toLowerCase())) {
    return '只支持 PNG、JPG、WebP 或 GIF 图片';
  }
  if (!PROVIDER_ICON_EXTENSIONS.test(String(file.name || ''))) {
    return '文件扩展名必须是 PNG、JPG、WebP 或 GIF';
  }
  if (file.size <= 0) return '图标文件为空';
  if (file.size > PROVIDER_ICON_MAX_BYTES) return '图标不能超过 5MB';
  return '';
}

const CAPABILITIES = [
  ['image', '图片'],
  ['video', '视频'],
  ['audio', '音色 / TTS'],
  ['text', '文本'],
] as const;

const ADAPTERS = [
  ['ark_image', '火山方舟图片 / Ark 兼容图片'],
  ['ark_video', '火山方舟视频 / Ark 兼容异步视频'],
  ['newapi_image', 'OpenAI 兼容图片 / 中转图片'],
  ['canvaspro_video', 'CanvasPro 视频（专用合同）'],
  ['openai_video', 'OpenAI 兼容视频 / /v1/videos'],
  ['agnes_image', 'Agnes Images'],
  ['agnes_video', 'Agnes Videos'],
  ['openai_chat', 'OpenAI 兼容 Chat Completions'],
  ['openai_image', 'OpenAI 兼容 Images'],
  ['dashscope_tts', '阿里云百炼 TTS'],
  ['fourstoken_image', '4SToken 图片（专用合同）'],
  ['fourstoken_video', '4SToken 视频（专用合同）'],
  ['fourstoken_newapi_video', '4SToken New API 视频（专用合同）'],
] as const;

const ADAPTER_CAPABILITIES: Record<string, string> = {
  ark_image: 'image',
  ark_video: 'video',
  newapi_image: 'image',
  canvaspro_video: 'video',
  openai_video: 'video',
  agnes_image: 'image',
  agnes_video: 'video',
  fourstoken_image: 'image',
  fourstoken_video: 'video',
  fourstoken_newapi_video: 'video',
  openai_chat: 'text',
  openai_image: 'image',
  dashscope_tts: 'audio',
};

export function adaptersForCapability(capability: string): {value: string; label: string}[] {
  return ADAPTERS
    .filter(([value]) => ADAPTER_CAPABILITIES[value] === capability)
    .map(([value, label]) => ({value, label}));
}

export function defaultAdapterForCapability(capability: string): string {
  return adaptersForCapability(capability)[0]?.value || 'openai_image';
}

export function defaultAdapterForProvider(
  capability: string,
  providerId: string,
): string {
  const normalized = providerId.toLowerCase();
  if (normalized === 'ark') {
    return capability === 'image' ? 'ark_image' : capability === 'video' ? 'ark_video' : defaultAdapterForCapability(capability);
  }
  if (normalized === 'agnes-ai') {
    return capability === 'image' ? 'agnes_image' : capability === 'video' ? 'agnes_video' : defaultAdapterForCapability(capability);
  }
  if (normalized === 'aliyun_bailian') {
    return capability === 'audio' ? 'dashscope_tts' : defaultAdapterForCapability(capability);
  }
  if (normalized === 'teamorouter') {
    return capability === 'text' ? 'openai_chat' : defaultAdapterForCapability(capability);
  }
  if (normalized === '4stoken' || normalized === '4stoken.cn') {
    return capability === 'image' ? 'fourstoken_image' : capability === 'video' ? 'fourstoken_video' : defaultAdapterForCapability(capability);
  }
  if (capability === 'video' && (normalized === 'new-apl' || normalized === 'new-api')) {
    return 'canvaspro_video';
  }
  return capability === 'image' ? 'newapi_image' : capability === 'video' ? 'openai_video' : defaultAdapterForCapability(capability);
}

function providerDraft(provider?: ProviderSpec): ProviderDraft {
  return provider ? {
    id: provider.id,
    name: provider.name,
    protocol: provider.protocol,
    base_url: provider.base_url,
    workspace_id: provider.workspace_id || '',
    icon: provider.icon || '',
    api_key: '',
    enabled: provider.enabled,
  } : {...EMPTY_PROVIDER};
}

export function modelDraft(model?: ModelSpec): ModelDraft {
  return model ? {
    id: model.id,
    providerId: model.providerId,
    capability: model.capability,
    adapter: model.adapter,
    remoteModel: model.remoteModel,
    label: model.label,
    description: model.description,
    badge: model.badge,
    enabled: model.enabled,
    defaultParams: JSON.stringify(model.defaultParams || {}, null, 2),
    params: JSON.stringify(model.params || {}, null, 2),
    limits: JSON.stringify(model.limits || {}, null, 2),
    pricing: JSON.stringify(model.pricing || {}, null, 2),
  } : {...EMPTY_MODEL};
}

function parseJson(text: string, label: string) {
  try {
    const value = JSON.parse(text || '{}');
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error();
    return value as Record<string, unknown>;
  } catch {
    throw new Error(`${label} 必须是合法 JSON 对象`);
  }
}

const MODEL_LIMIT_FIELDS = [
  ['maxRefs', '总参考素材（自动）', '图片+视频+音频+文本自动合计'],
  ['maxImages', '参考图片', '图片/角色/场景/道具参考上限'],
  ['maxVideos', '参考视频', '视频参考上限，0 表示不支持'],
  ['maxAudios', '参考音频 / 音色', '视频音色、参考音频上限'],
  ['maxTextRefs', '文本引用', '文本、脚本、提示补充参考上限'],
  ['maxPromptChars', '提示词字符', '提示词最大字符数'],
  ['maxConcurrency', '模型并发', '同时提交给该模型的任务数；留空按图片16 / 视频10 / 音频8 / 文本8'],
] as const;

type ModelLimitKey = typeof MODEL_LIMIT_FIELDS[number][0];
const REFERENCE_COUNT_LIMIT_KEYS = [
  'maxImages',
  'maxVideos',
  'maxAudios',
  'maxTextRefs',
] as const;

function parsedLimitObject(text: string): Record<string, unknown> {
  try {
    const value = JSON.parse(text || '{}');
    return value && typeof value === 'object' && !Array.isArray(value)
      ? value as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
}

function limitInputValue(text: string, key: ModelLimitKey): string {
  if (key === 'maxRefs') {
    const total = referenceLimitTotal(text);
    return total === null ? '' : String(total);
  }
  const value = Number(parsedLimitObject(text)[key]);
  return Number.isFinite(value) && value >= 0 ? String(Math.floor(value)) : '';
}

function referenceLimitTotal(text: string): number | null {
  const limits = parsedLimitObject(text);
  const hasCategoryLimit = REFERENCE_COUNT_LIMIT_KEYS.some((key) => key in limits);
  if (!hasCategoryLimit) return null;
  return REFERENCE_COUNT_LIMIT_KEYS.reduce((total, key) => {
    const value = Number(limits[key]);
    return total + (Number.isFinite(value) && value > 0 ? Math.floor(value) : 0);
  }, 0);
}

function withComputedReferenceLimit(limits: Record<string, unknown>): Record<string, unknown> {
  const next = {...limits};
  const hasCategoryLimit = REFERENCE_COUNT_LIMIT_KEYS.some((key) => key in next);
  if (!hasCategoryLimit) {
    delete next.maxRefs;
    return next;
  }
  const total = REFERENCE_COUNT_LIMIT_KEYS.reduce((sum, key) => {
    const value = Number(next[key]);
    return sum + (Number.isFinite(value) && value > 0 ? Math.floor(value) : 0);
  }, 0);
  next.maxRefs = total;
  return next;
}

function updateLimitJson(text: string, key: ModelLimitKey, raw: string): string {
  const limits = parsedLimitObject(text);
  if (!raw.trim()) {
    delete limits[key];
  } else {
    const value = Number(raw);
    if (Number.isFinite(value)) limits[key] = Math.max(0, Math.floor(value));
  }
  return JSON.stringify(withComputedReferenceLimit(limits), null, 2);
}

type ParameterDraftRow = {
  key: string;
  type: 'select' | 'range' | 'text' | 'boolean';
  defaultValue: string;
  optionsText: string;
  min: string;
  max: string;
  step: string;
  multiple: boolean;
  api: string;
  raw?: unknown;
};

const PARAM_TYPES: {value: ParameterDraftRow['type']; label: string}[] = [
  {value: 'select', label: '单选'},
  {value: 'range', label: '数字范围'},
  {value: 'text', label: '文本'},
  {value: 'boolean', label: '开关'},
];

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

/**
 * 容错取对象。必须同时接受对象与 JSON 字符串：
 * 模型目录里的 `defaultParams` / `params` / `pricing` 本来就是对象，
 * 编辑框里手填的才是字符串。只按 string 处理会把对象 String 化成
 * "[object Object]" 后抛错并被 catch 吞成 {}，导致参数与计费面板全空。
 */
function parseJsonObjectOrEmpty(text: unknown): Record<string, unknown> {
  if (isPlainRecord(text)) return text;
  if (typeof text !== 'string') return {};
  try {
    const value = JSON.parse(text || '{}');
    return isPlainRecord(value) ? value : {};
  } catch {
    return {};
  }
}

function formatDefaultValue(value: unknown): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return value === undefined ? '' : JSON.stringify(value);
}

function coerceParameterValue(value: string, type: ParameterDraftRow['type']): unknown {
  const trimmed = value.trim();
  if (type === 'boolean') return trimmed === 'true';
  if (type === 'range' && trimmed !== '' && Number.isFinite(Number(trimmed))) return Number(trimmed);
  return trimmed;
}

function parameterRowsFromModel(model: ModelDraft): ParameterDraftRow[] {
  const defaults = parseJsonObjectOrEmpty(model.defaultParams);
  const schemas = parseJsonObjectOrEmpty(model.params);
  const keys = [...new Set([...Object.keys(defaults), ...Object.keys(schemas)])];
  return keys.map((key) => {
    const raw = schemas[key];
    const spec: ModelParamSpec = isPlainRecord(raw)
      ? raw as ModelParamSpec
      : {type: typeof raw === 'string' ? raw : 'text'};
    const type = spec.type === 'select' || spec.type === 'range' || spec.type === 'boolean'
      ? spec.type
      : 'text';
    return {
      key,
      type,
      defaultValue: formatDefaultValue(defaults[key] ?? spec.default),
      optionsText: (spec.options || []).map(String).join(', '),
      min: spec.min === undefined ? '' : String(spec.min),
      max: spec.max === undefined ? '' : String(spec.max),
      step: spec.step === undefined ? '' : String(spec.step),
      multiple: spec.multiple === true,
      api: spec.api || '',
      raw,
    };
  });
}

export function parameterRowsFromDraft(model: ModelDraft): ParameterDraftRow[] {
  return parameterRowsFromModel(model);
}

export function rowsToParamsJson(rows: ParameterDraftRow[]): {defaultParams: string; params: string} {
  const defaults: Record<string, unknown> = {};
  const schemas: Record<string, unknown> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (!key) continue;
    const hasDefault = row.defaultValue !== '';
    if (hasDefault) defaults[key] = coerceParameterValue(row.defaultValue, row.type);
    const spec: Record<string, unknown> = {
      ...(isPlainRecord(row.raw) ? row.raw : {}),
      type: row.type,
    };
    if (row.type === 'select') {
      spec.options = row.optionsText.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean);
    } else {
      delete spec.options;
    }
    if (row.type === 'range') {
      if (row.min !== '') spec.min = Number(row.min);
      if (row.max !== '') spec.max = Number(row.max);
      if (row.step !== '') spec.step = Number(row.step);
    } else {
      delete spec.min;
      delete spec.max;
      delete spec.step;
    }
    if (row.multiple) spec.multiple = true; else delete spec.multiple;
    if (row.api.trim()) spec.api = row.api.trim(); else delete spec.api;
    schemas[key] = spec;
  }
  return {
    defaultParams: JSON.stringify(defaults, null, 2),
    params: JSON.stringify(schemas, null, 2),
  };
}

type PricingTierRow = {id: string; label: string; rate: string; rateWithVideo: string};

/**
 * 计费规则归一化入口。
 *
 * 这里必须接受「对象或字符串」两种输入：模型目录返回的 `pricing` 是对象，
 * 而编辑框里手工输入的是 JSON 字符串。此前签名只写了 `string`，于是
 * `pricingRowsFromJson(model.pricing)` 把对象喂给 `JSON.parse`，String 化后变成
 * "[object Object]" 抛错，又被 catch 吞成 {}——结果是「计费规则」面板打开永远空白，
 * 已保存的单价看不到，随手保存一次就把别人的定价清空。
 */
function pricingRowsFromJson(input: unknown): {
  currency: string;
  unit: string;
  fixed: string;
  tiers: PricingTierRow[];
  note: string;
  noteWithVideo: string;
} {
  const root = isPlainRecord(input) ? input : parseJsonObjectOrEmpty(input);
  const capability = root.image || root.video || root.audio || root.text;
  const rule = isPlainRecord(capability) ? capability : {};
  const rates = isPlainRecord(rule.rates) ? rule.rates : {};
  const ratesWithVideo = isPlainRecord(rule.ratesWithVideo) ? rule.ratesWithVideo : {};
  const labels = [...new Set([...Object.keys(rates), ...Object.keys(ratesWithVideo)])];
  return {
    currency: String(root.currency || 'CNY'),
    unit: String(rule.unit || 'second'),
    fixed: rule.fixed === undefined ? '' : String(rule.fixed),
    tiers: labels.length
      ? labels.map((label, index) => ({
          id: `${index}-${label}`,
          label,
          rate: String(rates[label] ?? ''),
          rateWithVideo: String(ratesWithVideo[label] ?? ''),
        }))
      : [{id: '0-default', label: '默认', rate: '', rateWithVideo: ''}],
    note: String(rule.note || ''),
    noteWithVideo: String(rule.noteWithVideo || ''),
  };
}

export function pricingRowsFromDraft(pricing: unknown): ReturnType<typeof pricingRowsFromJson> {
  return pricingRowsFromJson(JSON.stringify(pricing || {}));
}

export function pricingJsonFromRows(
  capability: string,
  rows: {
    currency: string;
    unit: string;
    fixed: string;
    tiers: PricingTierRow[];
    note: string;
    noteWithVideo: string;
  },
): string {
  const rule: Record<string, unknown> = {unit: rows.unit};
  if (rows.fixed !== '' && Number.isFinite(Number(rows.fixed))) rule.fixed = Number(rows.fixed);
  const rates: Record<string, number> = {};
  const ratesWithVideo: Record<string, number> = {};
  for (const tier of rows.tiers) {
    const label = tier.label.trim();
    if (!label) continue;
    if (tier.rate !== '' && Number.isFinite(Number(tier.rate))) rates[label] = Number(tier.rate);
    if (tier.rateWithVideo !== '' && Number.isFinite(Number(tier.rateWithVideo))) {
      ratesWithVideo[label] = Number(tier.rateWithVideo);
    }
  }
  if (Object.keys(rates).length > 0) rule.rates = rates;
  if (Object.keys(ratesWithVideo).length > 0) rule.ratesWithVideo = ratesWithVideo;
  if (rows.note.trim()) rule.note = rows.note.trim();
  if (rows.noteWithVideo.trim()) rule.noteWithVideo = rows.noteWithVideo.trim();
  const normalizedCapability = ['image', 'video', 'audio', 'text'].includes(capability)
    ? capability
    : 'image';
  const pricing: ModelPricing = {
    currency: rows.currency.trim() || 'CNY',
    [normalizedCapability]: rule,
  };
  return JSON.stringify(pricing, null, 2);
}

/**
 * 自绘下拉（替换原生 `<select>`）。
 *
 * ★ 为什么非换不可：用户截图圈出的那片**天蓝色高亮**来自 Windows 原生的
 *   列表弹层（`<option>` 的 `background` 在 Chrome/Windows 上根本不生效，
 *   系统用自带的 accent 色画选中条）。`appearance:none` 只能改合上时的外观，
 *   展开层完全在系统控件里，CSS 无法触及 —— 唯一解法是自绘。
 *
 * ★ "该向上还是向下弹"必须按**最近的滚动容器**算，不能只看 window：
 *   表单在 `.model-settings-body{overflow:auto}` 里，实测 1200px 高的视口下
 *   window 还有 553px、但 body 底部只剩 237px —— 用 window 判断会得出
 *   "装得下"的结论，结果列表被 body 裁掉、第三项根本点不到（真实踩过）。
 *   ⇒ 向上找最近的 overflow ≠ visible 的祖先，用它的 rect 当边界。
 */
function scrollBoundary(el: HTMLElement): {top: number; bottom: number} {
  let node: HTMLElement | null = el.parentElement;
  let top = 0;
  let bottom = window.innerHeight;
  // ★ 一路向上取**所有**滚动祖先的交集，不只第一个：
  //   弹窗本身 overflow:hidden（也会裁），外层 body 又 overflow:auto，
  //   只取最近一个会漏算另一层，仍然可能被切。
  while (node) {
    const cs = window.getComputedStyle(node);
    const oy = cs.overflowY;
    if (oy !== 'visible' && oy !== '') {
      const r = node.getBoundingClientRect();
      top = Math.max(top, r.top);
      bottom = Math.min(bottom, r.bottom);
    }
    node = node.parentElement;
  }
  return {top, bottom};
}

function ModelSelect({value, options, onChange, open, onToggle, ariaLabel}: {
  value: string;
  options: {value: string; label: string}[];
  onChange: (v: string) => void;
  open: boolean;
  onToggle: () => void;
  ariaLabel: string;
}) {
  const current = options.find((o) => o.value === value);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [dropUp, setDropUp] = useState(false);

  // 每次展开前量一次"滚动容器内"的可用空间。
  // ★ 必须用 useLayoutEffect（不是 useEffect）：后者在**浏览器绘制之后**才跑，
  //   首批渲染一定是 dropUp=false 的"向下"态。极端情况下（640px 高窗口 + 表单滚到底）
  //   这一帧会把列表甩到 popTop=-76 —— 完全在视口外，随后才被纠正，肉眼看就是闪一下。
  //   useLayoutEffect 在绘制前同步测量并改 state，用户看不到错误那一帧。
  // ★ 阈值用**真实列表高度**（items × 29 + 内边距 10 + 间距 4），不用写死 232：
  //   只有 3 项时其实只需 ~101px，写死 232 会在"其实装得下"时白白翻到上方。
  useLayoutEffect(() => {
    if (!open || !wrapRef.current) return;
    const rect = wrapRef.current.getBoundingClientRect();
    const bound = scrollBoundary(wrapRef.current);
    const need = Math.min(options.length * 29 + 10 + 4, 216 + 4);
    const below = bound.bottom - rect.bottom;
    const above = rect.top - bound.top;
    // 下方装不下、且上方更宽敞 → 向上弹
    setDropUp(below < need && above > below);
  }, [open, options.length]);

  return (
    <div className="model-select-wrap" ref={wrapRef}>
      <button
        type="button"
        className={`model-select ${open ? 'is-open' : ''}`}
        onClick={onToggle}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
      >
        <span>{current ? current.label : value || '—'}</span>
        <ChevronDown size={13} strokeWidth={2.2} aria-hidden="true" />
      </button>
      {open && (
        <div className={`model-select-pop ${dropUp ? 'is-up' : ''}`} role="listbox">
          {options.map((o) => (
            <button
              key={o.value}
              type="button"
              role="option"
              aria-selected={o.value === value}
              className={`model-select-item ${o.value === value ? 'is-active' : ''}`}
              onClick={() => onChange(o.value)}
            >
              <span>{o.label}</span>
              {o.value === value && <Check size={12} strokeWidth={2.4} aria-hidden="true" />}
            </button>
          ))}
          {!options.length && <p className="model-select-empty">暂无选项</p>}
        </div>
      )}
    </div>
  );
}

function initialProviderDraft(catalog: ModelCatalog): ProviderDraft {
  return catalog.providers.length
    ? providerDraft(catalog.providers[0])
    : {...EMPTY_PROVIDER};
}

function initialModelDraft(catalog: ModelCatalog): ModelDraft {
  return catalog.models.length
    ? modelDraft(catalog.models[0])
    : {
        ...EMPTY_MODEL,
        providerId: catalog.providers[0]?.id || '',
      };
}

export function ModelSettingsDialog({
  onClose,
  initialCatalog,
}: {
  onClose: () => void;
  initialCatalog: ModelCatalog;
}) {
  const [catalog, setCatalog] = useState<ModelCatalog>(initialCatalog);
  const [provider, setProvider] = useState<ProviderDraft>(() => initialProviderDraft(initialCatalog));
  const [model, setModel] = useState<ModelDraft>(() => initialModelDraft(initialCatalog));
  const [providerBusy, setProviderBusy] = useState(false);
  const [providerIconBusy, setProviderIconBusy] = useState(false);
  const [modelBusy, setModelBusy] = useState(false);
  const [status, setStatus] = useState('');
  const [testState, setTestState] = useState<{ok: boolean; message: string} | null>(null);
  const [discoveredModels, setDiscoveredModels] = useState<DiscoveredModelSpec[] | null>(null);
  const [discoveredSelected, setDiscoveredSelected] = useState<Record<string, boolean>>({});
  const [discoveredCapabilities, setDiscoveredCapabilities] = useState<Record<string, string>>({});
  const [discovering, setDiscovering] = useState(false);
  const [discoverQuery, setDiscoverQuery] = useState('');
  const [discoverCapability, setDiscoverCapability] = useState('');
  const [discoverMessage, setDiscoverMessage] = useState('');
  // 4 个自绘下拉共用一份"当前打开的是哪个"，保证同一时刻只开一个
  const [openSelect, setOpenSelect] = useState<'' | 'protocol' | 'providerId' | 'capability' | 'adapter'>('');
  const [detailKind, setDetailKind] = useState<'provider' | 'model'>(
    initialCatalog.models.length ? 'model' : 'provider',
  );
  const [advancedOpen, setAdvancedOpen] = useState({
    defaultParams: false,
    params: false,
    pricing: false,
  });
  const initialModel = initialModelDraft(initialCatalog);
  const [parameterRows, setParameterRows] = useState<ParameterDraftRow[]>(
    () => parameterRowsFromModel(initialModel),
  );
  const [pricingRows, setPricingRows] = useState(
    () => pricingRowsFromJson(initialCatalog.models[0]?.pricing),
  );
  const providerIconInputRef = useRef<HTMLInputElement | null>(null);

  const providerModels = (catalog?.models || []).filter(
    (item) => Boolean(provider.id) && item.providerId === provider.id,
  );
  const discoverQueryText = discoverQuery.trim().toLowerCase();
  const filteredDiscoveredModels = filterDiscoveredModels(
    discoveredModels || [],
    discoverCapability,
    Object.fromEntries(
      Object.entries(discoveredCapabilities).map(([key, value]) => [
        key.split(':').slice(1).join(':'),
        value,
      ]),
    ),
    discoverQueryText,
  );
  const selectableFilteredDiscoveredModels = filteredDiscoveredModels;
  const discoveredSelectedCount = selectableFilteredDiscoveredModels.filter(
    (item) => discoveredSelected[`${item.providerId}:${item.remoteModel}`],
  ).length;
  const allFilteredDiscoveredSelected = Boolean(selectableFilteredDiscoveredModels.length)
    && discoveredSelectedCount === selectableFilteredDiscoveredModels.length;

  const toggleAllFilteredDiscovered = () => {
    const checked = !allFilteredDiscoveredSelected;
    setDiscoveredSelected((current) => {
      const next = {...current};
      for (const item of selectableFilteredDiscoveredModels) {
        next[`${item.providerId}:${item.remoteModel}`] = checked;
      }
      return next;
    });
  };

  const selectProvider = (item: ProviderSpec) => {
    setProvider(providerDraft(item));
    setModel({...EMPTY_MODEL, providerId: item.id});
    setDetailKind('provider');
    setTestState(null);
  };

  const beginNewProvider = () => {
    setProvider({...EMPTY_PROVIDER});
    setModel({...EMPTY_MODEL});
    setDetailKind('provider');
    setTestState(null);
  };

  const selectModel = (item: ModelSpec) => {
    const next = modelDraft(item);
    setModel(next);
    setParameterRows(parameterRowsFromModel(next));
    setPricingRows(pricingRowsFromJson(next.pricing));
    setDetailKind('model');
  };

  const beginNewModel = () => {
    const providerId = provider.id || catalog?.providers[0]?.id || '';
    const next = {...EMPTY_MODEL, providerId};
    setModel(next);
    setParameterRows([]);
    setPricingRows(pricingRowsFromJson('{}'));
    setDetailKind('model');
  };

  const updateParameterRows = (next: ParameterDraftRow[]) => {
    setParameterRows(next);
    setModel((current) => ({...current, ...rowsToParamsJson(next)}));
  };

  const updatePricingRows = (next: ReturnType<typeof pricingRowsFromJson>) => {
    setPricingRows(next);
    setModel((current) => ({
      ...current,
      pricing: pricingJsonFromRows(current.capability, next),
    }));
  };

  const applyCatalog = (next: ModelCatalog) => {
    setCatalog(next);
    setProvider((current) => current.id || !next.providers.length
      ? current
      : providerDraft(next.providers[0]));
    setModel((current) => {
      if (current.id) {
        const selected = next.models.find((item) => item.id === current.id);
        return selected ? modelDraft(selected) : current;
      }
      const first = next.models[0];
      if (first) return modelDraft(first);
      return !next.providers.length
        ? current
        : {...current, providerId: next.providers[0].id};
    });
    setDetailKind((current) => next.models.length > 0 ? 'model' : current);
  };

  const reload = async () => {
    setStatus('正在读取模型目录…');
    try {
      applyCatalog(await getModelCatalog());
      setStatus('');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    }
  };

  useEffect(() => {
    if (detailKind !== 'model') return;
    setParameterRows(parameterRowsFromModel(model));
    setPricingRows(pricingRowsFromJson(model.pricing));
    // Refresh the visual editor only when switching models. Advanced JSON edits
    // should not be overwritten by an effect on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detailKind, model.id]);

  useEffect(() => {
    if (detailKind !== 'model' || !model.id) return;
    setProvider((current) => (
      current.id === model.providerId
        ? current
        : providerDraft(catalog?.providers.find((item) => item.id === model.providerId))
    ));
  }, [catalog, detailKind, model.id, model.providerId]);

  useEffect(() => {
    if (detailKind !== 'model') return;
    setModel((current) => ({
      ...current,
      pricing: pricingJsonFromRows(current.capability, pricingRows),
    }));
  }, [detailKind, model.capability]);

  // 点下拉以外的任何地方都收起（含弹窗内的输入框、另一侧面板）。
  // ★ 用 pointerdown 捕获阶段：这样点输入框时会先收起下拉，再正常聚焦输入框，
  //   不需要 preventDefault（会打断聚焦）。
  useEffect(() => {
    if (!openSelect) return;
    const close = (event: PointerEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && target.closest('.model-select-wrap')) return;
      setOpenSelect('');
    };
    window.addEventListener('pointerdown', close, true);
    return () => window.removeEventListener('pointerdown', close, true);
  }, [openSelect]);

  const saveProvider = async () => {
    if (providerBusy) return;
    setProviderBusy(true);
    setStatus('');
    try {
      applyCatalog(await saveProviderConfig(provider));
      setProvider((current) => ({...current, api_key: ''}));
      setDiscoveredModels(null);
      setDiscoverMessage('');
      setDetailKind('provider');
      setStatus('供应商已保存');
      window.dispatchEvent(new CustomEvent('canvas-model-catalog-changed'));
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setProviderBusy(false);
    }
  };

  const removeProvider = async () => {
    if (!provider.id) return;
    const ok = await dialog.confirm({
      title: `删除供应商「${provider.name || provider.id}」？`,
      message: '该供应商下配置的所有模型会一并删除，之后无法再用它生成内容。',
      confirmText: '删除供应商',
      tone: 'danger',
    });
    if (!ok) return;
    setProviderBusy(true);
    try {
      applyCatalog(await deleteProviderConfig(provider.id));
      setProvider({...EMPTY_PROVIDER});
      setModel({...EMPTY_MODEL});
      setDetailKind('provider');
      setStatus('供应商已删除');
      window.dispatchEvent(new CustomEvent('canvas-model-catalog-changed'));
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setProviderBusy(false);
    }
  };

  const uploadProviderIconFile = async (file: File) => {
    const error = providerIconFileError(file);
    if (error) {
      setStatus(error);
      return;
    }
    setProviderIconBusy(true);
    setStatus('正在上传供应商图标…');
    try {
      const icon = await uploadProviderIcon(file);
      setProvider((current) => ({...current, icon}));
      setStatus('图标已上传，保存供应商后生效');
    } catch (uploadError) {
      setStatus(uploadError instanceof Error ? uploadError.message : String(uploadError));
    } finally {
      setProviderIconBusy(false);
      if (providerIconInputRef.current) providerIconInputRef.current.value = '';
    }
  };

  const testProvider = async () => {
    if (!provider.id) {
      setTestState({ok: false, message: '先保存供应商再测试'});
      return;
    }
    setTestState({ok: false, message: '测试中…'});
    try {
      const result = await testProviderConfig(provider.id);
      setTestState(result);
    } catch (error) {
      setTestState({ok: false, message: error instanceof Error ? error.message : String(error)});
    }
  };

  const loadProviderModels = async () => {
    if (!provider.id) {
      setDiscoverMessage('先保存供应商，再拉取模型');
      return;
    }
    setDiscovering(true);
    setDiscoverMessage('');
    setDiscoveredModels(null);
    setDiscoverQuery('');
    try {
      const result = await discoverProviderModels(provider.id);
      setDiscoveredModels(result.models);
      setDiscoveredSelected({});
      setDiscoveredCapabilities({});
      setDiscoverMessage(`发现 ${result.count} 个模型，请确认后导入`);
    } catch (error) {
      setDiscoverMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setDiscovering(false);
    }
  };

  const importDiscoveredModels = async () => {
    const pending = filteredDiscoveredModels.filter((model) => {
      const key = `${model.providerId}:${model.remoteModel}`;
      if (!discoveredSelected[key]) return false;
      const capability = model.capability === 'unknown'
        ? discoveredCapabilities[key]
        : model.capability;
      return Boolean(capability);
    });
    if (!pending.length || modelBusy) return;
    setModelBusy(true);
    setStatus('');
    let savedCount = 0;
    try {
      let next = catalog;
      for (const item of pending) {
        const key = `${item.providerId}:${item.remoteModel}`;
        const capability = item.capability === 'unknown'
          ? discoveredCapabilities[key]
          : item.capability;
        const adapter = item.adapter || defaultAdapterForProvider(capability, item.providerId);
        next = await saveModelConfig({
          ...item,
          capability,
          adapter,
          enabled: true,
        } as unknown as Record<string, unknown>);
        savedCount += 1;
      }
      if (next) applyCatalog(next);
      setStatus(`已导入 ${pending.length} 个模型`);
      setDiscoveredModels(null);
      setDiscoveredSelected({});
      setDiscoveredCapabilities({});
      setDiscoverQuery('');
      setDiscoverMessage('');
      window.dispatchEvent(new CustomEvent('canvas-model-catalog-changed'));
    } catch (error) {
      if (savedCount) {
        window.dispatchEvent(new CustomEvent('canvas-model-catalog-changed'));
      }
      setStatus(
        `${savedCount ? `已导入 ${savedCount} 个，` : ''}${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      setModelBusy(false);
    }
  };

  const saveModel = async () => {
    if (modelBusy) return;
    setModelBusy(true);
    setStatus('');
    try {
      const adapterCapability = ADAPTER_CAPABILITIES[model.adapter];
      if (adapterCapability !== model.capability) {
        throw new Error('适配器与模型能力不匹配，请重新选择适配器');
      }
      const visualParams = rowsToParamsJson(parameterRows);
      const visualPricing = pricingJsonFromRows(model.capability, pricingRows);
      const payload = {
        ...model,
        defaultParams: advancedOpen.defaultParams
          ? parseJson(model.defaultParams, '默认参数')
          : parseJson(visualParams.defaultParams, '默认参数'),
        params: advancedOpen.params
          ? parseJson(model.params, '参数 Schema')
          : parseJson(visualParams.params, '参数 Schema'),
        limits: withComputedReferenceLimit(parseJson(model.limits, '限制')),
        pricing: advancedOpen.pricing
          ? parseJson(model.pricing, '计费规则')
          : parseJson(visualPricing, '计费规则'),
      };
      const next = await saveModelConfig(payload);
      applyCatalog(next);
      const saved = next.models.find((item) => model.id && item.id === model.id)
        || next.models.find((item) => (
          item.providerId === payload.providerId
          && item.remoteModel === payload.remoteModel
          && item.label === payload.label
        ));
      const savedProvider = saved
        ? next.providers.find((item) => item.id === saved.providerId)
        : undefined;
      if (savedProvider) setProvider(providerDraft(savedProvider));
      if (saved) {
        const nextDraft = modelDraft(saved);
        setModel(nextDraft);
        setParameterRows(parameterRowsFromModel(nextDraft));
        setPricingRows(pricingRowsFromJson(nextDraft.pricing));
      }
      setDetailKind('model');
      setStatus('模型已保存');
      window.dispatchEvent(new CustomEvent('canvas-model-catalog-changed'));
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setModelBusy(false);
    }
  };

  const removeModel = async () => {
    if (!model.id) return;
    const ok = await dialog.confirm({
      title: `删除模型「${model.label || model.id}」？`,
      message: '只删除这条模型配置，供应商本身保留。',
      confirmText: '删除模型',
      tone: 'danger',
    });
    if (!ok) return;
    setModelBusy(true);
    try {
      applyCatalog(await deleteModelConfig(model.id));
      setModel({...EMPTY_MODEL, providerId: model.providerId});
      setDetailKind('model');
      setStatus('模型已删除');
      window.dispatchEvent(new CustomEvent('canvas-model-catalog-changed'));
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setModelBusy(false);
    }
  };

  return (
    <div className="canvas-help-backdrop model-settings-backdrop">
      <section className="canvas-help model-settings-dialog" role="dialog" aria-modal="true">
        <header>
          <div>
            <strong>模型接口</strong>
            <span className="model-settings-subtitle">供应商、模型与参数 Schema 均为本地配置</span>
          </div>
          <div className="model-settings-header-actions">
            <button type="button" onClick={() => void reload()} title="刷新目录"><RefreshCw size={14} />刷新</button>
            <button type="button" className="canvas-inspector-close" onClick={onClose} aria-label="关闭"><X size={15} /></button>
          </div>
        </header>

        {status && <div className="model-settings-status">{status}</div>}

        <div className="model-settings-body">
          <aside className="model-settings-provider-nav">
            <div className="model-settings-nav-head">
              <div>
                <span className="model-settings-nav-kicker">供应商</span>
                <strong>{catalog?.providers.length || 0}</strong>
              </div>
              <button type="button" onClick={beginNewProvider}><Plus size={13} />新增</button>
            </div>
            <p className="model-settings-nav-note">选择供应商后，右侧编辑地址、密钥与启用状态。</p>
            <div className="model-settings-list">
              {catalog?.providers.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`model-settings-list-item ${provider.id === item.id ? 'is-active' : ''}`}
                  onClick={() => selectProvider(item)}
                >
                  <span className="model-settings-provider-icon" aria-hidden="true">
                    {item.icon
                      ? <img src={mediaSourceUrl(item.icon)} alt="" loading="lazy" />
                      : <Box size={15} strokeWidth={1.8} />}
                  </span>
                  <span><strong>{item.name}</strong><small>{item.protocol} · {item.base_url}</small></span>
                  <i className={item.has_api_key ? 'is-ready' : ''}>{item.has_api_key ? '已配密钥' : '缺密钥'}</i>
                </button>
              ))}
              {!catalog?.providers.length && <p className="model-settings-empty">还没有供应商</p>}
            </div>
          </aside>

          <aside className="model-settings-model-nav">
            <div className="model-settings-nav-head">
              <div>
                <span className="model-settings-nav-kicker">模型</span>
                <strong>{providerModels.length}</strong>
              </div>
              <button type="button" onClick={beginNewModel} disabled={!provider.id} title={!provider.id ? '先选择或保存供应商' : '新增模型'}><Plus size={13} />新增模型</button>
            </div>
            <p className="model-settings-model-context" title={provider.name || provider.id}>
              {provider.name || provider.id || '先选择供应商'}
            </p>
            <div className="model-settings-model-list">
              {providerModels.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`model-settings-model-item ${model.id === item.id && detailKind === 'model' ? 'is-active' : ''}`}
                  onClick={() => selectModel(item)}
                >
                  <span className={`model-settings-kind is-${item.capability}`}>{item.capability}</span>
                  <span><strong>{item.label}</strong><small>{item.remoteModel} · {item.adapter}</small></span>
                </button>
              ))}
              {provider.id && providerModels.length === 0 && <p className="model-settings-empty">该供应商还没有模型</p>}
              {!provider.id && <p className="model-settings-empty">选择供应商后查看模型</p>}
            </div>
          </aside>

          <section className="model-settings-detail">
            <header className="model-settings-detail-head">
              <div>
                <span className="model-settings-nav-kicker">{detailKind === 'provider' ? '供应商设置' : '模型设置'}</span>
                <h3>{detailKind === 'provider'
                  ? (provider.name || provider.id || '新增供应商')
                  : (model.label || model.id || '新增模型')}</h3>
                <p>{detailKind === 'provider'
                  ? '配置连接地址、密钥与供应商启用状态。'
                  : '配置模型能力、适配器、参考上限与供应商参数 Schema。'}</p>
              </div>
              {detailKind === 'model' && (
                <span className={`model-settings-kind is-${model.capability}`}>{model.capability}</span>
              )}
            </header>

            <div className="model-settings-detail-scroll">
              {detailKind === 'provider' ? (
                <div className="model-settings-form">
                  <label><span>供应商 ID</span><input value={provider.id} disabled={Boolean(catalog?.providers.some((item) => item.id === provider.id))} onChange={(event) => setProvider({...provider, id: event.target.value})} placeholder="例如 my-provider" /></label>
                  <label><span>显示名称</span><input value={provider.name} onChange={(event) => setProvider({...provider, name: event.target.value})} /></label>
                  <label><span>协议</span>
                    <ModelSelect
                      ariaLabel="协议"
                      value={provider.protocol}
                      options={[{value: 'ark', label: '火山方舟'}, {value: 'openai', label: 'OpenAI 兼容'}, {value: 'dashscope', label: '阿里云百炼'}]}
                      open={openSelect === 'protocol'}
                      onToggle={() => setOpenSelect((v) => (v === 'protocol' ? '' : 'protocol'))}
                      onChange={(v) => { setProvider({...provider, protocol: v}); setOpenSelect(''); }}
                    />
                  </label>
                  <label className="is-wide"><span>接口地址</span><input value={provider.base_url} onChange={(event) => setProvider({...provider, base_url: event.target.value})} placeholder="https://…/v1" /></label>
                  <div className="is-wide model-provider-icon-field">
                    <span>供应商图标（选填）</span>
                    <div className="model-provider-icon-control">
                      <span className={`model-provider-icon-preview ${provider.icon ? 'has-image' : ''}`} aria-hidden="true">
                        {provider.icon
                          ? <img src={mediaSourceUrl(provider.icon)} alt="" />
                          : <Box size={20} strokeWidth={1.7} />}
                      </span>
                      <div className="model-provider-icon-copy">
                        <strong>{provider.icon ? '已选择自定义图标' : '使用默认供应商图标'}</strong>
                        <small>PNG、JPG、WebP 或 GIF，最大 5MB</small>
                      </div>
                      <input
                        ref={providerIconInputRef}
                        type="file"
                        accept="image/png,image/jpeg,image/webp,image/gif"
                        hidden
                        onChange={(event) => {
                          const file = event.target.files?.[0];
                          if (file) void uploadProviderIconFile(file);
                        }}
                      />
                      <button
                        type="button"
                        onClick={() => providerIconInputRef.current?.click()}
                        disabled={providerIconBusy}
                      >
                        <Upload size={13} />
                        {providerIconBusy ? '上传中…' : provider.icon ? '替换图标' : '上传图标'}
                      </button>
                      {provider.icon && (
                        <button
                          type="button"
                          className="is-danger"
                          onClick={() => setProvider({...provider, icon: ''})}
                          disabled={providerIconBusy}
                        >
                          <Trash2 size={13} />
                          清除
                        </button>
                      )}
                    </div>
                  </div>
                  <label className="is-wide"><span>API Key {provider.api_key ? '' : <i>{catalog?.providers.find((item) => item.id === provider.id)?.api_key_hint || '未配置'}</i>}</span><input type="password" value={provider.api_key} onChange={(event) => setProvider({...provider, api_key: event.target.value})} placeholder="留空保留现有密钥" /></label>
                  <label><span>Workspace ID（百炼可选）</span><input value={provider.workspace_id} onChange={(event) => setProvider({...provider, workspace_id: event.target.value})} /></label>
                  <div className="model-settings-check">
                    <span>启用</span>
                    <div className="model-settings-check-body">
                      <button
                        type="button"
                        role="switch"
                        aria-checked={provider.enabled}
                        aria-label="启用该供应商"
                        className={`model-settings-switch ${provider.enabled ? 'is-on' : ''}`}
                        onClick={() => setProvider({...provider, enabled: !provider.enabled})}
                      >
                        <i />
                      </button>
                      <em className={provider.enabled ? 'is-on' : ''}>{provider.enabled ? '已启用' : '已停用'}</em>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="model-settings-form">
                  <label><span>模型 ID</span><input value={model.id} disabled={Boolean(catalog?.models.some((item) => item.id === model.id))} onChange={(event) => setModel({...model, id: event.target.value})} placeholder="稳定目录 ID" /></label>
                  <label><span>供应商</span>
                    <ModelSelect
                      ariaLabel="供应商"
                      value={model.providerId}
                      options={(catalog?.providers || []).map((item) => ({value: item.id, label: item.name}))}
                      open={openSelect === 'providerId'}
                      onToggle={() => setOpenSelect((v) => (v === 'providerId' ? '' : 'providerId'))}
                      onChange={(v) => { setModel({...model, providerId: v}); setOpenSelect(''); }}
                    />
                  </label>
                  <label><span>能力</span>
                    <ModelSelect
                      ariaLabel="能力"
                      value={model.capability}
                      options={CAPABILITIES.map(([value, label]) => ({value, label}))}
                      open={openSelect === 'capability'}
                      onToggle={() => setOpenSelect((v) => (v === 'capability' ? '' : 'capability'))}
                      onChange={(v) => {
                        const currentAdapterMatches = ADAPTER_CAPABILITIES[model.adapter] === v;
                        setModel({
                          ...model,
                          capability: v,
                          adapter: currentAdapterMatches
                            ? model.adapter
                            : defaultAdapterForProvider(v, model.providerId),
                        });
                        setOpenSelect('');
                      }}
                    />
                  </label>
                  <label><span>适配器</span>
                    <ModelSelect
                      ariaLabel="适配器"
                      value={model.adapter}
                      options={adaptersForCapability(model.capability)}
                      open={openSelect === 'adapter'}
                      onToggle={() => setOpenSelect((v) => (v === 'adapter' ? '' : 'adapter'))}
                      onChange={(v) => { setModel({...model, adapter: v}); setOpenSelect(''); }}
                    />
                  </label>
                  <label><span>远程模型 ID</span><input value={model.remoteModel} onChange={(event) => setModel({...model, remoteModel: event.target.value})} /></label>
                  <label><span>显示名称</span><input value={model.label} onChange={(event) => setModel({...model, label: event.target.value})} /></label>
                  <label><span>徽标</span><input value={model.badge} onChange={(event) => setModel({...model, badge: event.target.value})} maxLength={16} /></label>
                  <div className="model-settings-check">
                    <span>启用</span>
                    <div className="model-settings-check-body">
                      <button
                        type="button"
                        role="switch"
                        aria-checked={model.enabled}
                        aria-label="启用该模型"
                        className={`model-settings-switch ${model.enabled ? 'is-on' : ''}`}
                        onClick={() => setModel({...model, enabled: !model.enabled})}
                      >
                        <i />
                      </button>
                      <em className={model.enabled ? 'is-on' : ''}>{model.enabled ? '已启用' : '已停用'}</em>
                    </div>
                  </div>
                  <label className="is-full"><span>描述</span><input value={model.description} onChange={(event) => setModel({...model, description: event.target.value})} /></label>
                  <div className="model-settings-limit-panel">
                    <header>
                      <strong>模型能力与并发</strong>
                      <span>参考数量留空按 0 计入总数；模型并发留空使用该任务类型默认值</span>
                    </header>
                    <div className="model-settings-limit-grid">
                      {MODEL_LIMIT_FIELDS.map(([key, label, hint]) => (
                        <label key={key}>
                          <span>{label}<i>{hint}</i></span>
                          <input
                            type="number"
                            min={0}
                            step={1}
                            value={limitInputValue(model.limits, key)}
                            placeholder={key === 'maxConcurrency' ? '默认' : '不限'}
                            readOnly={key === 'maxRefs'}
                            className={key === 'maxRefs' ? 'is-computed' : ''}
                            title={key === 'maxRefs' ? '自动等于图片、视频、音频和文本引用上限之和' : undefined}
                            onChange={(event) => setModel({
                              ...model,
                              limits: key === 'maxRefs'
                                ? model.limits
                                : updateLimitJson(model.limits, key, event.target.value),
                            })}
                          />
                        </label>
                      ))}
                    </div>
                  </div>
                  <section className="model-param-editor is-full">
                    <header>
                      <div>
                        <strong>生成参数</strong>
                        <span>直接添加参数、选择控件类型和默认值；生成时按这里渲染参数控件。</span>
                      </div>
                      <button type="button" onClick={() => updateParameterRows([
                        ...parameterRows,
                        {key: '', type: 'text', defaultValue: '', optionsText: '', min: '', max: '', step: '', multiple: false, api: ''},
                      ])}><Plus size={12} />添加参数</button>
                    </header>
                    <div className="model-param-list">
                      {parameterRows.map((row, index) => (
                        <div className="model-param-row" key={`${row.key}-${index}`}>
                          <label>
                            <span>参数名</span>
                            <input value={row.key} placeholder="ratio" onChange={(event) => updateParameterRows(parameterRows.map((item, i) => i === index ? {...item, key: event.target.value} : item))} />
                          </label>
                          <label>
                            <span>控件</span>
                            <select value={row.type} onChange={(event) => updateParameterRows(parameterRows.map((item, i) => i === index ? {...item, type: event.target.value as ParameterDraftRow['type']} : item))}>
                              {PARAM_TYPES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                            </select>
                          </label>
                          <label>
                            <span>默认值</span>
                            <input value={row.defaultValue} placeholder={row.type === 'boolean' ? 'true / false' : row.type === 'range' ? '5' : '默认值'} onChange={(event) => updateParameterRows(parameterRows.map((item, i) => i === index ? {...item, defaultValue: event.target.value} : item))} />
                          </label>
                          {row.type === 'select' && (
                            <label className="is-param-wide">
                              <span>可选项（逗号分隔）</span>
                              <input value={row.optionsText} placeholder="1:1, 16:9, 9:16" onChange={(event) => updateParameterRows(parameterRows.map((item, i) => i === index ? {...item, optionsText: event.target.value} : item))} />
                            </label>
                          )}
                          {row.type === 'range' && (
                            <>
                              <label><span>最小</span><input type="number" value={row.min} onChange={(event) => updateParameterRows(parameterRows.map((item, i) => i === index ? {...item, min: event.target.value} : item))} /></label>
                              <label><span>最大</span><input type="number" value={row.max} onChange={(event) => updateParameterRows(parameterRows.map((item, i) => i === index ? {...item, max: event.target.value} : item))} /></label>
                              <label><span>步长</span><input type="number" value={row.step} onChange={(event) => updateParameterRows(parameterRows.map((item, i) => i === index ? {...item, step: event.target.value} : item))} /></label>
                            </>
                          )}
                          <button type="button" className="model-param-remove" title="删除参数" aria-label="删除参数" onClick={() => updateParameterRows(parameterRows.filter((_, i) => i !== index))}><Trash2 size={12} /></button>
                        </div>
                      ))}
                      {parameterRows.length === 0 && <p className="model-param-empty">还没有参数；点“添加参数”开始配置。</p>}
                    </div>
                    <button type="button" className="model-json-toggle" onClick={() => setAdvancedOpen((current) => ({...current, defaultParams: !current.defaultParams, params: !current.params}))}>
                      <Braces size={12} />{advancedOpen.defaultParams ? '收起默认参数 / 参数 Schema 原始 JSON' : '高级：编辑默认参数 / 参数 Schema JSON'}
                    </button>
                    {advancedOpen.defaultParams && (
                      <div className="model-json-advanced">
                        <label><span>默认参数 JSON</span><textarea value={model.defaultParams} onChange={(event) => setModel({...model, defaultParams: event.target.value})} rows={4} /></label>
                        <label><span>参数 Schema JSON</span><textarea value={model.params} onChange={(event) => setModel({...model, params: event.target.value})} rows={6} /></label>
                      </div>
                    )}
                  </section>
                  <section className="model-param-editor is-full">
                    <header>
                      <div><strong>计费规则</strong><span>按档位填写单价；有视频参考时可单独填写一组单价。</span></div>
                    </header>
                    <div className="model-pricing-row">
                      <label><span>币种</span><select value={pricingRows.currency} onChange={(event) => updatePricingRows({...pricingRows, currency: event.target.value})}><option value="CNY">人民币 CNY</option><option value="USD">美元 USD</option></select></label>
                      <label><span>单位</span><select value={pricingRows.unit} onChange={(event) => updatePricingRows({...pricingRows, unit: event.target.value})}><option value="second">每秒</option><option value="image">每张</option><option value="video">每个视频</option><option value="request">每次请求</option></select></label>
                      <label><span>固定费用</span><input type="number" min={0} step="0.001" value={pricingRows.fixed} placeholder="可选" onChange={(event) => updatePricingRows({...pricingRows, fixed: event.target.value})} /></label>
                    </div>
                    <div className="model-pricing-tiers">
                      {pricingRows.tiers.map((tier, index) => (
                        <div key={tier.id}>
                          <label><span>档位</span><input value={tier.label} placeholder="如 480P / 1.5K" onChange={(event) => updatePricingRows({...pricingRows, tiers: pricingRows.tiers.map((item, i) => i === index ? {...item, label: event.target.value} : item)})} /></label>
                          <label><span>单价</span><input type="number" min={0} step="0.001" value={tier.rate} placeholder="0.462" onChange={(event) => updatePricingRows({...pricingRows, tiers: pricingRows.tiers.map((item, i) => i === index ? {...item, rate: event.target.value} : item)})} /></label>
                          <label><span>含视频输入</span><input type="number" min={0} step="0.001" value={tier.rateWithVideo} placeholder="可选" onChange={(event) => updatePricingRows({...pricingRows, tiers: pricingRows.tiers.map((item, i) => i === index ? {...item, rateWithVideo: event.target.value} : item)})} /></label>
                          <button type="button" title="删除档位" aria-label="删除档位" onClick={() => updatePricingRows({...pricingRows, tiers: pricingRows.tiers.filter((_, i) => i !== index)})}><Trash2 size={12} /></button>
                        </div>
                      ))}
                      <button type="button" onClick={() => updatePricingRows({...pricingRows, tiers: [...pricingRows.tiers, {id: `${Date.now()}-`, label: '', rate: '', rateWithVideo: ''}]})}><Plus size={12} />添加档位</button>
                    </div>
                    <button type="button" className="model-json-toggle" onClick={() => setAdvancedOpen((current) => ({...current, pricing: !current.pricing}))}>
                      <Braces size={12} />{advancedOpen.pricing ? '收起计费规则原始 JSON' : '高级：编辑计费规则 JSON'}
                    </button>
                    {advancedOpen.pricing && <textarea className="model-pricing-json" value={model.pricing} onChange={(event) => setModel({...model, pricing: event.target.value})} rows={6} />}
                  </section>
                </div>
              )}
            </div>

            <div className="model-settings-actions">
              {detailKind === 'provider' ? (
                <>
                  <button type="button" className="is-primary" onClick={() => void saveProvider()} disabled={providerBusy}><Save size={13} />{providerBusy ? '保存中…' : '保存供应商'}</button>
                  <button type="button" onClick={() => void testProvider()}>测试连接</button>
                  <button type="button" onClick={() => void loadProviderModels()} disabled={discovering || providerBusy}>
                    <RefreshCw size={13} />{discovering ? '拉取中…' : '拉取模型'}
                  </button>
                  {provider.id && !catalog?.providers.find((item) => item.id === provider.id)?.builtin && (
                    <button type="button" className="is-danger" onClick={() => void removeProvider()}><Trash2 size={13} />删除</button>
                  )}
                  {testState && <span className={testState.ok ? 'is-ok' : 'is-error'}>{testState.message}</span>}
                  {discoverMessage && <span className={discoveredModels ? 'is-ok' : 'is-error'}>{discoverMessage}</span>}
                </>
              ) : (
                <>
                  <button type="button" className="is-primary" onClick={() => void saveModel()} disabled={modelBusy}><Save size={13} />{modelBusy ? '保存中…' : '保存模型'}</button>
                  {model.id && (
                    <button type="button" className="is-danger" onClick={() => void removeModel()} disabled={modelBusy}><Trash2 size={13} />删除模型</button>
                  )}
                  <span className="model-settings-hint"><Check size={12} />保存后自动刷新节点模型下拉</span>
                </>
              )}
            </div>
          </section>
        </div>
        {discoveredModels && (
          <div className="model-discover-panel">
            <header>
              <strong>发现 {discoveredModels.length} 个模型 · 已挑选 {discoveredSelectedCount}</strong>
              <button type="button" onClick={() => { setDiscoveredModels(null); setDiscoverQuery(''); setDiscoverCapability(''); }} aria-label="关闭模型发现结果"><X size={13} /></button>
            </header>
            <label className="model-discover-select-all">
              <span className="model-discover-check">
                <input
                  type="checkbox"
                  checked={allFilteredDiscoveredSelected}
                  disabled={!selectableFilteredDiscoveredModels.length}
                  aria-label="全选当前筛选结果"
                  onChange={toggleAllFilteredDiscovered}
                />
                <span aria-hidden="true"><Check size={11} strokeWidth={2.4} /></span>
              </span>
              <span>全选当前结果</span>
              <small>{discoveredSelectedCount} / {selectableFilteredDiscoveredModels.length}</small>
            </label>
            <label className="model-discover-search">
              <Search size={13} aria-hidden="true" />
              <input
                type="search"
                value={discoverQuery}
                onChange={(event) => setDiscoverQuery(event.target.value)}
                placeholder="搜索名称、模型 ID、能力或适配器"
                aria-label="搜索发现的模型"
              />
              {discoverQuery && <button type="button" onClick={() => setDiscoverQuery('')} aria-label="清空搜索"><X size={12} /></button>}
            </label>
            <div className="model-discover-type-filter" role="group" aria-label="按模型类型筛选">
              {[
                ['', '全部'],
                ['text', '文本'],
                ['image', '图片'],
                ['video', '视频'],
                ['audio', '音频'],
                ['__unknown__', '待选择'],
              ].map(([value, label]) => (
                <button
                  key={value || 'all'}
                  type="button"
                  className={discoverCapability === value ? 'is-active' : ''}
                  onClick={() => setDiscoverCapability(value)}
                >
                  {label}
                  <small>
                    {value
                      ? (discoveredModels || []).filter((item) => {
                        const selected = discoveredCapabilities[`${item.providerId}:${item.remoteModel}`];
                        const resolved = item.capability === 'unknown' ? selected || '' : item.capability;
                        return value === '__unknown__'
                          ? item.capability === 'unknown'
                          : resolved === value;
                      }).length
                      : (discoveredModels || []).length}
                  </small>
                </button>
              ))}
            </div>
            <div className="model-discover-list">
              {filteredDiscoveredModels.map((item) => {
                const key = `${item.providerId}:${item.remoteModel}`;
                const checked = Boolean(discoveredSelected[key]);
                const selectedCapability = item.capability === 'unknown'
                  ? discoveredCapabilities[key] || ''
                  : item.capability;
                return (
                  <div key={key} data-selected={checked ? 'true' : 'false'}>
                    <label
                      className="model-discover-check"
                      aria-label={`选择 ${item.remoteModel}`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(event) => {
                          const nextChecked = event.target.checked;
                          setDiscoveredSelected((current) => ({
                            ...current,
                            [key]: nextChecked,
                          }));
                          if (
                            nextChecked
                            && item.capability !== 'unknown'
                            && !item.alreadyConfigured
                          ) {
                            setDiscoveredCapabilities((current) => ({
                              ...current,
                              [key]: item.capability,
                            }));
                          }
                        }}
                      />
                      <span aria-hidden="true"><Check size={11} strokeWidth={2.4} /></span>
                    </label>
                    <span className={`model-settings-kind is-${selectedCapability || 'unknown'}`}>
                      {selectedCapability || '待选择'}
                    </span>
                    <span><strong>{item.label}</strong><small>{item.remoteModel}{item.adapter ? ` · ${item.adapter}` : ''}</small></span>
                    {item.capability === 'unknown' && !item.alreadyConfigured ? (
                      <select
                        aria-label={`选择 ${item.remoteModel} 的类型`}
                        value={selectedCapability}
                        onChange={(event) => {
                          const value = event.target.value;
                          setDiscoveredCapabilities((current) => ({
                            ...current,
                            [key]: value,
                          }));
                        }}
                      >
                        <option value="">选择类型</option>
                        {CAPABILITIES.map(([value, label]) => (
                          <option key={value} value={value}>{label}</option>
                        ))}
                      </select>
                    ) : (
                      <em>
                        {item.alreadyConfigured ? '已配置 · 可更新' : '待导入'}
                      </em>
                    )}
                  </div>
                );
              })}
              {!filteredDiscoveredModels.length && <p className="model-discover-empty">没有匹配的模型。</p>}
            </div>
            <footer>
              <span>已选 {discoveredSelectedCount} / {selectableFilteredDiscoveredModels.length} 个可导入</span>
              <button type="button" onClick={() => { setDiscoveredModels(null); setDiscoveredSelected({}); setDiscoverQuery(''); setDiscoverCapability(''); }}>取消</button>
              <button type="button" className="is-primary" disabled={modelBusy || !discoveredSelectedCount} onClick={() => void importDiscoveredModels()}>
                {modelBusy ? `处理中…（${discoveredSelectedCount}）` : `导入 ${discoveredSelectedCount} 个`}
              </button>
            </footer>
          </div>
        )}
      </section>
    </div>
  );
}
