import type {Canvas, CanvasEp, WorkspaceIndex} from './api';

type EpisodeSummary = WorkspaceIndex['episodes'][number];
type EpisodeSource = EpisodeSummary | CanvasEp;

function summaryEpisodeId(summary: EpisodeSource): string {
  return String(
    'id' in summary ? summary.id : summary.ep_id,
  ).trim().toUpperCase();
}

function canonicalEpisode(
  summary: EpisodeSource,
  previous?: CanvasEp,
): CanvasEp | null {
  const epId = summaryEpisodeId(summary)
    || String(previous?.ep_id || '').trim().toUpperCase();
  if (!epId) return null;
  const summaryRecord = summary as Record<string, unknown>;
  const shotCount = typeof summary.shot_count === 'number'
    ? summary.shot_count
    : previous?.shot_count ?? null;
  return {
    ...previous,
    ep_id: epId,
    title: String(summary.title || previous?.title || epId),
    progress: previous?.progress ?? null,
    storyboard_ready: typeof summary.storyboard_ready === 'boolean'
      ? summary.storyboard_ready
      : previous?.storyboard_ready ?? true,
    shot_count: shotCount,
    described_count: typeof summary.described_count === 'number'
      ? summary.described_count
      : previous?.described_count ?? shotCount,
    keyframes: previous?.keyframes ?? 0,
    clips: previous?.clips ?? 0,
    final_ready: previous?.final_ready ?? false,
    status: previous?.status || (shotCount ? 'ready' : 'empty'),
    updated_at: previous?.updated_at
      || String(summaryRecord.importedAt || summaryRecord.updatedAt || '') || null,
    segments_total: previous?.segments_total ?? (
      Array.isArray(summaryRecord.videoNodeIds)
        ? summaryRecord.videoNodeIds.length
        : null
    ),
    segments_rendered: previous?.segments_rendered ?? 0,
  };
}

/**
 * Merge one episode's graph into the complete project-level episode catalog.
 *
 * A compact workspace response intentionally contains only the selected
 * episode. Using it as the Canvas itself makes every sibling episode vanish.
 */
export function mergeEpisodeGraph<T extends Canvas | {empty: true}>(
  graph: T,
  projectEpisodes: EpisodeSource[],
  fallbackEpisodes: CanvasEp[] = [],
): T {
  if ('empty' in graph) return graph;
  const fallbackById = new Map(
    fallbackEpisodes.map((episode) => [episode.ep_id, episode]),
  );
  const graphById = new Map(
    graph.meta.episodes.map((episode) => [episode.ep_id, episode]),
  );
  const indexedEpisodes = projectEpisodes
    .map((summary) => {
      const episodeId = summaryEpisodeId(summary);
      return canonicalEpisode(
        summary,
        fallbackById.get(episodeId) || graphById.get(episodeId),
      );
    })
    .filter((episode): episode is CanvasEp => Boolean(episode));
  const episodes = indexedEpisodes.length > 0
    ? indexedEpisodes
    : fallbackEpisodes.length > 0
      ? fallbackEpisodes
      : graph.meta.episodes;
  return {
    ...graph,
    meta: {
      ...graph.meta,
      episodes,
      current_ep: graph.meta.current_ep
        || episodes[0]?.ep_id
        || 'EP001',
    },
  };
}
