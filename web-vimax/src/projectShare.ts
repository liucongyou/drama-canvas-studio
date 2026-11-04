export type ProjectShare = {
  id: string;
  projectId: string;
  tenantId: string;
  title: string;
  token: string;
  createdAt: string;
  expiresAt: string;
};

export function normalizeShareToken(value: unknown): string {
  return String(value ?? '').trim();
}

export function shareTokenInPath(pathname: string): string {
  const match = /^\/share\/([^/?#]+)/.exec(String(pathname || ''));
  return match ? normalizeShareToken(decodeURIComponent(match[1])) : '';
}
