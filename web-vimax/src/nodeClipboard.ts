import type {CanvasMediaNode, CanvasNote} from './api';


export type NodeClipboardSnapshot = {
  media: CanvasMediaNode[];
  notes: CanvasNote[];
  edges: {source: string; target: string}[];
};

export const NODE_CLIPBOARD_STORAGE_KEY = 'vimax.canvas.node-clipboard.v1';

function validSnapshot(value: unknown): value is NodeClipboardSnapshot {
  if (!value || typeof value !== 'object') return false;
  const snapshot = value as Partial<NodeClipboardSnapshot>;
  return Array.isArray(snapshot.media)
    && Array.isArray(snapshot.notes)
    && Array.isArray(snapshot.edges);
}

export function readNodeClipboard(): NodeClipboardSnapshot | null {
  try {
    const raw = window.sessionStorage.getItem(NODE_CLIPBOARD_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return validSnapshot(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function saveNodeClipboard(snapshot: NodeClipboardSnapshot): void {
  try {
    window.sessionStorage.setItem(
      NODE_CLIPBOARD_STORAGE_KEY,
      JSON.stringify(snapshot),
    );
  } catch {
    // The in-memory clipboard still works when storage is unavailable.
  }
}
