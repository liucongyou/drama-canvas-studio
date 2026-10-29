import type {ModelSpec} from './api';

export type ModelParamContractIssue = {
  key: string;
  message: string;
};

/**
 * The model catalog is the source of truth for editable generation params.
 * UI controls must not silently replace an unsupported value with a global
 * fallback; callers should surface this issue before enabling generation.
 */
export function validateModelParams(
  params: Record<string, unknown> | undefined,
  model: ModelSpec | null | undefined,
): ModelParamContractIssue[] {
  if (!model) return [];
  const values = params || {};
  const schema = model.params || {};
  const issues: ModelParamContractIssue[] = [];
  for (const [key, spec] of Object.entries(schema)) {
    const options = Array.isArray(spec?.options) ? spec.options : [];
    if (!options.length) continue;
    const value = values[key];
    if (value === undefined || value === null || value === '') continue;
    if (!options.some((option) => String(option) === String(value))) {
      issues.push({
        key,
        message: `${key}=${String(value)} 不在模型可选值 ${options.map(String).join('/')} 内`,
      });
    }
  }
  return issues;
}
