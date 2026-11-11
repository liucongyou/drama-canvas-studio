import type {AgentMessage, AgentRun} from './agentApi';

export function cleanRunText(content: string): string {
  let text = String(content || '').trim();
  if (!text) return '';
  const repairIndex = text.indexOf('上次画布预检问题');
  if (repairIndex >= 0) text = text.slice(0, repairIndex).trim();
  if (/^(生成完成，但|生成失败|导入预检未通过|投喂稿已通过(?:画布)?预检)/.test(text)) return '';
  return text;
}

export function transcriptMessages(run: AgentRun): AgentMessage[] {
  return (run.messages || [])
    .filter((message) => message.role === 'user' || message.role === 'assistant')
    .map((message) => ({...message, content: cleanRunText(message.content)}))
    .filter((message) => message.content);
}

export function executionDraftMessages(run: AgentRun): AgentMessage[] {
  return (run.messages || [])
    .filter((message) => message.role === 'draft')
    .map((message) => ({...message, content: String(message.content || '').trim()}))
    .filter((message) => message.content);
}

export function hasImportableFeed(run: AgentRun): boolean {
  const artifact = (run.artifacts || [])
    .filter((item) => item.kind === 'canvas_feed')
    .slice(-1)[0];
  if (!artifact) return false;
  const errors = artifact.payload && Array.isArray(artifact.payload.errors)
    ? artifact.payload.errors
    : [];
  return errors.length === 0;
}

export function pendingImportRuns(runs: Array<AgentRun | null | undefined>): AgentRun[] {
  const seen = new Set<string>();
  const pending: AgentRun[] = [];
  for (const run of runs) {
    if (!run || seen.has(run.id)) continue;
    seen.add(run.id);
    if (run.status === 'waiting_user' && hasImportableFeed(run)) pending.push(run);
  }
  return pending;
}
