export const LARGE_CANVAS_MEDIA_THRESHOLD = 400;
export const LARGE_CANVAS_VIEWPORT_MARGIN_PX = 260;
export const LARGE_CANVAS_OVERVIEW_CHUNK_SIZE = 220;
export const CANVAS_VIEWPORT_POLICY_VERSION = 3;
export const CANVAS_FOCUS_MIN_ZOOM = 0.5;
export const CANVAS_RESTORE_MIN_ZOOM = 0.12;
export const CANVAS_RESTORE_MAX_ZOOM = 8;

export type CanvasViewport = {
  x: number;
  y: number;
  zoom: number;
};

export type EpisodeViewportMap = Record<string, CanvasViewport>;

export type CanvasVisibleBounds = {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
};

export type CanvasBounds = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export function validRestoredViewport(
  value: unknown,
): CanvasViewport | null {
  if (!value || typeof value !== 'object') return null;
  const candidate = value as Partial<CanvasViewport>;
  const x = Number(candidate.x);
  const y = Number(candidate.y);
  const zoom = Number(candidate.zoom);
  if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(zoom)) {
    return null;
  }
  if (zoom < CANVAS_RESTORE_MIN_ZOOM || zoom > CANVAS_RESTORE_MAX_ZOOM) {
    return null;
  }
  return {x, y, zoom};
}

export function shouldPersistViewport(
  value: CanvasViewport,
  lastStored: CanvasViewport | null | undefined,
): boolean {
  if (!lastStored) return true;
  return (
    Math.abs(value.x - lastStored.x) >= 0.5
    || Math.abs(value.y - lastStored.y) >= 0.5
    || Math.abs(value.zoom - lastStored.zoom) >= 0.0001
  );
}

export function mergeEpisodeViewport(
  current: Record<string, unknown> | null | undefined,
  episodeId: string | null | undefined,
  viewport: CanvasViewport,
  projectId?: string,
): EpisodeViewportMap {
  const next: EpisodeViewportMap = {};
  Object.entries(current || {}).forEach(([key, value]) => {
    const valid = validRestoredViewport(value);
    if (valid) next[key] = valid;
  });
  const scope = String(episodeId || '').trim().toUpperCase();
  if (scope) {
    next[scope] = validRestoredViewport(viewport) || viewport;
  } else if (projectId) {
    next[projectId] = validRestoredViewport(viewport) || viewport;
  }
  return next;
}

export function selectEpisodeViewport(
  viewports: Record<string, unknown> | null | undefined,
  episodeId: string | null | undefined,
  projectId?: string,
): CanvasViewport | null {
  if (!viewports) return null;
  const scope = String(episodeId || '').trim().toUpperCase();
  if (scope && viewports[scope]) return validRestoredViewport(viewports[scope]);
  if (projectId && viewports[projectId]) {
    return validRestoredViewport(viewports[projectId]);
  }
  return null;
}

export function episodeIdFromWorkspace(
  currentEpisode: string | null | undefined,
  mediaNodes: Array<{episodeId?: string | null}>,
): string | null {
  const current = String(currentEpisode || '').trim().toUpperCase();
  if (current) return current;
  for (const node of mediaNodes) {
    const episodeId = String(node.episodeId || '').trim().toUpperCase();
    if (episodeId) return episodeId;
  }
  return null;
}

export function chooseActiveEpisodeViewport(
  serverViewports: Record<string, unknown>,
  currentEpisode: string | null,
  projectId: string,
  localViewport: CanvasViewport | null,
): CanvasViewport | null {
  return (
    selectEpisodeViewport(serverViewports, currentEpisode, projectId)
    || validRestoredViewport(localViewport)
  );
}

export function shouldSaveViewport(
  restoreInFlight: boolean,
  activeScope: string,
  writeScope: string,
): boolean {
  return Boolean(
    !restoreInFlight
    && activeScope
    && writeScope
    && activeScope === writeScope
  );
}

type ViewportNodeLike = {
  kind: string;
  x: number;
  y: number;
};

export function shouldUseLargeCanvasOverview(
  nodes: ViewportNodeLike[],
): boolean {
  return nodes.length > LARGE_CANVAS_MEDIA_THRESHOLD
    && nodes.some((node) => node.kind !== 'text');
}

export function visibleBoundsForViewport(
  viewport: CanvasViewport,
  viewportWidth: number,
  viewportHeight: number,
  marginPx: number = LARGE_CANVAS_VIEWPORT_MARGIN_PX,
): CanvasVisibleBounds {
  const zoom = Math.max(0.05, viewport.zoom || 1);
  const margin = marginPx / zoom;
  const x1 = -viewport.x / zoom - margin;
  const y1 = -viewport.y / zoom - margin;
  return {
    x1,
    y1,
    x2: x1 + Math.max(1, viewportWidth) / zoom + margin * 2,
    y2: y1 + Math.max(1, viewportHeight) / zoom + margin * 2,
  };
}

