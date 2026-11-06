export const MEDIA_RETRY_LIMIT = 1;

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
  return serverConfirmedMissing
    || failures > MEDIA_RETRY_LIMIT;
}

export function cacheBustedMediaUrl(
  url: string,
  attempt: number,
): string {
  if (!url || url.startsWith('data:') || url.startsWith('blob:')) return url;
  const separator = url.includes('?') ? '&' : '?';
  return `${url}${separator}__media_retry=${Math.max(1, attempt)}`;
}
