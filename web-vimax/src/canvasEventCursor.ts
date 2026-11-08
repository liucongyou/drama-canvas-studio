type MessageLike = {
  lastEventId?: string;
  data?: string;
};

type EventPayload = Record<string, unknown>;

export function canvasEventId(
  event: MessageLike | string,
  payload?: EventPayload,
): number {
  const fromEvent = typeof event === 'object'
    ? Number(event.lastEventId || 0)
    : Number(event || 0);
  if (Number.isFinite(fromEvent) && fromEvent > 0) return fromEvent;
  const fromPayload = Number(
    payload?.eventId
    ?? payload?.id
    ?? 0,
  );
  return Number.isFinite(fromPayload) && fromPayload > 0 ? fromPayload : 0;
}

/**
 * A workspace snapshot contains every persisted change through eventCursor.
 * Replayed events up to that boundary are historical notifications, not new
 * work; processing them would trigger a second projection read on every load.
 */
export function shouldProcessCanvasEvent(
  eventId: number,
  snapshotCursor: number,
): boolean {
  return eventId <= 0 || eventId > Math.max(0, snapshotCursor);
}

export function eventBelongsToEpisode(
  payload: EventPayload,
  currentEpisodeId: string,
): boolean {
  const eventEpisode = String(
    payload.episodeId
    ?? payload.episode_id
    ?? '',
  ).trim().toUpperCase();
  if (!eventEpisode) return true;
  const currentEpisode = String(currentEpisodeId || '').trim().toUpperCase();
  return !currentEpisode || eventEpisode === currentEpisode;
}

export function readCanvasEventCursor(sessionId: string): number {
  if (typeof window === 'undefined') return 0;
  try {
    const key = `vimax.canvas.event-cursor.${sessionId}`;
    return Math.max(0, Number(window.localStorage.getItem(key) || 0) || 0);
  } catch {
    return 0;
  }
}

export function writeCanvasEventCursor(
  sessionId: string,
  eventId: number,
): void {
  if (typeof window === 'undefined' || !(eventId > 0)) return;
  try {
    const key = `vimax.canvas.event-cursor.${sessionId}`;
    const current = Math.max(
      0,
      Number(window.localStorage.getItem(key) || 0) || 0,
    );
    if (eventId > current) {
      window.localStorage.setItem(key, String(eventId));
    }
  } catch {
    // EventSource still carries Last-Event-ID while this connection lives.
  }
}
