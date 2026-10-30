export function reconcileSelectedProjectId(
  currentProjectId: string,
  projectIds: readonly string[],
  hashProjectId = '',
): string {
  if (!currentProjectId || projectIds.includes(currentProjectId)) {
    return currentProjectId;
  }
  if (hashProjectId && projectIds.includes(hashProjectId)) {
    return hashProjectId;
  }
  return projectIds[0] || '';
}
