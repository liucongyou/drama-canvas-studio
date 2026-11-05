import type {CanvasMediaNodeKind} from './api';

type MediaDownloadNameInput = {
  projectTitle: string;
  episodeId: string;
  nodeTitle: string;
  kind: CanvasMediaNodeKind;
  url: string;
  versionIndex?: number;
  versionTotal?: number;
};

const EXTENSION_BY_KIND: Record<Exclude<CanvasMediaNodeKind, 'text'>, string> = {
  image: '.png',
  audio: '.mp3',
  video: '.mp4',
};

const MEDIA_EXTENSION_RE = /\.(png|jpe?g|webp|gif|mp4|webm|mov|m4v|mp3|wav|m4a)(?:[?#]|$)/i;

function cleanPart(value: string, fallback: string): string {
  const cleaned = String(value || '')
    .replace(MEDIA_EXTENSION_RE, '')
    .replace(/[\\/:*?"<>|\r\n]+/g, '_')
    .replace(/\s+/g, ' ')
    .replace(/_+/g, '_')
    .replace(/^[ ._]+|[ ._]+$/g, '');
  return cleaned || fallback;
}

function shortProjectTitle(value: string): string {
  const normalized = String(value || '').trim();
  const projectMatch = /^(.+?)(?:[：:，,]\s*第?\s*\d+\s*集|[：:·])/.exec(normalized);
  return cleanPart(projectMatch?.[1] || normalized, '项目');
}

function episodeLabel(value: string): string {
  const match = /(?:EP)?0*(\d+)/i.exec(String(value || '').trim());
  return match ? `EP${String(Number(match[1])).padStart(2, '0')}` : cleanPart(value, 'EP01');
}

function segmentLabel(value: string): string {
  const match = /(?:^|[^\d])段\s*(\d+)/.exec(String(value || ''));
  if (match) return `段${Number(match[1])}`;
  const crowd = /^群像｜(.+?)(?:[（(]图\s*\d+[）)])?$/.exec(String(value || ''));
  if (crowd) return cleanPart(`群像_${crowd[1]}`, '群像');
  return cleanPart(value, '素材');
}

function mediaExtension(url: string, kind: CanvasMediaNodeKind): string {
  if (kind === 'text') return '.txt';
  const match = MEDIA_EXTENSION_RE.exec(String(url || ''));
  return match ? `.${match[1].toLowerCase().replace('jpeg', 'jpg')}` : EXTENSION_BY_KIND[kind];
}

export function mediaDownloadFilename({
  projectTitle,
  episodeId,
  nodeTitle,
  kind,
  url,
  versionIndex = 0,
  versionTotal = 0,
}: MediaDownloadNameInput): string {
  const parts = [
    shortProjectTitle(projectTitle),
    episodeLabel(episodeId),
    segmentLabel(nodeTitle),
  ];
  if (versionTotal > 1) parts.push(`版本${Number(versionIndex) + 1}`);
  return `${parts.join('_')}${mediaExtension(url, kind)}`;
}
