export type EpisodeLoadDecision =
  | 'ignore'
  | 'load'
  | 'already-active';

export type EpisodeRequestActivity = {
  requestEpoch: number;
  target: string;
};

type EpisodeLoadInput = {
  target: string | null | undefined;
  active: string | null | undefined;
  current?: string | null;
  pending?: string | null;
};

/**
 * Decide whether an episode selection needs a workspace request.
 *
 * A failed load must never move the selector back to the previously painted
 * episode. The user's latest selection remains authoritative, and a retry
 * targets the same episode.
 */
export function episodeLoadDecision(input: EpisodeLoadInput): EpisodeLoadDecision {
  const target = normalizeEpisodeId(input.target);
  const active = normalizeEpisodeId(input.active);
  const current = normalizeEpisodeId(input.current);
  const pending = normalizeEpisodeId(input.pending);

  if (!target || target !== current) return 'ignore';
  if (target === active && !pending) return 'already-active';
  if (target === pending) return 'ignore';
  return 'load';
}

/**
 * The initial workspace request owns the first selection. A rendered default
 * episode is not evidence that the user asked for a second load; only an
 * explicit user action may hand ownership to the single-episode loader.
 */
export function episodeSelectionDecision(
  input: EpisodeLoadInput & {explicitSelection: boolean},
): EpisodeLoadDecision {
  if (!input.explicitSelection) return 'ignore';
  return episodeLoadDecision(input);
}

/**
 * Only the newest request may publish data or errors. Intentional aborts are
 * stale responses, not user-visible timeouts.
 */
export function shouldPublishEpisodeLoad(input: {
  requestEpoch: number;
  latestEpoch: number;
  target: string | null | undefined;
  current: string | null | undefined;
  aborted?: boolean;
}): boolean {
  if (input.aborted) return false;
  if (input.requestEpoch !== input.latestEpoch) return false;
  return normalizeEpisodeId(input.target) === normalizeEpisodeId(input.current);
}

export function normalizeEpisodeId(value: string | null | undefined): string {
  return String(value || '').trim().toUpperCase();
}

/**
 * Track the newest episode request by sequence, not by React render timing.
 * A delayed effect from a previous click must never supersede the latest one.
 */
export function nextEpisodeRequest(
  previous: EpisodeRequestActivity | null,
  targetValue: string | null | undefined,
): EpisodeRequestActivity | null {
  const target = normalizeEpisodeId(targetValue);
  if (!target) return previous;
  return {
    requestEpoch: (previous?.requestEpoch || 0) + 1,
    target,
  };
}
