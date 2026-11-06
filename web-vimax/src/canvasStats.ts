import type {Canvas, CanvasMediaNode} from './api';

export type CanvasHeaderStats = {
  assetCount: number;
  videoDurationSeconds: number;
};

function normalizedAssetTitle(raw: unknown): string {
  return String(raw || '')
    .trim()
    .replace(/^[^｜|]*[｜|]\s*/, '')
    .replace(/\s+/g, ' ')
    .toLocaleLowerCase('zh-CN');
}

/**
 * Header counts are based on the current episode's media cards, not on the
 * legacy asset projection. Generated results update media cards, while the
 * projection may still contain both the asset row and its generated copy.
 */
export function canvasHeaderStats(
  canvas: Canvas | null,
  mediaNodes: CanvasMediaNode[],
): CanvasHeaderStats {
  if (!canvas) return {assetCount: 0, videoDurationSeconds: 0};
  const countableNodes = mediaNodes.filter((node) => (
    node.kind === 'image'
    || node.kind === 'audio'
    || (node.kind === 'video' && Boolean(node.mediaUrl))
  ));
  let videoDurationSeconds = 0;
  countableNodes.forEach((node) => {
    if (node.kind === 'video' && node.status === 'done') {
      const duration = Number(node.actualDuration);
      if (Number.isFinite(duration) && duration > 0) {
        videoDurationSeconds += duration;
      }
    }
  });

  if (countableNodes.length === 0) {
    // Compatibility for a project that has not been projected into media
    // cards yet. Once cards exist, their IDs are the only count authority.
    const legacy = [
      ...canvas.characters.map((item) => `image:${item.name}`),
      ...canvas.scenes.map((item) => `image:${item.name}`),
      ...canvas.props.map((item) => `image:${item.name}`),
      ...canvas.voices.map((item) => `audio:${item.name}`),
    ].filter((key) => normalizedAssetTitle(key.slice(key.indexOf(':') + 1)));
    return {
      assetCount: new Set(legacy).size,
      videoDurationSeconds: 0,
    };
  }

  return {
    assetCount: countableNodes.length,
    videoDurationSeconds,
  };
}

export function formatCanvasDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds));
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, '0')}`;
}
