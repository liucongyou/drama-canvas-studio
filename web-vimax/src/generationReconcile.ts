export type ReconcileNodeLike = {
  id: string;
  status?: string;
  mediaUrl?: string;
  lastError?: string;
  generationFinishedAt?: string;
  providerTaskStatus?: string;
  providerTaskPhase?: string;
  providerTaskProgress?: number;
  params?: {
    mediaVersions?: Array<{url?: string}>;
    mediaVersionIndex?: number;
    videoVersions?: Array<{url?: string}>;
    videoVersionIndex?: number;
  };
};

export type ReconcileTaskLike = {
  id: string;
  node_id: string;
  status: string;
  finished_at?: string;
};

/**
 * A succeeded persisted task must win over a stale failed node.
 *
 * The old UI only accepted a terminal task while the node was still
 * "generating". A transient provider timeout could mark the node as failed,
 * then the background task would finish successfully, but the card stayed
 * failed forever and never showed the final media URL.
 */
export function reconcileSucceededNode<T extends ReconcileNodeLike>(
  node: T,
  task: ReconcileTaskLike,
  remote: ReconcileNodeLike,
): T | null {
  if (task.status !== 'succeeded' || task.node_id !== node.id) return null;
  if (remote.id !== node.id || remote.status !== 'done') return null;
  const nextUrl = remote.mediaUrl || node.mediaUrl;
  if (node.status === 'done' && node.mediaUrl === nextUrl && !node.lastError) return null;
  return {
    ...node,
    status: 'done',
    mediaUrl: nextUrl,
    ...(remote.params ? {params: {
      ...(node.params || {}),
      ...(remote.params.mediaVersions
        ? {mediaVersions: remote.params.mediaVersions}
        : {}),
      ...(remote.params.mediaVersionIndex !== undefined
        ? {mediaVersionIndex: remote.params.mediaVersionIndex}
        : {}),
      ...(remote.params.videoVersions
        ? {videoVersions: remote.params.videoVersions}
        : {}),
      ...(remote.params.videoVersionIndex !== undefined
        ? {videoVersionIndex: remote.params.videoVersionIndex}
        : {}),
    }} : {}),
    lastError: undefined,
    generationFinishedAt: remote.generationFinishedAt || task.finished_at,
    providerTaskStatus: 'completed',
    providerTaskPhase: '已完成',
    providerTaskProgress: 100,
  } as T;
}