export function focusedNodeVisibleBounds(
  node: {x: number; y: number; w: number; h: number},
  zoom: number,
  viewportWidth: number,
  viewportHeight: number,
  marginPx: number = LARGE_CANVAS_VIEWPORT_MARGIN_PX,
): CanvasVisibleBounds {
  const safeZoom = Math.max(0.05, zoom || 1);
  const halfWidth = Math.max(1, viewportWidth) / safeZoom / 2;
  const halfHeight = Math.max(1, viewportHeight) / safeZoom / 2;
  const margin = marginPx / safeZoom;
  const centerX = node.x + node.w / 2;
  const centerY = node.y + node.h / 2;
  return {
    x1: centerX - halfWidth - margin,
    y1: centerY - halfHeight - margin,
    x2: centerX + halfWidth + margin,
    y2: centerY + halfHeight + margin,
  };
}

export function viewportForCanvasBounds(
  bounds: CanvasBounds,
  viewportWidth: number,
  viewportHeight: number,
  options: {
    focus?: boolean;
    padding?: number;
    minZoom?: number;
    maxZoom?: number;
  } = {},
): CanvasViewport {
  const width = Math.max(1, viewportWidth);
  const height = Math.max(1, viewportHeight);
  const padding = Math.max(0, Math.min(0.35, options.padding ?? 0.15));
  const availableWidth = Math.max(1, width * (1 - padding * 2));
  const availableHeight = Math.max(1, height * (1 - padding * 2));
  const fitZoom = Math.min(
    availableWidth / Math.max(1, bounds.width),
    availableHeight / Math.max(1, bounds.height),
  );
  const minZoom = options.focus
    ? Math.max(CANVAS_FOCUS_MIN_ZOOM, options.minZoom ?? 0)
    : Math.max(0.05, options.minZoom ?? 0.05);
  const maxZoom = Math.max(minZoom, options.maxZoom ?? (options.focus ? 0.9 : 1));
  const zoom = Math.max(minZoom, Math.min(maxZoom, fitZoom));
  const centerX = bounds.x + bounds.width / 2;
  const x = width / 2 - centerX * zoom;
  const y = options.focus
    ? Math.max(42, Math.min(80, height * 0.1)) - bounds.y * zoom
    : height / 2 - (bounds.y + bounds.height / 2) * zoom;
  return {
    x: Math.round(x * 1000) / 1000,
    y: Math.round(y * 1000) / 1000,
    zoom: Math.round(zoom * 10000) / 10000,
  };
}

/**
 * Screen-space shift needed to keep a popup inside the canvas viewport.
 * Positive means moving canvas content up (increase viewport y).
 */
export function popupRevealShift(
  popupRect: {top: number; bottom: number},
  viewportHeight: number,
  options: {
    topInset?: number;
    bottomInset?: number;
  } = {},
): number {
  const topInset = Math.max(0, options.topInset ?? 12);
  const bottomInset = Math.max(0, options.bottomInset ?? 12);
  const safeTop = topInset;
  const safeBottom = Math.max(safeTop + 1, viewportHeight - bottomInset);
  if (popupRect.bottom > safeBottom) {
    return popupRect.bottom - safeBottom;
  }
  if (popupRect.top < safeTop) {
    return popupRect.top - safeTop;
  }
  return 0;
}

export function filterMediaNodesInBounds<T extends ViewportNodeLike>(
  nodes: T[],
  bounds: CanvasVisibleBounds,
  sizeOf: (node: T) => {w: number; h: number},
): T[] {
  return nodes.filter((node) => {
    const size = sizeOf(node);
    return (
      node.x + size.w >= bounds.x1
      && node.x <= bounds.x2
      && node.y + size.h >= bounds.y1
      && node.y <= bounds.y2
    );
  });
}

export function viewportLoadBounds(
  bounds: CanvasVisibleBounds,
  zoom: number,
  overscanPx = 640,
): CanvasVisibleBounds {
  const safeZoom = Math.max(0.05, zoom || 1);
  const overscan = Math.max(0, overscanPx) / safeZoom;
  return {
    x1: bounds.x1 - overscan,
    y1: bounds.y1 - overscan,
    x2: bounds.x2 + overscan,
    y2: bounds.y2 + overscan,
  };
}

export function shouldLoadMediaForViewport(input: {
  contentVisible: boolean;
  index: number;
  total: number;
  budget?: number;
}): boolean {
  if (input.contentVisible) return true;
  const budget = Math.max(1, input.budget ?? 12);
  return input.index >= 0 && input.index < Math.min(input.total, budget);
}
