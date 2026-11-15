export const MEDIA_RETRY_LIMIT = 2;

export function shouldRetryMediaLoad(
  failures: number,
  hasSource: boolean,
): boolean {
  return hasSource && failures > 0 && failures <= MEDIA_RETRY_LIMIT;
}

export function shouldMarkMediaMissing(
  failures: number,
  serverConfirmedMissing: boolean,
): boolean {
  return serverConfirmedMissing;
}

export function mediaMissingFromBackend(serverAvailability: unknown): boolean {
  return String(serverAvailability || '').trim().toLowerCase() === 'missing';
}

export function shouldAnalyzeAudioWaveform(
  mediaUrl: string,
  serverAvailability: unknown,
  pageOrigin?: string,
): boolean {
  if (!mediaUrl || mediaUrl.startsWith('data:')
    || mediaMissingFromBackend(serverAvailability)) {
    return false;
  }
  if (!pageOrigin) return true;
  try {
    const media = new URL(mediaUrl, pageOrigin);
    const page = new URL(pageOrigin);
    if (/^https?:$/.test(media.protocol) && media.origin !== page.origin) {
      return false;
    }
  } catch {
    return false;
  }
  return true;
}

export function isTransientMediaError(error?: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const name = String((error as {name?: unknown}).name || '');
  return name === 'AbortError' || name === 'NotReadableError'
    || name === 'NotSupportedError' || name === 'NetworkError';
}

export function shouldIgnorePlaybackInterruption(error?: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const name = String((error as {name?: unknown}).name || '');
  return name === 'AbortError';
}

export function cacheBustedMediaUrl(
  url: string,
  attempt: number,
): string {
  if (!url || url.startsWith('data:') || url.startsWith('blob:')) return url;
  const separator = url.includes('?') ? '&' : '?';
  return `${url}${separator}__media_retry=${Math.max(1, attempt)}`;
}
