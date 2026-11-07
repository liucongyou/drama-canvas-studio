import type {CanvasMediaNodeKind} from './api';


export const MAX_CANVAS_UPLOAD_FILES = 40;

const FILE_DRAG_TYPES = new Set([
  'Files',
  'application/x-moz-file',
]);
const CANVAS_DRAG_TYPES = new Set([
  'application/x-vimax-canvas',
  'application/reactflow',
  'application/xyflow',
]);

type DragTypes = Pick<DataTransfer, 'types'> | null | undefined;

function normalizedTypes(types: DragTypes): string[] {
  return Array.from(types?.types || []).map((value) => String(value));
}

export function dragHasFiles(dataTransfer: DragTypes): boolean {
  return normalizedTypes(dataTransfer).some((type) => (
    FILE_DRAG_TYPES.has(type)
    || type.toLowerCase() === 'files'
  ));
}

export function dragFromCanvasNode(dataTransfer: DragTypes): boolean {
  return normalizedTypes(dataTransfer).some((type) => (
    CANVAS_DRAG_TYPES.has(type)
    || type.startsWith('application/x-vimax-')
  ));
}

export function shouldArmCanvasFileDrop(dataTransfer: DragTypes): boolean {
  return dragHasFiles(dataTransfer) && !dragFromCanvasNode(dataTransfer);
}

export function canvasUploadKind(file: File): CanvasMediaNodeKind | null {
  const mime = String(file.type || '').toLowerCase();
  if (mime.startsWith('image/')) return 'image';
  if (mime.startsWith('audio/')) return 'audio';
  if (mime.startsWith('video/')) return 'video';
  const ext = String(file.name || '').split('.').pop()?.toLowerCase() || '';
  if (['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'avif'].includes(ext)) return 'image';
  if (['mp3', 'wav', 'ogg', 'm4a', 'flac', 'aac'].includes(ext)) return 'audio';
  if (['mp4', 'webm', 'mov'].includes(ext)) return 'video';
  return null;
}

export function prepareCanvasUploadFiles(
  files: File[],
  limit = MAX_CANVAS_UPLOAD_FILES,
): {
  accepted: File[];
  unsupported: number;
  overflow: number;
} {
  const supported = files.filter((file) => !!canvasUploadKind(file));
  return {
    accepted: supported.slice(0, Math.max(0, limit)),
    unsupported: files.length - supported.length,
    overflow: Math.max(0, supported.length - limit),
  };
}
