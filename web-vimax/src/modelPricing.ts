import type {CanvasMediaNode, ModelSpec} from './api';

export function pricingCurrencySymbol(currency?: string): string {
  return String(currency || 'CNY').toUpperCase() === 'USD' ? '$' : '¥';
}

export function modelPricingNote(model?: ModelSpec | null, hasVideoInput = false): string {
  const capability = model?.capability;
  const rule = capability ? model?.pricing?.[capability] : undefined;
  return String(
    (hasVideoInput ? rule?.noteWithVideo : '')
    || rule?.note
    || '未配置该模型的计费规则，实际费用以供应商账单为准',
  );
}

// Price must come from the selected model's pricing config. A provider-wide
// per-second fallback is incorrect because resolution/model/input modalities differ.
export function generationCostAmount(
  node: CanvasMediaNode,
  model?: ModelSpec | null,
  options?: {hasVideoInput?: boolean},
): number | null {
  const params = node.params || {};
  const rule = model?.pricing?.[node.kind];
  if (!rule) return null;
  const count = Math.max(1, Number(params.count || 1));
  const fixed = Number(rule.fixed);
  let amount = Number.NaN;

  if (node.kind === 'video') {
    if (rule.unit === 'second') {
      const rates = options?.hasVideoInput && rule.ratesWithVideo
        ? rule.ratesWithVideo
        : rule.rates;
      const rate = Number(rates?.[String(params.resolution || '')] ?? rates?.default);
      const duration = Math.max(0, Number(params.duration || 0));
      if (Number.isFinite(rate) && rate >= 0 && duration > 0) amount = rate * duration * count;
    } else if (rule.unit === 'video' || rule.unit === 'request') {
      if (Number.isFinite(fixed) && fixed >= 0) amount = fixed * count;
    }
  } else if (node.kind === 'image') {
    if (rule.unit === 'image' || rule.unit === 'request') {
      const rate = Number(
        rule.rates?.[String(params.clarity || '')]
        ?? rule.rates?.[String(params.ratio || '')]
        ?? rule.rates?.default
        ?? fixed,
      );
      if (Number.isFinite(rate) && rate >= 0) amount = rate * count;
    }
  } else if (node.kind === 'audio') {
    if (rule.unit === 'second') {
      const rate = Number(rule.rates?.default ?? fixed);
      const duration = Math.max(0, Number(params.previewDuration || params.duration || 0));
      if (Number.isFinite(rate) && rate >= 0 && duration > 0) amount = rate * duration;
    } else if (rule.unit === 'request' && Number.isFinite(fixed) && fixed >= 0) {
      amount = fixed;
    }
  }

  return Number.isFinite(amount) && amount >= 0 ? amount : null;
}

export function generationCostHint(
  node: CanvasMediaNode,
  model?: ModelSpec | null,
  options?: {hasVideoInput?: boolean},
): string {
  const rule = model?.pricing?.[node.kind];
  if (!rule) return '按供应商账单';
  const amount = generationCostAmount(node, model, options);
  if (amount === null) return '按供应商账单';
  const symbol = pricingCurrencySymbol(model?.pricing?.currency);
  return `≈${symbol}${amount.toFixed(amount < 1 ? 3 : 2)}`;
}
