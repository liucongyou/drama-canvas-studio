export function reconcileSelectedProjectId(
  currentProjectId: string,
  projectIds: readonly string[],
  hashProjectId = '',
): string {
  if (currentProjectId && projectIds.includes(currentProjectId)) {
    return currentProjectId;
  }
  if (hashProjectId && projectIds.includes(hashProjectId)) {
    return hashProjectId;
  }
  return projectIds[0] || '';
}

/**
 * Restoring the same project from a URL/hash is not a user-initiated project
 * switch. Keep the episode query in that case; clear it only when navigating
 * to a different project so an episode id cannot leak across projects.
 */
export function shouldClearEpisodeOnProjectRestore(
  currentProjectId: string,
  nextProjectId: string,
): boolean {
  return Boolean(
    currentProjectId
    && nextProjectId
    && currentProjectId !== nextProjectId,
  );
}
