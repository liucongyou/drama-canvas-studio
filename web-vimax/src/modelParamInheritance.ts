import type {CanvasMediaParams, ModelSpec} from './api';

const PARAM_KEYS = ['ratio', 'resolution', 'clarity', 'guidance'] as const;

function numeric(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Apply the parameters of a newly selected model without discarding choices
 * the user already made. Defaults are only used to fill a missing value.
 */
export function inheritModelParams(
  current: CanvasMediaParams,
  spec: ModelSpec,
): CanvasMediaParams {
  const defaults = spec.defaultParams || {};
  const next: CanvasMediaParams = {
    ...defaults,
    ...current,
    provider: spec.providerId,
    model: spec.id,
  };

  for (const key of PARAM_KEYS) {
    const options = spec.params?.[key]?.options;
    const value = next[key];
    if (Array.isArray(options) && options.length && !options.includes(value as never)) {
      next[key] = String(defaults[key] ?? options[0]) as never;
    }
  }

  const durationSpec = spec.params?.duration;
  if (durationSpec) {
    const min = numeric(durationSpec.min) ?? 1;
    const max = numeric(durationSpec.max) ?? Number.MAX_SAFE_INTEGER;
    next.duration = Math.max(min, Math.min(max, numeric(next.duration) ?? numeric(defaults.duration) ?? min));
  }

  const countSpec = spec.params?.count;
  if (Array.isArray(countSpec?.options) && countSpec.options.length) {
    const allowed = countSpec.options.map(Number);
    const currentCount = numeric(next.count) ?? numeric(defaults.count) ?? allowed[0];
    next.count = allowed.includes(currentCount) ? currentCount : Number(defaults.count ?? allowed[0]);
  }

  if (spec.capability === 'audio') {
    next.ttsModel = spec.remoteModel;
    if (!next.ttsMode) next.ttsMode = 'design';
  }

  return next;
}

/**
 * Only migrate a node whose stored model is no longer available.
 * Keep compatible choices and never persist model defaults as node state.
 */
export function migrateUnavailableModelParams(
  current: CanvasMediaParams,
  spec: ModelSpec,
  kind: 'text' | 'image' | 'audio' | 'video',
): CanvasMediaParams {
  const migrated = inheritModelParams(current, spec);
  const next: CanvasMediaParams = {
    ...current,
    provider: spec.providerId,
    model: spec.id,
  };
  if (kind === 'audio') next.ttsModel = spec.remoteModel;

  for (const key of PARAM_KEYS) {
    if (current[key] !== undefined && migrated[key] !== current[key]) {
      next[key] = migrated[key];
    }
  }
  if (
    current.duration !== undefined
    && migrated.duration !== current.duration
  ) {
    next.duration = migrated.duration;
  }
  if (current.count !== undefined && migrated.count !== current.count) {
    next.count = migrated.count;
  }
  if (
    kind === 'audio'
    && current.ttsMode === undefined
    && migrated.ttsMode !== undefined
  ) {
    next.ttsMode = migrated.ttsMode;
  }
  return next;
}

/**
 * Build the explicit generation snapshot. The node already has explicit
 * choices; this helper makes them compatible with the active model and fills
 * missing fields from defaults without rewriting unrelated values.
 */
export function inheritedGenerationParams(
  current: CanvasMediaParams,
  spec: ModelSpec,
): CanvasMediaParams {
  return inheritModelParams(current, spec);
}
