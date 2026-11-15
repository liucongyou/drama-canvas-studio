import {apiUrl} from './api';
import {APP_VERSION} from './changelog';

export type FrontendTelemetryInput = {
  message?: unknown;
  stack?: unknown;
  code?: unknown;
  projectId?: unknown;
  episodeId?: unknown;
  nodeId?: unknown;
  taskId?: unknown;
  route?: unknown;
  userAgent?: unknown;
  releaseVersion?: unknown;
  createdAt?: unknown;
};

const ALLOWED_FIELDS = [
  'message',
  'stack',
  'code',
  'projectId',
  'episodeId',
  'nodeId',
  'taskId',
  'route',
  'userAgent',
  'releaseVersion',
  'createdAt',
] as const;
const FIELD_LIMITS: Record<(typeof ALLOWED_FIELDS)[number], number> = {
  message: 2_000,
  stack: 12_000,
  code: 160,
  projectId: 200,
  episodeId: 80,
  nodeId: 200,
  taskId: 200,
  route: 600,
  userAgent: 400,
  releaseVersion: 120,
  createdAt: 80,
};
const SECRET_ASSIGNMENT =
  /\b(authorization|cookie|token|api[_-]?key|password|secret)(\s*[:=]\s*)([^\s,;]+)/gi;
const BEARER = /\bbearer\s+[A-Za-z0-9._~+/=-]+/gi;
const URL_QUERY = /(https?:\/\/[^\s?#]+)\?[^\s)]*/gi;

function cleanText(value: unknown, limit: number): string {
  return String(value ?? '')
    .trim()
    .replace(SECRET_ASSIGNMENT, '$1$2***')
    .replace(BEARER, 'Bearer ***')
    .replace(URL_QUERY, '$1')
    .slice(0, limit);
}

export function sanitizeTelemetry(
  input: FrontendTelemetryInput,
): Record<string, string> {
  const clean: Record<string, string> = {};
  for (const key of ALLOWED_FIELDS) {
    const value = cleanText(input[key], FIELD_LIMITS[key]);
    if (value) clean[key] = value;
  }
  if (!clean.message) return {};
  clean.createdAt ||= new Date().toISOString();
  return clean;
}

export function currentTelemetryContext() {
  if (typeof window === 'undefined') return {};
  const url = new URL(window.location.href);
  return {
    route: url.pathname,
    episodeId: url.searchParams.get('ep') || '',
    projectId: url.searchParams.get('project') || '',
    userAgent: navigator.userAgent,
    releaseVersion: APP_VERSION,
  };
}

export async function reportFrontendTelemetry(
  input: FrontendTelemetryInput,
): Promise<void> {
  const clean = sanitizeTelemetry({
    ...currentTelemetryContext(),
    ...input,
  });
  if (!clean.message) return;
  try {
    await fetch(apiUrl('/api/telemetry/frontend-errors'), {
      method: 'POST',
      credentials: 'include',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(clean),
      keepalive: true,
    });
  } catch {
    // Telemetry must never break the UI or replace the original error.
  }
}
