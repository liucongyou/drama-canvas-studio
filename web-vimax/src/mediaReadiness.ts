export type MediaReadinessPhase =
  | 'idle'
  | 'generating'
  | 'loading'
  | 'ready'
  | 'load_failed'
  | 'failed'
  | 'missing';

export type MediaReadinessInput = {
  kind: 'image' | 'audio' | 'video' | 'text';
  status?: string;
  hasMedia?: boolean;
  mediaMissing?: boolean;
  mediaReady?: boolean;
  loadFailed?: boolean;
};

export function mediaReadinessPhase(
  input: MediaReadinessInput,
): MediaReadinessPhase {
  if (input.mediaMissing) return 'missing';
  if (input.status === 'generating') return 'generating';
  if (input.status === 'failed') return 'failed';
  if (input.status !== 'done') return 'idle';
  if (input.kind === 'text') return 'ready';
  if (!input.hasMedia) return 'load_failed';
  if (input.mediaReady) return 'ready';
  if (input.loadFailed) return 'load_failed';
  return 'loading';
}

export function mediaReadinessLabel(
  phase: MediaReadinessPhase,
  kind: 'image' | 'audio' | 'video' | 'text',
): string {
  if (phase === 'generating') return '生成中';
  if (phase === 'loading') return '素材加载中';
  if (phase === 'ready') return '已生成';
  if (phase === 'load_failed') return '素材加载失败 · 重试';
  if (phase === 'failed') return '生成失败';
  if (phase === 'missing') return '媒体文件丢失';
  return kind === 'text' ? '文本' : '待生成';
}

export function mediaReadinessClass(phase: MediaReadinessPhase): string {
  if (phase === 'generating' || phase === 'loading') return 'is-loading';
  if (phase === 'ready') return 'is-done';
  if (phase === 'load_failed' || phase === 'failed' || phase === 'missing') {
    return 'is-failed';
  }
  return 'is-idle';
}
