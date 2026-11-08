export function clampDurationInput(
  raw: string,
  durationMin: number,
  durationMax: number,
  fallback: number,
) {
  const text = String(raw).trim();
  const parsed = text === "" ? Number.NaN : Number(text);
  return Number.isFinite(parsed)
    ? Math.max(durationMin, Math.min(durationMax, parsed))
    : Math.max(durationMin, Math.min(durationMax, fallback));
}
