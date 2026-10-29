export const LARGE_CANVAS_MEDIA_THRESHOLD = 400;
export const LARGE_CANVAS_VIEWPORT_MARGIN_PX = 260;
export const LARGE_CANVAS_OVERVIEW_CHUNK_SIZE = 220;

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
