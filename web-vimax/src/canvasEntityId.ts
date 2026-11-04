export function newCanvasEntityId(
  prefix: string,
  cryptoApi: Pick<Crypto, 'randomUUID'> | undefined = globalThis.crypto,
): string {
  const normalizedPrefix = String(prefix || 'entity').trim() || 'entity';
  const uuid = cryptoApi?.randomUUID?.();
  const suffix = uuid
    || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  return `${normalizedPrefix}-${suffix}`;
}
