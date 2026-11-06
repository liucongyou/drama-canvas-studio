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
  const keys = new Set<string>();
  const add = (kind: string, title: unknown) => {
    const normalized = normalizedAssetTitle(title);
    if (!normalized) return;
    keys.add(`${kind}:${normalized}`);
  };

  canvas.characters.forEach((item) => add('image', item.name));
  canvas.scenes.forEach((item) => add('image', item.name));
  canvas.props.forEach((item) => add('image', item.name));
  canvas.voices.forEach((item) => add('audio', item.name));

  let videoDurationSeconds = 0;
  mediaNodes.forEach((node) => {
    if (node.kind === 'video') {
      if (node.status === 'done' && node.mediaUrl) {
        const duration = Number(node.actualDuration);
        if (Number.isFinite(duration) && duration > 0) {
          videoDurationSeconds += duration;
        }
      }
      return;
    }
    if (node.kind === 'image' || node.kind === 'audio') {
      add(node.kind, node.title);
    }
  });

  return {
    assetCount: keys.size,
    videoDurationSeconds,
  };
}

export function formatCanvasDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds));
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, '0')}`;
}
