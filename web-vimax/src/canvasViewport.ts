export const LARGE_CANVAS_MEDIA_THRESHOLD = 400;
export const LARGE_CANVAS_VIEWPORT_MARGIN_PX = 260;
export const LARGE_CANVAS_OVERVIEW_CHUNK_SIZE = 220;
export const CANVAS_VIEWPORT_POLICY_VERSION = 2;
export const CANVAS_FOCUS_MIN_ZOOM = 0.5;

export type CanvasViewport = {
  x: number;
  y: number;
  zoom: number;
};

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
