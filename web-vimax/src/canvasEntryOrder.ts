import type {CanvasMediaNode} from './api';

type EntryFlowNode = {
  id: string;
  type?: string;
  position: {x: number; y: number};
  data?: unknown;
};

const LAYER = {
  background: 0,
  image: 10,
  text: 20,
  audio: 30,
  video: 40,
  foreground: 50,
} as const;

function numericSuffix(id: string): number {
  const match = /(?:^|[-_])(\d+)(?:$|[-_])/.exec(id);
  return match ? Number(match[1]) : Number.MAX_SAFE_INTEGER;
}

function stableCompare(left: string, right: string): number {
  if (left === right) return 0;
  return left < right ? -1 : 1;
}

export function mediaEntryOrder(
  left: CanvasMediaNode,
  right: CanvasMediaNode,
): number {
  const layer = LAYER[left.kind] - LAYER[right.kind];
  if (layer !== 0) return layer;
  const x = left.x - right.x;
  if (Math.abs(x) > 0.5) return x;
  const y = left.y - right.y;
  if (Math.abs(y) > 0.5) return y;
  const numeric = numericSuffix(left.id) - numericSuffix(right.id);
  if (numeric !== 0) return numeric;
  return stableCompare(left.id, right.id);
}

/**
 * Render cheap cards first so the first visible frame contains the graph
 * structure instead of whichever media type happened to arrive first.
 * Positions and IDs remain authoritative; this only changes mount order.
 */
export function sortMediaNodesForEntry(
  nodes: CanvasMediaNode[],
): CanvasMediaNode[] {
  return [...nodes].sort(mediaEntryOrder);
}

export function sortFlowNodesForEntry<T extends EntryFlowNode>(nodes: T[]): T[] {
  const layerOf = (node: T): number => {
    if (node.type === 'media') {
      const kind = (node.data as {node?: CanvasMediaNode} | undefined)
        ?.node?.kind;
      return kind ? LAYER[kind] : LAYER.foreground;
    }
    if (node.type === 'note') return LAYER.foreground;
    return LAYER.background;
  };
  return [...nodes].sort((left, right) => (
    layerOf(left) - layerOf(right)
    || left.position.x - right.position.x
    || left.position.y - right.position.y
    || stableCompare(left.id, right.id)
  ));
}
