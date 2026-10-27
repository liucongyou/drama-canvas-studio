export type ModelPresentation = {
  name: string;
  ref: string;
};

const MODEL_PRESENTATIONS: Record<string, ModelPresentation> = {
  'ep-m-20260911200253-v5snx': {name: 'Doubao-Seedream-5.0-pro', ref: 'ep-m-20260911200253-v5snx'},
  'doubao-seedream-5-0-pro-260628': {name: 'Doubao-Seedream-5.0-pro', ref: 'doubao-seedream-5-0-pro-260628'},
  'ep-m-20260917021019-cfgc7': {name: 'Doubao-Seedance-2.0-mini', ref: 'ep-m-20260917021019-cfgc7'},
  'doubao-seedance-2-0-mini-260615': {name: 'Doubao-Seedance-2.0-mini', ref: 'doubao-seedance-2-0-mini-260615'},
  'cosyvoice-v3.5-flash': {name: 'CosyVoice-v3.5-Flash', ref: 'cosyvoice-v3.5-flash'},
};

export function resolveModelPresentation(model: string): ModelPresentation {
  const raw = String(model || '').trim();
  return MODEL_PRESENTATIONS[raw.toLowerCase()] ?? {name: raw || '未配置', ref: ''};
}

export function hasModelPresentation(model: string): boolean {
  return Boolean(MODEL_PRESENTATIONS[String(model || '').trim().toLowerCase()]);
}
